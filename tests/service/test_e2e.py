"""Opt-in end-to-end run against a real public GitHub repository.

Enable with ``NUGUARD_SERVICE_E2E=1``. Override the target with
``NUGUARD_SERVICE_E2E_REPO`` / ``NUGUARD_SERVICE_E2E_REF`` (a branch, tag or
full 40-character SHA; pin a SHA for strict reproducibility).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.service.conftest import ServiceClient, auth, build_app, make_settings, wait_terminal

pytestmark = [
    pytest.mark.service_e2e,
    pytest.mark.skipif(os.environ.get("NUGUARD_SERVICE_E2E") != "1",
                       reason="opt-in: NUGUARD_SERVICE_E2E=1"),
]

REPO = os.environ.get("NUGUARD_SERVICE_E2E_REPO", "https://github.com/openai/openai-quickstart-python")
REF = os.environ.get("NUGUARD_SERVICE_E2E_REF") or None
TOOLS = ["nga-rules", "atlas", "semgrep", "osv", "supply-chain"]


@pytest.mark.asyncio
async def test_two_runs_give_identical_fingerprints_and_hash(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, jobs={"job_timeout_s": 600, "max_concurrent": 1},
                             analysis_defaults={"tools": TOOLS})
    results = []
    async with ServiceClient(build_app(settings)) as client:
        for _ in range(2):
            body = {"repo_url": REPO, "options": {"tools": TOOLS}}
            if REF:
                body["ref"] = REF
            job = (await client.post("/v1/scans", json=body, headers=auth())).json()
            final = await wait_terminal(client, job["id"], timeout=900)
            assert final["status"] in ("succeeded", "partial"), final
            results.append((await client.get(f"/v1/scans/{job['id']}/result",
                                              headers=auth())).json())
    first, second = results
    assert first["job"]["commit_sha"] == second["job"]["commit_sha"]
    assert {f["fingerprint"] for f in first["findings"]} == {
        f["fingerprint"] for f in second["findings"]}
    assert first["result_sha256"] == second["result_sha256"]
    for finding in first["findings"]:
        for loc in finding["locations"]:
            assert not loc["path"].startswith("/")
