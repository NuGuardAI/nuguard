"""Offline honesty: without network, dependency tools are skipped/failed, never ok."""

from __future__ import annotations

import shutil
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from nuguard.service.models import ScanOptions, ScanStatus
from nuguard.service.pipeline import JobSpec, run_pipeline
from tests.git_http_server import GitHttpServer, make_bare_repo
from tests.service.conftest import ServiceClient, auth, build_app, make_settings, wait_terminal
from tests.service.fakes import FakeRunner

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

TOKEN = "github_pat_OFFLINE_TEST_abcdefghijklmnopqrstuv"
FILES = {"app.py": "import requests\n", "requirements.txt": "requests==2.19.0\n"}


def _scan(tmp_path: Path, *, network: bool) -> object:
    root, _ = make_bare_repo(tmp_path, "agent", FILES)
    with GitHttpServer(root, TOKEN) as server:
        spec = JobSpec(job_id="scn_off", repo_url="https://github.com/a/b",
                       clone_url=server.url("agent"), options=ScanOptions(network=network),
                       tools=["nga-rules", "osv"], allowed_hosts=None)
        scratch = tmp_path / "s"
        scratch.mkdir()
        body, _ = run_pipeline(spec, TOKEN, scratch, lambda _e: None)
    return body


def test_network_false_marks_osv_skipped_and_job_partial(tmp_path: Path) -> None:
    body = _scan(tmp_path, network=False)
    osv = next(r for r in body.coverage.tool_runs if r.tool == "osv")
    assert osv.status.value == "skipped"
    assert body.status is ScanStatus.PARTIAL
    assert body.coverage.gaps == ["osv: skipped (offline mode: network lookups disabled)"]


def test_blocked_network_marks_osv_failed_and_job_partial(tmp_path: Path) -> None:
    with patch("nuguard.analysis.osv_client._post_json",
               side_effect=urllib.error.URLError("unreachable")):
        body = _scan(tmp_path, network=True)
    osv = next(r for r in body.coverage.tool_runs if r.tool == "osv")
    assert osv.status.value == "failed"
    assert osv.reason == "network unreachable"
    assert body.status is ScanStatus.PARTIAL
    assert body.coverage.gaps == ["osv: failed (network unreachable)"]


@pytest.mark.asyncio
async def test_server_network_false_narrows_requests(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, analysis_defaults={"network": False})
    runner = FakeRunner()
    async with ServiceClient(build_app(settings, runner=runner)) as client:
        job = (await client.post("/v1/scans", json={"repo_url": "https://github.com/a/b",
                                                    "options": {"network": True}},
                                 headers=auth())).json()
        await wait_terminal(client, job["id"])
        result = (await client.get(f"/v1/scans/{job['id']}/result", headers=auth())).json()
    assert runner.specs[job["id"]].options.network is False
    assert result["provenance"]["options_effective"]["network"] is False
