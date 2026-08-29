"""
Tests for src.py_port.popularpages.pageviews.pageviews_repository.PageviewsRepository.

Uses httpx.MockTransport to avoid real network calls.
"""

import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.py_port.popularpages.pageviews.pageviews_repository import (
    PageviewsRepository,
    _is_retryable,
    _retry_wait,
)


def _make_mock_repo(handler) -> PageviewsRepository:
    """
    Create a pageviews repository configured with a mocked HTTP request handler.

    Parameters:
        handler: A callable that handles requests made by the repository's mock transport.

    Returns:
        PageviewsRepository: A repository using the supplied mock HTTP handler.
    """
    repo = PageviewsRepository("en.wikipedia")
    repo._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return repo


# ---------------------------------------------------------------
# 1. Tests for PageviewsRepository.get_pageviews batch summation.
# ---------------------------------------------------------------
class TestGetPageviews:
    """Tests for `PageviewsRepository.get_pageviews` batch summation."""

    @pytest.mark.asyncio
    async def test_get_pageviews_sums_target_and_redirect(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "Star_Wars_(film)" in request.url.path:
                return httpx.Response(
                    200,
                    json={"items": [{"article": "Star_Wars_(film)", "views": 50}]},
                )
            if "Star_Wars" in request.url.path:
                return httpx.Response(
                    200,
                    json={"items": [{"article": "Star_Wars", "views": 100}]},
                )
            return httpx.Response(404)

        repo = _make_mock_repo(handler)
        batch = {"Star Wars": ["Star Wars", "Star Wars (film)"]}
        result = await repo.get_pageviews(batch, "2024010100", "2024013100")

        assert result == {"Star Wars": 150}

    @pytest.mark.asyncio
    async def test_get_pageviews_ignores_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        repo = _make_mock_repo(handler)
        batch = {"Nonexistent Page": ["Nonexistent Page"]}
        result = await repo.get_pageviews(batch, "2024010100", "2024013100")

        assert result == {"Nonexistent Page": 0}


# ---------------------------------------------------------------
# 2. Tests for PageviewsRepository._process_response.
# ---------------------------------------------------------------
class TestProcessResponse:
    """Tests for `PageviewsRepository._process_response`."""

    def test_process_response_no_items_returns_none(self):
        assert PageviewsRepository._process_response({}) == (None, None)
        assert PageviewsRepository._process_response({"items": []}) == (None, None)

    def test_process_response_sums_views_across_items(self):
        response = {
            "items": [
                {"article": "Foo_Bar", "views": 10},
                {"article": "Foo_Bar", "views": 20},
            ]
        }
        article, total = PageviewsRepository._process_response(response)
        assert article == "Foo Bar"
        assert total == 30


# ---------------------------------------------------------------
# 3. Tests for the HTTP client configuration.
# ---------------------------------------------------------------
class TestClientHeaders:
    """Tests for the HTTP client configuration."""

    def test_client_sets_user_agent_header(self):
        repo = PageviewsRepository("en.wikipedia")
        ua = repo._client.headers.get("User-Agent")
        assert ua is not None
        assert "py-popularpages" in ua


# ---------------------------------------------------------------
# 4. Tests for PageviewsRepository.get_title views.
# ---------------------------------------------------------------
class TestGetTitleViews:
    """Tests for `PageviewsRepository.get_title views`."""

    @pytest.mark.asyncio
    async def test_get_title_views_returns_per_title(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "Bar_Baz" in request.url.path:
                return httpx.Response(200, json={"items": [{"article": "Bar_Baz", "views": 7}]})
            if "Foo" in request.url.path:
                return httpx.Response(200, json={"items": [{"article": "Foo", "views": 3}]})
            return httpx.Response(404)

        repo = _make_mock_repo(handler)
        result = await repo.get_title_views(["Foo", "Bar Baz"], "2024010100", "2024013100")
        assert result == {"Foo": 3, "Bar Baz": 7}

    @pytest.mark.asyncio
    async def test_get_title_views_zero_on_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        repo = _make_mock_repo(handler)
        result = await repo.get_title_views(["Missing"], "2024010100", "2024013100")
        assert result == {"Missing": 0}


# ---------------------------------------------------------------
# 5. Tests for the _is_retryable error classifier.
# ---------------------------------------------------------------
class TestIsRetryable:
    """Tests for the `_is_retryable` error classifier."""

    def test_is_retryable_retries_on_429(self):
        resp = httpx.Response(429)
        exc = httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=resp)
        assert _is_retryable(exc) is True

    def test_is_retryable_does_not_retry_404(self):
        resp = httpx.Response(404)
        exc = httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=resp)
        assert _is_retryable(exc) is False

    def test_is_retryable_retries_transport_errors(self):
        assert _is_retryable(httpx.ConnectError("x")) is True
        assert _is_retryable(ValueError("x")) is False


# ---------------------------------------------------------------
# 6. Tests for the _retry_wait backoff helper.
# ---------------------------------------------------------------
class TestRetryWait:
    """Tests for the `_retry_wait` backoff helper."""

    def test_retry_wait_honors_retry_after_header(self):
        state = MagicMock()
        state.outcome.failed = True
        exc = httpx.HTTPStatusError("x", request=MagicMock(), response=MagicMock())
        exc.response.headers.get.return_value = "7"  # pyright: ignore[reportAttributeAccessIssue]
        state.outcome.exception.return_value = exc
        assert _retry_wait(state) == 7.0

    def test_retry_wait_falls_back_to_backoff(self):
        state = types.SimpleNamespace(outcome=None, attempt_number=1)
        val = _retry_wait(state)  # pyright: ignore[reportArgumentType]
        assert 1.0 <= val <= 30.0


# ---------------------------------------------------------------
# 7. Tests for PageviewsRepository._fetch_title_views error handling.
# ---------------------------------------------------------------
class TestFetchTitleViews:
    """Tests for `PageviewsRepository._fetch_title_views` error handling."""

    @pytest.mark.asyncio
    async def test_fetch_title_views_404_returns_zero(self):
        repo = PageviewsRepository("en.wikipedia")
        repo._get = AsyncMock(
            side_effect=httpx.HTTPStatusError("x", request=MagicMock(), response=MagicMock(status_code=404))
        )
        assert await repo._fetch_title_views("Foo", "2024010100", "2024013100") == ("Foo", 0)

    @pytest.mark.asyncio
    async def test_fetch_title_views_http_error_returns_zero(self):
        repo = PageviewsRepository("en.wikipedia")
        repo._get = AsyncMock(side_effect=httpx.ReadError("x"))
        assert await repo._fetch_title_views("Foo", "2024010100", "2024013100") == ("Foo", 0)


# ---------------------------------------------------------------
# 8. Live network test for get_monthly_pageviews (requires network; marked skip in CI).
# ---------------------------------------------------------------
class TestMonthlyPageviewsLive:
    """Live network test for `get_monthly_pageviews` (requires network; marked skip in CI)."""

    # @pytest.mark.skip(
    #     reason="Disabled upstream in the PHP version too (was 'ertestGetMonthlyPageviews', "
    #     "never actually run by PHPUnit)."
    # )
    @pytest.mark.network
    async def test_get_monthly_pageviews(self):
        pages = ["Star Wars", "Zootopia", "The Lion King"]
        batch = {p: [p] for p in pages}
        repo = PageviewsRepository("en.wikipedia")
        result = repo.get_pageviews(batch, "2017020100", "2017022800")
        expected = {
            "Star Wars": 491220,
            "Zootopia": 205129,
            "The Lion King": 305347,
        }
        assert result == expected
