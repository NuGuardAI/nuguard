"""Fingerprint algorithm: inputs that must and must not affect identity."""

from __future__ import annotations

from pathlib import Path

from nuguard.models.finding import Finding, FindingLocation, Severity
from nuguard.service import fingerprint as fp
from nuguard.service.results import project_findings

REPO = "github.com/acme/agent"


def _finding(**kw) -> Finding:
    base = dict(finding_id="semgrep-x-1234abcd", title="t", severity=Severity.HIGH,
                description="d", rule_id="r", source_tool="semgrep", kind="code")
    base.update(kw)
    return Finding(**base)


def test_normalize_repo() -> None:
    assert fp.normalize_repo("https://GitHub.com/Acme/Agent.git") == "github.com/acme/agent"
    assert fp.normalize_repo("https://github.com/acme/agent/tree/main/sub") == REPO


def test_prefix_and_length() -> None:
    value = fp.compute(REPO, "code", "semgrep", "r", "k")
    assert value.startswith("nf1_") and len(value) == 4 + 32


def test_code_identity_uses_python_symbol_not_lines(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("class A:\n    def run(self):\n        return eval(x)\n")
    finding = _finding(locations=[FindingLocation(path="app.py", start_line=3, end_line=3)])
    assert fp.identity_key(finding, "code", tmp_path) == "app.py#A.run"
    (tmp_path / "app.py").write_text("\n\n\nclass A:\n    def run(self):\n        return eval(x)\n")
    moved = _finding(locations=[FindingLocation(path="app.py", start_line=6, end_line=6)])
    assert fp.identity_key(moved, "code", tmp_path) == "app.py#A.run"


def test_code_identity_falls_back_to_whitespace_normalized_snippet(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("eval(  userInput )\n")
    a = _finding(locations=[FindingLocation(path="app.js", start_line=1)])
    key_a = fp.identity_key(a, "code", tmp_path)
    (tmp_path / "app.js").write_text("\n\neval( userInput )\n")
    b = _finding(locations=[FindingLocation(path="app.js", start_line=3)])
    assert fp.identity_key(b, "code", tmp_path) == key_a


def test_snippet_never_reads_outside_root(tmp_path: Path) -> None:
    assert fp.snippet_hash(tmp_path, "../../etc/passwd", 1, 1) == "nosnippet"


def test_posture_dependency_iac_container_keys() -> None:
    posture = _finding(kind="posture", component_ref="FRAMEWORK:openai-client")
    assert fp.identity_key(posture, "posture", None) == "FRAMEWORK:openai-client"
    dep = _finding(kind="dependency", affected_component="pkg:pypi/requests@2.19.1",
                   locations=[FindingLocation(path="requirements.txt")])
    assert fp.identity_key(dep, "dependency", None) == "pypi:requests@2#requirements.txt"
    patch_bump = dep.model_copy(update={"affected_component": "pkg:pypi/requests@2.31.0"})
    assert fp.identity_key(patch_bump, "dependency", None) == fp.identity_key(dep, "dependency", None)
    iac = _finding(kind="iac", locations=[FindingLocation(path="main.tf", symbol="aws_s3_bucket.b")])
    assert fp.identity_key(iac, "iac", None) == "main.tf#aws_s3_bucket.b"
    container = _finding(kind="container", container_image="python:3.12-slim@sha256:abc",
                         affected_component="openssl@3.0.1")
    assert fp.identity_key(container, "container", None) == "python#:openssl"


def test_titles_and_ids_do_not_affect_fingerprint(tmp_path: Path) -> None:
    a = _finding(kind="posture", component_ref="AGENT:triage", title="one", finding_id="x-1")
    b = a.model_copy(update={"title": "two", "finding_id": "x-2", "description": "other"})
    (fa,) = project_findings([a], repo=REPO, scan_root=tmp_path, include_posture=True)
    (fb,) = project_findings([b], repo=REPO, scan_root=tmp_path, include_posture=True)
    assert fa.fingerprint == fb.fingerprint


def test_duplicate_identities_are_disambiguated_deterministically(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def f():\n    eval(a)\n    eval(b)\n")
    one = _finding(locations=[FindingLocation(path="app.py", start_line=2)])
    two = _finding(locations=[FindingLocation(path="app.py", start_line=3)])
    first = project_findings([one, two], repo=REPO, scan_root=tmp_path, include_posture=True)
    second = project_findings([two, one], repo=REPO, scan_root=tmp_path, include_posture=True)
    assert len({f.fingerprint for f in first}) == 2
    assert [f.fingerprint for f in first] == [f.fingerprint for f in second]


def test_include_posture_false_drops_posture() -> None:
    posture = _finding(kind="posture", component_ref="AGENT:x")
    assert project_findings([posture], repo=REPO, scan_root=None, include_posture=False) == []
