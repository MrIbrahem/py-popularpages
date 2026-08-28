"""
Shared pytest fixtures.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_socket import disable_socket


@pytest.fixture(autouse=True)
def stop_nets(request):
    # Check if 'network' mark is present in the current test item
    if "network" in request.node.keywords:
        from pytest_socket import enable_socket

        enable_socket()
        return

    # Async tests need a real event loop. On Windows the ProactorEventLoop
    # creates an internal socketpair for its self-pipe, so the socket must be
    # allowed. These tests use httpx.MockTransport, so no real network traffic
    # occurs -- the socket is only used for the loop's internal self-pipe.
    if "asyncio" in request.node.keywords:
        from pytest_socket import enable_socket

        enable_socket()
        return

    # Otherwise, disable the socket for all other tests
    disable_socket(allow_unix_socket=True)


# ── mwclient fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_site() -> MagicMock:
    _mock_site = MagicMock(name="mw_site")
    _mock_site.username = "user"
    _mock_site.rights = ["autopatrol", "edit", "upload"]
    return _mock_site


@pytest.fixture
def mock_page() -> MagicMock:
    return MagicMock()
