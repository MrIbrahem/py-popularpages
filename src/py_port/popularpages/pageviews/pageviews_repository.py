"""
Wikimedia Pageviews REST API client, ported from src/PageviewsRepository.php.

Fetches monthly pageview timeseries for one or more articles (plus their
redirects) and sums them into per-target-page totals. Requests that receive
a 429 or 503 response are automatically retried with exponential backoff,
mirroring the PHP version's use of caseyamcl/guzzle_retry_middleware.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ..config import app_config

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """
    Decide whether a failed Pageviews request should be retried.

    Mirrors the PHP Guzzle retry middleware, which retries on 429/503 *and*
    connection timeouts, and honors the server's ``Retry-After`` header (the
    latter handled in :func:`_retry_wait`). PyMySQL-less HTTP errors that are
    not 4xx/5xx transport problems (e.g. DNS/connect) are also retried.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in app_config.pageviews.retry_status_codes
    # Connection/transport errors and timeouts are retryable too.
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    return False


def _retry_wait(retry_state: RetryCallState) -> float:
    """
    Wait time between retries.

    Honors a ``Retry-After`` header (seconds) when present on the failed
    response, otherwise falls back to exponential backoff (matching the PHP
    middleware's ``Retry-After``-aware behavior).
    """
    outcome = retry_state.outcome
    if outcome is not None and outcome.failed:
        exc = outcome.exception()
        if isinstance(exc, httpx.HTTPStatusError):
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return float(retry_after)
                except (TypeError, ValueError):
                    pass
    return wait_exponential(multiplier=1, min=1, max=30)(retry_state)


