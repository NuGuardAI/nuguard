"""The real pipeline (clone → SBOM → analysis → result) against a private local repo.

The repository is served over smart HTTP with basic auth (tests/git_http_server.py),
so the token path is exercised for real. The host policy is disabled for these
tests only (the server is http://127.0.0.1); policy enforcement is covered in
tests/sbom/test_secure_clone.py.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nuguard.service.models import ScanJob, ScanOptions, ScanStatus
from nuguard.service.pipeline import JobSpec, PipelineError, run_pipeline
from nuguard.service.results import assemble_result
from tests.git_http_server import GitHttpServer, make_bare_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

TOKEN = "github_pat_PIPELINE_TEST_abcdefghijklmnopqrstuvwxyz"
SEMGREP = (Path(sys.executable).parent / "semgrep").exists() or shutil.which("semgrep")

APP_V1 = {
    "app.py": (
        "from openai import OpenAI\n"
        "\n"
        "client = OpenAI()\n"
        "\n"
        "\n"
        "def run_calc(question: str):\n"
        "    resp = client.chat.completions.create(\n"
        "        model='gpt-4o',\n"
        "        messages=[{'role': 'user', 'content': question}],\n"
        "    )\n"
        "    code = resp.choices[0].message.content\n"
        "    return eval(code)\n"
    ),
    "requirements.txt": "openai==1.40.0\nrequests==2.19.0\n",
    "README.md": "demo\n",
}


def _reformatted(files: dict[str, str]) -> dict[str, str]:
    """Same code with blank lines inserted (shifts every line number)."""
    out = dict(files)
    out["app.py"] = "\n\n\n" + files["app.py"].replace("\n\n\ndef", "\n\n\n\n\ndef")
    return out


def _spec(url: str, *, tools: list[str], network: bool = False, **options) -> JobSpec:
    return JobSpec(
        job_id="scn_TEST",
        repo_url="https://github.com/acme/agent",
        clone_url=url,
        ref=None,
        subpath=None,
        options=ScanOptions(network=network, **options),
        tools=tools,
        allowed_hosts=None,
        clone_timeout_s=60,
    )


def _job(job_id: str, status: ScanStatus, sha: str | None) -> ScanJob:
    now = datetime.now(timezone.utc)
    return ScanJob(id=job_id, status=status, repo_url="https://github.com/acme/agent",
                   commit_sha=sha, created_at=now, started_at=now, finished_at=now)


@pytest.fixture()
def served(tmp_path: Path):
    root_v1, sha_v1 = make_bare_repo(tmp_path / "v1", "agent", APP_V1)
    make_bare_repo(tmp_path / "v2", "agent", _reformatted(APP_V1))
    # Serve both under one root: agent.git (v1) and agent2.git (reformatted).
    shutil.copytree(tmp_path / "v2" / "srv" / "agent.git", root_v1 / "agent2.git")
    with GitHttpServer(root_v1, TOKEN) as server:
        yield server, sha_v1


def _run(spec: JobSpec, tmp_path: Path, name: str, token: str | None = TOKEN):
    scratch = tmp_path / name
    scratch.mkdir()
    events: list[dict] = []
    body, sbom = run_pipeline(spec, token, scratch, events.append)
    return body, sbom, events, scratch


TOOLS = ["nga-rules", "atlas", "semgrep", "supply-chain"]


@pytest.mark.skipif(not SEMGREP, reason="semgrep not installed")
def test_private_repo_scan_end_to_end(served, tmp_path: Path) -> None:
    server, sha = served
    body, sbom, events, scratch = _run(_spec(server.url("agent"), tools=TOOLS), tmp_path, "a")

    assert [e["phase"] for e in events if e["type"] == "phase"] == [
        "cloning", "sbom", "analyzing", "finalizing"]
    assert {"type": "commit", "sha": sha} in events
    assert body.commit_sha == sha
    assert body.status is ScanStatus.SUCCEEDED, body.coverage.gaps
    assert [r.tool for r in body.coverage.tool_runs] == TOOLS
    assert "python" in body.coverage.languages_detected
    assert body.inventory.node_counts.get("MODEL", 0) >= 1
    assert sbom["nodes"]

    by_rule = {f.rule_id: f for f in body.findings}
    eval_finding = by_rule["nuguard-eval-on-llm-output"]
    assert eval_finding.kind == "code" and eval_finding.source_tool == "semgrep"
    assert eval_finding.locations[0].path == "app.py"
    assert eval_finding.locations[0].start_line == 12
    assert all(f.fingerprint.startswith("nf1_") for f in body.findings)

    text = body.model_dump_json()
    assert str(scratch) not in text and str(tmp_path) not in text
    assert TOKEN not in text and TOKEN not in json.dumps(sbom)
    for f in body.findings:
        for loc in f.locations:
            assert not loc.path.startswith("/")


@pytest.mark.skipif(not SEMGREP, reason="semgrep not installed")
def test_fingerprints_and_hash_are_stable(served, tmp_path: Path) -> None:
    server, _ = served
    spec = _spec(server.url("agent"), tools=TOOLS)
    first, _, _, _ = _run(spec, tmp_path, "r1")
    second, _, _, _ = _run(spec, tmp_path, "r2")
    reformatted, _, _, _ = _run(_spec(server.url("agent2"), tools=TOOLS), tmp_path, "r3")

    fps = {f.fingerprint for f in first.findings}
    assert fps
    assert fps == {f.fingerprint for f in second.findings}
    assert fps == {f.fingerprint for f in reformatted.findings}
    # line numbers did move, so the fingerprint really ignores them
    line = lambda body: next(f.locations[0].start_line for f in body.findings  # noqa: E731
                             if f.rule_id == "nuguard-eval-on-llm-output")
    assert line(reformatted) != line(first)

    a = assemble_result(first, _job("scn_A", first.status, first.commit_sha))
    b = assemble_result(second, _job("scn_B", second.status, second.commit_sha))
    assert a.result_sha256 == b.result_sha256


def test_offline_scan_is_partial_never_clean(served, tmp_path: Path) -> None:
    server, _ = served
    spec = _spec(server.url("agent"), tools=["nga-rules", "osv", "grype"], network=False)
    body, _, _, _ = _run(spec, tmp_path, "off")
    runs = {r.tool: r for r in body.coverage.tool_runs}
    assert runs["osv"].status.value == "skipped"
    assert runs["grype"].status.value == "skipped"
    assert body.status is ScanStatus.PARTIAL
    assert body.coverage.gaps and all("offline" in g for g in body.coverage.gaps)


def test_wrong_token_and_missing_ref_fail_cleanly(served, tmp_path: Path) -> None:
    server, _ = served
    with pytest.raises(PipelineError) as exc:
        _run(_spec(server.url("agent"), tools=["nga-rules"]), tmp_path, "w",
             token="github_pat_WRONG_TOKEN_0123456789abcdef")
    assert exc.value.code == "auth_failed"
    spec = _spec(server.url("agent"), tools=["nga-rules"]).model_copy(update={"ref": "nope"})
    with pytest.raises(PipelineError) as exc:
        _run(spec, tmp_path, "r")
    assert exc.value.code == "ref_not_found"


def test_subpath_and_include_options(served, tmp_path: Path) -> None:
    server, _ = served
    spec = _spec(server.url("agent"), tools=["nga-rules"], include_posture=False,
                 include_sbom=True)
    body, _, _, _ = _run(spec, tmp_path, "opts")
    assert all(f.kind != "posture" for f in body.findings)
    assert body.sbom is not None
    missing = _spec(server.url("agent"), tools=["nga-rules"]).model_copy(
        update={"subpath": "does/not/exist"})
    with pytest.raises(PipelineError) as exc:
        _run(missing, tmp_path, "sub")
    assert exc.value.code == "subpath_not_found"


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_symlink_out_of_repo_is_not_followed(tmp_path: Path) -> None:
    src_files = {"app.py": "print('hi')\n"}
    root, _ = make_bare_repo(tmp_path / "s", "agent", src_files)
    # Add a symlink pointing outside the repository.
    work = tmp_path / "s" / "agent-src"
    os.symlink("/etc/passwd", work / "leak.py")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=work, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "link"], cwd=work, env=env, check=True)
    subprocess.run(["git", "push", "-q", str(root / "agent.git"), "main"], cwd=work, env=env,
                   check=True)
    with GitHttpServer(root, TOKEN) as server:
        body, sbom, _, _ = _run(_spec(server.url("agent"), tools=["nga-rules", "supply-chain"]),
                                tmp_path, "sym")
    passwd = Path("/etc/passwd").read_text(errors="replace")
    first_line = passwd.splitlines()[0] if passwd else "root:"
    assert first_line not in body.model_dump_json()
    assert first_line not in json.dumps(sbom)
