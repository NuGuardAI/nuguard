"""Tests for DevToolConfigAdapter, GithubActionsAdapter, and LifecycleScriptAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.adapters.dev_tools import DevToolConfigAdapter, GithubActionsAdapter, LifecycleScriptAdapter
from nuguard.sbom.types import ComponentType, RelationshipType

FIXTURES = Path(__file__).parent.parent / "fixtures" / "supply_chain"


# ---------------------------------------------------------------------------
# DevToolConfigAdapter tests
# ---------------------------------------------------------------------------

class TestDevToolConfigAdapter:
    def test_claude_settings_creates_developer_tool_config_node(self):
        adapter = DevToolConfigAdapter()
        nodes, edges = adapter.scan(FIXTURES / "claude_bash_wildcard")
        types = {n.component_type for n in nodes}
        assert ComponentType.DEVELOPER_TOOL_CONFIG in types, (
            f"Expected DEVELOPER_TOOL_CONFIG node, got types: {types}"
        )

    def test_claude_settings_detects_broad_bash_permission(self):
        adapter = DevToolConfigAdapter()
        nodes, _ = adapter.scan(FIXTURES / "claude_bash_wildcard")
        claude_nodes = [n for n in nodes if n.metadata.tool_config_type == "claude-settings"]
        assert claude_nodes, "Expected a claude-settings node"
        node = claude_nodes[0]
        assert node.metadata.permissions_granted is not None
        broad = [p for p in node.metadata.permissions_granted if "Bash(*:*)" in p]
        assert broad, f"Expected Bash(*:*) in permissions_granted, got: {node.metadata.permissions_granted}"
        assert node.metadata.auto_execute is True

    def test_claude_settings_captures_denied_permissions(self):
        adapter = DevToolConfigAdapter()
        nodes, _ = adapter.scan(FIXTURES / "claude_bash_wildcard")
        claude_nodes = [n for n in nodes if n.metadata.tool_config_type == "claude-settings"]
        assert claude_nodes
        node = claude_nodes[0]
        assert node.metadata.permissions_denied is not None
        assert any("rm" in p for p in node.metadata.permissions_denied)

    def test_mcp_json_creates_mcp_server_nodes(self):
        adapter = DevToolConfigAdapter()
        nodes, edges = adapter.scan(FIXTURES / "mcp_untrusted")
        mcp_nodes = [n for n in nodes if n.component_type == ComponentType.MCP_SERVER]
        assert len(mcp_nodes) >= 1, f"Expected MCP_SERVER nodes, got: {nodes}"
        # One for evil-server and one for local-tools
        assert len(mcp_nodes) == 2

    def test_mcp_server_nodes_are_untrusted_by_default(self):
        adapter = DevToolConfigAdapter()
        nodes, _ = adapter.scan(FIXTURES / "mcp_untrusted")
        mcp_nodes = [n for n in nodes if n.component_type == ComponentType.MCP_SERVER]
        assert all(n.metadata.mcp_server_trusted is False for n in mcp_nodes)

    def test_mcp_json_creates_contains_edges(self):
        adapter = DevToolConfigAdapter()
        nodes, edges = adapter.scan(FIXTURES / "mcp_untrusted")
        contains_edges = [e for e in edges if e.relationship_type == RelationshipType.CONTAINS]
        assert len(contains_edges) >= 1, "Expected CONTAINS edges from config to MCP servers"

    def test_mcp_server_url_captured(self):
        adapter = DevToolConfigAdapter()
        nodes, _ = adapter.scan(FIXTURES / "mcp_untrusted")
        evil_server = next(
            (n for n in nodes if "evil-server" in n.name), None
        )
        assert evil_server is not None
        assert evil_server.metadata.mcp_server_url == "https://attacker.example.com/mcp"

    def test_no_dev_tool_config_in_clean_package(self):
        adapter = DevToolConfigAdapter()
        nodes, edges = adapter.scan(FIXTURES / "clean_package")
        dev_tool_nodes = [n for n in nodes if n.component_type == ComponentType.DEVELOPER_TOOL_CONFIG]
        assert not dev_tool_nodes, "Expected no dev tool config nodes in clean package"


# ---------------------------------------------------------------------------
# GithubActionsAdapter tests
# ---------------------------------------------------------------------------

class TestGithubActionsAdapter:
    def test_workflow_creates_github_workflow_node(self):
        adapter = GithubActionsAdapter()
        nodes, _ = adapter.scan(FIXTURES / "github_publish_oidc_unpinned")
        types = {n.component_type for n in nodes}
        assert ComponentType.GITHUB_WORKFLOW in types

    def test_workflow_oidc_detected(self):
        adapter = GithubActionsAdapter()
        nodes, _ = adapter.scan(FIXTURES / "github_publish_oidc_unpinned")
        wf_nodes = [n for n in nodes if n.component_type == ComponentType.GITHUB_WORKFLOW]
        assert wf_nodes
        node = wf_nodes[0]
        assert node.metadata.uses_oidc is True

    def test_workflow_publish_target_detected(self):
        adapter = GithubActionsAdapter()
        nodes, _ = adapter.scan(FIXTURES / "github_publish_oidc_unpinned")
        wf_nodes = [n for n in nodes if n.component_type == ComponentType.GITHUB_WORKFLOW]
        assert wf_nodes
        node = wf_nodes[0]
        assert node.metadata.publishes_to is not None
        assert "pypi" in node.metadata.publishes_to

    def test_workflow_action_refs_captured(self):
        adapter = GithubActionsAdapter()
        nodes, _ = adapter.scan(FIXTURES / "github_publish_oidc_unpinned")
        wf_nodes = [n for n in nodes if n.component_type == ComponentType.GITHUB_WORKFLOW]
        assert wf_nodes
        node = wf_nodes[0]
        assert node.metadata.action_refs is not None
        assert any("actions/checkout" in r for r in node.metadata.action_refs)

    def test_prt_workflow_trigger_captured(self):
        adapter = GithubActionsAdapter()
        nodes, _ = adapter.scan(FIXTURES / "github_prt_injection")
        wf_nodes = [n for n in nodes if n.component_type == ComponentType.GITHUB_WORKFLOW]
        assert wf_nodes
        node = wf_nodes[0]
        assert node.metadata.workflow_triggers is not None
        assert "pull_request_target" in node.metadata.workflow_triggers

    def test_no_workflow_nodes_for_empty_dir(self, tmp_path: Path):
        adapter = GithubActionsAdapter()
        nodes, edges = adapter.scan(tmp_path)
        assert not nodes
        assert not edges


# ---------------------------------------------------------------------------
# LifecycleScriptAdapter tests
# ---------------------------------------------------------------------------

class TestLifecycleScriptAdapter:
    def test_postinstall_bun_creates_lifecycle_node(self):
        adapter = LifecycleScriptAdapter()
        nodes, _ = adapter.scan(FIXTURES / "package_postinstall_bun")
        types = {n.component_type for n in nodes}
        assert ComponentType.LIFECYCLE_SCRIPT in types

    def test_lifecycle_node_phase_captured(self):
        adapter = LifecycleScriptAdapter()
        nodes, _ = adapter.scan(FIXTURES / "package_postinstall_bun")
        ls_nodes = [n for n in nodes if n.component_type == ComponentType.LIFECYCLE_SCRIPT]
        assert ls_nodes
        node = ls_nodes[0]
        assert node.metadata.script_phase == "postinstall"

    def test_lifecycle_node_downloads_binary_flag(self):
        adapter = LifecycleScriptAdapter()
        nodes, _ = adapter.scan(FIXTURES / "package_postinstall_bun")
        ls_nodes = [n for n in nodes if n.component_type == ComponentType.LIFECYCLE_SCRIPT]
        assert ls_nodes
        node = ls_nodes[0]
        assert node.metadata.downloads_binary is True

    def test_curl_bash_lifecycle_node_invokes_network(self):
        adapter = LifecycleScriptAdapter()
        nodes, _ = adapter.scan(FIXTURES / "package_curl_bash")
        ls_nodes = [n for n in nodes if n.component_type == ComponentType.LIFECYCLE_SCRIPT]
        assert ls_nodes
        node = ls_nodes[0]
        assert node.metadata.invokes_network is True
        assert node.metadata.invokes_shell is True

    def test_proc_environ_lifecycle_references_credentials(self):
        adapter = LifecycleScriptAdapter()
        nodes, _ = adapter.scan(FIXTURES / "package_proc_environ")
        ls_nodes = [n for n in nodes if n.component_type == ComponentType.LIFECYCLE_SCRIPT]
        assert ls_nodes
        node = ls_nodes[0]
        assert node.metadata.references_credentials is True

    def test_clean_package_lifecycle_nodes_have_no_malicious_flags(self):
        """clean_package's scripts (jest/tsc/eslint) are captured as
        LIFECYCLE_SCRIPT nodes (all npm scripts are, not just install-hooks)
        but none should have any malicious-pattern flag set.
        """
        adapter = LifecycleScriptAdapter()
        nodes, _ = adapter.scan(FIXTURES / "clean_package")
        ls_nodes = [n for n in nodes if n.component_type == ComponentType.LIFECYCLE_SCRIPT]
        assert {n.metadata.script_phase for n in ls_nodes} == {"test", "build", "lint"}
        assert not any(
            n.metadata.invokes_network
            or n.metadata.invokes_shell
            or n.metadata.downloads_binary
            or n.metadata.references_credentials
            for n in ls_nodes
        )
