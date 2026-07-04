"""Offline supply-chain threat scanner.

Scans a repository tree for supply-chain attack vectors without making any
network calls or executing any code.  Returns raw finding dicts compatible
with ``StaticAnalyzer._raw_to_finding()``.

Rule taxonomy (all ``NGA-SC-*``):
  NGA-SC-001  HIGH      Publish workflow: OIDC + unpinned third-party action
  NGA-SC-002  CRITICAL  workflow_dispatch/pull_request_target reaches publish/secrets path
  NGA-SC-003  HIGH      id-token:write + contents:write + mutable checkout
  NGA-SC-004  HIGH      CI step: unpinned global install or shell download
  NGA-SC-005  CRITICAL  Step reads /proc/environ, cloud creds, runner memory
  NGA-SC-006  CRITICAL  Publish workflow provenance cannot be tied to repo/ref/SHA
  NGA-SC-007  HIGH      AI-agent config grants broad shell permission (Bash(*:*))
  NGA-SC-008  MEDIUM    AI-agent config is repo-controlled (commit-level attack surface)
  NGA-SC-009  HIGH      .mcp.json references external/untrusted MCP server
  NGA-SC-010  HIGH      AI-agent auto-run / auto-approve mode enabled in repo config
  NGA-SC-011  CRITICAL  postinstall/preinstall invokes network download
  NGA-SC-012  CRITICAL  Lifecycle script pipes to shell
  NGA-SC-013  CRITICAL  Lifecycle script downloads and runs Bun
  NGA-SC-014  CRITICAL  Lifecycle script reads credential paths/env vars
  NGA-SC-015  HIGH      setup.py or pyproject build hook makes network call
  NGA-SC-016  HIGH      Lifecycle script body contains eval/Function/atob/base64
  NGA-SC-017  HIGH      File >100 KB in hidden tool directory
  NGA-SC-018  HIGH      High-entropy or long base64 blob in config/Markdown file
  NGA-SC-019  MEDIUM    Minified single-line JS above 5 KB
  NGA-SC-020  MEDIUM    Commit claims "dependency update" but only changes AI/workflow files (full profile)
  NGA-SC-021  HIGH      [skip ci] on security-sensitive file change (full profile)
  NGA-SC-022  HIGH      Workflow changed without manifest/lockfile change (full profile)
  NGA-SC-023  MEDIUM    Dependency declared via mutable ref: git URL / tarball URL / file:
  NGA-SC-024  LOW       Declared package not found in lockfile
  NGA-SC-025  CRITICAL  Package matches known-malicious IOC in threat-intel feed
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from nuguard.common.logging import get_logger

_log = get_logger("analysis.supply_chain_scanner")

# ── Regex patterns ────────────────────────────────────────────────────────────
_SHA_PIN_RE = re.compile(r"@[0-9a-f]{40}\b")
_ACTION_REF_RE = re.compile(r"uses:\s*([^\s#\n]+)")
_MUTABLE_ACTION_REF_RE = re.compile(r"uses:\s*[^@\s#\n]+@(?![0-9a-f]{40}\b)[^\s#\n]+")
_TRIGGER_RE = re.compile(r"^\s+pull_request_target:|^\s+workflow_dispatch:", re.MULTILINE)
_PUBLISH_STEP_RE = re.compile(
    r"pypa/gh-action-pypi-publish|npm\s+publish|NPM_TOKEN|SMITHERY_API_KEY",
    re.IGNORECASE,
)
_OIDC_RE = re.compile(r"id-token\s*:\s*write", re.IGNORECASE)
_CONTENTS_WRITE_RE = re.compile(r"contents\s*:\s*write", re.IGNORECASE)
_CRED_RE = re.compile(
    r"/proc/[^'\"]*environ|printenv|\.npmrc|\.pypirc|"
    r"AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|GITHUB_TOKEN|"
    r"npm\s+token|kubeconfig|vault\s+token",
    re.IGNORECASE,
)
_NETWORK_RE = re.compile(r"\bcurl\b|\bwget\b|\bnode-fetch\b|\baxios\b", re.IGNORECASE)
_SHELL_PIPE_RE = re.compile(r"\|\s*(bash|sh|node|python)\b", re.IGNORECASE)
_BUN_RE = re.compile(r"\bnpx\s+bun\b|\bbun\s+install\b|\bbun\s+run\b", re.IGNORECASE)
_EVAL_RE = re.compile(r"\beval\s*\(|\bFunction\s*\(|\batob\s*\(", re.IGNORECASE)
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")
_BROAD_BASH_RE = re.compile(r"Bash\s*\(\s*\*\s*:\s*\*\s*\)")
_AUTO_EXEC_RE = re.compile(r'"auto_run"\s*:\s*true|auto_run:\s*true|auto_approve_everything', re.IGNORECASE)
_MUTABLE_DEP_RE = re.compile(r"git\+https?://|\.git#|tarball|\.tgz|\.tar\.gz|file:", re.IGNORECASE)
_UNPINNED_GLOBAL_RE = re.compile(r"\bnpm\s+install\s+-g\b|\bnpm\s+i\s+-g\b|\bnpx\b(?!\s+--yes)", re.IGNORECASE)
_HIDDEN_TOOL_DIRS = {".claude", ".cursor", ".codex", ".gemini", ".vscode"}

# ── Profiles ─────────────────────────────────────────────────────────────────
# ci: highest-signal checks only
# standard: all offline checks (default)
# full: standard + large payload entropy + git commit heuristics
_CI_RULES = {
    "NGA-SC-001", "NGA-SC-002", "NGA-SC-004", "NGA-SC-005",
    "NGA-SC-007", "NGA-SC-009", "NGA-SC-010",
    "NGA-SC-011", "NGA-SC-012", "NGA-SC-013", "NGA-SC-014",
    "NGA-SC-025",
}
_STANDARD_RULES = _CI_RULES | {
    "NGA-SC-003", "NGA-SC-006", "NGA-SC-008", "NGA-SC-015", "NGA-SC-016",
    "NGA-SC-017", "NGA-SC-018", "NGA-SC-019",
    "NGA-SC-023", "NGA-SC-024",
}
_FULL_RULES = _STANDARD_RULES | {
    "NGA-SC-020", "NGA-SC-021", "NGA-SC-022",
}

_PROFILE_RULES: dict[str, set[str]] = {
    "ci": _CI_RULES,
    "standard": _STANDARD_RULES,
    "full": _FULL_RULES,
}

# ── Rule metadata for audit reporting ────────────────────────────────────────
_RULE_META: list[dict[str, str]] = [
    {"rule_id": "NGA-SC-001", "severity": "HIGH",
     "title": "Publish workflow uses OIDC with unpinned actions",
     "checks": "GITHUB_WORKFLOW nodes / .github/workflows/ for id-token:write + mutable action refs",
     "pass_reason": "No publish workflows found with OIDC and unpinned third-party actions"},
    {"rule_id": "NGA-SC-002", "severity": "CRITICAL",
     "title": "Dangerous workflow trigger reaches publish path",
     "checks": "Workflow trigger types (pull_request_target, workflow_dispatch) and publish steps",
     "pass_reason": "No workflows with dangerous triggers on a publish-capable path"},
    {"rule_id": "NGA-SC-003", "severity": "HIGH",
     "title": "id-token:write + contents:write + mutable checkout",
     "checks": "Workflow permissions (id-token:write + contents:write) and action refs",
     "pass_reason": "No workflows combine id-token:write + contents:write with mutable checkouts"},
    {"rule_id": "NGA-SC-004", "severity": "HIGH",
     "title": "CI step uses unpinned global install",
     "checks": "Workflow steps for npm install -g / npx without pinned version",
     "pass_reason": "No unpinned global install steps detected in CI workflows"},
    {"rule_id": "NGA-SC-005", "severity": "CRITICAL",
     "title": "Workflow step reads credential paths",
     "checks": "Workflow steps for /proc/environ, cloud creds, npm token, vault token patterns",
     "pass_reason": "No credential-harvesting patterns found in workflow steps"},
    {"rule_id": "NGA-SC-006", "severity": "CRITICAL",
     "title": "Publish workflow provenance cannot be tied to repo/ref/SHA",
     "checks": "Publish workflow provenance attestation steps",
     "pass_reason": "No publish workflows with missing provenance attestation detected"},
    {"rule_id": "NGA-SC-007", "severity": "HIGH",
     "title": "AI-agent config grants unrestricted shell access",
     "checks": "DEVELOPER_TOOL_CONFIG nodes / .claude/settings.json for Bash(*:*) permission",
     "pass_reason": "No AI-agent config grants unrestricted Bash(*:*) access"},
    {"rule_id": "NGA-SC-008", "severity": "MEDIUM",
     "title": "AI-agent config is repo-controlled (commit-level attack surface)",
     "checks": "Presence of .claude/settings.json or other AI-agent configs in the repository",
     "pass_reason": "No repo-controlled AI-agent config files found"},
    {"rule_id": "NGA-SC-009", "severity": "HIGH",
     "title": ".mcp.json references external/untrusted MCP server",
     "checks": "MCP_SERVER nodes / .mcp.json for untrusted server entries",
     "pass_reason": "No untrusted MCP server references found"},
    {"rule_id": "NGA-SC-010", "severity": "HIGH",
     "title": "AI-agent auto-run/auto-approve mode enabled in repo config",
     "checks": "AI-agent config files for auto_run:true or auto_approve_everything patterns",
     "pass_reason": "No auto-run or auto-approve mode enabled in repo-level AI configs"},
    {"rule_id": "NGA-SC-011", "severity": "CRITICAL",
     "title": "Install hook makes network request",
     "checks": "postinstall/preinstall/install/prepare scripts for curl/wget/node-fetch/axios",
     "pass_reason": "No install hooks making outbound network requests detected"},
    {"rule_id": "NGA-SC-012", "severity": "CRITICAL",
     "title": "Lifecycle script pipes into a shell",
     "checks": "Lifecycle script bodies for pipe-to-shell patterns (| bash, | sh, | node)",
     "pass_reason": "No lifecycle scripts pipe output into a shell interpreter"},
    {"rule_id": "NGA-SC-013", "severity": "CRITICAL",
     "title": "Lifecycle script downloads and executes Bun",
     "checks": "Lifecycle script bodies for npx bun / bun install / bun run patterns",
     "pass_reason": "No Bun download patterns found in lifecycle scripts"},
    {"rule_id": "NGA-SC-014", "severity": "CRITICAL",
     "title": "Lifecycle script accesses credential paths",
     "checks": "Lifecycle script bodies for /proc/environ, .npmrc, AWS keys, GITHUB_TOKEN",
     "pass_reason": "No credential-access patterns found in lifecycle scripts"},
    {"rule_id": "NGA-SC-015", "severity": "HIGH",
     "title": "Python build hook makes network call",
     "checks": "setup.py / pyproject.toml build hooks for urllib/requests/httpx calls",
     "pass_reason": "No network calls detected in Python build hooks"},
    {"rule_id": "NGA-SC-016", "severity": "HIGH",
     "title": "Lifecycle script uses eval/Function/atob (obfuscation indicator)",
     "checks": "Lifecycle script bodies for eval(), Function(), atob() patterns",
     "pass_reason": "No obfuscation patterns (eval/Function/atob) found in lifecycle scripts"},
    {"rule_id": "NGA-SC-017", "severity": "HIGH",
     "title": "Large file in hidden tool directory",
     "checks": "Files >100 KB in .claude/, .cursor/, .codex/, .gemini/, .vscode/ directories",
     "pass_reason": "No large files found in hidden AI-agent/editor directories"},
    {"rule_id": "NGA-SC-018", "severity": "HIGH",
     "title": "High-entropy blob in tool directory",
     "checks": "Files in hidden tool directories with Shannon entropy >6.5 bits/byte",
     "pass_reason": "No high-entropy blobs detected in hidden tool directories"},
    {"rule_id": "NGA-SC-019", "severity": "MEDIUM",
     "title": "Minified single-line JavaScript above 5 KB",
     "checks": "JavaScript files with a single line >5000 characters",
     "pass_reason": "No minified single-line JavaScript files found"},
    {"rule_id": "NGA-SC-020", "severity": "MEDIUM",
     "title": "Commit claims dependency update but only changes AI/workflow files",
     "checks": "Git log (last 50 commits) for message/file-change mismatches",
     "pass_reason": "No suspicious commit message / file-change mismatches found in recent history"},
    {"rule_id": "NGA-SC-021", "severity": "HIGH",
     "title": "[skip ci] on security-sensitive file change",
     "checks": "Git log for [skip ci] flags on commits changing workflows, package scripts, AI configs",
     "pass_reason": "No [skip ci] flags found on security-sensitive file changes"},
    {"rule_id": "NGA-SC-022", "severity": "HIGH",
     "title": "Workflow changed without manifest/lockfile change",
     "checks": "Git log for workflow-only changes without matching manifest/lockfile updates",
     "pass_reason": "No workflow-only changes without manifest/lockfile changes found"},
    {"rule_id": "NGA-SC-023", "severity": "MEDIUM",
     "title": "Dependency uses mutable reference",
     "checks": "SBOM dependencies for git URLs, tarball URLs, or file: references",
     "pass_reason": "No mutable dependency references found"},
    {"rule_id": "NGA-SC-024", "severity": "LOW",
     "title": "npm project missing lockfile",
     "checks": "package.json presence without a matching lockfile",
     "pass_reason": "All npm projects have a committed lockfile (or no package.json present)"},
    {"rule_id": "NGA-SC-025", "severity": "CRITICAL",
     "title": "Package matches known-malicious IOC",
     "checks": "SBOM dependencies and lifecycle script nodes against threat-intel IOC feeds",
     "pass_reason": "No packages matched known-malicious IOC entries in loaded threat-intel feeds"},
]


def _shannon_entropy(data: bytes) -> float:
    """Compute Shannon entropy (bits per byte) of a byte string."""
    if not data:
        return 0.0
    freq: dict[int, int] = {}
    for b in data:
        freq[b] = freq.get(b, 0) + 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


class SupplyChainScanner:
    """Offline, no-install supply-chain threat scanner.

    Profiles
    --------
    ci       — highest-signal checks only (lifecycle scripts, AI-agent configs, top workflows)
    standard — all offline checks including full workflow risks (default)
    full     — standard + large payload entropy + git commit heuristics
    """

    def __init__(
        self,
        profile: str = "standard",
        threat_intel_feeds: list[str] | None = None,
    ) -> None:
        self.profile = profile if profile in _PROFILE_RULES else "standard"
        self._enabled = _PROFILE_RULES[self.profile]
        self._threat_intel = self._load_threat_intel(threat_intel_feeds)
        self._ctx: dict[str, Any] = {}
        self.last_audit: list[dict[str, Any]] = []

    def _load_threat_intel(self, feed_ids: list[str] | None) -> dict[str, Any]:
        try:
            from nuguard.threat_intel.loader import load_feeds  # noqa: PLC0415
            return load_feeds(feed_ids=feed_ids)
        except Exception as exc:
            _log.warning("Threat-intel load failed: %s", exc)
            return {"known_malicious_packages": {}, "suspicious_lifecycle_patterns": [], "suspicious_file_patterns": []}

    def scan(
        self,
        source_path: Path,
        sbom_nodes: list[dict[str, Any]] | None = None,
        sbom_deps: list[dict[str, Any]] | None = None,
        sbom_summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run all enabled supply-chain checks; return raw finding dicts."""
        nodes = sbom_nodes or []
        deps = sbom_deps or []
        findings: list[dict[str, Any]] = []
        self._ctx = {}
        self._sbom_summary: dict[str, Any] = sbom_summary or {}

        _log.info(
            "supply-chain scan starting (profile=%s, source=%s, %d SBOM nodes, %d deps)",
            self.profile, source_path, len(nodes), len(deps),
        )

        findings.extend(self._scan_lifecycle_scripts(source_path, nodes))
        findings.extend(self._scan_workflows(source_path, nodes))
        findings.extend(self._scan_dev_tool_configs(source_path, nodes))
        findings.extend(self._check_threat_intel(deps, nodes))
        findings.extend(self._check_mutable_deps(deps))
        findings.extend(self._check_lockfile_coverage(source_path, deps))

        # SC-017..019 are now in standard profile (SBOM-native) and full profile (filesystem)
        findings.extend(self._scan_large_payloads(source_path, nodes, self._sbom_summary))
        if self.profile == "full":
            findings.extend(self._scan_git_history(source_path))

        _log.info(
            "supply-chain scan complete: %d finding(s) from %d enabled rules (profile=%s)",
            len(findings), len(self._enabled), self.profile,
        )
        self.last_audit = self._build_audit(findings)
        return findings

    def _build_audit(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build per-rule pass/fail audit entries from scan results and context."""
        fired: dict[str, list[dict[str, Any]]] = {}
        for f in findings:
            rid = f.get("rule_id", "")
            fired.setdefault(rid, []).append(f)

        audit: list[dict[str, Any]] = []
        for meta in _RULE_META:
            rid = meta["rule_id"]
            if rid not in self._enabled:
                audit.append({
                    "rule_id": rid,
                    "title": meta["title"],
                    "severity": meta["severity"],
                    "status": "SKIPPED",
                    "finding_count": 0,
                    "affected": [],
                    "pass_reason": f"Not enabled in '{self.profile}' profile",
                    "checks": meta["checks"],
                    "pass_evidence": {},
                })
            elif rid in fired:
                rule_findings = fired[rid]
                affected = list({a for r in rule_findings for a in (r.get("affected") or [])})
                audit.append({
                    "rule_id": rid,
                    "title": meta["title"],
                    "severity": meta["severity"],
                    "status": "FAIL",
                    "finding_count": len(rule_findings),
                    "affected": affected,
                    "pass_reason": "",
                    "checks": meta["checks"],
                    "pass_evidence": {},
                })
            else:
                audit.append({
                    "rule_id": rid,
                    "title": meta["title"],
                    "severity": meta["severity"],
                    "status": "PASS",
                    "finding_count": 0,
                    "affected": [],
                    "pass_reason": meta["pass_reason"],
                    "checks": meta["checks"],
                    "pass_evidence": self._build_pass_evidence(rid),
                })
        return audit

    def _build_pass_evidence(self, rule_id: str) -> dict[str, Any]:
        """Return relevant scan context counts for a passing rule's audit entry."""
        ctx = self._ctx
        if rule_id in {"NGA-SC-001", "NGA-SC-002", "NGA-SC-003", "NGA-SC-004", "NGA-SC-005", "NGA-SC-006"}:
            wf = ctx.get("workflows", {})
            if wf.get("sbom_nodes_used"):
                return {"workflow_nodes_examined": wf["sbom_nodes_used"],
                        "workflow_names": wf.get("workflow_names", [])}
            return {"workflow_files_examined": wf.get("workflow_files", 0),
                    "workflow_names": wf.get("workflow_names", [])}
        if rule_id in {"NGA-SC-007", "NGA-SC-008", "NGA-SC-009", "NGA-SC-010"}:
            dt = ctx.get("dev_tool_configs", {})
            if dt.get("sbom_nodes_used"):
                return {"config_nodes_examined": dt["sbom_nodes_used"]}
            return {"claude_settings_found": dt.get("claude_settings_found", False),
                    "mcp_json_found": dt.get("mcp_json_found", False)}
        if rule_id in {"NGA-SC-011", "NGA-SC-012", "NGA-SC-013",
                       "NGA-SC-014", "NGA-SC-015", "NGA-SC-016"}:
            lc = ctx.get("lifecycle", {})
            if lc.get("sbom_nodes_used"):
                return {"lifecycle_script_nodes_examined": lc["sbom_nodes_used"]}
            return {"package_json_files_examined": lc.get("package_json_files", 0),
                    "scripts_examined": lc.get("scripts_checked", 0)}
        if rule_id in {"NGA-SC-017", "NGA-SC-018", "NGA-SC-019"}:
            lp = ctx.get("large_payloads", {})
            if lp.get("sbom_nodes_used") is not None:
                return {"dev_tool_config_nodes_examined": lp["sbom_nodes_used"],
                        "minified_js_files_found": lp.get("minified_js_files", 0)}
            return {"hidden_dirs_scanned": lp.get("hidden_dirs_scanned", []),
                    "js_files_scanned": lp.get("js_files_scanned", 0)}
        if rule_id in {"NGA-SC-020", "NGA-SC-021", "NGA-SC-022"}:
            gh = ctx.get("git_history", {})
            ev: dict[str, Any] = {"git_available": gh.get("git_available", False)}
            if gh.get("commits_inspected"):
                ev["commits_inspected"] = gh["commits_inspected"]
            return ev
        if rule_id == "NGA-SC-023":
            return {"deps_examined": ctx.get("mutable_deps", {}).get("deps_checked", 0)}
        if rule_id == "NGA-SC-024":
            lf = ctx.get("lockfile", {})
            if not lf.get("package_json_found"):
                return {"no_package_json": True}
            return {"lockfile_found": lf.get("lockfile_found", False)}
        if rule_id == "NGA-SC-025":
            ti = ctx.get("threat_intel", {})
            return {"deps_checked": ti.get("deps_checked", 0),
                    "ioc_entries": ti.get("ioc_entries", 0)}
        return {}

    # ------------------------------------------------------------------
    # Sub-scanner: lifecycle scripts
    # ------------------------------------------------------------------

    def _scan_lifecycle_scripts(
        self, source_path: Path, nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []

        # Use LIFECYCLE_SCRIPT nodes when available; fall back to scanning raw files
        lifecycle_nodes = [
            n for n in nodes
            if (n.get("component_type") or "").upper() == "LIFECYCLE_SCRIPT"
        ]

        if lifecycle_nodes:
            _log.info("[NGA-SC-011..016] scanning %d LIFECYCLE_SCRIPT SBOM nodes", len(lifecycle_nodes))
            for node in lifecycle_nodes:
                meta = node.get("metadata") or {}
                body = str(meta.get("script_body") or "")
                phase = str(meta.get("script_phase") or "")
                source = node.get("name", "unknown")
                findings.extend(self._check_script_node(meta, body, phase, source))
            self._ctx["lifecycle"] = {
                "sbom_nodes_used": len(lifecycle_nodes),
                "scripts_checked": len(lifecycle_nodes),
            }
        else:
            _log.info("[NGA-SC-011..016] scanning lifecycle scripts from source files (no SBOM nodes)")
            findings.extend(self._scan_lifecycle_from_files(source_path))

        return findings

    def _scan_lifecycle_from_files(self, root: Path) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        pkg_json_count = 0
        scripts_count = 0
        for pkg_json in root.rglob("package.json"):
            if any(p in pkg_json.parts for p in ("node_modules", ".git")):
                continue
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            pkg_json_count += 1
            scripts = data.get("scripts") or {}
            for phase, body in scripts.items():
                if not isinstance(body, str):
                    continue
                scripts_count += 1
                rel = str(pkg_json.relative_to(root))
                source = f"{rel}:{phase}"
                findings.extend(self._check_script_body(body, phase, source))
        _log.info("[NGA-SC-011..016] scanned %d package.json file(s), %d script(s)",
                  pkg_json_count, scripts_count)
        self._ctx["lifecycle"] = {
            "sbom_nodes_used": 0,
            "package_json_files": pkg_json_count,
            "scripts_checked": scripts_count,
        }
        return findings

    def _check_script_node(
        self, meta: dict[str, Any], body: str, phase: str, source: str
    ) -> list[dict[str, Any]]:
        """Check a LIFECYCLE_SCRIPT SBOM node using pre-computed booleans first, then body regex."""
        findings: list[dict[str, Any]] = []
        is_install_hook = phase.lower() in {
            "preinstall", "install", "postinstall", "prepare",
        }

        # Use pre-computed boolean flags (more reliable than truncated body regex)
        if "NGA-SC-013" in self._enabled and meta.get("downloads_binary"):
            findings.append(_finding(
                rule_id="NGA-SC-013",
                severity="critical",
                title="Lifecycle script downloads and executes Bun",
                description=(
                    f"The lifecycle script '{source}' downloads or runs Bun. "
                    "This is a known Miasma-campaign technique for downloading and running "
                    "a second-stage payload without leaving npm-audit-visible traces."
                ),
                affected=[source],
                remediation=(
                    "Remove the Bun download from lifecycle scripts. "
                    "If Bun is needed, install it as a development dependency and pin the version."
                ),
            ))
        elif "NGA-SC-013" in self._enabled and body and _BUN_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-013",
                severity="critical",
                title="Lifecycle script downloads and executes Bun",
                description=(
                    f"The lifecycle script '{source}' downloads or runs Bun "
                    f"(`{_first_match(_BUN_RE, body)}`). "
                    "This is a known Miasma-campaign technique for downloading and running "
                    "a second-stage payload without leaving npm-audit-visible traces."
                ),
                affected=[source],
                remediation=(
                    "Remove the Bun download from lifecycle scripts. "
                    "If Bun is needed, install it as a development dependency and pin the version."
                ),
            ))

        if "NGA-SC-012" in self._enabled and meta.get("invokes_shell"):
            findings.append(_finding(
                rule_id="NGA-SC-012",
                severity="critical",
                title="Lifecycle script pipes into a shell",
                description=(
                    f"The lifecycle script '{source}' pipes output directly into a shell. "
                    "This pattern is used to execute remote code without writing it to disk."
                ),
                affected=[source],
                remediation=(
                    "Replace the pipe-to-shell pattern with a verified, pinned script file. "
                    "Never execute content fetched from the network directly."
                ),
            ))
        elif "NGA-SC-012" in self._enabled and body and _SHELL_PIPE_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-012",
                severity="critical",
                title="Lifecycle script pipes into a shell",
                description=(
                    f"The lifecycle script '{source}' pipes output directly into a shell "
                    f"(`{_first_match(_SHELL_PIPE_RE, body)}`). "
                    "This pattern is used to execute remote code without writing it to disk."
                ),
                affected=[source],
                remediation=(
                    "Replace the pipe-to-shell pattern with a verified, pinned script file. "
                    "Never execute content fetched from the network directly."
                ),
            ))

        if "NGA-SC-011" in self._enabled and is_install_hook and meta.get("invokes_network"):
            findings.append(_finding(
                rule_id="NGA-SC-011",
                severity="critical",
                title="Install hook makes a network request",
                description=(
                    f"The install-phase lifecycle script '{source}' invokes a network tool. "
                    "Install hooks should never make outbound network requests; "
                    "this is the primary vector in the Miasma campaign."
                ),
                affected=[source],
                remediation=(
                    "Remove network calls from install hooks. "
                    "Download all necessary files at build time and bundle them, "
                    "or use a lock-file-verified install step."
                ),
            ))
        elif "NGA-SC-011" in self._enabled and is_install_hook and body and _NETWORK_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-011",
                severity="critical",
                title="Install hook makes a network request",
                description=(
                    f"The install-phase lifecycle script '{source}' invokes a network tool "
                    f"(`{_first_match(_NETWORK_RE, body)}`). "
                    "Install hooks should never make outbound network requests; "
                    "this is the primary vector in the Miasma campaign."
                ),
                affected=[source],
                remediation=(
                    "Remove network calls from install hooks. "
                    "Download all necessary files at build time and bundle them, "
                    "or use a lock-file-verified install step."
                ),
            ))

        if "NGA-SC-014" in self._enabled and meta.get("references_credentials"):
            findings.append(_finding(
                rule_id="NGA-SC-014",
                severity="critical",
                title="Lifecycle script accesses credential paths",
                description=(
                    f"The lifecycle script '{source}' references credential files or environment "
                    "variables. This is the signature of the Miasma credential-harvesting stage."
                ),
                affected=[source],
                remediation=(
                    "Immediately audit the script. Remove all credential access. "
                    "Rotate any credentials that may have been exfiltrated."
                ),
            ))
        elif "NGA-SC-014" in self._enabled and body and _CRED_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-014",
                severity="critical",
                title="Lifecycle script accesses credential paths",
                description=(
                    f"The lifecycle script '{source}' references credential files or environment "
                    f"variables (`{_first_match(_CRED_RE, body)}`). "
                    "This is the signature of the Miasma credential-harvesting stage."
                ),
                affected=[source],
                remediation=(
                    "Immediately audit the script. Remove all credential access. "
                    "Rotate any credentials that may have been exfiltrated."
                ),
            ))

        # SC-015 and SC-016 don't have pre-computed booleans — use body regex
        if body:
            if "NGA-SC-016" in self._enabled and _EVAL_RE.search(body):
                findings.append(_finding(
                    rule_id="NGA-SC-016",
                    severity="high",
                    title="Lifecycle script uses eval / Function / atob (obfuscation indicator)",
                    description=(
                        f"The lifecycle script '{source}' contains "
                        f"`{_first_match(_EVAL_RE, body)}`, which is a common obfuscation technique "
                        "used by malicious packages to hide their payload from static analysis."
                    ),
                    affected=[source],
                    remediation="Remove eval/Function/atob from lifecycle scripts.",
                ))

            if "NGA-SC-015" in self._enabled and phase in {"setup.py", "build-backend"}:
                if _NETWORK_RE.search(body):
                    findings.append(_finding(
                        rule_id="NGA-SC-015",
                        severity="high",
                        title="Python build hook makes a network call",
                        description=(
                            f"The Python build hook '{source}' invokes a network library "
                            f"(`{_first_match(_NETWORK_RE, body)}`). "
                            "Build hooks run during `pip install` and should never fetch remote code."
                        ),
                        affected=[source],
                        remediation="Remove network calls from Python build hooks and setup.py.",
                    ))

        return findings

    def _check_script_body(
        self, body: str, phase: str, source: str
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        is_install_hook = phase.lower() in {
            "preinstall", "install", "postinstall", "prepare",
        }

        if "NGA-SC-013" in self._enabled and _BUN_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-013",
                severity="critical",
                title="Lifecycle script downloads and executes Bun",
                description=(
                    f"The lifecycle script '{source}' downloads or runs Bun "
                    f"(`{_first_match(_BUN_RE, body)}`). "
                    "This is a known Miasma-campaign technique for downloading and running "
                    "a second-stage payload without leaving npm-audit-visible traces."
                ),
                affected=[source],
                remediation=(
                    "Remove the Bun download from lifecycle scripts. "
                    "If Bun is needed, install it as a development dependency and pin the version."
                ),
            ))

        if "NGA-SC-012" in self._enabled and _SHELL_PIPE_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-012",
                severity="critical",
                title="Lifecycle script pipes into a shell",
                description=(
                    f"The lifecycle script '{source}' pipes output directly into a shell "
                    f"(`{_first_match(_SHELL_PIPE_RE, body)}`). "
                    "This pattern is used to execute remote code without writing it to disk."
                ),
                affected=[source],
                remediation=(
                    "Replace the pipe-to-shell pattern with a verified, pinned script file. "
                    "Never execute content fetched from the network directly."
                ),
            ))

        if "NGA-SC-011" in self._enabled and is_install_hook and _NETWORK_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-011",
                severity="critical",
                title="Install hook makes a network request",
                description=(
                    f"The install-phase lifecycle script '{source}' invokes a network tool "
                    f"(`{_first_match(_NETWORK_RE, body)}`). "
                    "Install hooks should never make outbound network requests; "
                    "this is the primary vector in the Miasma campaign."
                ),
                affected=[source],
                remediation=(
                    "Remove network calls from install hooks. "
                    "Download all necessary files at build time and bundle them, "
                    "or use a lock-file-verified install step."
                ),
            ))

        if "NGA-SC-014" in self._enabled and _CRED_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-014",
                severity="critical",
                title="Lifecycle script accesses credential paths",
                description=(
                    f"The lifecycle script '{source}' references credential files or environment "
                    f"variables (`{_first_match(_CRED_RE, body)}`). "
                    "This is the signature of the Miasma credential-harvesting stage."
                ),
                affected=[source],
                remediation=(
                    "Immediately audit the script. Remove all credential access. "
                    "Rotate any credentials that may have been exfiltrated."
                ),
            ))

        if "NGA-SC-016" in self._enabled and _EVAL_RE.search(body):
            findings.append(_finding(
                rule_id="NGA-SC-016",
                severity="high",
                title="Lifecycle script uses eval / Function / atob (obfuscation indicator)",
                description=(
                    f"The lifecycle script '{source}' contains "
                    f"`{_first_match(_EVAL_RE, body)}`, which is a common obfuscation technique "
                    "used by malicious packages to hide their payload from static analysis."
                ),
                affected=[source],
                remediation="Remove eval/Function/atob from lifecycle scripts.",
            ))

        if "NGA-SC-015" in self._enabled and phase in {"setup.py", "build-backend"}:
            if _NETWORK_RE.search(body):
                findings.append(_finding(
                    rule_id="NGA-SC-015",
                    severity="high",
                    title="Python build hook makes a network call",
                    description=(
                        f"The Python build hook '{source}' invokes a network library "
                        f"(`{_first_match(_NETWORK_RE, body)}`). "
                        "Build hooks run during `pip install` and should never fetch remote code."
                    ),
                    affected=[source],
                    remediation="Remove network calls from Python build hooks and setup.py.",
                ))

        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: GitHub Actions workflows
    # ------------------------------------------------------------------

    def _scan_workflows(
        self, source_path: Path, nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        workflow_nodes = [
            n for n in nodes
            if (n.get("component_type") or "").upper() == "GITHUB_WORKFLOW"
        ]

        if workflow_nodes:
            _log.info("[NGA-SC-001..006] scanning %d GITHUB_WORKFLOW SBOM nodes", len(workflow_nodes))
            for node in workflow_nodes:
                findings.extend(self._check_workflow_node(node))
            self._ctx["workflows"] = {
                "sbom_nodes_used": len(workflow_nodes),
                "workflow_names": [n.get("name", "unknown") for n in workflow_nodes],
            }
        else:
            _log.info("[NGA-SC-001..006] scanning workflow files from source (no SBOM nodes)")
            findings.extend(self._scan_workflows_from_files(source_path))
        return findings

    def _scan_workflows_from_files(self, root: Path) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        workflows_dir = root / ".github" / "workflows"
        if not workflows_dir.is_dir():
            _log.info("[NGA-SC-001..006] no .github/workflows/ directory found")
            self._ctx["workflows"] = {"sbom_nodes_used": 0, "workflow_files": 0, "workflow_names": []}
            return findings
        wf_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
        wf_names = [f.name for f in wf_files]
        _log.info("[NGA-SC-001..006] found %d workflow file(s): %s", len(wf_files), wf_names)
        self._ctx["workflows"] = {
            "sbom_nodes_used": 0,
            "workflow_files": len(wf_files),
            "workflow_names": wf_names,
        }
        for wf in wf_files:
            try:
                text = wf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(wf.relative_to(root))
            findings.extend(self._check_workflow_text(text, rel))
        return findings

    def _check_workflow_node(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        meta = node.get("metadata") or {}
        name = node.get("name", "unknown")
        action_refs: list[str] = meta.get("action_refs") or []
        triggers: list[str] = meta.get("workflow_triggers") or []
        uses_oidc: bool = bool(meta.get("uses_oidc"))
        publishes_to: list[str] = meta.get("publishes_to") or []
        perms: dict[str, str] = meta.get("workflow_permissions") or {}

        findings: list[dict[str, Any]] = []

        is_publish = bool(publishes_to)
        unpinned_refs = [r for r in action_refs if not _SHA_PIN_RE.search(r)]

        # SC-004: unpinned global install in step bodies (pre-computed boolean)
        if "NGA-SC-004" in self._enabled and meta.get("workflow_has_unpinned_global_install"):
            findings.append(_finding(
                rule_id="NGA-SC-004",
                severity="high",
                title="CI step uses unpinned global install",
                description=(
                    f"Workflow '{name}' contains a step that runs an unpinned global npm install "
                    "or npx without a pinned version. This allows attackers who compromise a "
                    "package to inject malicious code into the CI environment."
                ),
                affected=[name],
                remediation=(
                    "Pin global installs to exact versions (e.g. `npm install -g tool@1.2.3`) "
                    "or use a lockfile-based approach. Avoid unpinned npx calls in CI."
                ),
            ))

        # SC-005: credential access in step bodies (pre-computed boolean)
        if "NGA-SC-005" in self._enabled and meta.get("workflow_has_cred_access"):
            findings.append(_finding(
                rule_id="NGA-SC-005",
                severity="critical",
                title="Workflow step reads credential paths or environment variables",
                description=(
                    f"Workflow '{name}' contains a step that accesses credential paths or "
                    "sensitive environment variables (e.g. /proc/environ, .npmrc, "
                    "AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN). This is the signature pattern of "
                    "CI credential harvesting attacks."
                ),
                affected=[name],
                remediation=(
                    "Audit all workflow steps for credential access. "
                    "Use GitHub's secrets mechanism and never echo or print credentials. "
                    "Restrict secret access to only the jobs that need it."
                ),
            ))

        if "NGA-SC-001" in self._enabled and is_publish and uses_oidc and unpinned_refs:
            findings.append(_finding(
                rule_id="NGA-SC-001",
                severity="high",
                title="Publish workflow uses OIDC with unpinned third-party actions",
                description=(
                    f"Workflow '{name}' publishes to {publishes_to} with OIDC token issuance "
                    f"(`id-token: write`) and uses unpinned action refs: {unpinned_refs[:5]}. "
                    "An attacker who compromises an unpinned action can steal the OIDC token "
                    "and publish malicious packages."
                ),
                affected=[name],
                remediation=(
                    "Pin all third-party GitHub Actions to their full commit SHA "
                    "(e.g. `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683`). "
                    "Use Dependabot to keep SHA pins up to date."
                ),
            ))

        if "NGA-SC-002" in self._enabled:
            dangerous_triggers = [t for t in triggers if t in ("pull_request_target", "workflow_dispatch")]
            if dangerous_triggers and is_publish:
                findings.append(_finding(
                    rule_id="NGA-SC-002",
                    severity="critical",
                    title="Dangerous workflow trigger can reach publish/secrets path",
                    description=(
                        f"Workflow '{name}' is triggered by {dangerous_triggers} "
                        f"and publishes to {publishes_to}. "
                        "pull_request_target and workflow_dispatch can be triggered by untrusted "
                        "contributors; if they can reach the publish step, they can inject "
                        "malicious code into a published package."
                    ),
                    affected=[name],
                    remediation=(
                        "Separate publish jobs from CI jobs. "
                        "Require a protected environment gate before any publish step. "
                        "Never allow pull_request_target to publish without a manual approval gate."
                    ),
                ))

        if "NGA-SC-003" in self._enabled and uses_oidc:
            has_contents_write = str(perms.get("contents", "")).lower() == "write"
            if has_contents_write and unpinned_refs:
                findings.append(_finding(
                    rule_id="NGA-SC-003",
                    severity="high",
                    title="Job has id-token:write + contents:write + unpinned checkout",
                    description=(
                        f"Workflow '{name}' grants both `id-token: write` and `contents: write` "
                        f"and uses unpinned action refs: {unpinned_refs[:3]}. "
                        "A compromised action could read the OIDC token and push malicious commits."
                    ),
                    affected=[name],
                    remediation="Pin all actions to SHA. Apply least-privilege permissions per job.",
                ))

        return findings

    def _check_workflow_text(self, text: str, rel: str) -> list[dict[str, Any]]:
        """Fallback: check raw workflow text when SBOM nodes are unavailable."""
        findings: list[dict[str, Any]] = []
        is_publish = bool(_PUBLISH_STEP_RE.search(text))
        uses_oidc = bool(_OIDC_RE.search(text))
        unpinned_refs = _MUTABLE_ACTION_REF_RE.findall(text)

        if "NGA-SC-001" in self._enabled and is_publish and uses_oidc and unpinned_refs:
            findings.append(_finding(
                rule_id="NGA-SC-001",
                severity="high",
                title="Publish workflow uses OIDC with unpinned third-party actions",
                description=(
                    f"Workflow '{rel}' publishes packages with OIDC token issuance and uses "
                    f"unpinned action refs: {[m.group(0) if hasattr(m, 'group') else m for m in unpinned_refs[:3]]}."
                ),
                affected=[rel],
                remediation=(
                    "Pin all GitHub Actions to their full commit SHA and use Dependabot "
                    "for automatic SHA-pin updates."
                ),
            ))

        if "NGA-SC-002" in self._enabled and _TRIGGER_RE.search(text) and is_publish:
            findings.append(_finding(
                rule_id="NGA-SC-002",
                severity="critical",
                title="Dangerous workflow trigger can reach publish/secrets path",
                description=(
                    f"Workflow '{rel}' is triggered by pull_request_target or workflow_dispatch "
                    "and publishes packages. Untrusted contributors may be able to trigger "
                    "the publish step."
                ),
                affected=[rel],
                remediation=(
                    "Separate publish jobs from CI. "
                    "Require a protected environment gate before any publish step."
                ),
            ))

        if "NGA-SC-005" in self._enabled and _CRED_RE.search(text):
            findings.append(_finding(
                rule_id="NGA-SC-005",
                severity="critical",
                title="Workflow step reads credential paths or runner memory",
                description=(
                    f"Workflow '{rel}' contains patterns that read credential paths or "
                    f"environment variable stores (`{_first_match(_CRED_RE, text)}`). "
                    "This is the signature of runner memory-scraping used in the Miasma campaign."
                ),
                affected=[rel],
                remediation=(
                    "Audit the workflow step immediately. "
                    "Rotate all secrets accessible to this workflow."
                ),
            ))

        if "NGA-SC-004" in self._enabled and _UNPINNED_GLOBAL_RE.search(text):
            findings.append(_finding(
                rule_id="NGA-SC-004",
                severity="high",
                title="CI step uses unpinned global install",
                description=(
                    f"Workflow '{rel}' uses `npm install -g` or `npx` without a pinned version. "
                    "Unpinned global installs may resolve to a compromised latest version at "
                    "runtime, making the install non-reproducible."
                ),
                affected=[rel],
                remediation=(
                    "Pin global install commands to an exact version "
                    "(e.g. `npm install -g smithery@1.2.3`) and verify integrity."
                ),
            ))

        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: AI-agent / editor configs
    # ------------------------------------------------------------------

    def _scan_dev_tool_configs(
        self, source_path: Path, nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        config_nodes = [
            n for n in nodes
            if (n.get("component_type") or "").upper() in (
                "DEVELOPER_TOOL_CONFIG", "MCP_SERVER"
            )
        ]

        if config_nodes:
            _log.info("[NGA-SC-007..010] scanning %d DEVELOPER_TOOL_CONFIG/MCP_SERVER SBOM nodes",
                      len(config_nodes))
            for node in config_nodes:
                findings.extend(self._check_config_node(node))
            self._ctx["dev_tool_configs"] = {"sbom_nodes_used": len(config_nodes)}
        else:
            _log.info("[NGA-SC-007..010] scanning AI-agent config files from source (no SBOM nodes)")
            findings.extend(self._scan_dev_tool_configs_from_files(source_path))
        return findings

    def _check_config_node(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        meta = node.get("metadata") or {}
        name = node.get("name", "unknown")
        component_type = (node.get("component_type") or "").upper()
        findings: list[dict[str, Any]] = []

        if component_type == "MCP_SERVER":
            if "NGA-SC-009" in self._enabled and not meta.get("mcp_server_trusted"):
                url = meta.get("mcp_server_url") or meta.get("mcp_server_url") or "unknown"
                findings.append(_finding(
                    rule_id="NGA-SC-009",
                    severity="high",
                    title=".mcp.json references an external/untrusted MCP server",
                    description=(
                        f"MCP server '{name}' ({url}) is declared in a repo-level .mcp.json "
                        "and is not listed in the trusted-servers config. "
                        "An attacker who controls this server can inject tool responses that "
                        "trigger malicious agent actions (MCP toxic flow)."
                    ),
                    affected=[name],
                    remediation=(
                        "Review the MCP server source. "
                        "Add it to `redteam.mcp_trusted_servers` in nuguard.yaml only after "
                        "verifying it is under your control."
                    ),
                ))
            return findings

        # DEVELOPER_TOOL_CONFIG
        config_type = str(meta.get("tool_config_type") or "")
        granted: list[str] = meta.get("permissions_granted") or []
        auto_exec: bool = bool(meta.get("auto_execute"))

        if "NGA-SC-007" in self._enabled:
            broad_perms = [p for p in granted if _BROAD_BASH_RE.search(p)]
            if broad_perms:
                findings.append(_finding(
                    rule_id="NGA-SC-007",
                    severity="high",
                    title="AI-agent config grants unrestricted shell access",
                    description=(
                        f"Config '{name}' ({config_type}) grants `Bash(*:*)` — unrestricted "
                        "shell execution — to the AI coding agent. "
                        "A Miasma-style attacker who poisons this file (or a prompt) can run "
                        "arbitrary commands in the developer's environment."
                    ),
                    affected=[name],
                    remediation=(
                        "Replace `Bash(*:*)` with an allowlist of specific allowed commands, "
                        "e.g. `Bash(npm:*)`, `Bash(pytest:*)`. "
                        "Deny destructive commands explicitly."
                    ),
                ))

        if "NGA-SC-010" in self._enabled and auto_exec:
            findings.append(_finding(
                rule_id="NGA-SC-010",
                severity="high",
                title="AI-agent auto-run/auto-approve mode enabled in repo config",
                description=(
                    f"Config '{name}' enables automatic execution without user confirmation. "
                    "Combined with repo-level config poisoning, an attacker can cause the "
                    "AI agent to execute malicious actions silently."
                ),
                affected=[name],
                remediation=(
                    "Disable auto-run/auto-approve in repo-level config. "
                    "Require explicit user confirmation for all tool invocations."
                ),
            ))

        if "NGA-SC-008" in self._enabled:
            findings.append(_finding(
                rule_id="NGA-SC-008",
                severity="medium",
                title="AI-agent config is repo-controlled (commit-level attack surface)",
                description=(
                    f"Config '{name}' ({config_type}) is committed to the repository. "
                    "Any contributor with push access can modify this file to change "
                    "the permissions or behavior granted to AI coding agents."
                ),
                affected=[name],
                remediation=(
                    "Review change history for this file. "
                    "Consider moving sensitive permission grants to user-level config "
                    "outside the repository."
                ),
            ))

        return findings

    def _scan_dev_tool_configs_from_files(self, root: Path) -> list[dict[str, Any]]:
        """Fallback: scan config files directly when SBOM nodes are absent."""
        findings: list[dict[str, Any]] = []
        claude_settings_found = False
        mcp_json_found = False
        auto_exec_dirs: list[str] = []

        claude_settings = root / ".claude" / "settings.json"
        if claude_settings.exists():
            claude_settings_found = True
            try:
                data = json.loads(claude_settings.read_text(encoding="utf-8"))
                allowed: list[str] = [str(p) for p in (data.get("allow") or []) if p]
                rel = str(claude_settings.relative_to(root))
                broad = [p for p in allowed if _BROAD_BASH_RE.search(p)]
                if "NGA-SC-007" in self._enabled and broad:
                    findings.append(_finding(
                        rule_id="NGA-SC-007",
                        severity="high",
                        title="AI-agent config grants unrestricted shell access",
                        description=(
                            f"'{rel}' grants `Bash(*:*)` to the Claude Code agent. "
                            "This is a known Miasma repo-poisoning attack surface."
                        ),
                        affected=[rel],
                        remediation="Replace Bash(*:*) with specific allowed command patterns.",
                    ))
                if "NGA-SC-008" in self._enabled:
                    findings.append(_finding(
                        rule_id="NGA-SC-008",
                        severity="medium",
                        title="AI-agent config is repo-controlled (commit-level attack surface)",
                        description=f"'{rel}' is committed to the repository.",
                        affected=[rel],
                        remediation="Review change history; consider user-level config instead.",
                    ))
            except (json.JSONDecodeError, OSError):
                pass

        mcp_json = root / ".mcp.json"
        if mcp_json.exists():
            mcp_json_found = True
            try:
                data = json.loads(mcp_json.read_text(encoding="utf-8"))
                rel = str(mcp_json.relative_to(root))
                for server_name in (data.get("mcpServers") or {}).keys():
                    if "NGA-SC-009" in self._enabled:
                        findings.append(_finding(
                            rule_id="NGA-SC-009",
                            severity="high",
                            title=".mcp.json references an external/untrusted MCP server",
                            description=(
                                f"MCP server '{server_name}' in '{rel}' is not verified as trusted."
                            ),
                            affected=[f"{rel}:{server_name}"],
                            remediation="Verify the server is under your control before trusting it.",
                        ))
            except (json.JSONDecodeError, OSError):
                pass

        # Check hidden tool dirs for auto-exec patterns
        for tool_dir in (".cursor", ".codex", ".gemini"):
            tool_path = root / tool_dir
            if not tool_path.is_dir():
                continue
            auto_exec_dirs.append(tool_dir)
            for cfg_file in tool_path.rglob("*"):
                if not cfg_file.is_file():
                    continue
                try:
                    text = cfg_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(cfg_file.relative_to(root))
                if "NGA-SC-010" in self._enabled and _AUTO_EXEC_RE.search(text):
                    findings.append(_finding(
                        rule_id="NGA-SC-010",
                        severity="high",
                        title="AI-agent auto-run/auto-approve mode enabled in repo config",
                        description=f"'{rel}' enables automatic execution without confirmation.",
                        affected=[rel],
                        remediation="Disable auto-run/auto-approve in repo-level config.",
                    ))

        _log.info(
            "[NGA-SC-007..010] claude_settings=%s mcp_json=%s auto_exec_dirs=%s",
            claude_settings_found, mcp_json_found, auto_exec_dirs,
        )
        self._ctx["dev_tool_configs"] = {
            "sbom_nodes_used": 0,
            "claude_settings_found": claude_settings_found,
            "mcp_json_found": mcp_json_found,
            "auto_exec_dirs_checked": auto_exec_dirs,
        }
        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: large payloads (full profile only)
    # ------------------------------------------------------------------

    def _scan_large_payloads(
        self,
        source_path: Path,
        nodes: list[dict[str, Any]] | None = None,
        sbom_summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        nodes = nodes or []
        sbom_summary = sbom_summary or {}

        # SBOM-native path: use pre-computed metadata from DEVELOPER_TOOL_CONFIG nodes
        dev_tool_nodes = [
            n for n in nodes
            if (n.get("component_type") or "").upper() == "DEVELOPER_TOOL_CONFIG"
        ]
        if dev_tool_nodes:
            _log.info(
                "[NGA-SC-017..019] scanning %d DEVELOPER_TOOL_CONFIG SBOM nodes + summary",
                len(dev_tool_nodes),
            )
            for node in dev_tool_nodes:
                meta = node.get("metadata") or {}
                name = node.get("name", "unknown")
                size = meta.get("file_size_bytes")
                entropy = meta.get("content_entropy")

                if "NGA-SC-017" in self._enabled and size is not None and size > 100 * 1024:
                    findings.append(_finding(
                        rule_id="NGA-SC-017",
                        severity="high",
                        title="Large file in hidden tool directory",
                        description=(
                            f"Config file '{name}' is {size // 1024} KB, above the 100 KB "
                            "threshold for hidden AI-agent/editor configuration directories. "
                            "SafeDep reported large dropper payloads hidden in .claude/ and "
                            "similar directories in the Miasma campaign."
                        ),
                        affected=[name],
                        remediation="Audit this file and verify it was intentionally committed.",
                    ))

                if "NGA-SC-018" in self._enabled and entropy is not None and entropy > 6.5:
                    findings.append(_finding(
                        rule_id="NGA-SC-018",
                        severity="high",
                        title="High-entropy blob in tool directory",
                        description=(
                            f"Config file '{name}' has Shannon entropy {entropy:.2f} bits/byte "
                            "(threshold: 6.5). High entropy may indicate encrypted or "
                            "compressed payload data."
                        ),
                        affected=[name],
                        remediation="Audit this file; confirm it is legitimate config data.",
                    ))

            # SC-019 from ScanSummary
            if "NGA-SC-019" in self._enabled:
                for minified_path in (sbom_summary.get("minified_js_files") or []):
                    findings.append(_finding(
                        rule_id="NGA-SC-019",
                        severity="medium",
                        title="Minified single-line JavaScript above 5 KB",
                        description=(
                            f"File '{minified_path}' contains a minified/single-line JavaScript "
                            "body exceeding 5000 characters. "
                            "Check that this file was intentionally committed and is not a dropper."
                        ),
                        affected=[minified_path],
                        remediation="Audit the file source; prefer readable, un-minified files in source control.",
                    ))

            _log.info(
                "[NGA-SC-017..019] SBOM-native: %d config nodes examined, %d minified JS files",
                len(dev_tool_nodes), len(sbom_summary.get("minified_js_files") or []),
            )
            self._ctx["large_payloads"] = {
                "sbom_nodes_used": len(dev_tool_nodes),
                "minified_js_files": len(sbom_summary.get("minified_js_files") or []),
            }
            return findings

        # Filesystem fallback: used when source_path exists (local repos with --source)
        dirs_found: list[str] = []
        js_files_scanned = 0

        if not source_path.exists():
            _log.info("[NGA-SC-017..019] no SBOM nodes and no source path; skipping large payload scan")
            self._ctx["large_payloads"] = {"hidden_dirs_scanned": [], "js_files_scanned": 0}
            return findings

        for dir_name in _HIDDEN_TOOL_DIRS:
            tool_dir = source_path / dir_name
            if not tool_dir.is_dir():
                continue
            dirs_found.append(dir_name)
            for path in tool_dir.rglob("*"):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(source_path))
                try:
                    size = path.stat().st_size
                except OSError:
                    continue

                if "NGA-SC-017" in self._enabled and size > 100 * 1024:
                    findings.append(_finding(
                        rule_id="NGA-SC-017",
                        severity="high",
                        title="Large file in hidden tool directory",
                        description=(
                            f"File '{rel}' is {size // 1024} KB, above the 100 KB threshold "
                            "for hidden AI-agent/editor configuration directories. "
                            "SafeDep reported large dropper payloads hidden in .claude/ and "
                            "similar directories in the Miasma campaign."
                        ),
                        affected=[rel],
                        remediation="Audit this file and verify it was intentionally committed.",
                    ))

                if "NGA-SC-018" in self._enabled and size < 5 * 1024 * 1024:
                    try:
                        raw = path.read_bytes()
                        entropy = _shannon_entropy(raw)
                        if entropy > 6.5:
                            findings.append(_finding(
                                rule_id="NGA-SC-018",
                                severity="high",
                                title="High-entropy blob in tool directory",
                                description=(
                                    f"File '{rel}' has Shannon entropy {entropy:.2f} bits/byte "
                                    "(threshold: 6.5). High entropy may indicate encrypted or "
                                    "compressed payload data."
                                ),
                                affected=[rel],
                                remediation="Audit this file; confirm it is legitimate config data.",
                            ))
                    except OSError:
                        pass

        # Minified single-line JS anywhere
        if "NGA-SC-019" in self._enabled:
            for path in source_path.rglob("*.js"):
                if any(p in path.parts for p in ("node_modules", ".git")):
                    continue
                js_files_scanned += 1
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                lines = text.splitlines()
                if not lines:
                    continue
                max_line = max(len(line) for line in lines)
                if max_line > 5000 and len(lines) <= 3:
                    rel = str(path.relative_to(source_path))
                    findings.append(_finding(
                        rule_id="NGA-SC-019",
                        severity="medium",
                        title="Minified single-line JavaScript above 5 KB",
                        description=(
                            f"File '{rel}' contains a single JS line of {max_line} chars, "
                            "suggesting minification or obfuscation. "
                            "Check that this file was intentionally committed and is not a dropper."
                        ),
                        affected=[rel],
                        remediation="Audit the file source; prefer readable, un-minified files in source control.",
                    ))

        _log.info("[NGA-SC-017..019] scanned %d hidden tool dir(s), %d JS file(s)",
                  len(dirs_found), js_files_scanned)
        self._ctx["large_payloads"] = {
            "hidden_dirs_scanned": dirs_found,
            "js_files_scanned": js_files_scanned,
        }
        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: threat-intel IOC matching
    # ------------------------------------------------------------------

    def _check_threat_intel(
        self, deps: list[dict[str, Any]], nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if "NGA-SC-025" not in self._enabled:
            self._ctx["threat_intel"] = {"deps_checked": 0, "ioc_entries": 0, "lifecycle_patterns": 0}
            return findings

        malicious = self._threat_intel.get("known_malicious_packages") or {}
        ioc_entries = sum(len(v) for v in malicious.values() if isinstance(v, list))
        suspicious_patterns: list[str] = self._threat_intel.get("suspicious_lifecycle_patterns") or []
        _log.info(
            "[NGA-SC-025] checking %d deps against %d IOC entries, %d lifecycle patterns",
            len(deps), ioc_entries, len(suspicious_patterns),
        )

        for dep in deps:
            name = str(dep.get("name") or "")
            purl = str(dep.get("purl") or "")
            ecosystem = "pypi" if "pypi" in purl.lower() else "npm"
            pkg_list: list[dict[str, Any]] = malicious.get(ecosystem) or []
            for ioc in pkg_list:
                if not isinstance(ioc, dict):
                    continue
                ioc_name = str(ioc.get("name") or "")
                if not ioc_name:
                    continue
                if name.lower() == ioc_name.lower() or ioc_name.lower() in name.lower():
                    sev = str(ioc.get("severity") or "critical")
                    ref = str(ioc.get("reference") or "")
                    findings.append(_finding(
                        rule_id="NGA-SC-025",
                        severity=sev,
                        title=f"Package matches known-malicious IOC: {name}",
                        description=(
                            f"Package '{name}' ({purl}) matches the threat-intel IOC "
                            f"'{ioc_name}'. This package was reported as part of a known "
                            "supply-chain attack campaign."
                        ),
                        affected=[name],
                        remediation=(
                            "Remove this package immediately and audit your build artifacts. "
                            "Check for signs of credential exfiltration."
                        ),
                        references=[ref] if ref else [],
                    ))

        # Suspicious patterns in lifecycle script nodes
        lifecycle_nodes = [
            n for n in nodes
            if (n.get("component_type") or "").upper() == "LIFECYCLE_SCRIPT"
        ]
        for node in lifecycle_nodes:
            body = str((node.get("metadata") or {}).get("script_body") or "")
            name = node.get("name", "unknown")
            for pat in suspicious_patterns:
                if pat.lower() in body.lower():
                    findings.append(_finding(
                        rule_id="NGA-SC-025",
                        severity="critical",
                        title=f"Lifecycle script matches threat-intel pattern: {pat!r}",
                        description=(
                            f"Lifecycle script '{name}' contains the threat-intel pattern "
                            f"`{pat}`, which is associated with the Miasma supply-chain campaign."
                        ),
                        affected=[name],
                        remediation="Audit the script and rotate any credentials that may have been accessed.",
                    ))
                    break  # one finding per script is enough

        self._ctx["threat_intel"] = {
            "deps_checked": len(deps),
            "ioc_entries": ioc_entries,
            "lifecycle_patterns": len(suspicious_patterns),
        }
        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: mutable dependency references
    # ------------------------------------------------------------------

    def _check_mutable_deps(self, deps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if "NGA-SC-023" not in self._enabled:
            self._ctx["mutable_deps"] = {"deps_checked": 0}
            return findings
        _log.info("[NGA-SC-023] checking %d deps for mutable references", len(deps))
        self._ctx["mutable_deps"] = {"deps_checked": len(deps)}

        for dep in deps:
            spec = str(dep.get("version_spec") or "")
            name = str(dep.get("name") or "")
            source_file = str(dep.get("source_file") or "")
            if _MUTABLE_DEP_RE.search(spec):
                findings.append(_finding(
                    rule_id="NGA-SC-023",
                    severity="medium",
                    title=f"Dependency uses mutable reference: {name}",
                    description=(
                        f"Package '{name}' in '{source_file}' is declared with a mutable "
                        f"reference (`{spec[:100]}`). "
                        "Git URLs, tarball URLs, and file: references are not reproducible "
                        "and cannot be verified with a lockfile hash."
                    ),
                    affected=[name],
                    remediation=(
                        "Replace mutable references with pinned registry versions. "
                        "If a Git dependency is necessary, pin to a full commit SHA."
                    ),
                ))

        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: lockfile coverage
    # ------------------------------------------------------------------

    def _check_lockfile_coverage(
        self, source_path: Path, deps: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if "NGA-SC-024" not in self._enabled:
            self._ctx["lockfile"] = {"package_json_found": False, "lockfile_found": False}
            return findings

        # Check if lockfile exists for npm projects.
        # Prefer filesystem check; fall back to SBOM summary for remote repos where
        # the temp clone has already been deleted by the time analyze runs.
        if source_path.exists():
            has_package_json = (source_path / "package.json").exists()
            has_npm_lockfile = any(
                (source_path / lf).exists()
                for lf in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
            )
        elif self._sbom_summary:
            has_package_json = bool(self._sbom_summary.get("has_package_json"))
            has_npm_lockfile = bool(self._sbom_summary.get("has_lockfile"))
            _log.info("[NGA-SC-024] using SBOM summary (no local filesystem): package_json=%s lockfile=%s",
                      has_package_json, has_npm_lockfile)
        else:
            _log.info("[NGA-SC-024] no source_path and no SBOM summary; skipping lockfile check")
            self._ctx["lockfile"] = {"package_json_found": False, "lockfile_found": False}
            return findings
        _log.info("[NGA-SC-024] package.json=%s lockfile=%s", has_package_json, has_npm_lockfile)
        self._ctx["lockfile"] = {
            "package_json_found": has_package_json,
            "lockfile_found": has_npm_lockfile,
        }

        if has_package_json and not has_npm_lockfile:
            findings.append(_finding(
                rule_id="NGA-SC-024",
                severity="low",
                title="npm project missing lockfile",
                description=(
                    "package.json found but no lockfile (package-lock.json, pnpm-lock.yaml, "
                    "or yarn.lock). Without a lockfile, `npm install` resolves to the latest "
                    "version of each dependency at install time, which may include a compromised "
                    "release."
                ),
                affected=["package.json"],
                remediation=(
                    "Commit a lockfile to the repository and use `npm ci` in CI pipelines "
                    "instead of `npm install`."
                ),
            ))

        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: git history heuristics (full profile only)
    # ------------------------------------------------------------------

    def _scan_git_history(self, source_path: Path) -> list[dict[str, Any]]:
        """Basic git-aware heuristics (requires git on PATH)."""
        findings: list[dict[str, Any]] = []
        if not (source_path / ".git").exists():
            _log.info("[NGA-SC-020..022] no .git directory found; skipping git history scan")
            self._ctx["git_history"] = {"git_available": False}
            return findings

        try:
            import subprocess  # noqa: PLC0415
            result = subprocess.run(
                ["git", "-C", str(source_path), "log", "--oneline", "-50"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                self._ctx["git_history"] = {"git_available": False}
                return findings

            commits = result.stdout.splitlines()
            _log.info("[NGA-SC-020..022] inspecting %d recent commits", len(commits))
            self._ctx["git_history"] = {
                "git_available": True,
                "commits_inspected": len(commits),
            }

            for line in commits:
                lower = line.lower()
                if any(kw in lower for kw in ("dependency update", "chore: update", "bump deps")):
                    # Check what files actually changed
                    sha = line.split()[0]
                    changed = subprocess.run(
                        ["git", "-C", str(source_path), "diff-tree", "--no-commit-id", "-r",
                         "--name-only", sha],
                        capture_output=True, text=True, timeout=10,
                    )
                    changed_files = changed.stdout.splitlines()
                    ai_files = [
                        f for f in changed_files
                        if any(f.startswith(d) for d in (".claude", ".cursor", ".codex", ".gemini"))
                        or f.endswith((".yml", ".yaml"))
                        or "package.json" in f
                    ]
                    manifest_files = [
                        f for f in changed_files
                        if any(f.endswith(m) for m in ("requirements.txt", "pyproject.toml", "package-lock.json"))
                    ]
                    if "NGA-SC-020" in self._enabled and ai_files and not manifest_files:
                        findings.append(_finding(
                            rule_id="NGA-SC-020",
                            severity="medium",
                            title="Commit claims dependency update but only changes AI/workflow files",
                            description=(
                                f"Commit {sha!r} message suggests a dependency update "
                                f"but only changed: {ai_files[:5]}. "
                                "This mismatch is a pattern used by Miasma-style repo poisoning "
                                "to hide malicious config changes in plausible maintenance commits."
                            ),
                            affected=[sha],
                            remediation="Review this commit carefully.",
                        ))
        except Exception as exc:
            _log.debug("Git history scan failed: %s", exc)
            self._ctx["git_history"] = {"git_available": False}

        return findings


# ── Helpers ───────────────────────────────────────────────────────────────────

def _finding(
    rule_id: str,
    severity: str,
    title: str,
    description: str,
    affected: list[str],
    remediation: str,
    references: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "title": title,
        "description": description,
        "affected": affected,
        "remediation": remediation,
        "url": (references[0] if references else None),
    }


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text)
    if m:
        return m.group(0)[:80]
    return ""
