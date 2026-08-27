"""
Wikimedia Pageviews REST API client, ported from src/PageviewsRepository.php.

Fetches monthly pageview timeseries for one or more articles (plus their
redirects) and sums them into per-target-page totals. Requests that receive
a 429 or 503 response are automatically retried with exponential backoff,
mirroring the PHP version's use of caseyamcl/guzzle_retry_middleware.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .logger import log_to_file

ENDPOINT_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

REQUEST_TIMEOUT_SECONDS = 3.0
CONNECT_TIMEOUT_SECONDS = 3.0

# Delay between individual outgoing requests within a batch. This
# approximates the PHP client's `delay` option (500ms), which staggers
# dispatch of the underlying Guzzle promises.
REQUEST_DELAY_SECONDS = 0.5  # matches PHP's REQUEST_DELAY = 500ms

MAX_RETRY_ATTEMPTS = 5


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503)


class PageviewsRepository:
    """
    Fetches monthly pageviews from the Wikimedia Pageviews REST API.

    Much of this was borrowed from wikimedia/eventmetrics (GPL-3.0-or-later).
    The REST endpoint is separate from the wiki action API, so ``mwclient``
    does not apply here and we use ``httpx`` directly with ``tenacity`` for
    retries on 429/503.
    """

    def __init__(self, domain: str):
        """
        :param domain: The wiki domain, e.g. 'en.wikipedia'.
        """
        self.domain = domain
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        )

    async def aclose(self) -> None:
        """
        Close the underlying HTTP client. Call when done with this repository."""
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
        log_to_file(msg, self.domain)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        after=lambda retry_state: retry_state.args[0]._log_retry(retry_state),
        reraise=True,
    )
    async def _get(self, article: str, start: str, end: str) -> httpx.Response:
        url = f"{ENDPOINT_URL}/{self.domain}/all-access/user/{article}/monthly/{start}/{end}"
        response = await self._client.get(url)
        response.raise_for_status()
        return response

    async def get_pageviews(self, batch: dict[str, list[str]], start: str, end: str) -> dict[str, int]:
        """
        Get the combined pageviews of the given articles.

        :param batch: Keys are target page names, values are lists of the
            target page + its redirects (page titles as they should be
            queried for, i.e. with underscores replaced by spaces upstream).
        :param start: Start date in YYYYMMDD00 format.
        :param end: End date in YYYYMMDD00 format.
        :return: Dict mapping target page name -> total pageviews.
        """
        target_titles = list(batch.keys())
        pageviews: dict[str, int] = dict.fromkeys(target_titles, 0)

        # All unique page titles (targets + redirects) to be queried.
        all_titles: set[str] = set()
        for titles in batch.values():
            all_titles.update(t for t in titles if t)

        async def fetch_one(title: str) -> tuple[Any | None, int | None] | None:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            article = title.replace(" ", "_")
            try:
                response = await self._get(article, start, end)
                return self._process_response(response.json())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # No data available; okay to omit this page from the report.
                    return None
                log_to_file(f"Exception during pageviews request: {exc}", self.domain)
                return None
            except httpx.HTTPError as exc:
                log_to_file(f"Exception during pageviews request: {exc}", self.domain)
                return None

        results = await asyncio.gather(*(fetch_one(title) for title in all_titles))

        for result in results:
            if result is None:
                continue
            page, count = result
            for target in target_titles:
                if page in batch[target]:
                    pageviews[target] += count
                    break

        return pageviews

    @staticmethod
    def _process_response(response: dict) -> tuple[Any | None, int | None]:
        """
        Parse a Pageviews API response, returning (article, total views).

        :param response: Parsed JSON body from the API.
        :return: (article name with underscores replaced by spaces, total
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
