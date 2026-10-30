"""Unit tests for the NGA structural rules in nga_rules.py.

Each rule is tested for:
  - True positive: condition met → finding returned
  - True negative: condition absent → no finding
  - Edge cases where applicable

Rules under test:
  NGA-001  PII/PHI data handled by external LLM providers         CRITICAL
  NGA-002  Insufficient guardrails (sub-checks A + B)             HIGH
  NGA-003  Secrets handled insecurely — env vars or no secret store HIGH
  NGA-004  Containers running as root                              HIGH
  NGA-005  Unencrypted datastore containing PII/PHI               HIGH
  NGA-006  Missing auth on external API endpoint                   HIGH
  NGA-012  Missing HITL for irreversible tool actions              HIGH
  NGA-015  AI workloads without resource limits                    LOW
"""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.analysis.graph import AnalysisGraph
from nuguard.analysis.plugins.nga_rules import (
    _RULES,
    _rule_nga001_phi_to_external_llm,
    _rule_nga002_insufficient_guardrails,
    _rule_nga003_secrets_in_env,
    _rule_nga004_runs_as_root,
    _rule_nga005_unencrypted_pii_datastore,
    _rule_nga006_missing_auth_on_api_endpoint,
    _rule_nga009_no_audit_logging,
    _rule_nga010_pr_target_injection,
    _rule_nga011_github_env_injection,
    _rule_nga012_missing_hitl,
    _rule_nga013_no_k8s_network_policy,
    _rule_nga014_actions_runner_debug,
    _rule_nga015_no_resource_limits,
    _rule_nga021_idor_surface_no_protection,
    _rule_nga022_untrusted_mcp_tool,
    _rule_nga023_unprotected_vector_store,
    _rule_nga024_unauthenticated_agent_delegation,
    _rule_nga025_hidden_context_secret_leak,
    _rule_nga026_endpoint_no_rate_limit,
    _rule_nga027_missing_security_headers,
    _rule_nga028_permissive_cors,
    _rule_nga029_verbose_error_leak,
    _rule_nga030_jwt_no_algorithm_restriction,
)
from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.core.application_summary import _detect_app_instrumentation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model_node(name: str, provider: str) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "component_type": "MODEL",
        "metadata": {"extras": {"provider": provider}},
    }


def _guardrail_node(name: str = "guardrail") -> dict[str, Any]:
    return {"id": name, "name": name, "component_type": "GUARDRAIL", "metadata": {}}


def _deployment_node(name: str = "prod", **meta_kwargs: Any) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "component_type": "DEPLOYMENT",
        "metadata": dict(meta_kwargs),
    }


def _container_node(name: str = "app:latest", **meta_kwargs: Any) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "component_type": "CONTAINER_IMAGE",
        "metadata": dict(meta_kwargs),
    }


def _datastore_node(name: str = "db", **meta_kwargs: Any) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "component_type": "DATASTORE",
        "metadata": dict(meta_kwargs),
    }


def _agent_node(name: str = "agent") -> dict[str, Any]:
    return {"id": name, "name": name, "component_type": "AGENT", "metadata": {}}


def _tool_node(name: str, description: str = "") -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "component_type": "TOOL",
        "description": description,
        "metadata": {},
    }


def _api_node(name: str = "api") -> dict[str, Any]:
    return {"id": name, "name": name, "component_type": "API_ENDPOINT", "metadata": {}}


# ---------------------------------------------------------------------------
# NGA-001 — PII/PHI data handled by external LLM providers
# ---------------------------------------------------------------------------


class TestNga001:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        dc: list[str],
        classified_tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {"data_classification": dc}
        if classified_tables is not None:
            summary["classified_tables"] = classified_tables
        return _rule_nga001_phi_to_external_llm(nodes=nodes, summary=summary)

    def test_fires_phi_with_openai_model(self) -> None:
        nodes = [_model_node("gpt-4", "openai")]
        findings = self._run(nodes, ["PHI"])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-001"
        assert findings[0]["severity"] == "CRITICAL"
        assert "gpt-4" in findings[0]["affected"]

    def test_fires_pii_with_google_model(self) -> None:
        nodes = [_model_node("gemini-2.0-flash", "google")]
        findings = self._run(nodes, ["PII"])
        assert len(findings) == 1
        assert "gemini-2.0-flash" in findings[0]["affected"]

    def test_fires_both_phi_and_pii(self) -> None:
        nodes = [_model_node("gpt-4o-mini", "openai")]
        findings = self._run(nodes, ["PHI", "PII"], classified_tables=["patients", "users"])
        assert len(findings) == 1
        assert "2 classified table(s)" in findings[0]["description"]

    def test_no_finding_without_phi_pii(self) -> None:
        nodes = [_model_node("gpt-4", "openai")]
        findings = self._run(nodes, ["PUBLIC"])
        assert findings == []

    def test_no_finding_empty_data_classification(self) -> None:
        nodes = [_model_node("gpt-4", "openai")]
        findings = self._run(nodes, [])
        assert findings == []

    def test_no_finding_without_external_model(self) -> None:
        findings = self._run([], ["PHI"])
        assert findings == []

    def test_no_finding_internal_provider_only(self) -> None:
        nodes = [_model_node("llama3", "self-hosted")]
        findings = self._run(nodes, ["PHI"])
        assert findings == []

    def test_multiple_external_models_all_listed(self) -> None:
        nodes = [_model_node("gpt-4", "openai"), _model_node("claude-3", "anthropic")]
        findings = self._run(nodes, ["PHI"])
        assert len(findings) == 1
        assert "gpt-4" in findings[0]["affected"]
        assert "claude-3" in findings[0]["affected"]


# ---------------------------------------------------------------------------
# NGA-002 — Insufficient guardrails (sub-checks A and B)
# ---------------------------------------------------------------------------


class TestNga002:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga002_insufficient_guardrails(nodes=nodes, edges=edges or [], summary={})

    def test_fires_model_without_guardrail_sub_a(self) -> None:
        nodes = [_model_node("gpt-4", "openai")]
        findings = self._run(nodes)
        assert any(f["rule_id"] == "NGA-002" for f in findings)
        sub_a = [f for f in findings if "sub-check A" in f["title"]]
        assert len(sub_a) == 1
        assert sub_a[0]["severity"] == "HIGH"
        assert "gpt-4" in sub_a[0]["affected"]

    def test_fires_multiple_models_no_guardrail_sub_a(self) -> None:
        nodes = [_model_node("gpt-4", "openai"), _model_node("claude-3", "anthropic")]
        findings = self._run(nodes)
        sub_a = [f for f in findings if "sub-check A" in f["title"]]
        assert len(sub_a) == 1
        assert "2 LLM model node(s)" in sub_a[0]["description"]

    def test_no_finding_with_guardrail_present(self) -> None:
        nodes = [_model_node("gpt-4", "openai"), _guardrail_node("output-filter")]
        findings = self._run(nodes)
        assert findings == []

    def test_no_finding_no_models_at_all(self) -> None:
        findings = self._run([])
        assert findings == []

    def test_no_finding_for_llm_soft_rejected_model(self) -> None:
        """Regression: a MODEL node SBOM verification already flagged as a
        likely false positive (e.g. a mock model name found only in a unit
        test fixture, observed against OWASP Juice Shop's
        preconditionValidation.unit.test.ts) must not raise a 'no output
        guardrail' finding — there's no real model to guard."""
        model = _model_node("mock-model", "test-fixture")
        model["metadata"]["extras"]["llm_soft_rejected"] = True
        findings = self._run([model])
        assert findings == []

    def test_real_model_still_fires_alongside_soft_rejected_one(self) -> None:
        real = _model_node("gpt-4", "openai")
        fake = _model_node("mock-model", "test-fixture")
        fake["metadata"]["extras"]["llm_soft_rejected"] = True
        findings = self._run([real, fake])
        sub_a = [f for f in findings if "sub-check A" in f["title"]]
        assert len(sub_a) == 1
        assert sub_a[0]["affected"] == ["gpt-4"]

    def test_no_finding_guardrail_suppresses_even_multiple_models(self) -> None:
        nodes = [
            _model_node("gpt-4", "openai"),
            _model_node("llama3", "self-hosted"),
            _guardrail_node(),
        ]
        findings = self._run(nodes)
        assert findings == []

    def test_fires_sub_b_internet_agent_no_guardrail(self) -> None:
        agent = _agent_node("my-agent")
        api = _api_node("external-api")
        edge = {"source": "my-agent", "target": "external-api"}
        findings = self._run([agent, api], edges=[edge])
        sub_b = [f for f in findings if "sub-check B" in f["title"]]
        assert len(sub_b) == 1
        assert "my-agent" in sub_b[0]["affected"]

    def test_sub_b_suppressed_by_guardrail(self) -> None:
        agent = _agent_node("my-agent")
        api = _api_node("external-api")
        guardrail = _guardrail_node()
        edge = {"source": "my-agent", "target": "external-api"}
        findings = self._run([agent, api, guardrail], edges=[edge])
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-003 — Secrets handled insecurely
# ---------------------------------------------------------------------------


class TestNga003:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        security_findings: list[str] | None = None,
        secret_stores: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {
            "security_findings": security_findings or [],
            "secret_stores": secret_stores or [],
        }
        return _rule_nga003_secrets_in_env(nodes=nodes, summary=summary)

    def test_fires_secrets_in_env_finding(self) -> None:
        nodes = [_deployment_node("prod")]
        findings = self._run(nodes, security_findings=["secrets_in_env_vars"])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-003"
        assert findings[0]["severity"] == "HIGH"

    def test_fires_no_secret_store_with_deployment(self) -> None:
        nodes = [_deployment_node("prod")]
        findings = self._run(nodes, secret_stores=[])
        assert len(findings) == 1

    def test_no_finding_secret_store_configured(self) -> None:
        nodes = [_deployment_node("prod")]
        findings = self._run(nodes, secret_stores=["vault"])
        assert findings == []

    def test_no_finding_no_deployments_no_env_flag(self) -> None:
        findings = self._run([])
        assert findings == []

    def test_fires_env_flag_even_with_deployment_node(self) -> None:
        findings = self._run(
            [_deployment_node("generic")],
            security_findings=["secrets_in_env_vars"],
        )
        assert len(findings) == 1
        assert "Secrets are referenced" in findings[0]["description"]

    def test_description_includes_both_issues_when_both_present(self) -> None:
        nodes = [_deployment_node("prod")]
        findings = self._run(nodes, security_findings=["secrets_in_env_vars"], secret_stores=[])
        assert len(findings) == 1
        desc = findings[0]["description"]
        assert "Secrets are referenced" in desc
        assert "deployment resource(s)" in desc


