"""HTTP contract of the remote scan service, driven through a stub pipeline."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from nuguard.service.store import utcnow
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

REPO = "https://github.com/acme/support-agent"


def _body(**extra) -> dict:
    return {"repo_url": REPO, "ref": "main", **extra}


@pytest.mark.asyncio
async def test_submit_poll_result_and_location(settings) -> None:
    runner = FakeRunner()
    async with ServiceClient(build_app(settings, runner=runner)) as client:
        resp = await client.post("/v1/scans", json=_body(github_token=TOKEN,
                                                         client_reference="vf-1"),
                                 headers=auth())
        assert resp.status_code == 202
        job = resp.json()
        assert job["id"].startswith("scn_") and len(job["id"]) == 30
        assert resp.headers["location"] == f"/v1/scans/{job['id']}"
        assert job["status"] in ("queued", "running")
        assert TOKEN not in resp.text

        final = await wait_terminal(client, job["id"])
        assert final["status"] == "succeeded"
        assert final["commit_sha"] == "0" * 40
        assert final["client_reference"] == "vf-1"
        assert runner.tokens[job["id"]] == TOKEN  # vault → worker handoff

        result = (await client.get(f"/v1/scans/{job['id']}/result", headers=auth())).json()
        assert result["schema_version"] == "1.0"
        assert result["job"]["status"] == "succeeded"
        assert len(result["result_sha256"]) == 64
        assert {r["tool"] for r in result["coverage"]["tool_runs"]} == set(
            settings.analysis_defaults.tools
        )
        assert set(result["summary"]) == {"critical", "high", "medium", "low", "info"}


@pytest.mark.asyncio
async def test_prefer_wait_returns_finished_job(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.post("/v1/scans", json=_body(),
                                 headers={**auth(), "Prefer": "wait=10"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "succeeded"
        assert resp.headers["preference-applied"] == "wait=10"


@pytest.mark.asyncio
async def test_partial_when_a_tool_failed(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner("partial"))) as client:
        job = (await client.post("/v1/scans", json=_body(), headers=auth())).json()
        final = await wait_terminal(client, job["id"])
        assert final["status"] == "partial"
        result = (await client.get(f"/v1/scans/{job['id']}/result", headers=auth())).json()
        assert result["coverage"]["gaps"] == ["osv: failed (network unreachable)"]


@pytest.mark.asyncio
async def test_sarif_and_sbom_renderings(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        job = (await client.post("/v1/scans", json=_body(), headers=auth())).json()
        await wait_terminal(client, job["id"])
        sarif = await client.get(f"/v1/scans/{job['id']}/result?format=sarif", headers=auth())
        assert sarif.status_code == 200
        assert sarif.headers["content-type"].startswith("application/sarif+json")
        assert json.loads(sarif.text)["version"] == "2.1.0"
        for fmt, media in (("json", "application/json"), ("cyclonedx", "application/json"),
                           ("markdown", "text/markdown")):
            resp = await client.get(f"/v1/scans/{job['id']}/sbom?format={fmt}", headers=auth())
            assert resp.status_code == 200, (fmt, resp.text)
            assert resp.headers["content-type"].startswith(media)
        bad = await client.get(f"/v1/scans/{job['id']}/sbom?format=xml", headers=auth())
        assert bad.status_code == 422


@pytest.mark.asyncio
async def test_failed_job_reports_error_and_no_result(settings) -> None:
    runner = FakeRunner("error:repo_not_found_or_no_access")
    async with ServiceClient(build_app(settings, runner=runner)) as client:
        job = (await client.post("/v1/scans", json=_body(github_token=TOKEN),
                                 headers=auth())).json()
        final = await wait_terminal(client, job["id"])
        assert final["status"] == "failed"
        assert final["error"]["code"] == "repo_not_found_or_no_access"
        resp = await client.get(f"/v1/scans/{job['id']}/result", headers=auth())
        assert resp.status_code == 409
        assert resp.json()["code"] == "no_result"


# ── auth / ownership ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_and_invalid_key_is_401(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}):
            resp = await client.get("/v1/scans", headers=headers)
            assert resp.status_code == 401
            assert resp.headers["www-authenticate"] == "Bearer"
            assert resp.json()["code"] == "unauthorized"
        # Bad body without auth is still 401, not 422.
        resp = await client.post("/v1/scans", json={"repo_url": "http://x"})
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_other_callers_job_is_404(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        job = (await client.post("/v1/scans", json=_body(), headers=auth(KEY_A))).json()
        for path in ("", "/result", "/sbom"):
            resp = await client.get(f"/v1/scans/{job['id']}{path}", headers=auth(KEY_B))
            assert resp.status_code == 404
        assert (await client.post(f"/v1/scans/{job['id']}/cancel",
                                  headers=auth(KEY_B))).status_code == 404
        listing = (await client.get("/v1/scans", headers=auth(KEY_B))).json()
        assert listing["items"] == []


@pytest.mark.asyncio
async def test_failed_auth_is_rate_limited(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auth_fail_limit_per_minute=3)
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        codes = [(await client.get("/v1/scans", headers={"Authorization": "Bearer bad"})).status_code
                 for _ in range(5)]
        assert codes[:3] == [401, 401, 401]
        assert codes[3:] == [429, 429]


# ── validation ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"repo_url": "http://github.com/a/b"}, "invalid_request"),
        ({"repo_url": "https://gitlab.com/a/b"}, "host_not_allowed"),
        ({"repo_url": "https://127.0.0.1/a/b"}, "host_not_allowed"),
        ({"repo_url": "https://user:pw@github.com/a/b"}, "invalid_request"),
        ({"repo_url": "file:///etc/passwd"}, "invalid_request"),
        ({"repo_url": "https://github.com/a"}, "invalid_request"),
        ({"repo_url": REPO, "ref": "--upload-pack=touch /tmp/pwned"}, "invalid_request"),
        ({"repo_url": REPO, "ref": "a..b"}, "invalid_request"),
        ({"repo_url": REPO, "options": {"subpath": "../etc"}}, "invalid_request"),
        ({"repo_url": REPO, "options": {"tools": ["nope"]}}, "invalid_request"),
        ({"repo_url": REPO, "options": {"use_llm": True}}, "llm_not_available"),
        ({"repo_url": REPO, "github_token": "short"}, "invalid_request"),
        ({"repo_url": REPO, "unexpected": 1}, "invalid_request"),
        ({"repo_url": REPO, "callback_url": "https://hooks.example.com/x"}, "invalid_request"),
    ],
)
async def test_validation_errors(settings, body, code) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.post("/v1/scans", json=body, headers=auth())
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == code
        assert resp.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_tool_not_allowed_by_server(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, analysis_defaults={"tools": ["nga-rules", "semgrep"]})
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.post("/v1/scans", json=_body(options={"tools": ["osv"]}),
                                 headers=auth())
        assert resp.status_code == 422
        assert resp.json()["code"] == "tool_not_allowed"


@pytest.mark.asyncio
async def test_validation_error_never_echoes_token(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.post("/v1/scans", json={"repo_url": "http://x/a/b",
                                                    "github_token": TOKEN}, headers=auth())
        assert resp.status_code == 422
        assert TOKEN not in resp.text


@pytest.mark.asyncio
async def test_body_over_limit_is_413(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.post("/v1/scans", json=_body(client_reference="x" * 20_000),
                                 headers=auth())
        assert resp.status_code == 413
        assert resp.json()["code"] == "request_too_large"


# ── idempotency / limits / cancel / list ─────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotency(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        headers = {**auth(), "Idempotency-Key": "vf-run-1"}
        first = await client.post("/v1/scans", json=_body(github_token=TOKEN), headers=headers)
        # Same body with a different token is the same request (token excluded from hash).
        again = await client.post("/v1/scans",
                                  json=_body(github_token=TOKEN.replace("A", "B")),
                                  headers=headers)
        assert first.status_code == 202 and again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        conflict = await client.post("/v1/scans", json=_body(ref="dev"), headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"
        # Keys are scoped per caller.
        other = await client.post("/v1/scans", json=_body(),
                                  headers={**auth(KEY_B), "Idempotency-Key": "vf-run-1"})
        assert other.status_code == 202 and other.json()["id"] != first.json()["id"]


@pytest.mark.asyncio
async def test_queue_limits_return_429_with_retry_after(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, jobs={"max_concurrent": 1, "queue_limit": 50})
    runner = FakeRunner("hang")
    async with ServiceClient(build_app(settings, runner=runner)) as client:
        statuses = [(await client.post("/v1/scans", json=_body(), headers=auth())).status_code
                    for _ in range(8)]
        assert 429 in statuses
        limited = await client.post("/v1/scans", json=_body(), headers=auth())
        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
        assert limited.headers["retry-after"]

    settings = make_settings(tmp_path / "b", jobs={"max_concurrent": 1, "queue_limit": 2})
    async with ServiceClient(build_app(settings, runner=FakeRunner("hang"))) as client:
        codes = [(await client.post("/v1/scans", json=_body(), headers=auth(KEY_B))).json().get(
            "code") for _ in range(3)]
        assert codes[-1] == "queue_full"


@pytest.mark.asyncio
async def test_result_before_finish_is_409_then_cancel(settings) -> None:
    runner = FakeRunner("hang")
    async with ServiceClient(build_app(settings, runner=runner)) as client:
        job = (await client.post("/v1/scans", json=_body(github_token=TOKEN),
                                 headers=auth())).json()
        await runner.started.wait()
        resp = await client.get(f"/v1/scans/{job['id']}/result", headers=auth())
        assert resp.status_code == 409 and resp.json()["code"] == "not_finished"
        cancelled = await client.post(f"/v1/scans/{job['id']}/cancel", headers=auth())
        assert cancelled.status_code == 202
        final = await wait_terminal(client, job["id"])
        assert final["status"] == "cancelled"
        assert final["error"]["code"] == "cancelled"
        app_state = client._transport.app.state.service  # type: ignore[attr-defined]
        assert job["id"] not in app_state["dispatcher"].vault


@pytest.mark.asyncio
async def test_cancel_queued_job_is_immediate(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, jobs={"max_concurrent": 1})
    async with ServiceClient(build_app(settings, runner=FakeRunner("hang"))) as client:
        await client.post("/v1/scans", json=_body(), headers=auth())
        queued = (await client.post("/v1/scans", json=_body(), headers=auth())).json()
        resp = await client.post(f"/v1/scans/{queued['id']}/cancel", headers=auth())
        assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_list_pagination_and_filters(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        ids = [(await client.post("/v1/scans", json=_body(), headers=auth())).json()["id"]
               for _ in range(3)]
        for job_id in ids:
            await wait_terminal(client, job_id)
        page1 = (await client.get("/v1/scans?limit=2", headers=auth())).json()
        assert len(page1["items"]) == 2 and page1["next_cursor"]
        page2 = (await client.get(f"/v1/scans?limit=2&cursor={page1['next_cursor']}",
                                  headers=auth())).json()
        assert {j["id"] for j in page1["items"] + page2["items"]} == set(ids)
        assert page2["next_cursor"] is None
        filtered = (await client.get("/v1/scans?status=running", headers=auth())).json()
        assert filtered["items"] == []
        bad = await client.get("/v1/scans?cursor=%%%", headers=auth())
        assert bad.status_code == 422


@pytest.mark.asyncio
async def test_expired_result_is_410(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        job = (await client.post("/v1/scans", json=_body(), headers=auth())).json()
        await wait_terminal(client, job["id"])
        store = client._transport.app.state.service["store"]  # type: ignore[attr-defined]
        assert store.expire(utcnow() + timedelta(days=30)) == [job["id"]]
        resp = await client.get(f"/v1/scans/{job['id']}/result", headers=auth())
        assert resp.status_code == 410 and resp.json()["code"] == "expired"
        assert (await client.get(f"/v1/scans/{job['id']}", headers=auth())).json()["status"] == "expired"


# ── health / capabilities ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_readiness_capabilities(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        assert (await client.get("/v1/healthz")).json() == {"status": "ok", "failing": []}
        ready = await client.get("/v1/readyz")
        assert ready.status_code in (200, 503)
        assert "database" not in ready.json()["failing"]
        caps = (await client.get("/v1/capabilities", headers=auth())).json()
        assert caps["llm_available"] is False
        assert caps["allowed_git_hosts"] == ["github.com"]
        assert caps["tools_allowed"] == settings.analysis_defaults.tools
        assert (await client.get("/v1/capabilities")).status_code == 401


@pytest.mark.asyncio
async def test_unknown_route_is_problem_json(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner())) as client:
        resp = await client.get("/v1/nope", headers=auth())
        assert resp.status_code == 404 and resp.json()["code"] == "not_found"


@pytest.mark.asyncio
async def test_concurrent_idempotent_submissions_create_one_job(settings) -> None:
    import asyncio

    async with ServiceClient(build_app(settings, runner=FakeRunner("hang"))) as client:
        headers = {**auth(), "Idempotency-Key": "race"}
        responses = await asyncio.gather(*(
            client.post("/v1/scans", json=_body(), headers=headers) for _ in range(5)))
        assert {r.status_code for r in responses} <= {200, 202}
        assert len({r.json()["id"] for r in responses}) == 1


@pytest.mark.asyncio
async def test_prefer_wait_times_out_with_202(settings) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner("hang"))) as client:
        resp = await client.post("/v1/scans", json=_body(), headers={**auth(), "Prefer": "wait=1"})
        assert resp.status_code == 202
        assert resp.json()["status"] in ("queued", "running")


@pytest.mark.asyncio
async def test_idempotency_insert_race_returns_the_winner(settings, monkeypatch) -> None:
    async with ServiceClient(build_app(settings, runner=FakeRunner("hang"))) as client:
        headers = {**auth(), "Idempotency-Key": "race2"}
        first = (await client.post("/v1/scans", json=_body(), headers=headers)).json()
        store = client._transport.app.state.service["store"]  # type: ignore[attr-defined]
        real = store.find_idempotent
        calls = {"n": 0}

        def miss_once(*args, **kwargs):  # the losing request's pre-check misses the winner
            calls["n"] += 1
            return None if calls["n"] == 1 else real(*args, **kwargs)

        monkeypatch.setattr(store, "find_idempotent", miss_once)
        again = await client.post("/v1/scans", json=_body(), headers=headers)
        assert again.status_code == 200 and again.json()["id"] == first["id"]
        calls["n"] = 0
        conflict = await client.post("/v1/scans", json=_body(ref="other"), headers=headers)
        assert conflict.status_code == 409
