"""Supply-chain adapter: AI-agent / editor configuration files.

Creates DEVELOPER_TOOL_CONFIG, MCP_SERVER, LIFECYCLE_SCRIPT, and
GITHUB_WORKFLOW nodes from files that the normal SBOM extraction pass skips.

This adapter is intentionally kept out of the main extraction pipeline;
it runs as a separate second pass only when supply_chain_scan is enabled.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef,import-not-found]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

try:
    import yaml as _yaml  # type: ignore[import-untyped]
    _YAML_AVAILABLE = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False

from nuguard.sbom.models import Edge, Evidence, Node, NodeMetadata, SourceLocation
from nuguard.sbom.types import ComponentType, RelationshipType

_log = logging.getLogger("sbom.adapters.dev_tools")

# ── Broad-permission patterns ─────────────────────────────────────────────────
_BROAD_BASH_RE = re.compile(r"Bash\s*\(\s*\*\s*:\s*\*\s*\)")
_BROAD_BASH_NO_FILTER_RE = re.compile(r"^Bash$", re.IGNORECASE)

# ── GitHub Actions: SHA-pinned action ref (40 hex chars) ─────────────────────
_SHA_PIN_RE = re.compile(r"@[0-9a-f]{40}\b")

# ── Publish-step patterns ─────────────────────────────────────────────────────
_PUBLISH_NPM_RE = re.compile(r"npm\s+publish|NPM_TOKEN", re.IGNORECASE)
_PUBLISH_PYPI_RE = re.compile(r"pypa/gh-action-pypi-publish|twine\s+upload|PYPI_TOKEN|pypi", re.IGNORECASE)
_PUBLISH_SMITHERY_RE = re.compile(r"SMITHERY_API_KEY|smithery", re.IGNORECASE)
_OIDC_RE = re.compile(r"id-token\s*:\s*write", re.IGNORECASE)

# ── Credential-harvest patterns ───────────────────────────────────────────────
_CRED_PATTERNS = re.compile(
    r"/proc/[^/]*/environ|printenv|\.npmrc|\.pypirc|"
    r"AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|GITHUB_TOKEN|"
    r"npm\s+token|kubeconfig|vault\s+token",
    re.IGNORECASE,
)
_NETWORK_PATTERNS = re.compile(
    r"\bcurl\b|\bwget\b|\bfetch\b|\bgot\b|\baxios\b|\bhttpx\b|\brequests\b",
    re.IGNORECASE,
)
_SHELL_PIPE_RE = re.compile(r"\|\s*(bash|sh|node|python)\b", re.IGNORECASE)
_BUN_RE = re.compile(r"\bnpx\s+bun\b|\bbun\s+install\b|\bbun\s+run\b", re.IGNORECASE)
_UNPINNED_GLOBAL_RE = re.compile(r"\bnpm\s+install\s+-g\b|\bnpm\s+i\s+-g\b|\bnpx\b(?!\s+--yes)", re.IGNORECASE)


class DevToolConfigAdapter:
    """Scan a repository tree for AI-agent and editor configuration files.

    Returns a tuple of (nodes, edges) to append to an AiSbomDocument.
    """

    # Paths relative to repo root that this adapter handles
    CONFIG_PATTERNS: list[tuple[str, str]] = [
        (".claude/settings.json", "claude-settings"),
        ("CLAUDE.md", "claude-instructions"),
        ("AGENTS.md", "claude-instructions"),
        (".mcp.json", "mcp-config"),
        (".vscode/settings.json", "vscode-config"),
        ("gemini-extension.json", "gemini-config"),
        ("smithery.yaml", "smithery-config"),
    ]
    # Glob patterns (handled separately)
    GLOB_PATTERNS: list[tuple[str, str]] = [
        (".cursor/**", "cursor-config"),
        (".gemini/**", "gemini-config"),
        (".codex/**", "codex-config"),
    ]

    def scan(self, root: Path) -> tuple[list[Node], list[Edge]]:
        """Scan *root* for developer tool config files; return nodes and edges."""
        nodes: list[Node] = []
        edges: list[Edge] = []

        # Exact paths
        for rel_path, config_type in self.CONFIG_PATTERNS:
            path = root / rel_path
            if path.exists() and path.is_file():
                new_nodes, new_edges = self._process_file(path, root, config_type)
                nodes.extend(new_nodes)
                edges.extend(new_edges)

        # Glob patterns
        for pattern, config_type in self.GLOB_PATTERNS:
            for path in sorted(root.glob(pattern)):
                if path.is_file():
                    new_nodes, new_edges = self._process_file(path, root, config_type)
                    nodes.extend(new_nodes)
                    edges.extend(new_edges)

        return nodes, edges

    def _process_file(
        self, path: Path, root: Path, config_type: str
    ) -> tuple[list[Node], list[Edge]]:
        rel = str(path.relative_to(root))
        _log.debug("DevToolConfigAdapter: processing %s (%s)", rel, config_type)

        if config_type == "claude-settings":
            return self._parse_claude_settings(path, rel)
        if config_type == "mcp-config":
            return self._parse_mcp_json(path, rel)
        # Generic: just record the file as a DEVELOPER_TOOL_CONFIG node
        node = self._make_config_node(
            name=rel,
            config_type=config_type,
            source_file=rel,
        )
        return [node], []

    def _parse_claude_settings(
        self, path: Path, rel: str
    ) -> tuple[list[Node], list[Edge]]:
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return [self._make_config_node(rel, "claude-settings", rel)], []

        allowed: list[str] = [str(p) for p in (data.get("allow") or []) if p]
        denied: list[str] = [str(p) for p in (data.get("deny") or []) if p]

        auto_exec = any(
            _BROAD_BASH_RE.search(p) or _BROAD_BASH_NO_FILTER_RE.match(p)
            for p in allowed
        )

        meta = NodeMetadata(
            tool_config_type="claude-settings",
            permissions_granted=allowed or None,
            permissions_denied=denied or None,
            auto_execute=auto_exec if allowed else None,
            permission_scope="repo",
        )
        node = Node(
            id=uuid4(),
            name=rel,
            component_type=ComponentType.DEVELOPER_TOOL_CONFIG,
            confidence=0.95,
            metadata=meta,
            evidence=[Evidence(
                kind="config",
                confidence=0.95,
                detail=f"claude_settings: {rel}",
                location=SourceLocation(path=rel, line=1),
            )],
        )
        return [node], []

    def _parse_mcp_json(
        self, path: Path, rel: str
    ) -> tuple[list[Node], list[Edge]]:
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return [self._make_config_node(rel, "mcp-config", rel)], []

        parent_node = self._make_config_node(rel, "mcp-config", rel)
        nodes: list[Node] = [parent_node]
        edges: list[Edge] = []

        servers: dict[str, Any] = data.get("mcpServers", {})
        for server_name, server_cfg in servers.items():
            if not isinstance(server_cfg, dict):
                continue
            url: str | None = server_cfg.get("url") or server_cfg.get("endpoint")
            transport: str = str(server_cfg.get("transport", "stdio"))
            command = server_cfg.get("command")
            if url is None and command:
                url = f"stdio://{command}"

            server_node = Node(
                id=uuid4(),
                name=f"mcp-server:{server_name}",
                component_type=ComponentType.MCP_SERVER,
                confidence=0.9,
                metadata=NodeMetadata(
                    mcp_server_url=url,
                    mcp_server_trusted=False,
                    mcp_transport=transport,
                ),
                evidence=[Evidence(
                    kind="config",
                    confidence=0.9,
                    detail=f"mcp_server: {server_name} in {rel}",
                    location=SourceLocation(path=rel, line=1),
                )],
            )
            nodes.append(server_node)
            edges.append(Edge(
                source=parent_node.id,
                target=server_node.id,
                relationship_type=RelationshipType.CONTAINS,
            ))

        return nodes, edges

    def _make_config_node(
        self, name: str, config_type: str, source_file: str
    ) -> Node:
        return Node(
            id=uuid4(),
            name=name,
            component_type=ComponentType.DEVELOPER_TOOL_CONFIG,
            confidence=0.8,
            metadata=NodeMetadata(
                tool_config_type=config_type,
                permission_scope="repo",
            ),
            evidence=[Evidence(
                kind="config",
                confidence=0.8,
                detail=f"dev_tool_config: {config_type}",
                location=SourceLocation(path=source_file, line=1),
            )],
        )


class GithubActionsAdapter:
    """Scan .github/workflows/*.yml and produce GITHUB_WORKFLOW nodes."""

    def scan(self, root: Path) -> tuple[list[Node], list[Edge]]:
        """Return GITHUB_WORKFLOW nodes for every workflow YAML found."""
        nodes: list[Node] = []
        edges: list[Edge] = []
        workflow_dir = root / ".github" / "workflows"
        if not workflow_dir.is_dir():
            return nodes, edges
        for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
            if not path.is_file():
                continue
            n, e = self._parse_workflow(path, root)
            nodes.extend(n)
            edges.extend(e)
        return nodes, edges

    def _parse_workflow(
        self, path: Path, root: Path
    ) -> tuple[list[Node], list[Edge]]:
        rel = str(path.relative_to(root))
        try:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], []

        if not _YAML_AVAILABLE:
            node = self._make_workflow_node(rel, raw_text, {})
            return [node], []

        try:
            data: dict[str, Any] = _yaml.safe_load(raw_text) or {}
        except Exception:
            data = {}

        node = self._make_workflow_node(rel, raw_text, data)
        return [node], []

    def _make_workflow_node(
        self, rel: str, raw_text: str, data: dict[str, Any]
    ) -> Node:
        # Parse triggers
        on_block = data.get("on") or data.get(True) or {}  # type: ignore[call-overload]
        if isinstance(on_block, str):
            triggers = [on_block]
        elif isinstance(on_block, list):
            triggers = [str(t) for t in on_block]
        elif isinstance(on_block, dict):
            triggers = list(on_block.keys())
        else:
            triggers = []

        # Top-level permissions
        perms_raw = data.get("permissions")
        workflow_perms: dict[str, str] | None = None
        uses_oidc = False
        if isinstance(perms_raw, dict):
            workflow_perms = {k: str(v) for k, v in perms_raw.items()}
            uses_oidc = str(perms_raw.get("id-token", "")).lower() == "write"

        # Also check per-job permissions
        if not uses_oidc:
            for job_data in (data.get("jobs") or {}).values():
                if isinstance(job_data, dict):
                    job_perms = job_data.get("permissions") or {}
                    if isinstance(job_perms, dict):
                        if str(job_perms.get("id-token", "")).lower() == "write":
                            uses_oidc = True
                            break

        # Collect all action refs from uses: lines
        action_refs: list[str] = re.findall(r"uses:\s*([^\s#\n]+)", raw_text)

        # Detect publish targets
        publishes_to: list[str] = []
        if _PUBLISH_PYPI_RE.search(raw_text):
            publishes_to.append("pypi")
        if _PUBLISH_NPM_RE.search(raw_text):
            publishes_to.append("npm")
        if _PUBLISH_SMITHERY_RE.search(raw_text):
            publishes_to.append("smithery")

        # Compute step-level boolean flags from all job step `run:` bodies
        jobs: dict[str, Any] = data.get("jobs") or {}
        step_bodies_parts: list[str] = []
        for job_data in jobs.values():
            if isinstance(job_data, dict):
                for step in (job_data.get("steps") or []):
                    if isinstance(step, dict):
                        run_body = step.get("run")
                        if run_body and isinstance(run_body, str):
                            step_bodies_parts.append(run_body)
        step_bodies = "\n".join(step_bodies_parts)
        workflow_has_unpinned_global_install = bool(_UNPINNED_GLOBAL_RE.search(step_bodies)) if step_bodies else None
        workflow_has_cred_access = bool(_CRED_PATTERNS.search(step_bodies)) if step_bodies else None

        return Node(
            id=uuid4(),
            name=rel,
            component_type=ComponentType.GITHUB_WORKFLOW,
            confidence=0.9,
            metadata=NodeMetadata(
                workflow_triggers=triggers or None,
                workflow_permissions=workflow_perms,
                uses_oidc=uses_oidc if uses_oidc else None,
                publishes_to=publishes_to or None,
                action_refs=action_refs or None,
                workflow_has_unpinned_global_install=workflow_has_unpinned_global_install,
                workflow_has_cred_access=workflow_has_cred_access,
            ),
            evidence=[Evidence(
                kind="yaml",
                confidence=0.9,
                detail=f"github_workflow: {rel}",
                location=SourceLocation(path=rel, line=1),
            )],
        )


class LifecycleScriptAdapter:
    """Convert LifecycleScript records into SBOM LIFECYCLE_SCRIPT nodes."""

    def scan(self, root: Path) -> tuple[list[Node], list[Edge]]:
        """Return LIFECYCLE_SCRIPT nodes for all lifecycle scripts found."""
        from nuguard.sbom.deps import DependencyScanner  # lazy import avoids circular

        scanner = DependencyScanner()
        scripts = scanner.parse_lifecycle_scripts(root)

        nodes: list[Node] = []
        for script in scripts:
            body = script.body
            invokes_network = bool(_NETWORK_PATTERNS.search(body))
            invokes_shell = bool(_SHELL_PIPE_RE.search(body))
            downloads_binary = bool(_BUN_RE.search(body))
            references_credentials = bool(_CRED_PATTERNS.search(body))

            node = Node(
                id=uuid4(),
                name=f"{script.source_file}:{script.name}",
                component_type=ComponentType.LIFECYCLE_SCRIPT,
                confidence=0.9,
                metadata=NodeMetadata(
                    script_phase=script.name,
                    script_body=body[:2000],
                    invokes_network=invokes_network or None,
                    invokes_shell=invokes_shell or None,
                    downloads_binary=downloads_binary or None,
                    references_credentials=references_credentials or None,
                ),
                evidence=[Evidence(
                    kind="config",
                    confidence=0.9,
                    detail=f"lifecycle_script: {script.name} in {script.source_file}",
                    location=SourceLocation(path=script.source_file, line=1),
                )],
            )
            nodes.append(node)
        return nodes, []
