"""Stable finding identity: rule_id / source_tool / kind / locations, and posture dedupe."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nuguard.analysis.static_analyzer import StaticAnalyzer, _relative_path
from nuguard.sbom.models import AiSbomDocument, Edge, Evidence, Node, SourceLocation
from nuguard.sbom.types import ComponentType


def _analyzer(root: Path | None = None, **kw) -> StaticAnalyzer:
    flags = {f"enable_{t}": False for t in
             ("osv", "grype", "checkov", "trivy", "semgrep", "atlas", "supply_chain")}
    flags.update(kw)
    return StaticAnalyzer(source_path=root, **flags)


# ── path relativization ──────────────────────────────────────────────────────


def test_absolute_path_inside_root_becomes_relative(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("")
    assert _relative_path(str(tmp_path / "app" / "x.py"), tmp_path) == "app/x.py"


def test_checkov_style_path_is_joined_to_scan_target(tmp_path: Path) -> None:
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "main.tf").write_text("")
    assert _relative_path("/main.tf", tmp_path, str(tmp_path / "infra")) == "infra/main.tf"


def test_paths_outside_root_are_dropped(tmp_path: Path) -> None:
    assert _relative_path("/etc/passwd", tmp_path) is None
    assert _relative_path("../../etc/passwd", tmp_path, must_exist=True) is None
    assert _relative_path("https://example.com/x", tmp_path) is None
    assert _relative_path("pkg:pypi/requests@1", tmp_path) is None


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_symlink_escaping_root_is_dropped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "leak").symlink_to("/etc/passwd")
    assert _relative_path(str(root / "leak"), root) is None
    assert _relative_path("leak", root) is None


def test_relative_path_without_root_is_cleaned() -> None:
    assert _relative_path("./requirements.txt", None) == "requirements.txt"
    assert _relative_path(".github/workflows/ci.yml", None) == ".github/workflows/ci.yml"
    assert _relative_path("../x", None) is None


# ── per-tool projection ──────────────────────────────────────────────────────


def test_semgrep_finding_gets_relative_location(tmp_path: Path) -> None:
    (tmp_path / "chat.py").write_text("x\n" * 30)
    analyzer = _analyzer(tmp_path)
    raw = {"rule_id": "nuguard-eval-on-llm-output", "title": "t", "severity": "HIGH",
           "description": "d", "affected": [f"{tmp_path}/chat.py:22"],
           "path": str(tmp_path / "chat.py"), "start_line": 22, "end_line": 23,
           "scan_target": str(tmp_path)}
    finding = analyzer._to_finding(raw, "semgrep")
    assert finding.rule_id == "nuguard-eval-on-llm-output"
    assert finding.source_tool == "semgrep"
    assert finding.kind == "code"
    (loc,) = finding.locations
    assert (loc.path, loc.start_line, loc.end_line) == ("chat.py", 22, 23)


def test_semgrep_rule_id_drops_rules_file_prefix(tmp_path: Path) -> None:
    analyzer = _analyzer(tmp_path)
    for check_id in (
        "nuguard.analysis.plugins.semgrep_rules.nuguard-eval-on-llm-output",
        "usr.lib.python3.12.site-packages.nuguard.analysis.plugins.semgrep_rules."
        "nuguard-eval-on-llm-output",
    ):
        f = analyzer._to_finding({"rule_id": check_id, "severity": "HIGH", "title": "t"}, "semgrep")
        assert f.rule_id == "nuguard-eval-on-llm-output"


def test_checkov_and_trivy_misconfig_are_iac(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text("")
    analyzer = _analyzer(tmp_path)
    ck = analyzer._to_finding({"rule_id": "CKV_AWS_20", "severity": "HIGH", "title": "t",
                               "file_path": "/main.tf", "line_range": [3, 9],
                               "scan_target": str(tmp_path)}, "checkov")
    assert (ck.kind, ck.locations[0].path, ck.locations[0].start_line) == ("iac", "main.tf", 3)
    tv = analyzer._to_finding({"rule_id": "AVD-AWS-0086", "severity": "HIGH", "title": "t",
                               "source": "trivy-misconfig", "target": "main.tf",
                               "scan_target": str(tmp_path), "start_line": 4}, "trivy")
    assert (tv.kind, tv.locations[0].path) == ("iac", "main.tf")


def test_trivy_image_vuln_is_container() -> None:
    analyzer = _analyzer()
    f = analyzer._to_finding({"rule_id": "CVE-1", "severity": "LOW", "title": "t",
                              "source": "trivy", "container_image": "python:3.12"}, "trivy")
    assert f.kind == "container"


def test_supply_chain_paths_become_locations(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{}")
    analyzer = _analyzer(tmp_path)
    f = analyzer._to_finding({"rule_id": "NGA-SC-008", "severity": "HIGH", "title": "t",
                              "affected": [".mcp.json", "not-a-file"]}, "supply-chain")
    assert f.kind == "supply_chain"
    assert [loc.path for loc in f.locations] == [".mcp.json"]


# ── NGA-002 per-model-name fan-out ───────────────────────────────────────────


def _thirty_models_one_client(edges: bool) -> AiSbomDocument:
    client = Node(name="openai-client", component_type=ComponentType.FRAMEWORK, confidence=0.9,
                  evidence=[Evidence(kind="ast", confidence=0.9, detail="OpenAI()",
                                     location=SourceLocation(path="app.py", line=3))])
    models = [
        Node(name=f"model-{i:02d}", component_type=ComponentType.MODEL, confidence=0.9,
             evidence=[Evidence(kind="regex", confidence=0.9, detail=f"model-{i:02d}",
                                location=SourceLocation(path="app.py", line=10 + i))])
        for i in range(30)
    ]
    rel = [Edge(source=client.id, target=m.id, relationship_type="USES") for m in models]
    return AiSbomDocument(target="x", nodes=[client, *models], edges=rel if edges else [])


def _nga002_a(findings) -> list:
    return [f for f in findings if f.rule_id == "NGA-002" and "sub-check A" in f.title]


@pytest.mark.asyncio
@pytest.mark.parametrize("edges", [True, False])
async def test_nga002_without_dedupe_fires_per_model(edges: bool) -> None:
    findings = await _analyzer().analyze(_thirty_models_one_client(edges))
    assert len(_nga002_a(findings)) == 30


@pytest.mark.asyncio
async def test_nga002_dedupe_fires_once_per_client_node() -> None:
    findings = await _analyzer(dedupe_posture=True).analyze(_thirty_models_one_client(True))
    (finding,) = _nga002_a(findings)
    assert finding.title == "LLM models used by 'openai-client' have no output guardrail (sub-check A)"
    assert finding.evidence.startswith("30 model(s): model-00, model-01")
    assert finding.component_ref == "FRAMEWORK:openai-client"
    assert finding.kind == "posture"
    assert finding.locations and finding.locations[0].path == "app.py"


@pytest.mark.asyncio
async def test_nga002_dedupe_groups_unattached_models_by_file() -> None:
    findings = await _analyzer(dedupe_posture=True).analyze(_thirty_models_one_client(False))
    (finding,) = _nga002_a(findings)
    assert "'app.py'" in finding.title
    assert "30 model(s)" in (finding.evidence or "")


@pytest.mark.asyncio
async def test_dedupe_title_is_stable_when_model_list_changes() -> None:
    a = _nga002_a(await _analyzer(dedupe_posture=True).analyze(_thirty_models_one_client(True)))
    doc = _thirty_models_one_client(True)
    doc.nodes = doc.nodes[:5]
    doc.edges = doc.edges[:4]
    b = _nga002_a(await _analyzer(dedupe_posture=True).analyze(doc))
    assert a[0].title == b[0].title
    assert a[0].component_ref == b[0].component_ref


# ── every finding carries identity ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_finding_has_rule_id_source_tool_and_kind(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    analyzer = StaticAnalyzer(source_path=tmp_path, offline=True, dedupe_posture=True)
    with patch("shutil.which", return_value=None):
        findings = await analyzer.analyze(_thirty_models_one_client(True))
    assert findings
    for f in findings:
        assert f.rule_id and f.source_tool and f.kind, f
        for loc in f.locations:
            assert not loc.path.startswith("/")
            assert str(tmp_path) not in loc.path
