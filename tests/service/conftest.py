"""Shared fixtures for remote scan service tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")

from nuguard.service.app import create_app  # noqa: E402
from nuguard.service.auth import hash_key  # noqa: E402
from nuguard.service.settings import ApiKeyConfig, ServiceSettings  # noqa: E402

KEY_A = "ngs_test_key_alpha_0123456789"
KEY_B = "ngs_test_key_bravo_0123456789"
TOKEN = "github_pat_SERVICE_TEST_TOKEN_abcdefghijklmnop"


def make_settings(tmp_path: Path, **overrides: Any) -> ServiceSettings:
    base: dict[str, Any] = {
        "api_keys": [
            ApiKeyConfig(id="alpha", sha256=hash_key(KEY_A), max_concurrent_scans=2,
                         max_queued_scans=5),
            ApiKeyConfig(id="bravo", sha256=hash_key(KEY_B)),
        ],
        "jobs": {"scratch_dir": tmp_path / "scratch", "db_path": tmp_path / "db" / "service.db",
                 "max_concurrent": 2, "job_timeout_s": 30},
        "git": {"allowed_hosts": ["github.com"]},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return ServiceSettings(**base)


def auth(key: str = KEY_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


class ServiceClient:
    """Runs the app's lifespan and talks to it through an in-process transport."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._ctx: Any = None
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        self._ctx = self.app.router.lifespan_context(self.app)
        await self._ctx.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://svc.test"
        )
        return self.client

    async def __aexit__(self, *exc: Any) -> None:
        assert self.client is not None
        await self.client.aclose()
        await self._ctx.__aexit__(*exc)


async def wait_terminal(client: httpx.AsyncClient, job_id: str, key: str = KEY_A,
                        timeout: float = 60) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        job = (await client.get(f"/v1/scans/{job_id}", headers=auth(key))).json()
        if job["status"] not in ("queued", "running"):
            return job
        if loop.time() > deadline:
            raise AssertionError(f"job {job_id} did not finish: {job}")
        await asyncio.sleep(0.05)


@pytest.fixture()
def settings(tmp_path: Path) -> ServiceSettings:
    return make_settings(tmp_path)


def build_app(settings: ServiceSettings, **kwargs: Any) -> Any:
    return create_app(settings, **kwargs)