# ---------------------------------------------------------------------------
# NGA-004 — Containers running as root
# ---------------------------------------------------------------------------


class TestNga004:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        security_findings: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {"security_findings": security_findings or []}
        return _rule_nga004_runs_as_root(nodes=nodes, summary=summary)

    def test_fires_on_security_finding_with_container_node(self) -> None:
        nodes = [_container_node("node:20")]
        findings = self._run(nodes, security_findings=["container_runs_as_root"])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-004"
        assert findings[0]["severity"] == "HIGH"
        assert "node:20" in findings[0]["affected"]

    def test_fires_on_node_metadata_flag(self) -> None:
        nodes = [_deployment_node("prod", runs_as_root=True)]
        findings = self._run(nodes)
        assert len(findings) == 1
        assert "prod" in findings[0]["affected"]

    def test_fires_container_image_metadata_flag(self) -> None:
        nodes = [_container_node("myapp:latest", runs_as_root=True)]
        findings = self._run(nodes)
        assert len(findings) == 1

    def test_no_finding_no_trigger(self) -> None:
        nodes = [_deployment_node("prod"), _container_node("app:latest")]
        findings = self._run(nodes)
        assert findings == []

    def test_no_finding_empty_nodes(self) -> None:
        findings = self._run([])
        assert findings == []

    def test_security_finding_without_container_nodes_returns_empty(self) -> None:
        findings = self._run([], security_findings=["container_runs_as_root"])
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-015 — AI workloads without resource limits
# ---------------------------------------------------------------------------


class TestNga015:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        security_findings: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {"security_findings": security_findings or []}
        return _rule_nga015_no_resource_limits(nodes=nodes, summary=summary)

    def test_fires_on_security_finding(self) -> None:
        nodes = [_deployment_node("prod")]
        findings = self._run(nodes, security_findings=["no_resource_limits"])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-015"
        assert findings[0]["severity"] == "LOW"

    def test_fires_on_node_metadata_flag(self) -> None:
        nodes = [
            {
                "id": "prod",
                "name": "prod",
                "component_type": "DEPLOYMENT",
                "metadata": {"extras": {"no_resource_limits": True}},
            }
        ]
        findings = self._run(nodes)
        assert len(findings) == 1
        assert "prod" in findings[0]["affected"]

    def test_no_finding_no_deployment_nodes(self) -> None:
        findings = self._run([], security_findings=["no_resource_limits"])
        assert findings == []

    def test_no_finding_deployment_without_trigger(self) -> None:
        nodes = [_deployment_node("prod")]
        findings = self._run(nodes)
        assert findings == []

    def test_affected_narrows_to_flagged_nodes(self) -> None:
        flagged = {
            "id": "svc-a",
            "name": "svc-a",
            "component_type": "DEPLOYMENT",
            "metadata": {"extras": {"no_resource_limits": True}},
        }
        unflagged = _deployment_node("svc-b")
        findings = self._run([flagged, unflagged])
        assert len(findings) == 1
        assert findings[0]["affected"] == ["svc-a"]

    def test_security_finding_falls_back_to_all_deployments(self) -> None:
        nodes = [_deployment_node("svc-a"), _deployment_node("svc-b")]
        findings = self._run(nodes, security_findings=["no_resource_limits"])
        assert len(findings) == 1
        assert set(findings[0]["affected"]) == {"svc-a", "svc-b"}


# ---------------------------------------------------------------------------
# NGA-005 — Unencrypted datastore containing PII/PHI
# ---------------------------------------------------------------------------


class TestNga005:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        dc: list[str],
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {"data_classification": dc}
        return _rule_nga005_unencrypted_pii_datastore(nodes=nodes, summary=summary)

    def test_fires_on_unencrypted_pii_datastore(self) -> None:
        node = {
            "id": "db",
            "name": "patients-db",
            "component_type": "DATASTORE",
            "metadata": {"extras": {"encryption_at_rest": False}},
        }
        findings = self._run([node], dc=["PHI"])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-005"
        assert findings[0]["severity"] == "HIGH"

    def test_no_finding_if_encrypted(self) -> None:
        node = {
            "id": "db",
            "name": "patients-db",
            "component_type": "DATASTORE",
            "metadata": {"extras": {"encryption_at_rest": True}},
        }
        findings = self._run([node], dc=["PHI"])
        assert findings == []

    def test_no_finding_without_phi_label(self) -> None:
        node = {
            "id": "db",
            "name": "metrics-db",
            "component_type": "DATASTORE",
            "metadata": {},
        }
        findings = self._run([node], dc=["PUBLIC"])
        assert findings == []

    def test_no_finding_no_datastore_nodes(self) -> None:
        findings = self._run([_model_node("gpt-4", "openai")], dc=["PHI"])
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-006 — Missing auth on external API endpoint
# ---------------------------------------------------------------------------


