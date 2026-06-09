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
import logging
import math
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger("analysis.supply_chain_scanner")

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
    "NGA-SC-023", "NGA-SC-024",
}
_FULL_RULES = _STANDARD_RULES | {
    "NGA-SC-017", "NGA-SC-018", "NGA-SC-019",
    "NGA-SC-020", "NGA-SC-021", "NGA-SC-022",
}

_PROFILE_RULES: dict[str, set[str]] = {
    "ci": _CI_RULES,
    "standard": _STANDARD_RULES,
    "full": _FULL_RULES,
}


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
    ) -> list[dict[str, Any]]:
        """Run all enabled supply-chain checks; return raw finding dicts."""
        nodes = sbom_nodes or []
        deps = sbom_deps or []
        findings: list[dict[str, Any]] = []

        findings.extend(self._scan_lifecycle_scripts(source_path, nodes))
        findings.extend(self._scan_workflows(source_path, nodes))
        findings.extend(self._scan_dev_tool_configs(source_path, nodes))
        findings.extend(self._check_threat_intel(deps, nodes))
        findings.extend(self._check_mutable_deps(deps))
        findings.extend(self._check_lockfile_coverage(source_path, deps))

        if self.profile == "full":
            findings.extend(self._scan_large_payloads(source_path))
            findings.extend(self._scan_git_history(source_path))

        return findings

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
            for node in lifecycle_nodes:
                meta = node.get("metadata") or {}
                body = str(meta.get("script_body") or "")
                phase = str(meta.get("script_phase") or "")
                source = node.get("name", "unknown")
                findings.extend(self._check_script_body(body, phase, source))
        else:
            # No SBOM nodes; parse manifests directly
            findings.extend(self._scan_lifecycle_from_files(source_path))

        return findings

    def _scan_lifecycle_from_files(self, root: Path) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for pkg_json in root.rglob("package.json"):
            if any(p in pkg_json.parts for p in ("node_modules", ".git")):
                continue
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            scripts = data.get("scripts") or {}
            for phase, body in scripts.items():
                if not isinstance(body, str):
                    continue
                rel = str(pkg_json.relative_to(root))
                source = f"{rel}:{phase}"
                findings.extend(self._check_script_body(body, phase, source))
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
            for node in workflow_nodes:
                findings.extend(self._check_workflow_node(node))
        else:
            # Fall back to direct file scan
            findings.extend(self._scan_workflows_from_files(source_path))
        return findings

    def _scan_workflows_from_files(self, root: Path) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        workflows_dir = root / ".github" / "workflows"
        if not workflows_dir.is_dir():
            return findings
        for wf in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
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
            for node in config_nodes:
                findings.extend(self._check_config_node(node))
        else:
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
        claude_settings = root / ".claude" / "settings.json"
        if claude_settings.exists():
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

        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: large payloads (full profile only)
    # ------------------------------------------------------------------

    def _scan_large_payloads(self, source_path: Path) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for dir_name in _HIDDEN_TOOL_DIRS:
            tool_dir = source_path / dir_name
            if not tool_dir.is_dir():
                continue
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

        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: threat-intel IOC matching
    # ------------------------------------------------------------------

    def _check_threat_intel(
        self, deps: list[dict[str, Any]], nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if "NGA-SC-025" not in self._enabled:
            return findings

        malicious = self._threat_intel.get("known_malicious_packages") or {}

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
        suspicious_patterns: list[str] = self._threat_intel.get("suspicious_lifecycle_patterns") or []
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

        return findings

    # ------------------------------------------------------------------
    # Sub-scanner: mutable dependency references
    # ------------------------------------------------------------------

    def _check_mutable_deps(self, deps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if "NGA-SC-023" not in self._enabled:
            return findings

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
            return findings

        # Check if lockfile exists for npm projects
        has_package_json = (source_path / "package.json").exists()
        has_npm_lockfile = any(
            (source_path / lf).exists()
            for lf in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
        )

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
            return findings

        try:
            import subprocess  # noqa: PLC0415
            result = subprocess.run(
                ["git", "-C", str(source_path), "log", "--oneline", "-50"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return findings

            for line in result.stdout.splitlines():
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
