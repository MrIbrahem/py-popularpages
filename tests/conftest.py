"""
Shared pytest fixtures.
"""

from __future__ import annotations

from pathlib import Path
import sys
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from pytest_socket import disable_socket

if sys:
    # tempfile.gettempdir() returns the path to the system's directory for temporary files
    system_temp_dir = Path(tempfile.gettempdir())

    # Now correctly combine it with "test" and set the environment variable
    os.environ["POPULAR_PAGES_MAIN_DIR"] = str(system_temp_dir / "test")

    os.environ.setdefault("TOOL_REPLICA_USER", "user")
    os.environ.setdefault("TOOL_REPLICA_PASSWORD", "pass")


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
