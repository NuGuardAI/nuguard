"""Tests for supply-chain threat detection.

Covers SupplyChainScanner, SupplyChainPlugin, and DevToolConfigAdapter.
All tests are offline — no network calls, no git, no subprocess execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuguard.analysis.supply_chain_scanner import SupplyChainScanner
from nuguard.analysis.plugins.supply_chain import SupplyChainPlugin
from nuguard.analysis.models import AnalysisResult

# Fixture root
FIXTURES = Path(__file__).parent.parent / "fixtures" / "supply_chain"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule_ids(findings: list[dict]) -> set[str]:
    return {f.get("rule_id", "") for f in findings}


def _severities(findings: list[dict]) -> set[str]:
    return {f.get("severity", "") for f in findings}


def _scan(fixture: str, profile: str = "standard") -> list[dict]:
    scanner = SupplyChainScanner(profile=profile)
    return scanner.scan(FIXTURES / fixture)


# ---------------------------------------------------------------------------
# Lifecycle script tests
# ---------------------------------------------------------------------------

def test_postinstall_bun_is_critical():
    findings = _scan("package_postinstall_bun")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-013" in rule_ids, f"Expected NGA-SC-013, got {rule_ids}"
    bun_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-013"]
    assert all(f["severity"] == "critical" for f in bun_findings)


def test_curl_bash_lifecycle_is_critical():
    findings = _scan("package_curl_bash")
    rule_ids = _rule_ids(findings)
    # NGA-SC-012 (pipe-to-shell) should fire
    assert "NGA-SC-012" in rule_ids, f"Expected NGA-SC-012, got {rule_ids}"
    pipe_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-012"]
    assert all(f["severity"] == "critical" for f in pipe_findings)


def test_network_download_in_install_hook_is_critical():
    findings = _scan("package_curl_bash")
    rule_ids = _rule_ids(findings)
    # NGA-SC-011 (network in install hook) should also fire
    assert "NGA-SC-011" in rule_ids, f"Expected NGA-SC-011, got {rule_ids}"


def test_proc_environ_access_is_critical():
    findings = _scan("package_proc_environ")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-014" in rule_ids, f"Expected NGA-SC-014, got {rule_ids}"
    cred_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-014"]
    assert all(f["severity"] == "critical" for f in cred_findings)


def test_clean_package_no_lifecycle_findings():
    findings = _scan("clean_package")
    lifecycle_rules = {"NGA-SC-011", "NGA-SC-012", "NGA-SC-013", "NGA-SC-014"}
    found_lifecycle = {f.get("rule_id") for f in findings} & lifecycle_rules
    assert not found_lifecycle, f"Unexpected lifecycle findings on clean package: {found_lifecycle}"


# ---------------------------------------------------------------------------
# GitHub Actions workflow tests
# ---------------------------------------------------------------------------

def test_gha_oidc_unpinned_is_high():
    findings = _scan("github_publish_oidc_unpinned")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-001" in rule_ids, f"Expected NGA-SC-001, got {rule_ids}"
    oidc_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-001"]
    assert all(f["severity"] == "high" for f in oidc_findings)


def test_gha_prt_injection_is_critical():
    findings = _scan("github_prt_injection")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-002" in rule_ids, f"Expected NGA-SC-002, got {rule_ids}"
    prt_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-002"]
    assert all(f["severity"] == "critical" for f in prt_findings)


# ---------------------------------------------------------------------------
# AI-agent config tests
# ---------------------------------------------------------------------------

def test_claude_bash_wildcard_is_high():
    findings = _scan("claude_bash_wildcard")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-007" in rule_ids, f"Expected NGA-SC-007, got {rule_ids}"
    bash_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-007"]
    assert all(f["severity"] == "high" for f in bash_findings)


def test_claude_settings_also_emits_repo_controlled_warning():
    findings = _scan("claude_bash_wildcard")
    rule_ids = _rule_ids(findings)
    # NGA-SC-008: repo-controlled config
    assert "NGA-SC-008" in rule_ids, f"Expected NGA-SC-008, got {rule_ids}"
    assert any(f["severity"] == "medium" for f in findings if f.get("rule_id") == "NGA-SC-008")


def test_mcp_untrusted_server_is_high():
    findings = _scan("mcp_untrusted")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-009" in rule_ids, f"Expected NGA-SC-009, got {rule_ids}"
    mcp_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-009"]
    assert all(f["severity"] == "high" for f in mcp_findings)


# ---------------------------------------------------------------------------
# Large payload tests (full profile only)
# ---------------------------------------------------------------------------

def test_large_payload_in_hidden_dir_fires_in_full_profile():
    findings = _scan("large_dropper", profile="full")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-017" in rule_ids, f"Expected NGA-SC-017 in full profile, got {rule_ids}"
    large_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-017"]
    assert all(f["severity"] == "high" for f in large_findings)


def test_large_payload_skipped_in_ci_profile():
    findings = _scan("large_dropper", profile="ci")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-017" not in rule_ids, "NGA-SC-017 should be skipped in ci profile"


def test_large_payload_runs_in_standard_profile():
    # SC-017..019 moved from full-only to standard (SBOM-native).
    findings = _scan("large_dropper", profile="standard")
    rule_ids = _rule_ids(findings)
    # The large_dropper fixture has a >100 KB file in .claude/ — SC-017 must fire
    assert "NGA-SC-017" in rule_ids, f"NGA-SC-017 should fire in standard profile, got {rule_ids}"


# ---------------------------------------------------------------------------
# Threat-intel IOC matching
# ---------------------------------------------------------------------------

def test_threat_intel_match_is_critical():
    scanner = SupplyChainScanner(profile="standard")
    deps = [{
        "name": "@redhat-cloud-services/frontend-components",
        "version_spec": "==10.1.0",
        "purl": "pkg:npm/%40redhat-cloud-services/frontend-components@10.1.0",
        "group": "runtime",
        "source_file": "package.json",
    }]
    # Scan without source path (no files needed for IOC check)
    findings = scanner.scan(
        Path("/tmp"),
        sbom_nodes=[],
        sbom_deps=deps,
    )
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-025" in rule_ids, f"Expected NGA-SC-025 for known-malicious package, got {rule_ids}"
    ioc_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-025"]
    assert all(f["severity"] == "critical" for f in ioc_findings)


# ---------------------------------------------------------------------------
# SupplyChainPlugin tests
# ---------------------------------------------------------------------------

def test_supply_chain_plugin_returns_ok_status():
    plugin = SupplyChainPlugin()
    result = plugin.run(
        sbom={"nodes": [], "deps": []},
        config={"source_path": str(FIXTURES / "clean_package")},
    )
    assert isinstance(result, AnalysisResult)
    assert result.status == "ok"


def test_supply_chain_plugin_runs_without_source_path():
    # Plugin now runs SBOM-native rules even without a local source directory.
    plugin = SupplyChainPlugin()
    result = plugin.run(sbom={"nodes": [], "deps": []}, config={})
    assert result.status == "ok"


def test_supply_chain_plugin_runs_with_nonexistent_source():
    # Plugin falls back to SBOM-native rules when source path does not exist.
    plugin = SupplyChainPlugin()
    result = plugin.run(
        sbom={"nodes": [], "deps": []},
        config={"source_path": "/nonexistent/path/12345"},
    )
    assert result.status == "ok"


def test_supply_chain_plugin_finds_curl_bash():
    plugin = SupplyChainPlugin()
    result = plugin.run(
        sbom={"nodes": [], "deps": []},
        config={"source_path": str(FIXTURES / "package_curl_bash")},
    )
    assert result.status == "ok"
    rule_ids = {f.get("rule_id") for f in result.findings}
    assert "NGA-SC-012" in rule_ids


def test_supply_chain_plugin_uses_sbom_lifecycle_nodes():
    """When SBOM has LIFECYCLE_SCRIPT nodes, scanner uses them instead of file scan."""
    plugin = SupplyChainPlugin()
    sbom = {
        "nodes": [{
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "package.json:postinstall",
            "component_type": "LIFECYCLE_SCRIPT",
            "confidence": 0.9,
            "metadata": {
                "script_phase": "postinstall",
                "script_body": "npx bun install && node payload.js",
                "invokes_network": False,
            },
        }],
        "deps": [],
    }
    result = plugin.run(
        sbom=sbom,
        config={"source_path": str(FIXTURES / "clean_package")},
    )
    assert result.status == "ok"
    rule_ids = {f.get("rule_id") for f in result.findings}
    assert "NGA-SC-013" in rule_ids


def test_gha_publish_without_oidc_has_no_provenance():
    # NGA-SC-006: publish workflow that isn't using OIDC has no verifiable
    # link between the artifact and the repo/ref/SHA that built it.
    findings = _scan("github_prt_injection")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-006" in rule_ids, f"Expected NGA-SC-006, got {rule_ids}"
    prov_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-006"]
    assert all(f["severity"] == "critical" for f in prov_findings)


def test_gha_publish_with_oidc_has_provenance():
    # The OIDC-publishing fixture should NOT trip NGA-SC-006.
    findings = _scan("github_publish_oidc_unpinned")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-006" not in rule_ids, f"Unexpected NGA-SC-006 with OIDC in use: {rule_ids}"


# ---------------------------------------------------------------------------
# Workflow governance tests (NGA-SC-026/027)
# ---------------------------------------------------------------------------

def test_publish_job_without_environment_gate_is_high():
    findings = _scan("github_prt_injection")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-026" in rule_ids, f"Expected NGA-SC-026, got {rule_ids}"
    gate_findings = [f for f in findings if f.get("rule_id") == "NGA-SC-026"]
    assert all(f["severity"] == "high" for f in gate_findings)


def test_scan_step_with_continue_on_error_is_flagged():
    findings = _scan("github_swallowed_scan_failure")
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-027" in rule_ids, f"Expected NGA-SC-027, got {rule_ids}"


def test_clean_package_no_workflow_governance_findings():
    findings = _scan("clean_package")
    governance_rules = {"NGA-SC-026", "NGA-SC-027"}
    found = {f.get("rule_id") for f in findings} & governance_rules
    assert not found, f"Unexpected workflow-governance findings on clean package: {found}"


# ---------------------------------------------------------------------------
# Git history heuristics (NGA-SC-020/021/022) — subprocess.run is mocked so
# these stay offline/no-git per this module's docstring.
# ---------------------------------------------------------------------------

class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def test_skip_ci_on_workflow_change_fires_sc021(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if "log" in cmd:
            return _FakeCompleted("abc1234 fix: urgent hotfix [skip ci]\n")
        if "diff-tree" in cmd:
            return _FakeCompleted(".github/workflows/publish.yml\n")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)
    scanner = SupplyChainScanner(profile="full")
    findings = scanner.scan(tmp_path)
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-021" in rule_ids, f"Expected NGA-SC-021, got {rule_ids}"


def test_workflow_only_change_fires_sc022(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if "log" in cmd:
            return _FakeCompleted("abc1234 tweak CI\n")
        if "diff-tree" in cmd:
            return _FakeCompleted(".github/workflows/ci.yml\n")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)
    scanner = SupplyChainScanner(profile="full")
    findings = scanner.scan(tmp_path)
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-022" in rule_ids, f"Expected NGA-SC-022, got {rule_ids}"


def test_workflow_change_with_manifest_change_does_not_fire_sc022(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if "log" in cmd:
            return _FakeCompleted("abc1234 bump ci and deps\n")
        if "diff-tree" in cmd:
            return _FakeCompleted(".github/workflows/ci.yml\npackage-lock.json\n")
        raise AssertionError(f"unexpected git command: {cmd}")

    monkeypatch.setattr("subprocess.run", fake_run)
    scanner = SupplyChainScanner(profile="full")
    findings = scanner.scan(tmp_path)
    rule_ids = _rule_ids(findings)
    assert "NGA-SC-022" not in rule_ids, f"Unexpected NGA-SC-022: {rule_ids}"


# ---------------------------------------------------------------------------
# OWASP Top 10 CI/CD Security Risks mapping
# ---------------------------------------------------------------------------

def test_every_nga_sc_rule_has_a_cicd_top10_mapping():
    from nuguard.analysis.supply_chain_scanner import _RULE_META
    from nuguard.common.control_mappings.cicd import cicd_refs_for_rule

    for meta in _RULE_META:
        rule_id = meta["rule_id"]
        assert cicd_refs_for_rule(rule_id), f"{rule_id} has no OWASP CI/CD Top 10 mapping"


def test_cicd_top10_mapping_covers_all_ten_categories():
    from nuguard.common.control_mappings.cicd import CICD_TOP10, NGA_TO_CICD_TOP10

    covered = {cat for refs in NGA_TO_CICD_TOP10.values() for cat in refs}
    assert covered == set(CICD_TOP10), (
        f"NGA-SC rules don't cover every OWASP CI/CD Top 10 category: "
        f"missing {set(CICD_TOP10) - covered}"
    )


def test_supply_chain_plugin_uses_github_workflow_nodes():
    """When SBOM has GITHUB_WORKFLOW nodes, scanner uses them for workflow checks."""
    plugin = SupplyChainPlugin()
    sbom = {
        "nodes": [{
            "id": "00000000-0000-0000-0000-000000000002",
            "name": ".github/workflows/publish.yml",
            "component_type": "GITHUB_WORKFLOW",
            "confidence": 0.9,
            "metadata": {
                "workflow_triggers": ["push"],
                "uses_oidc": True,
                "publishes_to": ["pypi"],
                "action_refs": ["actions/checkout@main", "pypa/gh-action-pypi-publish@release/v1"],
                "workflow_permissions": {"id-token": "write", "contents": "read"},
            },
        }],
        "deps": [],
    }
    result = plugin.run(
        sbom=sbom,
        config={"source_path": str(FIXTURES / "clean_package")},
    )
    assert result.status == "ok"
    rule_ids = {f.get("rule_id") for f in result.findings}
    assert "NGA-SC-001" in rule_ids
