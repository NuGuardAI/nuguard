"""Shared test fixtures for WS1 API smoke tests."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.config import get_settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Return a TestClient with startup DB init disabled for unit tests."""

    # Ensure tests remain CI-safe without relying on a .env file.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("NUGUARD_API_KEY_SECRET", "change-me")
    get_settings.cache_clear()

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(api_main, "init_db", _noop_init_db)

    with TestClient(api_main.app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Authorization headers for protected /v1 API endpoints."""
    return {"Authorization": "Bearer change-me"}
