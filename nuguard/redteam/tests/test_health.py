"""Smoke tests for the health endpoint."""

import pytest

from src.api.routes import health as health_routes


@pytest.mark.parametrize(
    ("postgres_ok", "redis_ok", "expected_status"),
    [
        (True, True, "healthy"),
        (False, True, "degraded"),
        (True, False, "degraded"),
    ],
)
def test_health_status_mapping(
    client,
    monkeypatch: pytest.MonkeyPatch,
    postgres_ok: bool,
    redis_ok: bool,
    expected_status: str,
) -> None:
    """Health endpoint reports aggregated dependency status."""

    async def _pg() -> bool:
        return postgres_ok

    async def _redis() -> bool:
        return redis_ok

    monkeypatch.setattr(health_routes, "check_postgres_health", _pg)
    monkeypatch.setattr(health_routes, "check_redis_health", _redis)

    response = client.get("/health")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == expected_status
    assert "X-Request-ID" in response.headers
