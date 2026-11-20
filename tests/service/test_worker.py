"""Real worker processes: protocol, timeout/cancel kill the whole tree, crash, restart."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from nuguard.service.dispatcher import SubprocessRunner
from nuguard.service.store import JobRecord, utcnow
from nuguard.service.store.sqlite import SqliteJobStore
from tests.service.conftest import (
    TOKEN,
    ServiceClient,
    auth,
    build_app,
    make_settings,
    wait_terminal,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups / ps")

REPO = "https://github.com/acme/support-agent"


class CapturingRunner(SubprocessRunner):
    """The real subprocess runner, also recording every event the worker emits."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.envs: list[dict[str, str]] = []

    async def run(self, spec, token, *, scratch, env, timeout_s, cancel, on_event):  # type: ignore[override]
        self.envs.append(env)

        async def capture(message: dict[str, Any]) -> None:
            self.events.append(message)
            await on_event(message)

        return await super().run(spec, token, scratch=scratch, env=env, timeout_s=timeout_s,
                                 cancel=cancel, on_event=capture)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True).stdout
    return bool(out.strip()) and not out.strip().startswith(b"Z")


@pytest.mark.asyncio
async def test_stub_worker_round_trip_with_token_over_stdin(settings) -> None:
    runner = CapturingRunner()
    app = build_app(settings, runner=runner, test_hooks={"*": ["dump_env", "stub"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO, "github_token": TOKEN},
                                 headers=auth())).json()
        final = await wait_terminal(client, job["id"])
        assert final["status"] == "succeeded", final
        result = (await client.get(f"/v1/scans/{job['id']}/result", headers=auth())).json()
        assert result["job"]["commit_sha"] == "0" * 40
    (env_event,) = [e for e in runner.events if e["type"] == "test_env"]
    assert TOKEN not in json.dumps(env_event)  # not in the worker's env or argv
    assert not (Path(settings.jobs.scratch_dir) / job["id"]).exists()  # scratch removed


