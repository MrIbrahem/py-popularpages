"""
Tests for src.popularpages.pageviews_repository.PageviewsRepository.

Uses httpx.MockTransport to avoid real network calls.
"""

import httpx
import pytest

from src.popularpages.pageviews_repository import PageviewsRepository


def _make_mock_repo(handler) -> PageviewsRepository:
    repo = PageviewsRepository("en.wikipedia")
    repo._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return repo


@pytest.mark.asyncio
async def test_get_pageviews_sums_target_and_redirect():
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
async def test_get_pageviews_ignores_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    repo = _make_mock_repo(handler)
    batch = {"Nonexistent Page": ["Nonexistent Page"]}
    result = await repo.get_pageviews(batch, "2024010100", "2024013100")

    assert result == {"Nonexistent Page": 0}


def test_process_response_no_items_returns_none():
    assert PageviewsRepository._process_response({}) == (None, None)
    assert PageviewsRepository._process_response({"items": []}) == (None, None)


def test_process_response_sums_views_across_items():
    response = {
        "items": [
            {"article": "Foo_Bar", "views": 10},
            {"article": "Foo_Bar", "views": 20},
        ]
    }
    article, total = PageviewsRepository._process_response(response)
    assert article == "Foo Bar"
    assert total == 30
