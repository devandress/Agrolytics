"""Shared fixtures for API integration tests."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped loop so the app's module-level async engine pool (created
    once at import time) is never reused across a closed event loop between tests.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def client():
    """Async HTTP client wired directly to the ASGI app (no network socket)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
