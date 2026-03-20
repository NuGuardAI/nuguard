"""WS1 scan lifecycle endpoint tests with in-memory session fakes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from src.api.routes import tests as tests_routes


@dataclass
class _ResultProxy:
    row: dict[str, Any] | None

    def mappings(self) -> _ResultProxy:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.row


class _FakeSession:
    def __init__(self, store: dict[str, dict[str, Any]]):
        self.store = store

    async def execute(self, query: Any, params: dict[str, Any]):
        sql = str(query)
        if "INSERT INTO redteam_tests" in sql:
            self.store[params["id"]] = {
                "id": params["id"],
                "status": params["status"],
                "created_at": params["created_at"],
                "started_at": None,
                "completed_at": None,
                "error_message": None,
            }
            return _ResultProxy(None)

        if "SELECT id, status, created_at, started_at, completed_at, error_message" in sql:
            return _ResultProxy(self.store.get(params["test_id"]))

        raise AssertionError(f"Unexpected SQL in test fake: {sql}")

    async def commit(self) -> None:
        return None


class _FakeSessionContext:
    def __init__(self, store: dict[str, dict[str, Any]]):
        self.session = _FakeSession(store)

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, store: dict[str, dict[str, Any]]):
        self.store = store

    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext(self.store)


def test_scan_create_and_get(
    client,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Create endpoint returns 202 and status endpoint resolves the created test."""
    store: dict[str, dict[str, Any]] = {}

    monkeypatch.setattr(tests_routes, "get_session_factory", lambda: _FakeSessionFactory(store))

    async def _fake_run_test(test_id: str) -> None:
        now = datetime.now(UTC)
        store[test_id]["status"] = "complete"
        store[test_id]["started_at"] = now
        store[test_id]["completed_at"] = now

    monkeypatch.setattr(tests_routes, "run_test", _fake_run_test)

    create_response = client.post(
        "/v1/redteam",
        json={"config": {"profile": "ci"}},
        headers=auth_headers,
    )
    assert create_response.status_code == 202

    payload = create_response.json()
    test_id = payload["test_id"]
    assert payload["status"] == "pending"

    get_response = client.get(f"/v1/redteam/{test_id}", headers=auth_headers)
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["test_id"] == test_id
    assert get_payload["status"] in {"pending", "running", "complete"}


def test_scan_get_404_when_missing(
    client,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Status endpoint returns 404 for unknown test id."""
    store: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(tests_routes, "get_session_factory", lambda: _FakeSessionFactory(store))

    response = client.get("/v1/redteam/not-found", headers=auth_headers)
    assert response.status_code == 404


def test_scan_create_requires_auth(client) -> None:
    """Create endpoint must reject missing Authorization header."""
    response = client.post("/v1/redteam", json={"config": {}})
    assert response.status_code == 401


def test_scan_get_requires_auth(client) -> None:
    """Status endpoint must reject missing Authorization header."""
    response = client.get("/v1/redteam/any-id")
    assert response.status_code == 401
