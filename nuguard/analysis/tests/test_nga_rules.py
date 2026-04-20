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
  NGA-005  AI workloads without resource limits                    LOW
  NGA-006  Unencrypted datastore containing PII/PHI               HIGH
  NGA-007  Missing auth on external API endpoint                   HIGH
  NGA-019  Missing HITL for irreversible tool actions              HIGH
"""

from __future__ import annotations

from typing import Any

from nuguard.analysis.plugins.nga_rules import (
    _RULES,
    _rule_nga001_phi_to_external_llm,
    _rule_nga002_insufficient_guardrails,
    _rule_nga003_secrets_in_env,
    _rule_nga004_runs_as_root,
    _rule_nga005_no_resource_limits,
    _rule_nga006_unencrypted_pii_datastore,
    _rule_nga007_missing_auth_on_api_endpoint,
    _rule_nga019_missing_hitl,
)

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
# NGA-005 — AI workloads without resource limits
# ---------------------------------------------------------------------------


class TestNga005:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        security_findings: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {"security_findings": security_findings or []}
        return _rule_nga005_no_resource_limits(nodes=nodes, summary=summary)

    def test_fires_on_security_finding(self) -> None:
        nodes = [_deployment_node("prod")]
        findings = self._run(nodes, security_findings=["no_resource_limits"])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-005"
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
# NGA-006 — Unencrypted datastore containing PII/PHI
# ---------------------------------------------------------------------------


class TestNga006:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        dc: list[str],
    ) -> list[dict[str, Any]]:
        summary: dict[str, Any] = {"data_classification": dc}
        return _rule_nga006_unencrypted_pii_datastore(nodes=nodes, summary=summary)

    def test_fires_on_unencrypted_pii_datastore(self) -> None:
        node = {
            "id": "db",
            "name": "patients-db",
            "component_type": "DATASTORE",
            "metadata": {"extras": {"encryption_at_rest": False}},
        }
        findings = self._run([node], dc=["PHI"])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-006"
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
# NGA-007 — Missing auth on external API endpoint
# ---------------------------------------------------------------------------


class TestNga007:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga007_missing_auth_on_api_endpoint(nodes=nodes, summary={}, edges=edges or [])

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
        assert findings[0]["rule_id"] == "NGA-007"
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


# ---------------------------------------------------------------------------
# NGA-019 — Missing HITL for irreversible tool actions
# ---------------------------------------------------------------------------


class TestNga019:
    def _run(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return _rule_nga019_missing_hitl(nodes=nodes, edges=edges or [], summary={})

    def test_fires_on_agent_with_delete_tool_no_hitl(self) -> None:
        agent = _agent_node("my-agent")
        tool = _tool_node("delete-records-tool", description="deletes records permanently")
        edge = {"source": "my-agent", "target": "delete-records-tool", "edge_type": "CALLS"}
        findings = self._run([agent, tool], edges=[edge])
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "NGA-019"
        assert findings[0]["severity"] == "HIGH"

    def test_no_finding_when_agent_has_hitl_metadata(self) -> None:
        """Agent with HITL pattern in metadata (e.g. 'interrupt') suppresses NGA-019."""
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
        # Check the first five to verify ordering
        assert names[0] == "_rule_nga001_phi_to_external_llm"
        assert names[1] == "_rule_nga002_insufficient_guardrails"
        assert names[2] == "_rule_nga003_secrets_in_env"
        assert names[3] == "_rule_nga004_runs_as_root"
        assert names[4] == "_rule_nga005_no_resource_limits"
        # NGA-008 is retired; verify it's absent
        assert "_rule_nga008" not in " ".join(names)
        # Total should be 18 rules (001-019 minus 008)
        assert len(_RULES) == 18

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