class PageviewsRepository:
    """
    Fetches monthly pageviews from the Wikimedia Pageviews REST API.

    Much of this was borrowed from wikimedia/eventmetrics (GPL-3.0-or-later).
    The REST endpoint is separate from the wiki action API, so ``mwclient``
    does not apply here and we use ``httpx`` directly with ``tenacity`` for
    retries on 429/503.
    """

    def __init__(
        self,
        domain: str,
        delay_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        """
        Initialize the Pageviews REST API client for a wiki.

        Creates the async HTTP client with the configured timeouts and
        user-agent, and applies the per-request rate-limit delay. An optional
        custom transport can be supplied (used in tests to avoid real network
        calls).

        Args:
            domain (str): The wiki domain, e.g. 'en.wikipedia'.
            delay_seconds (float | None): Override for the per-request rate-limit delay
                (defaults to ``app_config.pageviews.request_delay_seconds``).
            transport (httpx.AsyncBaseTransport | None): Optional custom transport (e.g. ``httpx.MockTransport``
                in tests). When omitted, the real default transport is used, which
                builds a genuine TLS/SSL context — pass a transport explicitly in
                tests to skip that cost and avoid any real network capability.
        """
        self.domain = domain
        logger.debug("PageviewsRepository initialized for domain '%s'", domain)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                app_config.pageviews.request_timeout_seconds,
                connect=app_config.pageviews.connect_timeout_seconds,
            ),
            headers={"User-Agent": app_config.other.user_agent},
            transport=transport,
        )

        if delay_seconds is None:
            delay_seconds = app_config.pageviews.request_delay_seconds

        self.delay_seconds = delay_seconds

    async def aclose(self) -> None:
        """
        Close the underlying HTTP client. Call when done with this repository.
        """
        logger.debug("Closing PageviewsRepository HTTP client for '%s'", self.domain)
        await self._client.aclose()

    def _log_retry(self, retry_state: RetryCallState) -> None:
        outcome = retry_state.outcome
        status = "no response"
        if outcome and outcome.failed:
            exc = outcome.exception()
            if isinstance(exc, httpx.HTTPStatusError):
                status = str(exc.response.status_code)

        msg = (
            f"Attempt #{retry_state.attempt_number} to retry pageviews request. "
            f"Server responded with {status}. "
            f"Waiting {retry_state.next_action.sleep:.2f} seconds."
            if retry_state.next_action
            else "Retrying pageviews request."
        )
        logger.info("[%s] %s", self.domain, msg)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=_retry_wait,
        stop=stop_after_attempt(app_config.pageviews.max_retry_attempts),
        after=lambda retry_state: retry_state.args[0]._log_retry(retry_state),
        reraise=True,
    )
    async def _get(self, article: str, start: str, end: str) -> httpx.Response:
        # Percent-encode the article title (mirrors PHP's rawurlencode()). MediaWiki
        # page titles may contain &, /, ?, #, +, % etc.; without encoding the URL is
        # malformed and the API returns no data (silently counted as 0 pageviews).
        encoded_article = quote(article, safe="")
        url = (
            f"{app_config.pageviews.endpoint_url}/{self.domain}/all-access/user/{encoded_article}/monthly/{start}/{end}"
        )
        logger.debug("GET %s", url)
        response = await self._client.get(url)
        response.raise_for_status()
        logger.debug("GET %s -> %s", url, response.status_code)
        return response

    async def get_pageviews(self, batch: dict[str, list[str]], start: str, end: str) -> dict[str, int]:
        """
        Get the combined pageviews of the given articles.

        For each target page in the batch, fetches the monthly views of the
        target and all of its redirects, then sums them so the caller receives
        one total per target. Redirects are handled internally via the batched
        mapping.

        Args:
            batch (dict[str, list[str]]): Keys are target page names, values are lists of the
                target page + its redirects (page titles as they should be
                queried for, i.e. with underscores replaced by spaces upstream).
            start (str): Start date in YYYYMMDD00 format.
            end (str): End date in YYYYMMDD00 format.

        Returns:
            dict[str, int]: Dict mapping target page name -> total pageviews.
        """
        target_titles = list(batch.keys())
        pageviews: dict[str, int] = dict.fromkeys(target_titles, 0)

        # All unique page titles (targets + redirects) to be queried.
        all_titles: set[str] = set()
        for key, titles in batch.items():
            all_titles.add(key)
            all_titles.update(t for t in titles if t)

        logger.info(
            "Fetching pageviews for %d target(s) across %d unique titles (start=%s, end=%s)",
            len(target_titles),
            len(all_titles),
            start,
            end,
        )

        # gather returns a list of tuples: [(title, views_count), (title, views_count), ...]
        results = await asyncio.gather(
            *(
                self._fetch_title_views(
                    t,
                    start,
                    end,
                )
                for t in all_titles
            )
        )

        # Convert list of tuples to a dict.
        views_by_title = dict(results)

        for title, count in views_by_title.items():
            for target in target_titles:
                if title == target or title in batch[target]:
                    pageviews[target] += count
                    break

        return pageviews

    async def _fetch_title_views(self, title: str, start: str, end: str) -> tuple[str, int]:
        """
        Fetch the total monthly pageviews for a single title.

        Returns (title, views_count).

        Returns (title, 0) when the title has no data (404) or when a transport/network
        error occurs; only 429/5xx-style retryable failures are retried by the
        tenacity wrapper on :meth:`_get`.
        """
        await asyncio.sleep(self.delay_seconds)
        article = title.replace(" ", "_")
        try:
            response = await self._get(article, start, end)
            page, count = self._process_response(response.json())
            if page is None or count is None:
                return title, 0
            return title, int(count)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # No data available; okay to treat as 0 views.
                return title, 0
            logger.error("[%s] Exception during pageviews request: %s", self.domain, exc)
            return title, 0
        except httpx.HTTPError as exc:
            logger.error("[%s] Exception during pageviews request: %s", self.domain, exc)
            return title, 0

    @staticmethod
    def _process_response(response: dict) -> tuple[Any | None, int | None]:
        """
        Parse a Pageviews API response, returning (article, total views).

        Sums the per-month ``views`` across all items in the response and returns
        the canonical article name (with underscores replaced by spaces). Returns
        ``(None, None)`` when the response contains no items.

        Args:
            response (dict): Parsed JSON body from the API.

        Returns:
            tuple[Any | None, int | None]: (article name with underscores replaced by spaces, total
                pageviews) or None if there were no pageviews.
        """
        items = response.get("items")
        if not items:
            return None, None

        article = None
        total_views = 0
        # Reverse so the final ``article`` matches the PHP behaviour (first item).
        for item in reversed(items):
            total_views += int(item["views"])
            article = item["article"].replace("_", " ")

        return article, total_views

    # ---------------------------------------------------
    # Public Methods
    # ---------------------------------------------------
    async def get_title_views(self, titles: list[str], start: str, end: str) -> dict[str, int]:
        """
        Fetch total pageviews for each of the given titles, once each.

        Unlike :meth:`get_pageviews`, this does *not* deal with target/redirect
        grouping; it simply returns ``{title: total_views}`` for every title
        requested. Callers (e.g. the cross-project :class:`PageviewsCache`)
        are responsible for summing a target with its redirects.

        Args:
            titles (list[str]): Page titles (spaces) to query, deduplicated by caller.
            start (str): Start date in YYYYMMDD00 format.
            end (str): End date in YYYYMMDD00 format.

        Returns:
            dict[str, int]: Dict mapping each requested title -> total pageviews (0 if
                missing / errored).
        """

        # gather returns a list of tuples: [(title, views_count), (title, views_count), ...]
        results = await asyncio.gather(
            *(
                self._fetch_title_views(
                    t,
                    start,
                    end,
                )
                for t in titles
            )
        )

        # Convert list of tuples to a dict.
        return dict(results)


__all__ = [
    "PageviewsRepository",
]
