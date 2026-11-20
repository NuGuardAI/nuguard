"""Security requirements (remote-scan-api spec §9), one test per item."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from nuguard.service.models import ScanStatus
from tests.git_http_server import GitHttpServer, make_bare_repo
from tests.service.conftest import (
    KEY_A,
    KEY_B,
    TOKEN,
    ServiceClient,
    auth,
    build_app,
    make_settings,
    wait_terminal,
)
from tests.service.fakes import FakeRunner
from tests.service.test_worker import CapturingRunner

REPO = "https://github.com/acme/support-agent"
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX process tools")


# 1. The token never appears in DB bytes, logs, responses, webhooks, problems, argv, git errors.


@pytest.mark.asyncio
async def test_1_token_absent_from_responses_logs_and_db(settings, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        created = await client.post("/v1/scans", json={"repo_url": REPO, "github_token": TOKEN},
                                    headers={**auth(), "Idempotency-Key": "k"})
        job = await wait_terminal(client, created.json()["id"])
        texts = [created.text, json.dumps(job)]
        for path in ("", "/result", "/result?format=sarif", "/sbom"):
            texts.append((await client.get(f"/v1/scans/{job['id']}{path}", headers=auth())).text)
        texts.append((await client.get("/v1/scans", headers=auth())).text)
        bad = await client.post("/v1/scans", json={"repo_url": "nope", "github_token": TOKEN},
                                headers=auth())
        texts.append(bad.text)  # ProblemDetail of a validation error
    assert all(TOKEN not in text for text in texts)
    assert TOKEN not in caplog.text
    for path in Path(settings.jobs.db_path).parent.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path


@pytest.mark.asyncio
async def test_1_token_absent_from_webhook_body(tmp_path: Path, monkeypatch) -> None:
    from nuguard.service import webhooks

    bodies: list[bytes] = []

    async def fake_deliver(url, body, settings, **_kw):
        bodies.append(body)
        return True

    monkeypatch.setattr(webhooks, "deliver", fake_deliver)
    settings = make_settings(tmp_path, webhooks={"allowed_hosts": ["hooks.example.com"],
                                                 "secret": "s3cret"})
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        job = (await client.post("/v1/scans", json={
            "repo_url": REPO, "github_token": TOKEN,
            "callback_url": "https://hooks.example.com/cb"}, headers=auth())).json()
        await wait_terminal(client, job["id"])
        for _ in range(50):
            if bodies:
                break
            await asyncio.sleep(0.02)
    assert bodies and all(TOKEN.encode() not in b for b in bodies)
    assert json.loads(bodies[0])["id"] == job["id"]


@posix_only
@pytest.mark.asyncio
async def test_1_token_absent_from_worker_argv(settings) -> None:
    runner = CapturingRunner()
    app = build_app(settings, runner=runner, test_hooks={"*": ["pid", "sleep:30"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO, "github_token": TOKEN},
                                 headers=auth())).json()
        while not any(e["type"] == "test_pid" for e in runner.events):
            await asyncio.sleep(0.05)
        pid = next(e["pid"] for e in runner.events if e["type"] == "test_pid")
        argv = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)],
                              capture_output=True).stdout.decode()
        await client.post(f"/v1/scans/{job['id']}/cancel", headers=auth())
        await wait_terminal(client, job["id"], timeout=30)
    assert "nuguard.service.worker" in argv
    assert TOKEN not in argv
    assert all(TOKEN not in json.dumps(env) for env in runner.envs)


@pytest.mark.skipif(shutil.which("git") is None, reason="git")
def test_1_token_absent_from_git_error_details(tmp_path: Path) -> None:
    from nuguard.service.models import ScanOptions
    from nuguard.service.pipeline import JobSpec, PipelineError, run_pipeline

    root, _ = make_bare_repo(tmp_path, "agent", {"a.py": "x = 1\n"})
    wrong = "github_pat_WRONG_TOKEN_abcdefghijklmnop0123"
    with GitHttpServer(root, TOKEN) as server:
        spec = JobSpec(job_id="scn_x", repo_url="https://github.com/a/b",
                       clone_url=server.url("agent"), options=ScanOptions(),
                       tools=["nga-rules"], allowed_hosts=None)
        scratch = tmp_path / "s"
        scratch.mkdir()
        with pytest.raises(PipelineError) as exc:
            run_pipeline(spec, wrong, scratch, lambda _e: None)
    assert wrong not in exc.value.detail and wrong not in str(exc.value)


# 2–4. Unsafe repository URLs, refs and subpaths are rejected before anything is cloned.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"repo_url": "http://github.com/a/b"},
        {"repo_url": "https://gitlab.com/a/b"},
        {"repo_url": "https://10.0.0.5/a/b"},
        {"repo_url": "https://[::1]/a/b"},
        {"repo_url": "https://x-access-token:t@github.com/a/b"},
        {"repo_url": "file:///etc/passwd"},
        {"repo_url": REPO, "ref": "--upload-pack=touch /tmp/pwned"},
        {"repo_url": REPO, "options": {"subpath": "../../etc"}},
        {"repo_url": REPO + "/tree/main/../../etc"},
    ],
)
async def test_2_3_4_unsafe_inputs_are_422_and_never_cloned(settings, body) -> None:
    runner = FakeRunner()
    async with ServiceClient(build_app(settings, runner=runner)) as client:
        resp = await client.post("/v1/scans", json=body, headers=auth())
        assert resp.status_code == 422, resp.text
        await asyncio.sleep(0.05)
    assert runner.specs == {}


# 5. Another caller's job is 404.


@pytest.mark.asyncio
async def test_5_other_callers_job_is_404(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth(KEY_A))).json()
        assert (await client.get(f"/v1/scans/{job['id']}", headers=auth(KEY_B))).status_code == 404


# 6. Results never contain absolute paths or the scratch prefix (real pipeline in
#    test_pipeline.py::test_private_repo_scan_end_to_end; here through the API).


@pytest.mark.asyncio
async def test_6_result_has_no_scratch_paths(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth())).json()
        await wait_terminal(client, job["id"])
        text = (await client.get(f"/v1/scans/{job['id']}/result", headers=auth())).text
    assert str(settings.jobs.scratch_dir) not in text


# 7. Ambient credentials in the server environment are invisible to the worker.


@posix_only
@pytest.mark.asyncio
async def test_7_ambient_credentials_not_in_worker(settings, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-gh-token")
    monkeypatch.setenv("LITELLM_API_KEY", "ambient-llm-key")
    runner = CapturingRunner()
    app = build_app(settings, runner=runner, test_hooks={"*": ["dump_env", "stub"]})
    async with ServiceClient(app) as client:
        job = (await client.post("/v1/scans", json={"repo_url": REPO}, headers=auth())).json()
        await wait_terminal(client, job["id"])
    env = next(e["env"] for e in runner.events if e["type"] == "test_env")
    assert "GITHUB_TOKEN" not in env and "LITELLM_API_KEY" not in env


# 8. Symlinks out of the clone: covered end to end in
#    test_pipeline.py::test_symlink_out_of_repo_is_not_followed; under the host
#    policy git also checks symlinks out as plain files.


@pytest.mark.skipif(shutil.which("git") is None, reason="git")
def test_8_policy_clone_checks_out_symlinks_as_files(tmp_path: Path, monkeypatch) -> None:
    from nuguard.sbom.extractor import git_clone

    root, _ = make_bare_repo(tmp_path, "agent", {"a.py": "x = 1\n"})
    work = tmp_path / "agent-src"
    os.symlink("/etc/passwd", work / "leak")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    subprocess.run(["git", "add", "-A"], cwd=work, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "l"], cwd=work, env=env, check=True)
    subprocess.run(["git", "push", "-q", str(root / "agent.git"), "main"], cwd=work, env=env,
                   check=True)
    original_env = git_clone.git_env
    # The local test server is http://127.0.0.1, which the policy itself rejects;
    # relax only the URL check and the https-only protocol, keep the rest.
    monkeypatch.setattr(git_clone, "check_clone_url", lambda url, hosts: None)
    monkeypatch.setattr(git_clone, "git_env", lambda o, h: {
        k: v for k, v in original_env(o, h).items() if k != "GIT_ALLOW_PROTOCOL"})
    with GitHttpServer(root, TOKEN) as server:
        options = git_clone.CloneOptions(token=TOKEN, allowed_hosts=("127.0.0.1",))
        git_clone.secure_clone(server.url("agent"), None, tmp_path / "out", options)
    leak = tmp_path / "out" / "leak"
    assert leak.is_file() and not leak.is_symlink()
    assert leak.read_text() == "/etc/passwd"


# 9. Body over 16 KB → 413.


@pytest.mark.asyncio
async def test_9_large_body_is_413(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.post("/v1/scans", content=b"{" + b" " * 17_000 + b"}",
                                 headers={**auth(), "Content-Type": "application/json"})
        assert resp.status_code == 413


# 10. Failed authentication is rate-limited per remote address.


@pytest.mark.asyncio
async def test_10_failed_auth_rate_limited(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        codes = [(await client.get("/v1/scans", headers=auth("ngs_wrong"))).status_code
                 for _ in range(11)]
        assert codes[-1] == 429
        # Even a valid key is refused while the address is blocked.
        assert (await client.get("/v1/scans", headers=auth())).status_code == 429


# 11. use_llm without server LLM → 422 llm_not_available.


@pytest.mark.asyncio
async def test_11_llm_not_available(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.post("/v1/scans", json={"repo_url": REPO, "options": {"use_llm": True}},
                                 headers=auth())
        assert resp.status_code == 422 and resp.json()["code"] == "llm_not_available"


# 12. Error responses never include stack traces.


@pytest.mark.asyncio
async def test_12_unhandled_errors_have_no_traceback(settings, monkeypatch) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        store = client._transport.app.state.service["store"]  # type: ignore[attr-defined]

        def boom(*_a, **_k):
            raise RuntimeError("secret detail /var/lib/nuguard/scratch/x Traceback")

        monkeypatch.setattr(store, "list_jobs", boom)
        # Starlette re-raises after the 500 handler so the server can log the
        # traceback; inspect the response a real client receives instead.
        import httpx

        app = client._transport.app  # type: ignore[attr-defined]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://svc.test",
        ) as raw:
            resp = await raw.get("/v1/scans", headers=auth())
    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"
    for leak in ("Traceback", "secret detail", "/var/lib", "RuntimeError"):
        assert leak not in resp.text


def test_terminal_status_enum_is_closed() -> None:
    assert {s.value for s in ScanStatus} == {
        "queued", "running", "succeeded", "partial", "failed", "cancelled", "expired"}
