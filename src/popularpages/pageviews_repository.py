"""Wikimedia Pageviews REST API client, ported from src/PageviewsRepository.php.

Fetches monthly pageview timeseries for one or more articles (plus their
redirects) and sums them into per-target-page totals. Requests that receive
a 429 or 503 response are automatically retried with exponential backoff,
mirroring the PHP version's use of caseyamcl/guzzle_retry_middleware.
"""

from __future__ import annotations

import asyncio

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .logger import log_to_file

ENDPOINT = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
REQUEST_DELAY_SECONDS = 0.5  # matches PHP's REQUEST_DELAY = 500ms


def _should_retry(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503)


class PageviewsRepository:
    """Fetches monthly pageviews from the Wikimedia Pageviews REST API.

    Much of this was borrowed from wikimedia/eventmetrics (GPL-3.0-or-later).
    The REST endpoint is separate from the wiki action API, so ``mwclient``
    does not apply here and we use ``httpx`` directly with ``tenacity`` for
    retries on 429/503.
    """

    def __init__(self, domain: str):
        self.domain = domain
        self._client = httpx.AsyncClient(timeout=3.0)

    @retry(
        retry=retry_if_exception(_should_retry),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
    )
    async def _get(self, article: str, start: str, end: str) -> httpx.Response:
        url = f"{ENDPOINT}/{self.domain}/all-access/user/{article}/monthly/{start}/{end}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp

    async def get_pageviews(self, batch: dict[str, list[str]], start: str, end: str) -> dict[str, int]:
        """Return combined pageviews for every target page in ``batch``.

        ``batch`` maps a target page title to a list of that page plus its
        redirects. Redirects contribute their views to the target.
        """
        target_titles = list(batch.keys())
        pageviews: dict[str, int] = dict.fromkeys(target_titles, 0)

        # Unique set of all titles (targets + redirects) across the batch.
        all_titles = set()
        for titles in batch.values():
            all_titles.update(titles)

        async def fetch_one(title: str):
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            try:
                resp = await self._get(title.replace(" ", "_"), start, end)
                return self._process_response(resp.json())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None  # no data available; acceptable to skip
                log_to_file(f"Exception during pageviews request: {exc}", self.domain)
                return None
            except httpx.HTTPError as exc:
                log_to_file(f"Exception during pageviews request: {exc}", self.domain)
                return None

        results = await asyncio.gather(*(fetch_one(t) for t in all_titles))

        for result in results:
            if result is None:
                continue
            page, count = result
            for target in target_titles:
                if page in batch[target]:
                    pageviews[target] += count
                    break

        return pageviews

    def _process_response(self, response: dict) -> tuple[str, int] | None:
        items = response.get("items")
        if not items:
            return None
        article = None
        total = 0
        # Reverse so the final ``article`` matches the PHP behaviour (first item).
        for item in reversed(items):
            total += int(item["views"])
            article = item["article"].replace("_", " ")
        return article, total