class TestNga006:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga006_missing_auth_on_api_endpoint(nodes=nodes, summary={}, edges=edges or [])

    def test_fires_on_api_endpoint_with_no_incoming_edge(self) -> None:
        """API endpoint with no incoming edges and no_auth_required flag fires."""
        node = {
            "id": "api",
            "name": "inference-api",
            "component_type": "API_ENDPOINT",
            "metadata": {"extras": {"no_auth_required": True}},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-006"
        assert findings[0]["severity"] == "HIGH"

    def test_fires_on_api_endpoint_no_incoming_edge_at_all(self) -> None:
        """API endpoint with no edges at all (unprotected) fires."""
        node = {
            "id": "api",
            "name": "inference-api",
            "component_type": "API_ENDPOINT",
            "metadata": {},
        }
        findings = self._run([node])
        assert len(findings) == 1

    def test_no_finding_with_auth_edge_from_auth_node(self) -> None:
        """API endpoint protected by an AUTH→API edge does not fire."""
        auth_node = {"id": "auth", "name": "auth", "component_type": "AUTH", "metadata": {}}
        api_node = {"id": "api", "name": "inference-api", "component_type": "API_ENDPOINT", "metadata": {}}
        edge = {"source": "auth", "target": "api"}
        findings = self._run([auth_node, api_node], edges=[edge])
        assert findings == []

    def test_no_finding_when_api_has_incoming_from_non_auth(self) -> None:
        """API endpoint with any incoming edge (e.g. agent calls it) does not fire.

        The rule requires EITHER no_auth_required OR no incoming edge.
        An agent→api edge means there IS an incoming edge, so the OR is False.
        """
        agent = _agent_node("my-agent")
        api_node = {"id": "api", "name": "inference-api", "component_type": "API_ENDPOINT", "metadata": {}}
        edge = {"source": "my-agent", "target": "api"}
        findings = self._run([agent, api_node], edges=[edge])
        assert findings == []

    def test_no_finding_no_api_nodes(self) -> None:
        findings = self._run([_model_node("gpt-4", "openai")])
        assert findings == []

    def test_fires_when_auth_required_is_false(self) -> None:
        """auth_required=False (computed by the enricher from AUTH edges) fires NGA-006."""
        node = {
            "id": "api",
            "name": "inference-api",
            "component_type": "API_ENDPOINT",
            "metadata": {"auth_required": False},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-006"

    def test_no_finding_when_auth_required_true_and_protected_by_edge(self) -> None:
        """auth_required=True with a real protecting AUTH edge does not fire.

        This mirrors the state the enricher actually produces: auth_required is
        only True when an AUTH→PROTECTS edge exists, so protected_targets already
        excludes the endpoint before the auth_required check is even reached.
        """
        auth_node = {"id": "auth", "name": "auth", "component_type": "AUTH", "metadata": {}}
        api_node = {
            "id": "api",
            "name": "inference-api",
            "component_type": "API_ENDPOINT",
            "metadata": {"auth_required": True},
        }
        edge = {"source": "auth", "target": "api"}
        findings = self._run([auth_node, api_node], edges=[edge])
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-012 — Missing HITL for irreversible tool actions
# ---------------------------------------------------------------------------


class TestNga012:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga012_missing_hitl(nodes=nodes, edges=edges or [], summary={})

    def test_fires_on_agent_with_delete_tool_no_hitl(self) -> None:
        agent = _agent_node("my-agent")
        tool = _tool_node("delete-records-tool", description="deletes records permanently")
        edge = {"source": "my-agent", "target": "delete-records-tool", "edge_type": "CALLS"}
        findings = self._run([agent, tool], edges=[edge])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-012"
        assert findings[0]["severity"] == "HIGH"

    def test_no_finding_when_agent_has_hitl_metadata(self) -> None:
        """Agent with HITL pattern in metadata (e.g. 'interrupt') suppresses NGA-012."""
        agent = {
            "id": "my-agent",
            "name": "my-agent",
            "component_type": "AGENT",
            "metadata": {"extras": {"interrupt_before": ["send-email-tool"]}},
        }
        tool = _tool_node("send-email-tool", description="sends emails")
        edge = {"source": "my-agent", "target": "send-email-tool", "edge_type": "CALLS"}
        findings = self._run([agent, tool], edges=[edge])
        assert findings == []

    def test_no_finding_for_benign_tools(self) -> None:
        agent = _agent_node("my-agent")
        tool = _tool_node("search-tool", description="searches the web")
        edge = {"source": "my-agent", "target": "search-tool", "edge_type": "CALLS"}
        findings = self._run([agent, tool], edges=[edge])
        assert findings == []

    def test_no_finding_no_agents(self) -> None:
        tool = _tool_node("delete-tool", description="deletes data permanently")
        findings = self._run([tool])
        assert findings == []


# ---------------------------------------------------------------------------
# _RULES registry
# ---------------------------------------------------------------------------


class TestRulesRegistry:
    def test_registry_contains_all_rules(self) -> None:
        names = [r.__name__ for r in _RULES]
        # Verify CRITICAL/HIGH rules at the top, then MEDIUM, then LOW
        assert names[0] == "_rule_nga001_phi_to_external_llm"
        assert names[1] == "_rule_nga002_insufficient_guardrails"
        assert names[2] == "_rule_nga003_secrets_in_env"
        assert names[3] == "_rule_nga004_runs_as_root"
        assert names[4] == "_rule_nga005_unencrypted_pii_datastore"
        # NGA-019, NGA-020, NGA-021 are present (added after NGA-018)
        assert "_rule_nga019_unguarded_write_to_sensitive_datastore" in " ".join(names)
        assert "_rule_nga020_unguarded_agent_delegation" in " ".join(names)
        assert "_rule_nga021_idor_surface_no_protection" in " ".join(names)
        assert "_rule_nga022_untrusted_mcp_tool" in " ".join(names)
        assert "_rule_nga023_unprotected_vector_store" in " ".join(names)
        assert "_rule_nga024_unauthenticated_agent_delegation" in " ".join(names)
        assert "_rule_nga025_hidden_context_secret_leak" in " ".join(names)
        assert "_rule_nga026_endpoint_no_rate_limit" in " ".join(names)
        assert "_rule_nga027_missing_security_headers" in " ".join(names)
        assert "_rule_nga028_permissive_cors" in " ".join(names)
        assert "_rule_nga029_verbose_error_leak" in " ".join(names)
        assert "_rule_nga030_jwt_no_algorithm_restriction" in " ".join(names)
        # Rules run NGA-001 to NGA-030 with no gaps
        assert len(_RULES) == 30

    def test_all_rules_return_list(self) -> None:
        for rule in _RULES:
            result = rule(nodes=[], edges=[], summary={})
            assert isinstance(result, list), f"{rule.__name__} did not return a list"

    def test_all_findings_have_required_keys(self) -> None:
        required = {"rule_id", "severity", "title", "description", "affected", "remediation"}
        nodes = [
            _model_node("gpt-4", "openai"),
            _deployment_node("prod"),
            _container_node("app:latest", runs_as_root=True),
        ]
        summary = {
            "data_classification": ["PHI"],
            "security_findings": [
                "secrets_in_env_vars",
                "container_runs_as_root",
                "no_resource_limits",
            ],
            "secret_stores": [],
        }
        for rule in _RULES:
            for finding in rule(nodes=nodes, edges=[], summary=summary):
                missing = required - finding.keys()
                assert not missing, f"{rule.__name__} finding missing keys: {missing}"


# ---------------------------------------------------------------------------
# NGA-009 — AI application has no audit logging enabled (instrumentation path)
# ---------------------------------------------------------------------------


class TestNga009Instrumentation:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga009_no_audit_logging(nodes=nodes, summary=summary or {})

    def test_passes_with_instrumentation_summary(self) -> None:
        """Explicitly enabled tracing is valid audit evidence."""
        nodes = [_agent_node("orchestrator")]
        summary: dict[str, Any] = {
            "instrumentation": {"tools": ["opentelemetry"], "tracing_enabled": True}
        }
        findings = self._run(nodes, summary)
        assert findings == []

    def test_fails_without_any_logging(self) -> None:
        """Agents present, no log paths / audit libs / instrumentation → finding returned."""
        nodes = [_agent_node("orchestrator")]
        summary: dict[str, Any] = {"log_paths": [], "frameworks": [], "instrumentation": {}}
        findings = self._run(nodes, summary)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-009"
        assert findings[0]["severity"] == "HIGH"
        assert "orchestrator" in findings[0]["affected"]

    def test_framework_name_alone_is_not_active_audit_evidence(self) -> None:
        findings = self._run(
            [_agent_node("orchestrator")],
            {"frameworks": ["opentelemetry", "langfuse"]},
        )

        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-009"

    def test_middleware_name_in_prose_is_not_active_audit_evidence(self) -> None:
        findings = self._run(
            [_agent_node("orchestrator")],
            {"use_case_summary": "Consider adding RotatingFileHandler in production."},
        )

        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-009"

    def test_active_langsmith_tracing_populates_summary_and_passes(self) -> None:
        active_sources = (
            (
                "agent.py",
                "from langchain_core.tracers import LangChainTracer\n"
                "tracer = LangChainTracer()\n",
            ),
            (
                "agent.py",
                "from langchain_core.tracers.langchain import LangChainTracer\n"
                "tracer = LangChainTracer()\n",
            ),
            ("agent.py", "from langsmith.run_trees import RunTree\nrun = RunTree(name='agent')\n"),
            (
                "agent.py",
                "from langsmith import get_current_run_tree\nrun = get_current_run_tree()\n",
            ),
            ("agent.py", "from langsmith import traceable\n@traceable\ndef agent():\n    pass\n"),
            (
                "agent.py",
                "from langsmith import traceable as observed\n"
                "@observed\ndef agent():\n    pass\n",
            ),
            (
                "agent.py",
                "from langsmith import run_trees as rt\n"
                "run = rt.RunTree(name='agent')\n",
            ),
            (
                "agent.ts",
                'import { traceable as observed } from "langsmith/traceable";\n'
                "const agent = observed(async () => 'ok');\n",
            ),
            (".env", 'LANGSMITH_TRACING="true"\n'),
        )

        for path, source in active_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[(path, source)], dep_names={"langsmith"}
            )
            assert instrumentation is not None
            assert instrumentation.tools == ["langsmith"]
            assert instrumentation.tracing_enabled is True
            summary = {"instrumentation": instrumentation.model_dump(mode="json")}
            assert self._run([_agent_node("orchestrator")], summary) == []

    def test_python_langsmith_binding_must_remain_live_in_its_scope(self) -> None:
        inactive_sources = (
            (
                "from langsmith import traceable\n"
                "def run(traceable):\n"
                "    return traceable(lambda: None)\n"
            ),
            (
                "from langsmith import traceable\n"
                "def run():\n"
                "    traceable = lambda fn: fn\n"
                "    return traceable(lambda: None)\n"
            ),
            (
                "from langsmith import traceable\n"
                "traceable = lambda fn: fn\n"
                "run = traceable(lambda: None)\n"
            ),
            (
                "import langsmith as ls\n"
                "def run(ls):\n"
                "    return ls.traceable(lambda: None)\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "from langsmith import traceable\n"
                    "def build():\n"
                    "    @traceable\n"
                    "    def run():\n"
                    "        return 'ok'\n"
                    "    return run\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            (
                "from langsmith.wrappers import wrap_openai\n"
                "client = wrap_openai(OpenAI())\n"
            ),
            (
                "import langsmith.wrappers as wrappers\n"
                "client = wrappers.wrap_openai(OpenAI())\n"
            ),
            (
                "import langchain_core.tracers as tracers\n"
                "tracer = tracers.LangChainTracer()\n"
            ),
            (
                "import langchain_core.tracers\n"
                "tracer = langchain_core.tracers.LangChainTracer()\n"
            ),
            (
                "from langchain_core import tracers\n"
                "tracer = tracers.LangChainTracer()\n"
            ),
        ],
    )
    def test_python_official_langsmith_wrappers_and_tracer_namespaces(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["langsmith"]
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            "from local.wrappers import wrap_openai\nwrap_openai(OpenAI())\n",
            (
                "import langchain_core.tracers.testing as tracers\n"
                "tracers.LangChainTracer()\n"
            ),
        ],
    )
    def test_python_langsmith_new_forms_require_exact_provenance(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names=set()
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        "source",
        [
            "from langsmith.testing import traceable\ntraceable(lambda: None)\n",
            (
                "from langchain_core.tracers.testing import LangChainTracer\n"
                "LangChainTracer()\n"
            ),
            "from opentelemetry.testing import get_tracer\nget_tracer('agent')\n",
            "from ddtrace.testing import trace\ntrace('agent')\n",
            "from jaeger.testing import Config\nConfig({})\n",
        ],
    )
    def test_python_instrumentation_exports_require_canonical_modules(self, source: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names=set()
        )

        assert instrumentation is None or instrumentation.tracing_enabled is not True

    def test_python_global_and_nonlocal_writes_invalidate_langsmith_imports(self) -> None:
        inactive_sources = (
            (
                "from langsmith import traceable\n"
                "def replace():\n"
                "    global traceable\n"
                "    traceable = lambda fn: fn\n"
                "    return traceable(lambda: None)\n"
            ),
            (
                "def outer():\n"
                "    from langsmith import traceable\n"
                "    def replace():\n"
                "        nonlocal traceable\n"
                "        traceable = lambda fn: fn\n"
                "        return traceable(lambda: None)\n"
                "    return replace\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "def enable():\n"
                    "    global observed\n"
                    "    from langsmith import traceable as observed\n"
                    "    return observed(lambda: None)\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

    def test_python_exception_pattern_and_comprehension_bindings_shadow_imports(
        self,
    ) -> None:
        inactive_sources = (
            (
                "from langsmith import traceable\n"
                "def run():\n"
                "    try:\n"
                "        raise RuntimeError\n"
                "    except RuntimeError as traceable:\n"
                "        return traceable(lambda: None)\n"
            ),
            (
                "from langsmith import traceable\n"
                "def run(value):\n"
                "    match value:\n"
                "        case {'handler': traceable}:\n"
                "            return traceable(lambda: None)\n"
            ),
            (
                "from langsmith import traceable\n"
                "def run(functions):\n"
                "    return [traceable(lambda: None) for traceable in functions]\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "from langsmith import traceable\n"
                    "def run(functions):\n"
                    "    ignored = [fn for traceable in functions for fn in [traceable]]\n"
                    "    return traceable(lambda: None)\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

    def test_langsmith_dependency_and_import_alone_still_fail(self) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[],
            files=[("agent.py", "from langsmith import Client\n")],
            dep_names={"langsmith"},
        )

        assert instrumentation is None
        findings = self._run(
            [_agent_node("orchestrator"), _tool_node("langsmith")],
            {"instrumentation": instrumentation},
        )
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-009"

    def test_comments_and_docstrings_do_not_activate_langsmith(self) -> None:
        source = '''\
from langsmith import RunTree
# RunTree(name="commented")
"""RunTree(name="documented")
LANGSMITH_TRACING=true
"""
'''
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
        )

        assert instrumentation is None

    def test_test_and_example_paths_do_not_activate_langsmith(self) -> None:
        source = "from langsmith import RunTree\nrun = RunTree(name='fixture')\n"
        non_runtime_files = (
            ("tests/test_tracing.py", source),
            ("test_tracing.py", source),
            ("examples/tracing.py", source),
            ("example.py", source),
            ("trace_example.py", source),
            ("trace-demo.py", source),
            ("fixtures/tracing.py", source),
            ("docs/tracing.py", source),
            ("conftest.py", source),
            (".env.example", "LANGSMITH_TRACING=true\n"),
            (".env.test", "LANGSMITH_TRACING=true\n"),
            (".env.testing.local", "LANGSMITH_TRACING=true\n"),
            (".env.e2e.local", "LANGSMITH_TRACING=true\n"),
            ("test-env.local", "LANGSMITH_TRACING=true\n"),
            ("test.env.production", "LANGSMITH_TRACING=true\n"),
            (
                "Tests/helpers.py",
                "from langsmith import traceable\n@traceable\ndef agent(): pass\n",
            ),
            (
                "Specs/helpers.ts",
                'import { traceable } from "langsmith/traceable";\ntraceable(() => 1);\n',
            ),
            ("config/tests.yaml", "environment:\n  LANGSMITH_TRACING: true\n"),
            (
                ".github/workflows/test.yml",
                "jobs:\n  test:\n    env:\n      LANGSMITH_TRACING: true\n",
            ),
        )
        for path, content in non_runtime_files:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[(path, content)], dep_names={"langsmith"}
            )
            assert instrumentation is None

        runtime = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "config/runtime.yaml",
                    "environment:\n  LANGSMITH_TRACING: true\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert runtime is not None
        assert runtime.tracing_enabled is True

        runtime_source = _detect_app_instrumentation(
            nodes=[],
            files=[("src/docs_runtime.py", source)],
            dep_names={"langsmith"},
        )
        assert runtime_source is not None
        assert runtime_source.tracing_enabled is True

    def test_local_fake_runtree_does_not_activate_langsmith(self) -> None:
        source = "class RunTree:\n    pass\n\nrun = RunTree()\n"
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names=set()
        )

        assert instrumentation is None

    def test_python_tracing_config_requires_real_environment_target(self) -> None:
        inactive_sources = (
            "LANGSMITH_TRACING = True\n",
            "settings.LANGSMITH_TRACING = True\n",
            'settings = {"LANGSMITH_TRACING": True}\n',
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    'import os\nos.environ["LANGSMITH_TRACING"] = "true"\n',
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            (
                "from langsmith import tracing_context\n"
                "with tracing_context(enabled=True):\n"
                "    run_agent()\n"
            ),
            (
                "from langsmith.run_helpers import tracing_context as observed\n"
                "with observed(enabled='yes'):\n"
                "    run_agent()\n"
            ),
            (
                "from langsmith import tracing_context\n"
                "@tracing_context(enabled=True)\n"
                "def run_agent():\n"
                "    pass\n"
            ),
            (
                "from langsmith.run_helpers import ensure_traceable\n"
                "agent = ensure_traceable(run_agent)\n"
            ),
            (
                "import langsmith.run_helpers as helpers\n"
                "agent = helpers.ensure_traceable(run_agent)\n"
            ),
        ],
    )
    def test_python_supported_langsmith_helpers_enable_tracing(self, source: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["langsmith"]
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            (
                "from langsmith import tracing_context\n"
                "with tracing_context(enabled=False):\n"
                "    run_agent()\n"
            ),
            (
                "from langsmith import tracing_context\n"
                "with tracing_context():\n"
                "    run_agent()\n"
            ),
            (
                "from langsmith import tracing_context\n"
                "context = tracing_context(enabled=True)\n"
            ),
        ],
    )
    def test_python_tracing_context_requires_explicit_truthy_enablement(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
        )

        assert instrumentation is None or instrumentation.tracing_enabled is None

    def test_javascript_traceable_requires_import_provenance_and_runtime_use(self) -> None:
        active_sources = (
            (
                'import { traceable as observed } from "langsmith/traceable";\n'
                "const run = observed(async () => 'ok');\n"
            ),
            (
                'import * as ls from "langsmith";\n'
                "const run = ls.traceable(async () => 'ok');\n"
            ),
            (
                'const { traceable: observed } = require("langsmith");\n'
                "const run = observed(async () => 'ok');\n"
            ),
            (
                'const ls = require("langsmith");\n'
                "const run = ls.traceable(async () => 'ok');\n"
            ),
            (
                'const observed = require("langsmith").traceable;\n'
                "const run = observed(async () => 'ok');\n"
            ),
            ('require("langsmith").traceable(async () => "ok");\n'),
        )
        for source in active_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
            )
            assert instrumentation is not None
            assert instrumentation.tracing_enabled is True

        inactive_sources = (
            'import { traceable } from "langsmith/traceable";\n',
            "const traceable = (fn) => fn;\nconst run = traceable(() => 'local');\n",
            (
                'import { traceable } from "langsmith/traceable";\n'
                "// traceable(async () => 'commented');\n"
                "const docs = `traceable(async () => 'documented')`;\n"
            ),
            (
                "const docs = `\n"
                'import { traceable } from "langsmith/traceable";\n'
                "traceable(async () => 'example');\n"
                "`;\n"
            ),
            (
                "function load(require) {\n"
                '  const { traceable } = require("langsmith");\n'
                "  return traceable(async () => 'local');\n"
                "}\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

    @pytest.mark.parametrize(
        "source",
        [
            (
                'import { wrapOpenAI } from "langsmith/wrappers";\n'
                "const client = wrapOpenAI(new OpenAI());\n"
            ),
            (
                'import * as wrappers from "langsmith/wrappers";\n'
                "const client = wrappers.wrapOpenAI(new OpenAI());\n"
            ),
            (
                'const { wrapOpenAI: wrap } = require("langsmith/wrappers");\n'
                "const client = wrap(new OpenAI());\n"
            ),
            (
                'const wrappers = require("langsmith/wrappers");\n'
                "const client = wrappers.wrapOpenAI(new OpenAI());\n"
            ),
        ],
    )
    def test_javascript_official_langsmith_openai_wrapper(self, source: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["langsmith"]
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            'require("langsmith/testing").traceable(() => 1);\n',
            (
                "function require() { return { traceable: (fn) => fn }; }\n"
                'require("langsmith").traceable(() => 1);\n'
            ),
            (
                'import { wrapAnthropic } from "langsmith/wrappers";\n'
                "wrapAnthropic(new Anthropic());\n"
            ),
            (
                'import { wrapOpenAI } from "local/wrappers";\n'
                "wrapOpenAI(new OpenAI());\n"
            ),
        ],
    )
    def test_javascript_langsmith_new_forms_require_exact_provenance(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names=set()
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        "source",
        [
            'import { traceable } from "langsmith/testing";\ntraceable(() => 1);\n',
            'import * as ls from "langsmith/testing";\nls.traceable(() => 1);\n',
            'const ls = require("langsmith/testing");\nls.traceable(() => 1);\n',
            (
                'import { trace } from "@opentelemetry/testing";\n'
                'trace.getTracer("agent");\n'
            ),
            (
                'import * as otel from "@opentelemetry/testing";\n'
                'otel.trace.getTracer("agent");\n'
            ),
        ],
    )
    def test_javascript_instrumentation_exports_require_canonical_packages(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names=set()
        )

        assert instrumentation is None

    def test_javascript_langsmith_alias_must_be_unshadowed(self) -> None:
        inactive_sources = (
            (
                'import { traceable } from "langsmith/traceable";\n'
                "function run(traceable) { return traceable(() => 'local'); }\n"
            ),
            (
                'import * as ls from "langsmith";\n'
                "function run() {\n"
                "  const ls = { traceable: (fn) => fn };\n"
                "  return ls.traceable(() => 'local');\n"
                "}\n"
            ),
            (
                'import { traceable } from "langsmith/traceable";\n'
                "traceable = (fn) => fn;\n"
                "const run = traceable(() => 'local');\n"
            ),
            (
                'import { traceable } from "langsmith/traceable";\n'
                "class Runner { run(traceable) { return traceable(() => 'local'); } }\n"
            ),
            (
                'import { traceable } from "langsmith/traceable";\n'
                "const runner = { run(traceable) { return traceable(() => 'local'); } };\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.ts",
                    'import { traceable } from "langsmith/traceable";\n'
                    "const run = traceable(() => 'observed');\n"
                    "function helper(traceable) { return traceable; }\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

    def test_javascript_commonjs_binding_must_be_used_in_declaration_scope(self) -> None:
        inactive_sources = (
            (
                "function setup() {\n"
                '  const { traceable } = require("langsmith");\n'
                "}\n"
                "traceable(() => 'out-of-scope');\n"
            ),
            (
                "function setup() {\n"
                '  const tracer = require("dd-trace");\n'
                "}\n"
                "tracer.init();\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.js", source)], dep_names=set()
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.js",
                    "function setup() {\n"
                    '  const { traceable } = require("langsmith");\n'
                    "  return traceable(() => 'observed');\n"
                    "}\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

    def test_trusted_python_members_must_remain_live(self) -> None:
        inactive_sources = (
            (
                "import langsmith as ls\n"
                "ls.traceable = lambda fn: fn\n"
                "run = ls.traceable(lambda: None)\n"
            ),
            (
                "import langsmith as ls\n"
                "del ls.traceable\n"
                "run = ls.traceable(lambda: None)\n"
            ),
            (
                "import langsmith as ls\n"
                "setattr(ls, 'traceable', lambda fn: fn)\n"
                "run = ls.traceable(lambda: None)\n"
            ),
            (
                "import langsmith as ls\n"
                "delattr(ls, 'traceable')\n"
                "run = ls.traceable(lambda: None)\n"
            ),
            (
                "from opentelemetry import trace\n"
                "trace.get_tracer = local_get_tracer\n"
                "trace.get_tracer(__name__)\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.py", source)], dep_names=set()
            )
            assert instrumentation is None or instrumentation.tracing_enabled is not True

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "import langsmith as ls\n"
                    "ls.Client = local_client\n"
                    "run = ls.traceable(lambda: None)\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

        shadowed_builtin = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "import langsmith as ls\n"
                    "def setattr(*args):\n"
                    "    return None\n"
                    "setattr(ls, 'traceable', local)\n"
                    "run = ls.traceable(lambda: None)\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert shadowed_builtin is not None
        assert shadowed_builtin.tracing_enabled is True

    def test_trusted_javascript_members_must_remain_live(self) -> None:
        inactive_sources = (
            (
                'const ls = require("langsmith");\n'
                "ls.traceable = (fn) => fn;\n"
                "ls.traceable(() => 'local');\n"
            ),
            (
                'const ls = require("langsmith");\n'
                "delete ls.traceable;\n"
                "ls.traceable(() => 'local');\n"
            ),
            (
                'const ls = require("langsmith");\n'
                'ls["traceable"] = (fn) => fn;\n'
                "ls.traceable(() => 'local');\n"
            ),
            (
                'const ls = require("langsmith");\n'
                'delete ls["traceable"];\n'
                "ls.traceable(() => 'local');\n"
            ),
            (
                'const otel = require("@opentelemetry/api");\n'
                "otel.trace.getTracer = () => localTracer;\n"
                'otel.trace.getTracer("agent");\n'
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.js", source)], dep_names=set()
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.js",
                    'const ls = require("langsmith");\n'
                    'ls["Client"] = LocalClient;\n'
                    "ls[\"traceable\"](() => 'observed');\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert active is not None
        assert active.tracing_enabled is True

    def test_javascript_traceable_method_declarations_are_not_calls(self) -> None:
        inactive_sources = (
            (
                'import { traceable } from "langsmith/traceable";\n'
                "class Runner { traceable() { return 'local'; } }\n"
            ),
            (
                'import { traceable } from "langsmith/traceable";\n'
                "const runner = { traceable() { return 'local'; } };\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

    @pytest.mark.parametrize(
        "source",
        [
            (
                'import { RunTree as ObservedRun } from "langsmith";\n'
                "const run = new ObservedRun({ name: 'agent' });\n"
            ),
            (
                'import * as ls from "langsmith";\n'
                "const run = new ls.RunTree({ name: 'agent' });\n"
            ),
        ],
    )
    def test_javascript_runtree_construction_requires_import_provenance(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["langsmith"]
        assert instrumentation.tracing_enabled is True

    def test_javascript_regex_literals_do_not_supply_runtime_evidence(self) -> None:
        inactive_sources = (
            (
                "const pattern = "
                r"/import\s*\{\s*traceable\s*\}\s*from\s*['\"]langsmith['\"];\s*traceable\(/;"
                "\n"
            ),
            (
                "const pattern = "
                r"/require\(['\"]dd-trace['\"]\)\.init\(\)/;"
                "\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.js", source)], dep_names=set()
            )
            assert instrumentation is None

    def test_javascript_template_expressions_remain_executable(self) -> None:
        langsmith = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.ts",
                    'import { traceable } from "langsmith/traceable";\n'
                    "const label = `run:${traceable(() => 'observed')}`;\n",
                )
            ],
            dep_names={"langsmith"},
        )
        assert langsmith is not None
        assert langsmith.tools == ["langsmith"]
        assert langsmith.tracing_enabled is True

        datadog = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.js",
                    'const label = `trace:${require("dd-trace").init()}`;\n',
                )
            ],
            dep_names=set(),
        )
        assert datadog is not None
        assert datadog.tools == ["datadog"]
        assert datadog.tracing_enabled is True

    def test_config_detection_ignores_inert_examples_and_arbitrary_keys(self) -> None:
        inactive_files = (
            ("config.yaml", "# environment:\n#   LANGSMITH_TRACING: true\n"),
            (
                "config.yaml",
                "docs: |\n  environment:\n    LANGSMITH_TRACING: true\n",
            ),
            (
                "config.yaml",
                'docs: "Set this in production:\nenvironment:\n'
                '  LANGSMITH_TRACING: true\n"\n',
            ),
            ("config.yaml", "LANGSMITH_TRACING: true\n"),
            (
                "config.yaml",
                'environment: "{LANGSMITH_TRACING: true}"\n',
            ),
            (
                "config.yaml",
                "environment: '[LANGSMITH_TRACING=true]'\n",
            ),
            ("config.json", '{"LANGSMITH_TRACING": true}\n'),
            (
                ".env",
                'EXAMPLE="Set this in production:\nLANGSMITH_TRACING=true\n"\n',
            ),
            (
                "agent.ts",
                "const example = `\nprocess.env.LANGSMITH_TRACING = 'true';\n`;\n",
            ),
        )
        for path, source in inactive_files:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[(path, source)], dep_names={"langsmith"}
            )
            assert instrumentation is None

        active_files = (
            ("compose.yaml", 'environment:\n  LANGSMITH_TRACING: "true"\n'),
            ("compose.yaml", "environment: {LANGSMITH_TRACING: true}\n"),
            ("compose.yaml", "environment: [LANGSMITH_TRACING=true]\n"),
            ("config.json", '{"environment": {"LANGSMITH_TRACING": true}}\n'),
            ("config.json", '{"environment": ["LANGSMITH_TRACING=true"]}\n'),
            ("agent.ts", "process.env.LANGSMITH_TRACING = 'true';\n"),
        )
        for path, source in active_files:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[(path, source)], dep_names={"langsmith"}
            )
            assert instrumentation is not None
            assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            (
                "function configure(process) {\n"
                "  process.env.LANGSMITH_TRACING = 'true';\n"
                "}\n"
            ),
            (
                "const process = { env: {} };\n"
                "process.env.LANGSMITH_TRACING = 'true';\n"
            ),
            (
                'import process from "local-process";\n'
                "process.env.LANGSMITH_TRACING = 'true';\n"
            ),
        ],
    )
    def test_javascript_langsmith_env_requires_global_process(self, source: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        "source",
        [
            (
                'import proc from "node:process";\n'
                'proc.env["LANGSMITH_TRACING"] = "true";\n'
            ),
            (
                'import * as proc from "node:process";\n'
                'proc["env"].LANGSMITH_TRACING_V2 = "yes";\n'
            ),
            (
                'const proc = require("process");\n'
                'proc["env"]["LANGCHAIN_TRACING_V2"] = 1;\n'
            ),
            (
                'import { env as runtimeEnv } from "node:process";\n'
                'runtimeEnv["LANGSMITH_TRACING"] = "true";\n'
            ),
            (
                'const { env: runtimeEnv } = require("node:process");\n'
                'runtimeEnv.LANGSMITH_TRACING = "on";\n'
            ),
        ],
    )
    def test_javascript_langsmith_env_accepts_canonical_process_modules(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            (
                'const proc = require("local-process");\n'
                'proc.env["LANGSMITH_TRACING"] = "true";\n'
            ),
            (
                "function require() { return process; }\n"
                'const proc = require("node:process");\n'
                'proc.env["LANGSMITH_TRACING"] = "true";\n'
            ),
            (
                'import proc from "node:process";\n'
                'proc.env["OTHER_TRACING"] = "true";\n'
            ),
        ],
    )
    def test_javascript_langsmith_env_rejects_lookalike_process_forms(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        ("dependency", "tool"),
        [
            ("opentelemetry", "opentelemetry"),
            ("ddtrace", "datadog"),
            ("jaeger", "jaeger"),
        ],
    )
    def test_tracing_dependency_alone_does_not_enable_tracing(
        self, dependency: str, tool: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[], dep_names={dependency}
        )

        assert instrumentation is not None
        assert instrumentation.tools == [tool]
        assert instrumentation.tracing_enabled is None
        findings = self._run(
            [_agent_node("orchestrator")],
            {"instrumentation": instrumentation.model_dump(mode="json")},
        )
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-009"

    @pytest.mark.parametrize(
        ("dependency", "source"),
        [
            (
                "opentelemetry",
                "from opentelemetry import trace\ntracer = trace.get_tracer(__name__)\n",
            ),
            (
                "ddtrace",
                "from ddtrace import tracer\ntracer.configure(hostname='collector')\n",
            ),
            (
                "jaeger",
                "from jaeger_client import Config\n"
                "tracer = Config({}).initialize_tracer()\n",
            ),
        ],
    )
    def test_active_tracing_runtime_enables_tracing(
        self, dependency: str, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names={dependency}
        )

        assert instrumentation is not None
        assert instrumentation.tracing_enabled is True

    def test_python_tracing_binding_must_remain_live_in_its_scope(self) -> None:
        inactive_sources = (
            (
                "from ddtrace import tracer\n"
                "def configure(tracer):\n"
                "    tracer.configure(hostname='local')\n"
            ),
            (
                "from ddtrace import tracer\n"
                "def configure():\n"
                "    tracer = local_tracer\n"
                "    tracer.configure(hostname='local')\n"
            ),
            (
                "from opentelemetry import trace\n"
                "trace = local_trace\n"
                "trace.get_tracer(__name__)\n"
            ),
            (
                "from opentelemetry import trace\n"
                "trace.unrelated.get_tracer(__name__)\n"
            ),
            (
                "from ddtrace import tracer\n"
                "tracer.unrelated.configure(hostname='local')\n"
            ),
            (
                "from opentelemetry import trace\n"
                "def replace():\n"
                "    global trace\n"
                "    trace = local_trace\n"
                "    trace.get_tracer(__name__)\n"
            ),
            (
                "def outer():\n"
                "    from opentelemetry import trace\n"
                "    def replace():\n"
                "        nonlocal trace\n"
                "        trace = local_trace\n"
                "        trace.get_tracer(__name__)\n"
                "    return replace\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.py", source)], dep_names=set()
            )
            assert instrumentation is not None
            assert instrumentation.tracing_enabled is None

        unrelated = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "import datadogger\ndatadogger.tracer.configure(hostname='local')\n",
                )
            ],
            dep_names=set(),
        )
        assert unrelated is None

    @pytest.mark.parametrize(
        ("source", "tool"),
        [
            (
                'import tracer from "dd-trace";\ntracer.init();\n',
                "datadog",
            ),
            (
                'import { trace as telemetry } from "@opentelemetry/api";\n'
                'telemetry.getTracer("agent");\n',
                "opentelemetry",
            ),
            (
                'import * as otel from "@opentelemetry/api";\n'
                'otel.trace.getTracer("agent");\n',
                "opentelemetry",
            ),
            (
                'import initTracer from "jaeger-client";\ninitTracer({});\n',
                "jaeger",
            ),
        ],
    )
    def test_javascript_tracing_requires_bound_import_and_merges_tool(
        self, source: str, tool: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names=set()
        )

        assert instrumentation is not None
        assert instrumentation.tools == [tool]
        assert instrumentation.tracing_enabled is True

    def test_javascript_unrelated_runtime_call_does_not_enable_tracing(self) -> None:
        inactive_sources = (
            'import tracer from "dd-trace";\nunrelated.init();\n',
            (
                'import { trace as telemetry } from "@opentelemetry/api";\n'
                'unrelated.getTracer("agent");\n'
            ),
            "const tracer = { init() {} };\ntracer.init();\n",
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.ts", source)], dep_names=set()
            )
            assert instrumentation is None

    def test_javascript_tracing_aliases_and_member_paths_are_exact(self) -> None:
        inactive_sources = (
            (
                'import tracer from "dd-trace";\n'
                "function configure(tracer) { tracer.init(); }\n"
            ),
            (
                'import tracer from "dd-trace";\n'
                "tracer.unrelated.init();\n"
            ),
            (
                'import * as otel from "@opentelemetry/api";\n'
                "otel.unrelated.getTracer('agent');\n"
            ),
            (
                'import { trace as telemetry } from "@opentelemetry/api";\n'
                "function configure() {\n"
                "  const telemetry = { getTracer() {} };\n"
                "  telemetry.getTracer('agent');\n"
                "}\n"
            ),
            (
                'import tracer from "dd-trace";\n'
                "class Runner { configure(tracer) { tracer.init(); } }\n"
            ),
            (
                'import tracer from "dd-trace";\n'
                "const runner = { configure(tracer) { tracer.init(); } };\n"
            ),
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.ts", source)], dep_names=set()
            )
            assert instrumentation is None

        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.ts",
                    'import tracer from "dd-trace";\n'
                    "tracer.init();\n"
                    "function helper(tracer) { return tracer; }\n",
                )
            ],
            dep_names=set(),
        )
        assert active is not None
        assert active.tools == ["datadog"]
        assert active.tracing_enabled is True

    @pytest.mark.parametrize("module", ["dd-trace/init", "dd-trace/init.js"])
    def test_esm_ddtrace_side_effect_import_enables_tracing(self, module: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[],
            files=[("agent.mjs", f'import "{module}";\n')],
            dep_names=set(),
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["datadog"]
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        ("source", "tool"),
        [
            ('require("dd-trace").init();\n', "datadog"),
            ('require("dd-trace/init");\n', "datadog"),
            ('require("dd-trace/init.js");\n', "datadog"),
            (
                'require("@opentelemetry/api").trace.getTracer("agent");\n',
                "opentelemetry",
            ),
            ('require("jaeger-client").initTracer({});\n', "jaeger"),
        ],
    )
    def test_commonjs_active_tracing_requires_canonical_provenance(
        self, source: str, tool: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.js", source)], dep_names=set()
        )

        assert instrumentation is not None
        assert instrumentation.tools == [tool]
        assert instrumentation.tracing_enabled is True

    def test_commonjs_unrelated_or_inert_require_does_not_enable_tracing(self) -> None:
        inactive_sources = (
            'require("dd-trace");\nunrelated.init();\n',
            'require("unrelated").init();\n',
            'require("@opentelemetry/api");\nunrelated.getTracer("agent");\n',
            'require("@opentelemetry/api").unrelated.getTracer("agent");\n',
            'require("dd-trace").unrelated.init();\n',
            'require("dd-trace/testing");\n',
            '// require("dd-trace/init");\n',
            'const docs = "require(\\"dd-trace\\").init()";\n',
            'loader.require("dd-trace").init();\n',
            (
                "function load(require) {\n"
                '  return require("dd-trace").init();\n'
                "}\n"
            ),
            '(require) => require("dd-trace").init();\n',
        )
        for source in inactive_sources:
            instrumentation = _detect_app_instrumentation(
                nodes=[], files=[("agent.js", source)], dep_names=set()
            )
            assert instrumentation is None

    def test_python_jaeger_runtime_evidence_merges_tool_without_dependency(self) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "from jaeger_client import Config\n"
                    "tracer = Config({}).initialize_tracer()\n",
                )
            ],
            dep_names=set(),
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["jaeger"]
        assert instrumentation.tracing_enabled is True

    def test_metrics_only_instrumentation_still_fails(self) -> None:
        findings = self._run(
            [_agent_node("orchestrator")],
            {
                "instrumentation": {
                    "tools": ["prometheus"],
                    "metrics_enabled": True,
                    "tracing_enabled": None,
                }
            },
        )

        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-009"

    def test_explicitly_disabled_tracing_still_fails(self) -> None:
        findings = self._run(
            [_agent_node("orchestrator")],
            {
                "instrumentation": {
                    "tools": ["opentelemetry"],
                    "tracing_enabled": False,
                }
            },
        )

        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-009"

    @pytest.mark.parametrize(
        "source",
        [
            "from langsmith import trace\nwith trace('agent'):\n    run_agent()\n",
            (
                "from langsmith.run_helpers import trace as observed\n"
                "with observed('agent'):\n    run_agent()\n"
            ),
            (
                "from langsmith import wrappers\n"
                "client = wrappers.wrap_anthropic(client)\n"
            ),
            (
                "import langsmith.wrappers as wrappers\n"
                "client = wrappers.wrap_gemini(client)\n"
            ),
            (
                "from langsmith.wrappers import wrap_anthropic\n"
                "client = wrap_anthropic(client)\n"
            ),
            (
                "from langsmith.wrappers import wrap_gemini\n"
                "client = wrap_gemini(client)\n"
            ),
        ],
    )
    def test_supported_python_langsmith_trace_and_wrappers(self, source: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["langsmith"]
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            "from langsmith import wrap_anthropic\nwrap_anthropic(client)\n",
            "from langsmith import wrap_gemini\nwrap_gemini(client)\n",
        ],
    )
    def test_python_langsmith_wrappers_require_the_wrappers_module(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names={"langsmith"}
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        "source",
        [
            (
                'import { wrapSDK } from "langsmith/wrappers";\n'
                "const client = wrapSDK(rawClient);\n"
            ),
            (
                'import { wrapAnthropic } from "langsmith/wrappers/anthropic";\n'
                "const client = wrapAnthropic(rawClient);\n"
            ),
            (
                'import { wrapGemini } from "langsmith/wrappers/gemini";\n'
                "const client = wrapGemini(rawClient);\n"
            ),
            (
                'const { wrapAnthropic } = require("langsmith/wrappers/anthropic");\n'
                "const client = wrapAnthropic(rawClient);\n"
            ),
            'require("langsmith/wrappers").wrapSDK(rawClient);\n',
        ],
    )
    def test_supported_javascript_langsmith_wrappers(self, source: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.ts", source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tools == ["langsmith"]
        assert instrumentation.tracing_enabled is True

    def test_nested_function_binding_effects_do_not_escape_before_invocation(self) -> None:
        active = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "from langsmith import traceable\n"
                    "def replace():\n"
                    "    global traceable\n"
                    "    traceable = lambda fn: fn\n"
                    "@traceable\n"
                    "def run_agent():\n"
                    "    pass\n",
                )
            ],
            dep_names={"langsmith"},
        )
        inactive = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "def enable():\n"
                    "    global observed\n"
                    "    from langsmith import traceable as observed\n"
                    "observed(lambda: None)\n",
                )
            ],
            dep_names={"langsmith"},
        )

        assert active is not None
        assert active.tracing_enabled is True
        assert inactive is None

    def test_property_require_is_not_treated_as_langsmith_namespace(self) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.js",
                    'const wrapped = require("langsmith").traceable;\n'
                    "new wrapped.RunTree();\n",
                )
            ],
            dep_names={"langsmith"},
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        ("path", "source"),
        [
            (".env", "langsmith_tracing=true\n"),
            (
                "compose.yaml",
                "services:\n  app:\n    environment:\n      langsmith_tracing: true\n",
            ),
            ("agent.js", "process.env.langsmith_tracing = true;\n"),
            (
                "runtime.json",
                '{"environment": ["langsmith_tracing=true"]}',
            ),
        ],
    )
    def test_langsmith_environment_keys_are_case_sensitive(
        self, path: str, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[(path, source)], dep_names={"langsmith"}
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        ("path", "source"),
        [
            (".env", "LANGSMITH_TRACING=TRUE\n"),
            (
                "compose.yaml",
                "services:\n  app:\n    environment:\n      LANGSMITH_TRACING: YES\n",
            ),
            ("agent.js", 'process.env.LANGSMITH_TRACING = "ON";\n'),
            (
                "runtime.json",
                '{"environment": ["LANGSMITH_TRACING=On"]}',
            ),
        ],
    )
    def test_langsmith_environment_values_remain_case_insensitive(
        self, path: str, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[(path, source)], dep_names={"langsmith"}
        )

        assert instrumentation is not None
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            (
                "from langsmith import traceable\n"
                "import fake as traceable\n"
                "traceable(lambda: None)\n"
            ),
            (
                "from langsmith import traceable\n"
                "from fake import traceable\n"
                "traceable(lambda: None)\n"
            ),
            (
                "import langsmith as ls\n"
                "import fake as ls\n"
                "ls.traceable(lambda: None)\n"
            ),
            (
                "from opentelemetry import trace\n"
                "from fake import trace\n"
                "trace.get_tracer(__name__)\n"
            ),
        ],
    )
    def test_python_instrumentation_import_rebindings_are_respected(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names=set()
        )

        assert instrumentation is None or instrumentation.tracing_enabled is None

    def test_python_trusted_reimport_resets_member_invalidation(self) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[],
            files=[
                (
                    "agent.py",
                    "import langsmith as ls\n"
                    "ls.traceable = local_traceable\n"
                    "import langsmith as ls\n"
                    "ls.traceable(lambda: None)\n",
                )
            ],
            dep_names=set(),
        )

        assert instrumentation is not None
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "source",
        [
            "from langsmith import ensure_traceable\nensure_traceable(fn)\n",
            "from langsmith import wrap_openai\nwrap_openai(client)\n",
            "import langsmith as ls\nls.wrappers.wrap_openai(client)\n",
            "import langchain_core as lc\nlc.tracers.LangChainTracer()\n",
        ],
    )
    def test_python_langsmith_root_requires_real_exports_or_loaded_submodules(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names=set()
        )

        assert instrumentation is None

    @pytest.mark.parametrize(
        "source",
        [
            "import langsmith.wrappers\nlangsmith.wrappers.wrap_openai(client)\n",
            "import langchain_core.tracers\nlangchain_core.tracers.LangChainTracer()\n",
        ],
    )
    def test_python_explicitly_loaded_langsmith_submodules_are_active(
        self, source: str
    ) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[], files=[("agent.py", source)], dep_names=set()
        )

        assert instrumentation is not None
        assert instrumentation.tracing_enabled is True

    @pytest.mark.parametrize(
        "path",
        [
            ".env.sample",
            ".env.template",
            ".env.demo",
            ".env.production.sample",
        ],
    )
    def test_dotenv_templates_do_not_enable_langsmith(self, path: str) -> None:
        instrumentation = _detect_app_instrumentation(
            nodes=[],
            files=[(path, "LANGSMITH_TRACING=true\n")],
            dep_names={"langsmith"},
        )

        assert instrumentation is None

    def test_full_extraction_keeps_instrumentation_after_content_sample(
        self, tmp_path: Any
    ) -> None:
        filler = tmp_path / "aaa"
        filler.mkdir()
        for index in range(205):
            (filler / f"module_{index:03}.py").write_text(
                f"VALUE = {index}\n", encoding="utf-8"
            )
        production = tmp_path / "zzz" / "middleware"
        production.mkdir(parents=True)
        (production / "tracing.py").write_text(
            "from deepagents import create_deep_agent\n"
            "from langsmith.run_helpers import get_current_run_tree\n"
            "agent = create_deep_agent(name='design-agent', model='openai:gpt-4o', tools=[])\n"
            "run = get_current_run_tree()\n",
            encoding="utf-8",
        )

        document = AiSbomExtractor().extract_from_path(
            tmp_path,
            AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=300),
        )

        assert document.summary is not None
        assert document.summary.instrumentation is not None
        assert document.summary.instrumentation.tools == ["langsmith"]
        assert document.summary.instrumentation.tracing_enabled is True
        findings = self._run(
            [_agent_node("design-agent")],
            {"instrumentation": document.summary.instrumentation.model_dump(mode="json")},
        )
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-010 — GitHub Actions: pull_request_target with untrusted context injection
# ---------------------------------------------------------------------------