@pytest.mark.asyncio
async def test_ambient_credentials_never_reach_worker(settings, monkeypatch) -> None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "LITELLM_API_KEY", "NUGUARD_LLM_API_KEY",
                 "AISBOM_LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "ambient-secret-value")
    runner = CapturingRunner()
    app = build_app(settings, runner=runner, test_hooks={"*": ["dump_env", "stub"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth())).json()
        await wait_terminal(client, job["id"])
    (env_event,) = [e for e in runner.events if e["type"] == "test_env"]
    assert "ambient-secret-value" not in json.dumps(env_event["env"])
    assert env_event["env"]["HOME"].startswith(str(settings.jobs.scratch_dir))


@pytest.mark.asyncio
async def test_timeout_kills_worker_and_scanner_grandchildren(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, jobs={"job_timeout_s": 2})
    runner = CapturingRunner()
    app = build_app(settings, runner=runner, test_hooks={"*": ["pid", "grandchild", "sleep:120"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth())).json()
        final = await wait_terminal(client, job["id"], timeout=30)
    assert final["status"] == "failed"
    assert final["error"]["code"] == "job_timeout"
    worker = next(e["pid"] for e in runner.events if e["type"] == "test_pid")
    grandchild = next(e["pid"] for e in runner.events if e["type"] == "test_grandchild")
    deadline = time.time() + 10
    while (_alive(worker) or _alive(grandchild)) and time.time() < deadline:
        await asyncio.sleep(0.1)
    assert not _alive(worker)
    assert not _alive(grandchild)


@pytest.mark.asyncio
async def test_cancel_running_worker_kills_it(settings) -> None:
    runner = CapturingRunner()
    app = build_app(settings, runner=runner, test_hooks={"*": ["pid", "sleep:120"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth())).json()
        while not any(e["type"] == "test_pid" for e in runner.events):
            await asyncio.sleep(0.05)
        await client.post(f"/v1/scans/{job['id']}/cancel", headers=auth())
        final = await wait_terminal(client, job["id"], timeout=30)
    assert final["status"] == "cancelled"
    worker = next(e["pid"] for e in runner.events if e["type"] == "test_pid")
    assert not _alive(worker)


@pytest.mark.asyncio
async def test_worker_crash_is_worker_lost(settings) -> None:
    app = build_app(settings, runner=CapturingRunner(), test_hooks={"*": ["crash"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth())).json()
        final = await wait_terminal(client, job["id"], timeout=30)
    assert final["status"] == "failed"
    assert final["error"]["code"] == "worker_lost"


@pytest.mark.asyncio
async def test_worker_error_event_maps_to_job_error(settings) -> None:
    app = build_app(settings, runner=CapturingRunner(),
                    test_hooks={"*": ["error:ref_not_found"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth())).json()
        final = await wait_terminal(client, job["id"], timeout=30)
    assert final["error"]["code"] == "ref_not_found"


@pytest.mark.asyncio
async def test_restart_recovery(settings) -> None:
    store = SqliteJobStore(settings.jobs.db_path)
    now = utcnow()

    def record(job_id: str, status: str, has_token: bool) -> JobRecord:
        body = json.dumps({"repo_url": REPO, "options": {}})
        return JobRecord(id=job_id, owner_id="alpha", status=status, repo_url=REPO,
                         request_json=body, request_sha256="x", created_at=now,
                         has_token=has_token)

    token_record = record("scn_TOKENQUEUED00000000000000", "queued", True)
    token_record.idempotency_key = "retry-me"
    store.create(token_record)
    store.create(record("scn_RUNNING000000000000000000", "running", False))
    store.create(record("scn_PUBLICQUEUED0000000000000", "queued", False))
    store.close()

    app = build_app(settings, runner=CapturingRunner(), test_hooks={"*": ["stub"]})
    async with ServiceClient(app) as client:
        token_job = await wait_terminal(client, "scn_TOKENQUEUED00000000000000")
        running_job = await wait_terminal(client, "scn_RUNNING000000000000000000")
        public_job = await wait_terminal(client, "scn_PUBLICQUEUED0000000000000", timeout=30)
        # The failed job released its Idempotency-Key: resubmitting creates a new job.
        retry = await client.post("/v1/scans", json={"repo_url": REPO, "github_token": TOKEN},
                                  headers={**auth(), "Idempotency-Key": "retry-me"})
        assert retry.status_code == 202
        assert retry.json()["id"] != "scn_TOKENQUEUED00000000000000"
    assert token_job["error"]["code"] == "credential_unavailable"
    assert running_job["error"]["code"] == "worker_lost"
    assert public_job["status"] == "succeeded"


def test_db_never_contains_token(tmp_path: Path) -> None:
    async def scenario() -> Path:
        settings = make_settings(tmp_path)
        app = build_app(settings, runner=CapturingRunner(), test_hooks={"*": ["stub"]})
        async with ServiceClient(app) as client:
            job = (await client.post("/v1/scans", json={"repo_url": REPO, "github_token": TOKEN},
                                     headers={**auth(), "Idempotency-Key": "k1"})).json()
            await wait_terminal(client, job["id"], timeout=30)
            await client.get(f"/v1/scans/{job['id']}/result", headers=auth())
        return settings.jobs.db_path

    db_path = asyncio.run(scenario())
    for path in db_path.parent.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path


def test_worker_env_sets_pythonpath_only_for_source_checkouts(settings, monkeypatch, tmp_path) -> None:
    """An installed package must not export site-packages to scanner CLIs (their own venvs)."""
    from nuguard.service import dispatcher

    env = dispatcher.worker_env(settings, tmp_path / "s1", use_llm=False)
    root = Path(dispatcher.__file__).resolve().parents[2]
    assert env.get("PYTHONPATH") == (str(root) if (root / "pyproject.toml").is_file() else None)

    fake = tmp_path / "site-packages" / "nuguard" / "service" / "dispatcher.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    monkeypatch.setattr(dispatcher, "__file__", str(fake))
    assert "PYTHONPATH" not in dispatcher.worker_env(settings, tmp_path / "s2", use_llm=False)