class TestNga010StructuredFindings:
    def _run(
        self,
        nodes: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga010_pr_target_injection(nodes=nodes or [], summary=summary or {})

    def test_fires_from_structured_findings(self) -> None:
        """Structured finding with rule_signal NGA-010 → finding with path/line in description."""
        summary: dict[str, Any] = {
            "workflow_security_findings": [
                {
                    "rule_signal": "NGA-010",
                    "path": "ci.yml",
                    "line": 5,
                    "snippet": "pull_request_target...${{ github.event.pull_request.head.sha }}",
                    "confidence": 0.9,
                }
            ]
        }
        findings = self._run(summary=summary)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-010"
        assert "ci.yml" in findings[0]["description"]
        assert "line 5" in findings[0]["description"]

    def test_fires_from_raw_content_fallback(self) -> None:
        """No structured findings but github_actions_content has injection pattern → finding."""
        summary: dict[str, Any] = {
            "github_actions_content": (
                "on:\n  pull_request_target:\njobs:\n  build:\n    steps:\n"
                "      - run: echo ${{ github.event.pull_request.head.sha }}"
            )
        }
        findings = self._run(summary=summary)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-010"

    def test_passes_when_no_workflow(self) -> None:
        """No structured findings and no raw content → empty list."""
        findings = self._run(summary={})
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-011 — GitHub Actions: GITHUB_ENV written from untrusted input
# ---------------------------------------------------------------------------


class TestNga011StructuredFindings:
    def _run(
        self,
        nodes: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga011_github_env_injection(nodes=nodes or [], summary=summary or {})

    def test_fires_from_structured_findings(self) -> None:
        """Structured finding with rule_signal NGA-011 → finding with path/line in description."""
        summary: dict[str, Any] = {
            "workflow_security_findings": [
                {
                    "rule_signal": "NGA-011",
                    "path": "deploy.yml",
                    "line": 12,
                    "snippet": 'echo "${{ github.event.issue.title }}" >> $GITHUB_ENV',
                    "confidence": 0.9,
                }
            ]
        }
        findings = self._run(summary=summary)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-011"
        assert "deploy.yml" in findings[0]["description"]
        assert "line 12" in findings[0]["description"]

    def test_fires_from_raw_content_fallback(self) -> None:
        """No structured findings but github_actions_content has env injection → finding."""
        summary: dict[str, Any] = {
            "github_actions_content": (
                'echo "${{ github.event.issue.title }}" >> $GITHUB_ENV'
            )
        }
        findings = self._run(summary=summary)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-011"

    def test_passes_when_no_workflow(self) -> None:
        """No structured findings and no raw content → empty list."""
        findings = self._run(summary={})
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-013 — No Kubernetes NetworkPolicy for AI workload
# ---------------------------------------------------------------------------


def _k8s_deployment_node(
    name: str = "ai-service",
    namespace: str = "default",
    has_network_policy: bool | None = None,
) -> dict[str, Any]:
    """Return a minimal K8s DEPLOYMENT node dict as seen by the analysis layer."""
    meta: dict[str, Any] = {
        "deployment_target": "kubernetes",
        "extras": {"k8s_namespace": namespace},
    }
    if has_network_policy is not None:
        meta["has_network_policy"] = has_network_policy
    return {
        "id": name,
        "name": name,
        "component_type": "DEPLOYMENT",
        "metadata": meta,
    }


def _k8s_marker_node(namespace: str = "default") -> dict[str, Any]:
    """Return a NetworkPolicy marker node dict as emitted by K8sAdapter (after serialization)."""
    return {
        "id": f"netpol:k8s:{namespace}",
        "name": f"NetworkPolicy:{namespace}",
        "component_type": "DEPLOYMENT",
        "metadata": {
            # iac_format at top-level tests the is_network_policy_namespace filter
            "iac_format": "kubernetes",
            "extras": {
                "is_network_policy_namespace": True,
                "k8s_namespace": namespace,
            },
        },
    }


class TestNga013CrossFileNetworkPolicy:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga013_no_k8s_network_policy(nodes=nodes, summary=summary or {})

    def test_passes_with_node_has_network_policy(self) -> None:
        """K8s DEPLOYMENT node with has_network_policy=True → no finding."""
        nodes = [_k8s_deployment_node("ai-service", "prod", has_network_policy=True)]
        findings = self._run(nodes)
        assert findings == []

    def test_passes_with_summary_namespace(self) -> None:
        """Workload namespace in summary['k8s_network_policy_namespaces'] → no finding."""
        nodes = [_k8s_deployment_node("ai-service", "prod")]
        summary: dict[str, Any] = {"k8s_network_policy_namespaces": ["prod"]}
        findings = self._run(nodes, summary)
        assert findings == []

    def test_fails_without_any_coverage(self) -> None:
        """K8s DEPLOYMENT with no same-file policy and namespace not in summary → finding."""
        nodes = [_k8s_deployment_node("ai-service", "default")]
        findings = self._run(nodes)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-013"
        assert findings[0]["severity"] == "MEDIUM"
        assert "ai-service" in findings[0]["affected"]

    def test_ignores_marker_nodes(self) -> None:
        """Only a NetworkPolicy marker node (is_network_policy_namespace=True) → no workloads → empty."""
        nodes = [_k8s_marker_node("prod")]
        findings = self._run(nodes)
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-014 — GitHub Actions: ACTIONS_RUNNER_DEBUG secret exposed
# ---------------------------------------------------------------------------


class TestNga014StructuredFindings:
    def _run(
        self,
        nodes: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga014_actions_runner_debug(nodes=nodes or [], summary=summary or {})

    def test_fires_from_structured_findings(self) -> None:
        """Structured finding with rule_signal NGA-014 → finding with path/line in description."""
        summary: dict[str, Any] = {
            "workflow_security_findings": [
                {
                    "rule_signal": "NGA-014",
                    "path": "debug.yml",
                    "line": 8,
                    "snippet": "ACTIONS_RUNNER_DEBUG: ${{ secrets.ACTIONS_RUNNER_DEBUG }}",
                    "confidence": 0.9,
                }
            ]
        }
        findings = self._run(summary=summary)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-014"
        assert "debug.yml" in findings[0]["description"]
        assert "line 8" in findings[0]["description"]

    def test_fires_from_raw_content_fallback(self) -> None:
        """No structured findings but github_actions_content references ACTIONS_RUNNER_DEBUG → finding."""
        summary: dict[str, Any] = {
            "github_actions_content": "env:\n  ACTIONS_RUNNER_DEBUG: true\n"
        }
        findings = self._run(summary=summary)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-014"

    def test_passes_when_no_workflow(self) -> None:
        """No structured findings and no raw content → empty list."""
        findings = self._run(summary={})
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-015 — AI workloads without resource limits (_has_no_limits asymmetry fix)
# ---------------------------------------------------------------------------


class TestNga015ResourceLimitsAsymmetry:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        security_findings: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {"security_findings": security_findings or []}
        return _rule_nga015_no_resource_limits(nodes=nodes, summary=summary)

    def test_fires_when_has_resource_limits_false(self) -> None:
        """DEPLOYMENT with metadata.has_resource_limits=False triggers NGA-015."""
        nodes = [
            {
                "id": "svc",
                "name": "svc",
                "component_type": "DEPLOYMENT",
                "metadata": {"has_resource_limits": False},
            }
        ]
        findings = self._run(nodes)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-015"
        assert "svc" in findings[0]["affected"]

    def test_fires_when_no_resource_limits_true(self) -> None:
        """DEPLOYMENT with extras.no_resource_limits=True triggers NGA-015."""
        nodes = [
            {
                "id": "svc",
                "name": "svc",
                "component_type": "DEPLOYMENT",
                "metadata": {"extras": {"no_resource_limits": True}},
            }
        ]
        findings = self._run(nodes)
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-015"

    def test_passes_when_has_resource_limits_true(self) -> None:
        """DEPLOYMENT with metadata.has_resource_limits=True → no finding."""
        nodes = [
            {
                "id": "svc",
                "name": "svc",
                "component_type": "DEPLOYMENT",
                "metadata": {"has_resource_limits": True},
            }
        ]
        findings = self._run(nodes)
        assert findings == []


# ---------------------------------------------------------------------------
# NGA-021 — IDOR-prone endpoint without authorization checks
# ---------------------------------------------------------------------------


class TestNga021:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        graph = AnalysisGraph({"nodes": nodes, "edges": edges or []})
        return _rule_nga021_idor_surface_no_protection(nodes=nodes, graph=graph)

    def test_fires_on_idor_surface_endpoint_with_no_protection(self) -> None:
        node = {
            "id": "api",
            "name": "get-user-profile",
            "component_type": "API_ENDPOINT",
            "metadata": {"idor_surface": True, "path_params": ["user_id"]},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-021"
        assert findings[0]["severity"] == "HIGH"
        assert "get-user-profile" in findings[0]["affected"]

    def test_no_finding_when_protected_by_auth_edge(self) -> None:
        auth_node = {"id": "auth", "name": "auth", "component_type": "AUTH", "metadata": {}}
        api_node = {
            "id": "api",
            "name": "get-user-profile",
            "component_type": "API_ENDPOINT",
            "metadata": {"idor_surface": True, "path_params": ["user_id"]},
        }
        edge = {"source": "auth", "target": "api", "relationship_type": "PROTECTS"}
        findings = self._run([auth_node, api_node], edges=[edge])
        assert findings == []

    def test_no_finding_when_protected_by_guardrail_edge(self) -> None:
        guardrail = {"id": "guard", "name": "guard", "component_type": "GUARDRAIL", "metadata": {}}
        api_node = {
            "id": "api",
            "name": "get-user-profile",
            "component_type": "API_ENDPOINT",
            "metadata": {"idor_surface": True, "path_params": ["user_id"]},
        }
        edge = {"source": "guard", "target": "api", "relationship_type": "PROTECTS"}
        findings = self._run([guardrail, api_node], edges=[edge])
        assert findings == []

    def test_no_finding_when_idor_surface_false(self) -> None:
        node = {
            "id": "api",
            "name": "list-public-posts",
            "component_type": "API_ENDPOINT",
            "metadata": {"idor_surface": False},
        }
        findings = self._run([node])
        assert findings == []

    def test_no_finding_when_idor_surface_none(self) -> None:
        node = _api_node("plain-endpoint")
        findings = self._run([node])
        assert findings == []

    def test_no_finding_no_api_nodes(self) -> None:
        findings = self._run([_model_node("gpt-4", "openai")])
        assert findings == []

    def test_no_finding_without_graph(self) -> None:
        """Rule requires graph traversal; returns [] gracefully when graph is None."""
        node = {
            "id": "api",
            "name": "get-user-profile",
            "component_type": "API_ENDPOINT",
            "metadata": {"idor_surface": True, "path_params": ["user_id"]},
        }
        assert _rule_nga021_idor_surface_no_protection(nodes=[node], graph=None) == []


# ---------------------------------------------------------------------------
# NGA-022 — Tool sourced from an untrusted MCP server
# ---------------------------------------------------------------------------


def _mcp_tool_node(
    name: str = "search_web", trust_level: str = "untrusted", mcp_server_url: str = "https://evil.example/mcp",
) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "component_type": "TOOL",
        "metadata": {"mcp_server_url": mcp_server_url, "trust_level": trust_level},
    }


class TestNga022:
    def _run(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        graph = AnalysisGraph({"nodes": nodes, "edges": edges or []})
        return _rule_nga022_untrusted_mcp_tool(nodes=nodes, graph=graph)

    def test_fires_on_untrusted_mcp_tool_no_guardrail(self) -> None:
        node = _mcp_tool_node()
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-022"
        assert findings[0]["severity"] == "HIGH"
        assert "search_web" in findings[0]["affected"]

    def test_no_finding_when_trusted(self) -> None:
        node = _mcp_tool_node(trust_level="trusted")
        findings = self._run([node])
        assert findings == []

    def test_no_finding_when_not_mcp_tool(self) -> None:
        findings = self._run([_tool_node("get_weather")])
        assert findings == []

    def test_no_finding_when_protected_by_guardrail(self) -> None:
        node = _mcp_tool_node()
        guardrail = {"id": "guard", "name": "guard", "component_type": "GUARDRAIL", "metadata": {}}
        edge = {"source": "guard", "target": "search_web", "relationship_type": "PROTECTS"}
        findings = self._run([node, guardrail], edges=[edge])
        assert findings == []

    def test_works_without_graph(self) -> None:
        node = _mcp_tool_node()
        findings = _rule_nga022_untrusted_mcp_tool(nodes=[node], graph=None)
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# NGA-023 — Vector/embedding store without auth or encryption
# ---------------------------------------------------------------------------


class TestNga023:
    def _run(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _rule_nga023_unprotected_vector_store(nodes=nodes)

    def test_fires_on_unprotected_pinecone_store(self) -> None:
        node = _datastore_node("embeddings-db", datastore_type="pinecone")
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-023"
        assert "embeddings-db" in findings[0]["affected"]

    def test_no_finding_when_auth_and_encryption_present(self) -> None:
        node = _datastore_node(
            "embeddings-db", datastore_type="chroma",
            auth_detail={"protocol": "api_key"}, encryption_detail={"at_rest": True},
        )
        findings = self._run([node])
        assert findings == []

    def test_no_finding_for_non_vector_datastore(self) -> None:
        node = _datastore_node("orders-db", datastore_type="postgresql")
        findings = self._run([node])
        assert findings == []

    def test_no_finding_no_datastores(self) -> None:
        assert self._run([]) == []


# ---------------------------------------------------------------------------
# NGA-024 — Unauthenticated inter-agent delegation
# ---------------------------------------------------------------------------


class TestNga024:
    def _run(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        graph = AnalysisGraph({"nodes": nodes, "edges": edges or []})
        return _rule_nga024_unauthenticated_agent_delegation(graph=graph)

    def test_fires_on_delegation_with_no_auth(self) -> None:
        src = _agent_node("orchestrator")
        tgt = _agent_node("worker")
        edge = {"source": "orchestrator", "target": "worker", "relationship_type": "DELEGATES_TO"}
        findings = self._run([src, tgt], edges=[edge])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-024"
        assert "orchestrator" in findings[0]["affected"]
        assert "worker" in findings[0]["affected"]

    def test_no_finding_when_target_has_auth_detail(self) -> None:
        src = _agent_node("orchestrator")
        tgt = {
            "id": "worker", "name": "worker", "component_type": "AGENT",
            "metadata": {"auth_detail": {"protocol": "mtls"}},
        }
        edge = {"source": "orchestrator", "target": "worker", "relationship_type": "DELEGATES_TO"}
        findings = self._run([src, tgt], edges=[edge])
        assert findings == []

    def test_no_finding_without_delegation(self) -> None:
        findings = self._run([_agent_node("solo")])
        assert findings == []

    def test_no_finding_without_graph(self) -> None:
        assert _rule_nga024_unauthenticated_agent_delegation(graph=None) == []


# ---------------------------------------------------------------------------
# NGA-025 — Credential embedded in system prompt / hidden context
# ---------------------------------------------------------------------------


class TestNga025:
    def _run(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _rule_nga025_hidden_context_secret_leak(nodes=nodes)

    def test_fires_on_prompt_with_aws_key(self) -> None:
        node = {
            "id": "sys-prompt", "name": "system-prompt", "component_type": "PROMPT",
            "metadata": {"extras": {"content": "Use key AKIAABCDEFGHIJKLMNOP to call the service."}},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-025"
        assert "system-prompt" in findings[0]["affected"]

    def test_fires_on_agent_system_prompt_excerpt(self) -> None:
        node = {
            "id": "agent1", "name": "billing-agent", "component_type": "AGENT",
            "metadata": {"system_prompt_excerpt": "api_key: 'sk-abcdefghijklmnopqrstuvwx'"},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert "billing-agent" in findings[0]["affected"]

    def test_no_finding_on_benign_prompt(self) -> None:
        node = {
            "id": "sys-prompt", "name": "system-prompt", "component_type": "PROMPT",
            "metadata": {"extras": {"content": "You are a helpful assistant that answers questions."}},
        }
        findings = self._run([node])
        assert findings == []

    def test_no_finding_no_prompt_or_agent_nodes(self) -> None:
        assert self._run([_tool_node("get_weather")]) == []


# ---------------------------------------------------------------------------
# NGA-026 — AI endpoint without application-level rate limiting
# ---------------------------------------------------------------------------


class TestNga026:
    def _run(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _rule_nga026_endpoint_no_rate_limit(nodes=nodes)

    def test_fires_on_endpoint_with_no_rate_limit(self) -> None:
        node = _api_node("chat-completions")
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-026"
        assert "chat-completions" in findings[0]["affected"]

    def test_no_finding_when_rate_limited(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"rate_limited": True},
        }
        findings = self._run([node])
        assert findings == []

    def test_no_finding_when_rate_limit_detail_present(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"rate_limit_detail": {"requests_per_minute": 60}},
        }
        findings = self._run([node])
        assert findings == []

    def test_no_finding_no_api_endpoints(self) -> None:
        assert self._run([_model_node("gpt-4", "openai")]) == []


# ---------------------------------------------------------------------------
# NGA-027 — AI endpoint missing security headers
# ---------------------------------------------------------------------------


class TestNga027:
    def _run(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _rule_nga027_missing_security_headers(nodes=nodes)

    def test_fires_when_security_headers_detail_absent(self) -> None:
        node = _api_node("chat-completions")
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-027"
        assert "chat-completions" in findings[0]["affected"]

    def test_fires_when_headers_confirmed_missing(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"security_headers_detail": {"missing": ["csp"]}},
        }
        findings = self._run([node])
        assert len(findings) == 1

    def test_no_finding_when_headers_confirmed_present(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"security_headers_detail": {"csp": True, "missing": []}},
        }
        assert self._run([node]) == []

    def test_no_finding_no_api_endpoints(self) -> None:
        assert self._run([_model_node("gpt-4", "openai")]) == []


# ---------------------------------------------------------------------------
# NGA-028 — API endpoint has an overly permissive CORS policy
# ---------------------------------------------------------------------------


class TestNga028:
    def _run(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _rule_nga028_permissive_cors(nodes=nodes)

    def test_fires_on_wildcard_origin(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"cors_policy": {"origin": "*"}},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-028"
        assert findings[0]["severity"] == "MEDIUM"

    def test_high_severity_on_wildcard_with_credentials(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"cors_policy": {"origin": "*", "wildcard_with_credentials": True}},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["severity"] == "HIGH"

    def test_no_finding_on_explicit_origin(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"cors_policy": {"origin": "https://example.com"}},
        }
        assert self._run([node]) == []

    def test_no_finding_no_cors_policy(self) -> None:
        assert self._run([_api_node("chat-completions")]) == []


# ---------------------------------------------------------------------------
# NGA-029 — API endpoint's error handler leaks stack traces
# ---------------------------------------------------------------------------


class TestNga029:
    def _run(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _rule_nga029_verbose_error_leak(nodes=nodes)

    def test_fires_when_debug_error_leak_true(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"debug_error_leak": True},
        }
        findings = self._run([node])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-029"

    def test_no_finding_when_debug_error_leak_false(self) -> None:
        node = {
            "id": "api", "name": "chat-completions", "component_type": "API_ENDPOINT",
            "metadata": {"debug_error_leak": False},
        }
        assert self._run([node]) == []


# ---------------------------------------------------------------------------
# NGA-030 — JWT verification with no pinned algorithm allow-list
# ---------------------------------------------------------------------------


class TestNga030:
    def _run(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _rule_nga030_jwt_no_algorithm_restriction(nodes=nodes)

    def _jwt_node(self, jwt_algorithm_restricted: bool | None) -> dict[str, Any]:
        auth_detail: dict[str, Any] = {}
        if jwt_algorithm_restricted is not None:
            auth_detail["jwt_algorithm_restricted"] = jwt_algorithm_restricted
        return {
            "id": "auth", "name": "JWT", "component_type": "AUTH",
            "metadata": {"auth_type": "jwt", "auth_detail": auth_detail},
        }

    def test_fires_when_algorithm_restriction_confirmed_false(self) -> None:
        findings = self._run([self._jwt_node(False)])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-030"
        assert findings[0]["severity"] == "HIGH"

    def test_fires_when_restriction_not_confirmed(self) -> None:
        # No verify call site found at all — pessimistic default, mirrors
        # NGA-027's "not confirmed" treatment of missing security headers.
        findings = self._run([self._jwt_node(None)])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-030"

    def test_no_finding_when_algorithm_restriction_confirmed_true(self) -> None:
        assert self._run([self._jwt_node(True)]) == []

    def test_no_finding_on_non_jwt_auth(self) -> None:
        node = {
            "id": "auth", "name": "OAuth2", "component_type": "AUTH",
            "metadata": {"auth_type": "oauth2", "auth_detail": {}},
        }
        assert self._run([node]) == []

    def test_no_finding_on_non_auth_node(self) -> None:
        assert self._run([_api_node("chat-completions")]) == []

    def test_no_finding_no_api_endpoints(self) -> None:
        assert self._run([_model_node("gpt-4", "openai")]) == []
