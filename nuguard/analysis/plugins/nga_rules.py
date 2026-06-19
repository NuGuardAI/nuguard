"""NuGuard AI (NGA) structural analysis rules for AI SBOMs.

Runs deterministic, offline checks against the SBOM graph and metadata.
Every finding carries a stable rule ID (NGA-xxx), severity, affected
components, and a remediation hint.

NGA-001  PII/PHI data handled by external LLM providers        CRITICAL
NGA-002  Insufficient guardrail coverage                        HIGH (two sub-checks)
NGA-003  Secrets exposed as env vars or no secret store         HIGH
NGA-004  Containers / K8s workloads running as root             HIGH
NGA-005  Unencrypted datastore containing PII/PHI              HIGH
NGA-006  Missing authentication on external AI API endpoint     HIGH
NGA-007  Overly permissive IAM role for AI workload             HIGH
NGA-008  LLM model weight loaded from untrusted registry        HIGH
NGA-009  AI application has no audit logging enabled            HIGH
NGA-010  GitHub Actions: pull_request_target with untrusted injection  HIGH
NGA-011  GitHub Actions: GITHUB_ENV written from untrusted input  HIGH
NGA-012  Agent pipeline lacks HITL approval for high-risk actions  HIGH
NGA-013  No network policy for AI workload in K8s               MEDIUM
NGA-014  GitHub Actions: ACTIONS_RUNNER_DEBUG secret exposed    MEDIUM
NGA-015  AI workloads without CPU/memory resource limits        LOW
NGA-016  Container image using latest tag                       LOW
NGA-017  AI workload missing health check                       LOW
NGA-018  Multiple AI agents sharing a datastore, no IAM isolation  LOW
"""

from __future__ import annotations

import re
from typing import Any, Callable

from nuguard.analysis.graph import AnalysisGraph
from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin
from nuguard.common.logging import get_logger

_log = get_logger("analysis.nga_rules")

# ── Severity ordering ────────────────────────────────────────────────────────
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

# ── External LLM providers whose APIs leave your trust boundary ───────────────
_EXTERNAL_PROVIDERS = {
    "openai", "anthropic", "google", "cohere", "mistral",
    "deepseek", "ai21", "amazon", "azure",
}

# ── Trusted model registries (NGA-008) ───────────────────────────────────────
_DEFAULT_TRUSTED_REGISTRIES = {"huggingface.co", "ollama.ai"}

# ── Irreversible tool name patterns (NGA-012) ─────────────────────────────────
_IRREVERSIBLE_TOOL_PATTERNS = re.compile(
    r"send[_\-]?email|delete[_\-]?|drop[_\-]?|execute[_\-]?sql|"
    r"write[_\-]?|charge[_\-]?|create[_\-]?payment|pay[_\-]?|"
    r"post[_\-]?|publish[_\-]?|rm[_\-]?|remove[_\-]?|destroy[_\-]?",
    re.IGNORECASE,
)

# ── Read-only tool name prefixes (NGA-012 — override weak irreversible matches) ──
# A tool whose name starts with one of these verbs is presumed read-only unless
# its privilege_scope contains a write-capable scope (see _WRITE_PRIVILEGE_SCOPES).
_READ_ONLY_TOOL_PREFIXES = re.compile(
    r"^(?:get|fetch|lookup|search|list|status|display|show|view|check|find|retrieve|query)[_\-]",
    re.IGNORECASE,
)

# ── Write-capable privilege scopes (NGA-012) ──────────────────────────────────
# Any TOOL node with one of these scopes is always considered irreversible,
# regardless of its name.
_WRITE_PRIVILEGE_SCOPES = {
    "DB_WRITE", "FILESYSTEM_WRITE", "CODE_EXECUTION",
    "EMAIL_OUT", "SOCIAL_MEDIA_OUT",
}

# ── HITL pattern indicators in AGENT metadata (NGA-012) ──────────────────────
_HITL_PATTERNS = {
    "interrupt", "interrupt_before", "interrupt_after",
    "human_input", "requires_action", "HumanApprovalCallbackHandler",
    "hitl", "human_in_the_loop", "human_approval",
}

# ── GitHub Actions patterns (NGA-010/011/014) ─────────────────────────────────
_PATTERN_PR_TARGET_INJECTION = re.compile(
    r"pull_request_target.*\$\{\{.*github\.event\.pull_request\.",
    re.DOTALL,
)
_PATTERN_GITHUB_ENV_INJECTION = re.compile(
    r'echo\s+.*\$\{\{.*\}\}.*>>\s*\$GITHUB_ENV'
)
_PATTERN_DEBUG_SECRET = re.compile(r'ACTIONS_RUNNER_DEBUG')

# ── Component type sets ──────────────────────────────────────────────────────
_GUARDRAIL_TYPES = {"GUARDRAIL"}
_MODEL_TYPES = {"MODEL"}
_AGENT_TYPES = {"AGENT"}
_DATASTORE_TYPES = {"DATASTORE"}
_API_ENDPOINT_TYPES = {"API_ENDPOINT"}
_DEPLOYMENT_TYPES = {"DEPLOYMENT"}
_CONTAINER_TYPES = {"CONTAINER_IMAGE"}
_IAM_TYPES = {"IAM"}


# ── Shared helpers ────────────────────────────────────────────────────────────

def _node_extras(node: dict[str, Any]) -> dict[str, Any]:
    return node.get("metadata", {}).get("extras", {}) or {}


def _depl_meta(node: dict[str, Any]) -> dict[str, Any]:
    """Return a flattened view of deployment/IaC metadata for a node."""
    meta: dict[str, Any] = node.get("metadata") or {}
    extras: dict[str, Any] = meta.get("extras") or {}
    merged = dict(extras)
    for key in (
        "deployment_target", "secret_store", "encryption_at_rest",
        "encryption_key_ref", "runs_as_root", "has_health_check",
        "has_resource_limits", "no_resource_limits", "ha_mode", "availability_zones",
        "iam_type", "permissions", "iam_scope", "trust_principals",
        "base_image", "image_name", "image_tag",
        "has_network_policy", "source_url", "integrity_hash", "checksum",
    ):
        v = meta.get(key)
        if v is not None:
            merged[key] = v
    return merged


def _has_phi_pii(labels: list[str]) -> bool:
    return bool(set(labels) & {"PHI", "PII"})


def _finding(
    rule_id: str,
    severity: str,
    title: str,
    description: str,
    affected: list[str],
    remediation: str,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "rule_id": rule_id,
        "severity": severity,
        "title": title,
        "description": description,
        "affected": affected,
        "remediation": remediation,
    }
    result.update(extra)
    return result


def _node_ids_with_edge_to(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    target_types: set[str],
) -> set[str]:
    """Return IDs of all nodes that have at least one edge pointing to a node
    of the given component type(s)."""
    target_ids = {n["id"] for n in nodes if n.get("component_type") in target_types}
    return {e["source"] for e in edges if e.get("target") in target_ids}


def _nodes_reachable_from(
    start_id: str,
    edges: list[dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    max_depth: int = 6,
) -> set[str]:
    """BFS over directed edges from start_id; returns reachable node IDs."""
    visited: set[str] = set()
    queue = [start_id]
    depth = 0
    while queue and depth < max_depth:
        next_q = []
        for nid in queue:
            for e in edges:
                if e.get("source") == nid and e.get("target") not in visited:
                    visited.add(e["target"])
                    next_q.append(e["target"])
        queue = next_q
        depth += 1
    return visited


# ── NGA-001 ──────────────────────────────────────────────────────────────────


def _rule_nga001_phi_to_external_llm(
    nodes: list[dict[str, Any]],
    summary: dict[str, Any],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """CRITICAL — PHI/PII data present while external LLM providers are used."""
    dc_labels: list[str] = summary.get("data_classification") or []
    if not _has_phi_pii(dc_labels):
        return []

    # Build a map of model_id → (model_node, provider_str) for external models
    external_model_map: dict[str, tuple[dict[str, Any], str]] = {}
    for n in nodes:
        if n.get("component_type", "") not in _MODEL_TYPES:
            continue
        extras = _node_extras(n)
        provider = (
            extras.get("provider")
            or (n.get("metadata") or {}).get("provider")
            or ""
        ).lower()
        if any(ep in provider for ep in _EXTERNAL_PROVIDERS):
            external_model_map[str(n["id"])] = (n, provider)

    if not external_model_map:
        return []

    # Graph-based: emit one finding per (agent, model, datastore) data-flow path
    if graph is not None:
        findings: list[dict[str, Any]] = []
        seen_triples: set[tuple[str, str, str]] = set()
        for agent in graph.nodes_of_type("AGENT"):
            # Which external models does this agent use?
            for model in graph.targets(str(agent["id"]), "USES"):
                mid = str(model["id"])
                if mid not in external_model_map:
                    continue
                _, provider_str = external_model_map[mid]
                # Which datastores does this agent access that carry PII/PHI?
                for interm, ds, access_type in graph.accesses_paths(str(agent["id"])):
                    ds_meta = ds.get("metadata") or {}
                    ds_labels: list[str] = (
                        ds_meta.get("data_classification")
                        or ds_meta.get("extras", {}).get("data_classification")
                        or []
                    )
                    if not _has_phi_pii(ds_labels):
                        continue
                    triple = (str(agent["id"]), mid, str(ds["id"]))
                    if triple in seen_triples:
                        continue
                    seen_triples.add(triple)
                    agent_name = agent.get("name", "")
                    model_name = model.get("name", "")
                    ds_name = ds.get("name", "")
                    interm_name = interm.get("name", "") if interm else None
                    if interm_name:
                        path = (f"'{agent_name}' → CALLS → '{interm_name}' "
                                f"→ ACCESSES[{access_type or 'read'}] → '{ds_name}' "
                                f"(data_classification={ds_labels}) "
                                f"→ USES → '{model_name}' (provider={provider_str})")
                        affected = [agent_name, interm_name, ds_name, model_name]
                        remediation = (
                            f"Add output filtering on '{interm_name}' before data reaches "
                            f"'{model_name}'. Mask or redact {ds_labels} fields in "
                            f"'{interm_name}' output before the '{model_name}' call."
                        )
                    else:
                        path = (f"'{agent_name}' → ACCESSES[{access_type or 'read'}] "
                                f"→ '{ds_name}' (data_classification={ds_labels}) "
                                f"→ USES → '{model_name}' (provider={provider_str})")
                        affected = [agent_name, ds_name, model_name]
                        remediation = (
                            f"Add output filtering on '{agent_name}' before data reaches "
                            f"'{model_name}'. Mask or redact {ds_labels} fields before "
                            f"the '{model_name}' call."
                        )
                    findings.append(_finding(
                        "NGA-001", "CRITICAL",
                        f"PII/PHI data flow to external LLM: '{agent_name}' → '{model_name}'",
                        f"Data flow detected: {path}. Regulated data (PII/PHI) may be "
                        "transmitted outside your trust boundary, potentially violating "
                        "applicable data protection regulations.",
                        affected,
                        remediation,
                        evidence=path,
                    ))
        if findings:
            return findings
        # Agent→Model edges present but no datastore paths found: fall through to summary check

    # Fallback: summary-level check (no graph or no edge data)
    phi_tables: list[str] = summary.get("classified_tables") or []
    external_model_names = [n.get("name", "") for n, _ in external_model_map.values()]
    return [
        _finding(
            "NGA-001", "CRITICAL",
            "PII/PHI data handled by external LLM providers",
            f"The SBOM contains {', '.join(sorted(set(dc_labels)))} data "
            f"({len(phi_tables)} classified table(s)) and calls external LLM "
            f"provider(s): {', '.join(external_model_names)}. Regulated data (PII/PHI) may be "
            "transmitted outside your trust boundary, potentially violating applicable "
            "data protection regulations.",
            external_model_names,
            "Ensure regulated data is stripped or anonymised before being included in prompts "
            "sent to external providers. Consider a self-hosted model for sensitive workloads "
            "or establish a data processing agreement (DPA) with each provider.",
        )
    ]


# ── NGA-002 ──────────────────────────────────────────────────────────────────


def _rule_nga002_insufficient_guardrails(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """HIGH — Insufficient guardrail coverage (three sub-checks).

    Sub-check A: LLM MODEL with no GUARDRAIL PROTECTS it AND no GUARDRAIL PROTECTS any of
    its using AGENTs — path-aware, not just "any guardrail exists".
    Sub-check B: AGENT with outbound external API_ENDPOINT edge and no guardrail on
    the endpoint or the agent.
    Sub-check C (graph-only): DELEGATES_TO edge where neither side is guardrail-protected.
    """
    findings: list[dict[str, Any]] = []

    # Count global guardrail nodes to detect potential SBOM extraction gaps.
    global_guardrail_count = sum(
        1 for n in nodes if n.get("component_type") in _GUARDRAIL_TYPES
    )

    def _gap_note(description: str) -> str:
        """Append an SBOM modeling gap note when guardrail nodes exist without PROTECTS edges."""
        if global_guardrail_count:
            return (
                description
                + f" Note: {global_guardrail_count} guardrail node(s) detected in the SBOM"
                " but no PROTECTS edge connects them to this path — this may be an SBOM"
                " extraction gap rather than a real vulnerability."
                " Verify guardrail coverage in source before remediating."
            )
        return description

    if graph is not None:
        # Sub-check A: per MODEL, check if it or any of its using agents is protected
        for model in graph.nodes_of_type("MODEL"):
            if graph.has_protection(str(model["id"])):
                continue
            using_agents = graph.sources(str(model["id"]), "USES")
            unprotected_agents = [a for a in using_agents if not graph.has_protection(str(a["id"]))]
            if using_agents and len(unprotected_agents) < len(using_agents):
                # At least one calling agent is protected — model is covered
                continue
            model_name = model.get("name", "")
            agent_names = [a.get("name", "") for a in using_agents]
            affected = [model_name] + agent_names
            evidence = (
                f"MODEL '{model_name}' has no GUARDRAIL on its PROTECTS path. "
                f"Used by: {agent_names or ['(no agent edges)']}"
            )
            findings.append(_finding(
                "NGA-002", "HIGH",
                f"LLM model '{model_name}' has no output guardrail (sub-check A)",
                _gap_note(evidence),
                affected,
                f"Attach a guardrail (e.g. LlamaGuard, NeMo Guardrails) to "
                f"'{model_name}' or to each calling agent: {agent_names or ['unknown']}.",
                evidence=evidence,
                modeling_gap_risk=bool(global_guardrail_count),
            ))

        # Sub-check B: per unauthenticated API_ENDPOINT exposing an AGENT
        for ep in graph.nodes_of_type("API_ENDPOINT"):
            ep_id = str(ep["id"])
            ep_name = ep.get("name", "")
            # Find agents the endpoint protects or that call it
            exposed_agents = graph.sources(ep_id, "PROTECTS") + graph.targets(ep_id, "CALLS")
            agent_exposed = [a for a in exposed_agents if (a.get("component_type") or "").upper() == "AGENT"]
            if not agent_exposed:
                continue
            # Only flag if neither endpoint nor any exposed agent has protection
            if graph.has_protection(ep_id):
                continue
            unguarded_agents = [a for a in agent_exposed if not graph.has_protection(str(a["id"]))]
            if not unguarded_agents:
                continue
            for agent in unguarded_agents:
                agent_name = agent.get("name", "")
                evidence = (
                    f"Endpoint '{ep_name}' is publicly reachable and exposes "
                    f"agent '{agent_name}' without a guardrail."
                )
                findings.append(_finding(
                    "NGA-002", "HIGH",
                    f"Internet-capable agent '{agent_name}' has no output guardrail (sub-check B)",
                    _gap_note(evidence),
                    [ep_name, agent_name],
                    f"Place a guardrail before '{agent_name}' on endpoint '{ep_name}'.",
                    evidence=evidence,
                    modeling_gap_risk=bool(global_guardrail_count),
                ))

        # Sub-check C: DELEGATES_TO edges where neither side is protected
        for src_agent in graph.nodes_of_type("AGENT"):
            src_id = str(src_agent["id"])
            for tgt_agent in graph.targets(src_id, "DELEGATES_TO"):
                tgt_id = str(tgt_agent["id"])
                if graph.has_protection(src_id) or graph.has_protection(tgt_id):
                    continue
                src_name = src_agent.get("name", "")
                tgt_name = tgt_agent.get("name", "")
                evidence = (
                    f"'{src_name}' DELEGATES_TO '{tgt_name}' with no guardrail on either agent."
                )
                findings.append(_finding(
                    "NGA-002", "HIGH",
                    f"Unguarded delegation: '{src_name}' → '{tgt_name}' (sub-check C)",
                    _gap_note(evidence),
                    [src_name, tgt_name],
                    f"Add a guardrail between '{src_name}' and '{tgt_name}' to prevent "
                    "prompt injection from propagating across the delegation boundary.",
                    evidence=evidence,
                    modeling_gap_risk=bool(global_guardrail_count),
                ))

        return findings

    # Fallback: flat-list checks (no graph available)
    guardrail_ids = {n["id"] for n in nodes if n.get("component_type") in _GUARDRAIL_TYPES}
    model_nodes = [n for n in nodes if n.get("component_type") in _MODEL_TYPES]
    if model_nodes and not guardrail_ids:
        desc = (
            f"{len(model_nodes)} LLM model node(s) produce output with no output-validation "
            "or guardrail step detected anywhere in the SBOM graph."
        )
        findings.append(_finding(
            "NGA-002", "HIGH",
            "LLM models with no output guardrail (sub-check A)",
            _gap_note(desc),
            [n.get("name", "") for n in model_nodes],
            "Implement structured output parsing and validation. Add a GUARDRAIL component "
            "(response classifier, PII filter, or output validator) between model output "
            "and downstream consumers.",
            modeling_gap_risk=bool(global_guardrail_count),
        ))
    api_endpoint_ids = {n["id"] for n in nodes if n.get("component_type") in _API_ENDPOINT_TYPES}
    if api_endpoint_ids and not guardrail_ids:
        agents_with_outbound = set()
        for e in edges:
            if e.get("target") in api_endpoint_ids:
                agents_with_outbound.add(e.get("source", ""))
        agent_nodes_outbound = [
            n for n in nodes
            if n.get("component_type") in _AGENT_TYPES
            and n["id"] in agents_with_outbound
        ]
        if agent_nodes_outbound:
            desc = (
                f"{len(agent_nodes_outbound)} agent(s) make outbound API calls with no output "
                "guardrail detected. Internet-capable agents without output filtering can "
                "exfiltrate data or be manipulated by adversarial external content."
            )
            findings.append(_finding(
                "NGA-002", "HIGH",
                "Internet-capable agent with no output guardrail (sub-check B)",
                _gap_note(desc),
                [n.get("name", "") for n in agent_nodes_outbound],
                "Add an output guardrail or content filter between the agent and any external "
                "API endpoints it calls. Log all outbound requests for audit purposes.",
                modeling_gap_risk=bool(global_guardrail_count),
            ))
    return findings


# ── NGA-003 ──────────────────────────────────────────────────────────────────


def _rule_nga003_secrets_in_env(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — Secrets exposed as environment variables or no secret store configured."""
    security_findings: list[str] = summary.get("security_findings") or []
    secret_stores: list[str] = summary.get("secret_stores") or []
    deployment_nodes = [n for n in nodes if n.get("component_type") == "DEPLOYMENT"]

    has_secrets_in_env = "secrets_in_env_vars" in security_findings
    has_no_store = not secret_stores and deployment_nodes

    if not has_secrets_in_env and not has_no_store:
        return []

    affected = [
        n.get("name", "")
        for n in deployment_nodes
        if has_secrets_in_env or not _depl_meta(n).get("secret_store")
    ]
    if not affected:
        affected = [n.get("name", "") for n in deployment_nodes]

    detail = ""
    if has_secrets_in_env:
        detail = "Secrets are referenced as plain environment variables. "
    if has_no_store:
        detail += (
            f"{len(deployment_nodes)} deployment resource(s) have no secret "
            "management store configured. "
        )
    detail += (
        "Plaintext secrets appear in process listings, 'docker inspect' output, and CI system logs."
    )
    return [
        _finding(
            "NGA-003", "HIGH",
            "Secrets exposed as env vars or no secret store configured",
            detail,
            affected,
            "Migrate to a dedicated secret management service: AWS Secrets Manager, "
            "Azure Key Vault, GCP Secret Manager, or HashiCorp Vault. "
            "For GitHub Actions, replace static 'secrets.X' with OIDC federation.",
        )
    ]


# ── NGA-004 ──────────────────────────────────────────────────────────────────


def _rule_nga004_runs_as_root(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — Container images or K8s workloads running as root."""
    security_findings: list[str] = summary.get("security_findings") or []
    root_nodes = [
        n for n in nodes
        if n.get("component_type") in ("DEPLOYMENT", "CONTAINER_IMAGE")
        and _depl_meta(n).get("runs_as_root") is True
    ]
    if not root_nodes and "container_runs_as_root" not in security_findings:
        return []
    if not root_nodes:
        root_nodes = [n for n in nodes if n.get("component_type") == "CONTAINER_IMAGE"]
    if not root_nodes:
        return []
    return [
        _finding(
            "NGA-004", "HIGH",
            "Containers running as root",
            f"{len(root_nodes)} container/workload node(s) run as root (UID 0). "
            "Root containers can write to the host filesystem via volume mounts and "
            "trivially escalate privileges on a container escape. "
            "NIST SP 800-190 §4.4 explicitly recommends running containers as non-root.",
            [n.get("name", "") for n in root_nodes],
            "Add 'USER nonroot' (or a specific non-zero UID) to Dockerfiles. "
            "Set 'securityContext.runAsNonRoot: true' and 'runAsUser: 1000' in K8s pod specs. "
            "Use distroless or rootless base images. "
            "Apply 'allowPrivilegeEscalation: false' and drop all Linux capabilities.",
        )
    ]


# ── NGA-005 ──────────────────────────────────────────────────────────────────


def _rule_nga005_unencrypted_pii_datastore(
    nodes: list[dict[str, Any]],
    summary: dict[str, Any],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """HIGH — Unencrypted datastore containing PII/PHI."""
    dc_labels: list[str] = summary.get("data_classification") or []
    if not _has_phi_pii(dc_labels):
        return []

    unencrypted = [
        n for n in nodes
        if n.get("component_type") in _DATASTORE_TYPES
        and _depl_meta(n).get("encryption_at_rest") is False
    ]
    if not unencrypted:
        return []

    findings: list[dict[str, Any]] = []
    for ds in unencrypted:
        ds_name = ds.get("name", "")
        ds_id = str(ds["id"])
        # Check node-level data_classification labels if present
        ds_meta = ds.get("metadata") or {}
        node_labels: list[str] = (
            ds_meta.get("data_classification")
            or ds_meta.get("extras", {}).get("data_classification")
            or dc_labels
        )
        write_agent_names: list[str] = []
        if graph is not None:
            write_agents = graph.write_agents_for(ds_id)
            write_agent_names = [a.get("name", "") for a in write_agents]
        write_note = (
            f" Write access granted to: {', '.join(write_agent_names)}."
            if write_agent_names
            else " No write-capable agents detected via graph edges."
        )
        evidence = (
            f"Datastore '{ds_name}' has encryption_at_rest=False and stores "
            f"{node_labels}.{write_note}"
        )
        affected = [ds_name] + write_agent_names
        findings.append(_finding(
            "NGA-005", "HIGH",
            f"Unencrypted datastore '{ds_name}' contains PII/PHI",
            evidence,
            affected,
            f"Enable encryption at rest for '{ds_name}'. "
            + (
                f"Restrict write access — currently granted to: {', '.join(write_agent_names)}. "
                if write_agent_names
                else ""
            )
            + "Rotate encryption keys on a schedule and store them "
            "in a dedicated key management service (KMS).",
            evidence=evidence,
        ))
    return findings


# ── NGA-006 ──────────────────────────────────────────────────────────────────


def _rule_nga006_missing_auth_on_api_endpoint(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """HIGH — Missing authentication on external AI API endpoint."""
    auth_ids = {n["id"] for n in nodes if n.get("component_type") == "AUTH"}
    guardrail_ids = {n["id"] for n in nodes if n.get("component_type") in _GUARDRAIL_TYPES}
    protected_targets = {e.get("target") for e in edges if e.get("source") in auth_ids | guardrail_ids}

    unprotected = [
        n for n in nodes
        if n.get("component_type") in _API_ENDPOINT_TYPES
        and n["id"] not in protected_targets
        and (
            _node_extras(n).get("no_auth_required") is True
            or (n.get("metadata") or {}).get("no_auth_required") is True
            or not any(e.get("target") == n["id"] for e in edges)
        )
    ]
    if not unprotected:
        return []

    if graph is not None:
        findings: list[dict[str, Any]] = []
        for ep in unprotected:
            ep_name = ep.get("name", "")
            ep_id = str(ep["id"])
            # Find agents exposed via this endpoint
            exposed = (
                graph.sources(ep_id, "PROTECTS")
                + [n for n in graph.targets(ep_id, "CALLS") if (n.get("component_type") or "").upper() == "AGENT"]
            )
            exposed_agent_names = list({a.get("name", "") for a in exposed if (a.get("component_type") or "").upper() == "AGENT"})
            # Note if any exposed agent touches PII data
            pii_note = ""
            if graph and exposed_agent_names:
                for ag in exposed:
                    if (ag.get("component_type") or "").upper() != "AGENT":
                        continue
                    for _interm, ds, _at in graph.accesses_paths(str(ag["id"])):
                        ds_meta = ds.get("metadata") or {}
                        ds_labels = (
                            ds_meta.get("data_classification")
                            or ds_meta.get("extras", {}).get("data_classification")
                            or []
                        )
                        if _has_phi_pii(ds_labels):
                            pii_note = " Exposed agents access PII/PHI datastores — elevated risk."
                            break
            evidence = (
                f"Endpoint '{ep_name}' has no AUTH node. "
                + (f"Exposes: {', '.join(exposed_agent_names)}. " if exposed_agent_names else "")
                + pii_note
            )
            affected = [ep_name] + exposed_agent_names
            findings.append(_finding(
                "NGA-006", "HIGH",
                f"Missing authentication on API endpoint '{ep_name}'",
                evidence,
                affected,
                f"Add authentication middleware (API key, JWT, OAuth 2.0) to '{ep_name}'. "
                + (
                    f"The exposed agents ({', '.join(exposed_agent_names)}) require "
                    "verified caller identity before processing requests."
                    if exposed_agent_names
                    else "Use an API gateway to centralise auth enforcement."
                ),
                evidence=evidence,
            ))
        return findings

    return [
        _finding(
            "NGA-006", "HIGH",
            "Missing authentication on external AI API endpoint",
            f"{len(unprotected)} API endpoint(s) lack an AUTH or GUARDRAIL edge "
            "in the SBOM graph, indicating no authentication layer was detected. "
            "Unauthenticated model inference endpoints expose the AI system to "
            "abuse, prompt injection from anonymous users, and cost escalation.",
            [n.get("name", "") for n in unprotected],
            "Add authentication middleware (API key, JWT, OAuth 2.0) to all public-facing "
            "AI API endpoints. Consider rate limiting and usage quotas to prevent abuse. "
            "Use an API gateway to centralise auth enforcement.",
        )
    ]


# ── NGA-007 ──────────────────────────────────────────────────────────────────


def _rule_nga007_overly_permissive_iam(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — Overly permissive IAM role for AI workload."""
    deployment_ids = {n["id"] for n in nodes if n.get("component_type") in _DEPLOYMENT_TYPES}
    # Find IAM nodes attached to deployments
    iam_with_deployment: list[dict[str, Any]] = []
    for e in edges:
        if e.get("source") in deployment_ids:
            target_node = next((n for n in nodes if n["id"] == e.get("target")), None)
            if target_node and target_node.get("component_type") in _IAM_TYPES:
                iam_with_deployment.append(target_node)
        elif e.get("target") in deployment_ids:
            src_node = next((n for n in nodes if n["id"] == e.get("source")), None)
            if src_node and src_node.get("component_type") in _IAM_TYPES:
                iam_with_deployment.append(src_node)

    overpermissive = []
    for iam_node in iam_with_deployment:
        perms = _depl_meta(iam_node).get("permissions") or []
        if isinstance(perms, str):
            perms = [perms]
        if any(p in ("*", "admin", "AdministratorAccess") for p in perms):
            overpermissive.append(iam_node)

    if not overpermissive:
        return []

    return [
        _finding(
            "NGA-007", "HIGH",
            "Overly permissive IAM role for AI workload",
            f"{len(overpermissive)} IAM role(s) attached to AI deployment(s) grant "
            "wildcard ('*') or admin-level permissions. AI workloads with excessive IAM "
            "permissions can be exploited by prompt injection to perform administrative "
            "actions or exfiltrate data from unrelated services.",
            [n.get("name", "") for n in overpermissive],
            "Apply the principle of least privilege: restrict IAM roles to only the specific "
            "actions and resources the AI workload needs. Replace wildcard actions with "
            "explicit action lists. Use separate roles per service and rotate credentials.",
        )
    ]


# ── NGA-008 ──────────────────────────────────────────────────────────────────


def _rule_nga008_untrusted_model_registry(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — LLM model weight loaded from untrusted registry."""
    trusted: set[str] = _DEFAULT_TRUSTED_REGISTRIES | set(
        summary.get("trusted_registries") or []
    )

    untrusted = []
    for n in nodes:
        if n.get("component_type") not in _MODEL_TYPES:
            continue
        meta = n.get("metadata") or {}
        source_url: str = (
            meta.get("source_url")
            or meta.get("extras", {}).get("source_url")
            or ""
        )
        if not source_url:
            continue
        is_trusted = any(r in source_url for r in trusted)
        has_digest = bool(
            meta.get("digest")
            or meta.get("extras", {}).get("digest")
            or meta.get("checksum")
        )
        if not is_trusted and not has_digest:
            untrusted.append(n)

    if not untrusted:
        return []

    return [
        _finding(
            "NGA-008", "HIGH",
            "LLM model weights loaded from untrusted registry",
            f"{len(untrusted)} model(s) are loaded from sources not in the trusted registry "
            f"allowlist ({', '.join(sorted(trusted))}) and have no checksum/digest for "
            "integrity verification. Tampered model weights can embed backdoors or "
            "poisoned behaviours that persist through fine-tuning.",
            [n.get("name", "") for n in untrusted],
            "Only load model weights from trusted registries (HuggingFace, Ollama, or your "
            "own private registry). Always verify integrity with a SHA-256 checksum or "
            "content-hash before loading. Add untrusted sources to an explicit blocklist.",
        )
    ]


# ── NGA-009 ──────────────────────────────────────────────────────────────────

_AUDIT_LIBS = {
    "opentelemetry", "langfuse", "arize", "whylogs",
    "mlflow", "wandb", "helicone",
}
_AUDIT_MIDDLEWARE = {
    "RequestLoggingMiddleware", "AuditLogHandler",
    "RotatingFileHandler", "TimedRotatingFileHandler",
}


def _rule_nga009_no_audit_logging(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — AI application has no audit logging enabled."""
    agent_nodes = [n for n in nodes if n.get("component_type") in _AGENT_TYPES]
    if not agent_nodes:
        return []

    log_paths: list[str] = summary.get("log_paths") or []
    frameworks: list[str] = [f.lower() for f in (summary.get("frameworks") or [])]

    has_audit_lib = any(lib in frameworks for lib in _AUDIT_LIBS)
    has_log_paths = bool(log_paths)
    has_middleware = any(
        mw in str(summary) for mw in _AUDIT_MIDDLEWARE
    )
    has_instrumentation = bool(summary.get("instrumentation"))

    if has_audit_lib or has_log_paths or has_middleware or has_instrumentation:
        return []

    return [
        _finding(
            "NGA-009", "HIGH",
            "AI application has no audit logging enabled",
            f"{len(agent_nodes)} agent(s) detected but no audit logging evidence found "
            "(no log paths, no logging middleware, no observability library such as "
            "OpenTelemetry, Langfuse, Arize, or whylogs). "
            "Audit logs are essential for incident response, compliance (HIPAA §164.312(b), "
            "SOC 2 CC7.2), and detecting prompt injection post-hoc.",
            [n.get("name", "") for n in agent_nodes],
            "Integrate a structured logging or observability library (OpenTelemetry, Langfuse, "
            "Arize) to capture every prompt, response, tool call, and user session. "
            "Store logs in an append-only store with tamper detection. "
            "Set retention policies aligned with your compliance obligations.",
        )
    ]


# ── NGA-010 ──────────────────────────────────────────────────────────────────


def _rule_nga010_pr_target_injection(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — GitHub Actions: pull_request_target with untrusted context injection."""
    # Preferred: structured findings emitted by GitHubActionsAdapter
    structured = [
        f for f in (summary.get("workflow_security_findings") or [])
        if f.get("rule_signal") == "NGA-010"
    ]
    if structured:
        first = structured[0]
        return [
            _finding(
                "NGA-010", "HIGH",
                "GitHub Actions: pull_request_target with untrusted context injection",
                f"Detected in {first['path']} (line {first['line']}): {first['snippet']}",
                ["GitHub Actions workflow"],
                "Never use '${{{{ github.event.pull_request.head... }}}}' directly in run steps "
                "under pull_request_target. Pass untrusted data through environment variables "
                "with explicit quoting, or use 'actions/github-script' with safe property access. "
                "Consider switching to 'pull_request' trigger for untrusted code paths.",
            )
        ]

    # Fallback: raw regex on concatenated workflow content
    workflow_content: str = summary.get("github_actions_content") or ""
    if not workflow_content:
        # Check individual nodes for workflow metadata
        for n in nodes:
            extras = _node_extras(n)
            wf = extras.get("workflow_content") or ""
            if _PATTERN_PR_TARGET_INJECTION.search(wf):
                workflow_content = wf
                break

    if not workflow_content or not _PATTERN_PR_TARGET_INJECTION.search(workflow_content):
        return []

    return [
        _finding(
            "NGA-010", "HIGH",
            "GitHub Actions: pull_request_target with untrusted context injection",
            "A workflow uses the 'pull_request_target' trigger and references "
            "'${{ github.event.pull_request... }}' in run steps or env — this allows "
            "fork PR authors to inject arbitrary commands into a privileged workflow context "
            "that has write access to the repository and secrets.",
            ["GitHub Actions workflow"],
            "Never use '${{ github.event.pull_request.head... }}' directly in run steps "
            "under pull_request_target. Pass untrusted data through environment variables "
            "with explicit quoting, or use 'actions/github-script' with safe property access. "
            "Consider switching to 'pull_request' trigger for untrusted code paths.",
        )
    ]


# ── NGA-011 ──────────────────────────────────────────────────────────────────


def _rule_nga011_github_env_injection(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — GitHub Actions: GITHUB_ENV written from untrusted input."""
    # Preferred: structured findings emitted by GitHubActionsAdapter
    structured = [
        f for f in (summary.get("workflow_security_findings") or [])
        if f.get("rule_signal") == "NGA-011"
    ]
    if structured:
        first = structured[0]
        return [
            _finding(
                "NGA-011", "HIGH",
                "GitHub Actions: GITHUB_ENV written from untrusted input",
                f"Detected in {first['path']} (line {first['line']}): {first['snippet']}",
                ["GitHub Actions workflow"],
                "Never write untrusted input directly to $GITHUB_ENV. Validate and sanitise "
                "all user-controlled data before using it in environment variable assignments. "
                "Use 'github.event.pull_request.number' (integer) instead of string fields "
                "where possible.",
            )
        ]

    # Fallback: raw regex on concatenated workflow content
    workflow_content: str = summary.get("github_actions_content") or ""
    if not workflow_content:
        for n in nodes:
            wf = _node_extras(n).get("workflow_content") or ""
            if _PATTERN_GITHUB_ENV_INJECTION.search(wf):
                workflow_content = wf
                break

    if not workflow_content or not _PATTERN_GITHUB_ENV_INJECTION.search(workflow_content):
        return []

    return [
        _finding(
            "NGA-011", "HIGH",
            "GitHub Actions: GITHUB_ENV written from untrusted input",
            "A workflow step writes to '$GITHUB_ENV' using an expression that includes "
            "untrusted input (e.g. PR title, body, or comment). This allows an attacker "
            "to inject arbitrary environment variables into subsequent steps, potentially "
            "overriding PATH, credentials, or security-sensitive flags.",
            ["GitHub Actions workflow"],
            "Never write untrusted input directly to $GITHUB_ENV. Validate and sanitise "
            "all user-controlled data before using it in environment variable assignments. "
            "Use 'github.event.pull_request.number' (integer) instead of string fields "
            "where possible.",
        )
    ]


# ── NGA-012 ──────────────────────────────────────────────────────────────────


def _rule_nga012_missing_hitl(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """HIGH — Agent pipeline lacks human-in-the-loop approval for high-risk actions."""
    agent_nodes = [n for n in nodes if n.get("component_type") in _AGENT_TYPES]

    def _agent_has_hitl(agent: dict[str, Any]) -> bool:
        meta = agent.get("metadata") or {}
        extras = meta.get("extras") or {}
        agent_str = str(meta) + str(extras)
        return any(p.lower() in agent_str.lower() for p in _HITL_PATTERNS)

    def _tool_is_irreversible(tool: dict[str, Any]) -> tuple[bool, str]:
        name = tool.get("name", "")
        meta = tool.get("metadata") or {}
        # Authoritative: explicit write-capable privilege scope always wins
        scope = meta.get("privilege_scope") or _node_extras(tool).get("privilege_scope") or []
        if isinstance(scope, str):
            scope = [scope]
        if any(s.upper() in _WRITE_PRIVILEGE_SCOPES for s in scope):
            return True, f"privilege_scope={scope}"
        # Read-only prefix overrides weak name-based pattern matches
        if _READ_ONLY_TOOL_PREFIXES.match(name):
            return False, ""
        # Fall back to name/metadata pattern matching
        name_meta = name + " " + str(meta)
        m = _IRREVERSIBLE_TOOL_PATTERNS.search(name_meta)
        return bool(m), (m.group(0) if m else "")

    if graph is not None:
        findings: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for agent in agent_nodes:
            if _agent_has_hitl(agent):
                continue
            agent_name = agent.get("name", "")
            agent_id = str(agent["id"])
            # O(n) indexed BFS follows CALLS and DELEGATES_TO edges
            reachable_tools = graph.reachable_of_type(
                agent_id, ["CALLS", "DELEGATES_TO"], "TOOL"
            )
            for tool in reachable_tools:
                is_irrev, matched = _tool_is_irreversible(tool)
                if not is_irrev:
                    continue
                pair = (agent_id, str(tool["id"]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                tool_name = tool.get("name", "")
                evidence = (
                    f"'{agent_name}' can reach irreversible tool '{tool_name}' "
                    f"(matched pattern: '{matched}') without a HITL checkpoint."
                )
                findings.append(_finding(
                    "NGA-012", "HIGH",
                    f"Agent '{agent_name}' can invoke '{tool_name}' without HITL approval",
                    evidence,
                    [agent_name, tool_name],
                    f"Add an interrupt/approval step in '{agent_name}' before calling "
                    f"'{tool_name}'. Use LangGraph 'interrupt_before', CrewAI 'human_input=True', "
                    "OpenAI Assistants 'requires_action' event, or LangChain "
                    "'HumanApprovalCallbackHandler'. Log all irreversible tool calls.",
                    evidence=evidence,
                ))
        return findings

    # Fallback: O(n²) BFS (no graph)
    nodes_by_id = {n["id"]: n for n in nodes}
    tool_ids = {n["id"] for n in nodes if n.get("component_type") == "TOOL"}
    risky_agents = []
    for agent in agent_nodes:
        if _agent_has_hitl(agent):
            continue
        reachable = _nodes_reachable_from(agent["id"], edges, nodes_by_id)
        reachable_tools = [nodes_by_id[nid] for nid in reachable if nid in tool_ids and nid in nodes_by_id]
        if any(_tool_is_irreversible(t)[0] for t in reachable_tools):
            risky_agents.append(agent)
    if not risky_agents:
        return []
    return [
        _finding(
            "NGA-012", "HIGH",
            "Agent pipeline lacks HITL approval for high-risk tool actions",
            f"{len(risky_agents)} agent(s) can invoke irreversible or high-impact tools "
            "(email send, database write/delete, payment, file deletion, external API mutation) "
            "with no human-in-the-loop (HITL) approval gate detected. "
            "Agents silently executing irreversible actions are a critical safety and liability risk.",
            [n.get("name", "") for n in risky_agents],
            "Add a human approval gate before irreversible tool invocations: "
            "LangGraph: use interrupt_before/interrupt_after; "
            "CrewAI: set human_input=True on the task; "
            "OpenAI Assistants: handle the requires_action event; "
            "LangChain: add HumanApprovalCallbackHandler. "
            "Log all irreversible tool calls with the approving user and timestamp.",
        )
    ]


# ── NGA-013 ──────────────────────────────────────────────────────────────────


def _rule_nga013_no_k8s_network_policy(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """MEDIUM — No network policy for AI workload in K8s."""
    k8s_deployments = [
        n for n in nodes
        if n.get("component_type") in _DEPLOYMENT_TYPES
        and (
            _depl_meta(n).get("deployment_target", "").lower() in ("kubernetes", "k8s")
            or (n.get("metadata") or {}).get("iac_format", "").lower() == "kubernetes"
        )
        # Exclude NetworkPolicy marker nodes emitted by K8sAdapter
        and not (n.get("metadata") or {}).get("extras", {}).get("is_network_policy_namespace")
    ]
    if not k8s_deployments:
        return []

    # Cross-file NetworkPolicy coverage from summary
    policy_namespaces: set[str] = set(summary.get("k8s_network_policy_namespaces") or [])

    no_netpol = [
        n for n in k8s_deployments
        if not _depl_meta(n).get("has_network_policy")  # same-file coverage
        and (n.get("metadata") or {}).get("extras", {}).get("k8s_namespace", "") not in policy_namespaces
    ]
    if not no_netpol:
        return []

    return [
        _finding(
            "NGA-013", "MEDIUM",
            "No Kubernetes NetworkPolicy for AI workload",
            f"{len(no_netpol)} K8s deployment(s) have no NetworkPolicy detected. "
            "Without network policies, any compromised pod in the cluster can reach "
            "AI workloads directly, enabling lateral movement and data exfiltration.",
            [n.get("name", "") for n in no_netpol],
            "Define Kubernetes NetworkPolicy resources that restrict ingress/egress for "
            "AI workload pods to only required services. Default-deny all traffic and "
            "explicitly allow only necessary communication paths.",
        )
    ]


# ── NGA-014 ──────────────────────────────────────────────────────────────────


def _rule_nga014_actions_runner_debug(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """MEDIUM — GitHub Actions: ACTIONS_RUNNER_DEBUG secret exposed."""
    # Preferred: structured findings emitted by GitHubActionsAdapter
    structured = [
        f for f in (summary.get("workflow_security_findings") or [])
        if f.get("rule_signal") == "NGA-014"
    ]
    if structured:
        first = structured[0]
        return [
            _finding(
                "NGA-014", "MEDIUM",
                "GitHub Actions: ACTIONS_RUNNER_DEBUG secret exposed",
                f"Detected in {first['path']} (line {first['line']}): {first['snippet']}",
                ["GitHub Actions workflow"],
                "Remove 'ACTIONS_RUNNER_DEBUG' from workflow files and organisation secrets "
                "when not actively debugging. Use GitHub's built-in 'Enable debug logging' "
                "option in re-run settings instead of a persistent secret.",
            )
        ]

    # Fallback: raw regex on concatenated workflow content
    workflow_content: str = summary.get("github_actions_content") or ""
    if not workflow_content:
        for n in nodes:
            wf = _node_extras(n).get("workflow_content") or ""
            if _PATTERN_DEBUG_SECRET.search(wf):
                workflow_content = wf
                break

    if not workflow_content or not _PATTERN_DEBUG_SECRET.search(workflow_content):
        return []

    return [
        _finding(
            "NGA-014", "MEDIUM",
            "GitHub Actions: ACTIONS_RUNNER_DEBUG secret exposed",
            "The workflow references 'ACTIONS_RUNNER_DEBUG'. When set to 'true', "
            "this leaks verbose runner debug output to public workflow logs, including "
            "environment variables, file contents, and potentially secrets.",
            ["GitHub Actions workflow"],
            "Remove 'ACTIONS_RUNNER_DEBUG' from workflow files and organisation secrets "
            "when not actively debugging. Use GitHub's built-in 'Enable debug logging' "
            "option in re-run settings instead of a persistent secret.",
        )
    ]


# ── NGA-015 ──────────────────────────────────────────────────────────────────


def _rule_nga015_no_resource_limits(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """LOW — AI workloads deployed without CPU/memory resource limits."""
    security_findings: list[str] = summary.get("security_findings") or []
    depl_nodes = [n for n in nodes if n.get("component_type") == "DEPLOYMENT"]
    if not depl_nodes:
        return []

    def _has_no_limits(n: dict[str, Any]) -> bool:
        meta = _depl_meta(n)
        return meta.get("no_resource_limits") is True or meta.get("has_resource_limits") is False

    limited_nodes = [n for n in depl_nodes if _has_no_limits(n)]
    if not limited_nodes and "no_resource_limits" not in security_findings:
        return []
    affected = limited_nodes if limited_nodes else depl_nodes
    return [
        _finding(
            "NGA-015", "LOW",
            "AI workloads without resource limits",
            f"{len(affected)} deployment node(s) have no CPU or memory resource limits "
            "configured. Unbounded AI workloads can starve co-located services, "
            "trigger node OOM kills, and enable denial-of-service via runaway inference "
            "or prompt-flooding attacks.",
            [n.get("name", "") for n in affected],
            "Set explicit 'resources.requests' and 'resources.limits' for CPU and memory "
            "in every K8s workload spec. For serverless deployments configure concurrency "
            "and timeout limits. Consider LLM-specific limits such as max_tokens per "
            "request to cap inference cost and latency.",
        )
    ]


# ── NGA-016 ──────────────────────────────────────────────────────────────────


def _rule_nga016_latest_image_tag(
    nodes: list[dict[str, Any]], **_: Any
) -> list[dict[str, Any]]:
    """LOW — Container image using 'latest' tag."""
    latest_images = [
        n for n in nodes
        if n.get("component_type") in _CONTAINER_TYPES
        and (
            _depl_meta(n).get("image_tag", "").lower() in ("latest", "")
            or (n.get("name", "").endswith(":latest"))
            or (":" not in n.get("name", "") and n.get("name", ""))
        )
    ]
    if not latest_images:
        return []

    return [
        _finding(
            "NGA-016", "LOW",
            "Container image using 'latest' tag",
            f"{len(latest_images)} container image(s) use the 'latest' tag or have no "
            "explicit tag. The 'latest' tag is mutable — a registry push can silently "
            "change the running image, breaking reproducibility and enabling supply-chain "
            "attacks if the registry is compromised.",
            [n.get("name", "") for n in latest_images],
            "Pin all container images to an immutable digest (e.g. 'image@sha256:...') "
            "or a specific semantic version tag. Use image signing (Cosign/Notary) to "
            "verify provenance before deployment.",
        )
    ]


# ── NGA-017 ──────────────────────────────────────────────────────────────────


def _rule_nga017_missing_health_check(
    nodes: list[dict[str, Any]], **_: Any
) -> list[dict[str, Any]]:
    """LOW — AI workload missing health check."""
    no_health = [
        n for n in nodes
        if n.get("component_type") in (_DEPLOYMENT_TYPES | _CONTAINER_TYPES)
        and not _depl_meta(n).get("has_health_check")
    ]
    if not no_health:
        return []

    return [
        _finding(
            "NGA-017", "LOW",
            "AI workload missing health check",
            f"{len(no_health)} deployment/container node(s) have no health check configured. "
            "Without a health check, orchestration platforms cannot detect a hung or "
            "degraded AI service and will continue routing traffic to a broken instance.",
            [n.get("name", "") for n in no_health],
            "Add a HEALTHCHECK instruction to Dockerfiles and configure liveness/readiness "
            "probes in K8s pod specs. The health endpoint should verify the model is "
            "loaded and responding within an acceptable latency threshold.",
        )
    ]


# ── NGA-018 ──────────────────────────────────────────────────────────────────


def _rule_nga018_shared_datastore_no_iam_isolation(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """LOW — Multiple AI agents sharing a single datastore with no IAM isolation."""
    iam_ids = {n["id"] for n in nodes if n.get("component_type") in _IAM_TYPES}

    if graph is not None:
        # Build ds_id → {agent_id: (agent_node, interm_node | None)} mapping
        # using transitive accesses_paths (covers direct + via-tool + delegated)
        ds_to_agent_paths: dict[str, dict[str, tuple[dict[str, Any], dict[str, Any] | None]]] = {}
        for agent in graph.nodes_of_type("AGENT"):
            agent_id = str(agent["id"])
            for interm, ds, _at2 in graph.accesses_paths(agent_id):
                ds_id = str(ds["id"])
                ds_to_agent_paths.setdefault(ds_id, {})[agent_id] = (agent, interm)

        findings: list[dict[str, Any]] = []
        for ds in graph.nodes_of_type("DATASTORE"):
            ds_id = str(ds["id"])
            agent_map = ds_to_agent_paths.get(ds_id, {})
            if len(agent_map) < 2:
                continue
            # Check if IAM PROTECTS this datastore
            if graph.has_protection(ds_id):
                continue
            # Also skip if any IAM node exists in graph (legacy: any IAM = isolated)
            if iam_ids:
                continue
            ds_name = ds.get("name", "")
            indirect_paths = []
            affected_names = [ds_name]
            for agent_id, (agent, interm) in agent_map.items():
                agent_name = agent.get("name", "")
                affected_names.append(agent_name)
                if interm is not None:
                    indirect_paths.append(
                        f"'{agent_name}'→CALLS→'{interm.get('name', '')}'"
                        f"→ACCESSES→'{ds_name}'"
                    )
            indirect_note = (
                f" Indirect paths: {'; '.join(indirect_paths)}."
                if indirect_paths
                else ""
            )
            agent_names = [a.get("name", "") for a, _ in agent_map.values()]
            evidence = (
                f"Datastore '{ds_name}' is accessible by {len(agent_map)} agents "
                f"without IAM isolation: {', '.join(agent_names)}.{indirect_note}"
            )
            findings.append(_finding(
                "NGA-018", "LOW",
                f"Agents share datastore '{ds_name}' with no IAM isolation",
                evidence,
                affected_names,
                f"Add IAM policies to scope each agent's access to '{ds_name}'. "
                "Consider separate datastores or row-level security per tenant.",
                evidence=evidence,
            ))
        return findings

    # Fallback: direct-edge-only check
    datastore_ids = {n["id"] for n in nodes if n.get("component_type") in _DATASTORE_TYPES}
    agent_ids_set = {n["id"] for n in nodes if n.get("component_type") in _AGENT_TYPES}
    ds_to_agents: dict[str, set[str]] = {ds_id: set() for ds_id in datastore_ids}
    for e in edges:
        src, tgt = e.get("source", ""), e.get("target", "")
        if src in agent_ids_set and tgt in datastore_ids:
            ds_to_agents[tgt].add(src)
        elif tgt in agent_ids_set and src in datastore_ids:
            ds_to_agents[src].add(tgt)
    shared_no_iam = [
        ds_id for ds_id, agents in ds_to_agents.items()
        if len(agents) >= 2 and not iam_ids
    ]
    if not shared_no_iam:
        return []
    affected_names = [
        n.get("name", ds_id)
        for ds_id in shared_no_iam
        for n in nodes if n["id"] == ds_id
    ]
    return [
        _finding(
            "NGA-018", "LOW",
            "Multiple agents share a datastore with no IAM isolation",
            f"{len(shared_no_iam)} datastore(s) are accessed by 2 or more agent(s) "
            "with no IAM node detected to differentiate access rights. A compromised "
            "or misbehaving agent can read or overwrite another agent's data.",
            affected_names,
            "Create separate IAM roles/database users with distinct permissions for each "
            "agent. Use row-level security (RLS) or collection-level access control in "
            "vector stores to prevent one agent's queries from returning another's data.",
        )
    ]


# ── NGA-019 ──────────────────────────────────────────────────────────────────


def _rule_nga019_unguarded_write_to_sensitive_datastore(
    nodes: list[dict[str, Any]],
    summary: dict[str, Any],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """HIGH — Agent can write to a PII/PHI datastore with no guardrail on the path."""
    if graph is None:
        return []  # Requires graph traversal; no fallback

    dc_labels: list[str] = summary.get("data_classification") or []
    if not _has_phi_pii(dc_labels):
        # Quick gate: skip if SBOM summary has no PII/PHI at all
        pass  # Still check node-level labels below

    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for agent in graph.nodes_of_type("AGENT"):
        agent_id = str(agent["id"])
        agent_name = agent.get("name", "")
        for interm, ds, access_type in graph.accesses_paths(agent_id):
            if (access_type or "").lower() not in ("write", "readwrite"):
                continue
            ds_id = str(ds["id"])
            ds_meta = ds.get("metadata") or {}
            ds_labels: list[str] = (
                ds_meta.get("data_classification")
                or ds_meta.get("extras", {}).get("data_classification")
                or []
            )
            if not _has_phi_pii(ds_labels):
                continue
            pair = (agent_id, ds_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            ds_name = ds.get("name", "")
            path_nodes = [agent] + ([interm] if interm else []) + [ds]
            if any(graph.has_protection(str(n["id"])) for n in path_nodes):
                continue
            interm_name = interm.get("name", "") if interm else None
            if interm_name:
                path = (f"'{agent_name}' → CALLS → '{interm_name}' "
                        f"→ ACCESSES[{access_type}] → '{ds_name}' "
                        f"(data_classification={ds_labels}). No guardrail on this path.")
                affected = [agent_name, interm_name, ds_name]
                remediation = (
                    f"Add a guardrail or validation step on '{interm_name}' before "
                    f"it writes to '{ds_name}'. Apply schema validation, field-level "
                    "access control, and audit logging for all write operations."
                )
            else:
                path = (f"'{agent_name}' → ACCESSES[{access_type}] → '{ds_name}' "
                        f"(data_classification={ds_labels}). No guardrail on this path.")
                affected = [agent_name, ds_name]
                remediation = (
                    f"Add a guardrail or validation step before '{agent_name}' "
                    f"writes to '{ds_name}'. Apply schema validation, field-level "
                    "access control, and audit logging for all write operations."
                )
            findings.append(_finding(
                "NGA-019", "HIGH",
                f"Unguarded write path to sensitive datastore: '{ds_name}'",
                path,
                affected,
                remediation,
                evidence=path,
            ))
    return findings


# ── NGA-020 ──────────────────────────────────────────────────────────────────


def _rule_nga020_unguarded_agent_delegation(
    nodes: list[dict[str, Any]],
    graph: AnalysisGraph | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """MEDIUM — Agent DELEGATES_TO another agent with no guardrail on either side."""
    if graph is None:
        return []  # Requires graph traversal; no fallback

    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for src_agent in graph.nodes_of_type("AGENT"):
        src_id = str(src_agent["id"])
        for tgt_agent in graph.targets(src_id, "DELEGATES_TO"):
            tgt_id = str(tgt_agent["id"])
            pair = (src_id, tgt_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if graph.has_protection(src_id) or graph.has_protection(tgt_id):
                continue
            src_name = src_agent.get("name", "")
            tgt_name = tgt_agent.get("name", "")
            evidence = (
                f"'{src_name}' DELEGATES_TO '{tgt_name}'. "
                "Neither agent has a GUARDRAIL on its PROTECTS path."
            )
            findings.append(_finding(
                "NGA-020", "MEDIUM",
                f"Unguarded delegation: '{src_name}' → '{tgt_name}'",
                evidence,
                [src_name, tgt_name],
                f"Insert a guardrail between '{src_name}' and '{tgt_name}' to block "
                "prompt injection from propagating across the delegation boundary.",
                evidence=evidence,
            ))
    return findings


# ── Rule registry ─────────────────────────────────────────────────────────────

_RULES: list[Callable[..., list[dict[str, Any]]]] = [
    _rule_nga001_phi_to_external_llm,           # NGA-001 CRITICAL
    _rule_nga002_insufficient_guardrails,        # NGA-002 HIGH
    _rule_nga003_secrets_in_env,                 # NGA-003 HIGH
    _rule_nga004_runs_as_root,                   # NGA-004 HIGH
    _rule_nga005_unencrypted_pii_datastore,      # NGA-005 HIGH
    _rule_nga006_missing_auth_on_api_endpoint,   # NGA-006 HIGH
    _rule_nga007_overly_permissive_iam,          # NGA-007 HIGH
    _rule_nga008_untrusted_model_registry,       # NGA-008 HIGH
    _rule_nga009_no_audit_logging,               # NGA-009 HIGH
    _rule_nga010_pr_target_injection,            # NGA-010 HIGH
    _rule_nga011_github_env_injection,           # NGA-011 HIGH
    _rule_nga012_missing_hitl,                   # NGA-012 HIGH
    _rule_nga013_no_k8s_network_policy,          # NGA-013 MEDIUM
    _rule_nga014_actions_runner_debug,           # NGA-014 MEDIUM
    _rule_nga015_no_resource_limits,             # NGA-015 LOW
    _rule_nga016_latest_image_tag,               # NGA-016 LOW
    _rule_nga017_missing_health_check,           # NGA-017 LOW
    _rule_nga018_shared_datastore_no_iam_isolation,  # NGA-018 LOW
    _rule_nga019_unguarded_write_to_sensitive_datastore,  # NGA-019 HIGH
    _rule_nga020_unguarded_agent_delegation,         # NGA-020 MEDIUM
]

# Per-rule metadata used by verbose audit mode (parallel to _RULES).
_RULE_META: list[dict[str, str]] = [
    {
        "rule_id": "NGA-001", "severity": "CRITICAL",
        "title": "PII/PHI data handled by external LLM providers",
        "checks": "SBOM data_classification × MODEL nodes with external provider",
        "pass_reason": "No PII/PHI data classification found, or no external MODEL provider detected",
    },
    {
        "rule_id": "NGA-002", "severity": "HIGH",
        "title": "Insufficient guardrail coverage",
        "checks": "MODEL nodes and AGENT→API_ENDPOINT edges vs. GUARDRAIL nodes",
        "pass_reason": "All MODEL/AGENT paths have at least one GUARDRAIL node",
    },
    {
        "rule_id": "NGA-003", "severity": "HIGH",
        "title": "Secrets or credentials exposed in environment variables",
        "checks": "SBOM env_vars for high-entropy or secret-named values",
        "pass_reason": "No secret-like environment variables detected in SBOM",
    },
    {
        "rule_id": "NGA-004", "severity": "HIGH",
        "title": "Container runs as root",
        "checks": "CONTAINER_IMAGE nodes for runs_as_root flag or missing non-root user",
        "pass_reason": "No containers detected running as root",
    },
    {
        "rule_id": "NGA-005", "severity": "HIGH",
        "title": "PII/PHI stored in unencrypted datastore",
        "checks": "DATASTORE nodes for encryption_at_rest flag × PII/PHI data classification",
        "pass_reason": "All datastores have encryption at rest, or no PII/PHI classification found",
    },
    {
        "rule_id": "NGA-006", "severity": "HIGH",
        "title": "API endpoint missing authentication",
        "checks": "API_ENDPOINT nodes for missing AUTH edge coverage",
        "pass_reason": "All API endpoints are covered by at least one AUTH node",
    },
    {
        "rule_id": "NGA-007", "severity": "HIGH",
        "title": "Overly permissive IAM role",
        "checks": "IAM nodes for wildcard permissions or admin-level grants",
        "pass_reason": "No IAM nodes with wildcard or admin permissions detected",
    },
    {
        "rule_id": "NGA-008", "severity": "HIGH",
        "title": "Model loaded from untrusted or unverified registry",
        "checks": "MODEL nodes for registry source and integrity verification",
        "pass_reason": "All MODEL nodes sourced from trusted registries with verification",
    },
    {
        "rule_id": "NGA-009", "severity": "HIGH",
        "title": "No audit logging configured",
        "checks": "SBOM for audit_logging flag and DATASTORE/API_ENDPOINT coverage",
        "pass_reason": "Audit logging is configured in the SBOM",
    },
    {
        "rule_id": "NGA-010", "severity": "HIGH",
        "title": "GitHub Actions pull_request_target injection risk",
        "checks": "GitHub Actions workflow files for pull_request_target trigger with dangerous patterns",
        "pass_reason": "No pull_request_target injection patterns found in CI workflows",
    },
    {
        "rule_id": "NGA-011", "severity": "HIGH",
        "title": "GitHub Actions environment variable injection",
        "checks": "GitHub Actions workflow files for unsanitised env variable injection",
        "pass_reason": "No environment variable injection patterns found in CI workflows",
    },
    {
        "rule_id": "NGA-012", "severity": "HIGH",
        "title": "Agent pipeline lacks HITL approval for high-risk tool actions",
        "checks": "AGENT nodes with irreversible/high-impact TOOL edges for HITL approval gates",
        "pass_reason": "All high-risk tool invocations have a human-in-the-loop approval gate",
    },
    {
        "rule_id": "NGA-013", "severity": "MEDIUM",
        "title": "Kubernetes deployment missing NetworkPolicy",
        "checks": "Kubernetes DEPLOYMENT nodes for NetworkPolicy coverage",
        "pass_reason": "All Kubernetes deployments have NetworkPolicy configured",
    },
    {
        "rule_id": "NGA-014", "severity": "MEDIUM",
        "title": "GitHub Actions runner debug mode enabled",
        "checks": "GitHub Actions workflows for ACTIONS_RUNNER_DEBUG or ACTIONS_STEP_DEBUG set to true",
        "pass_reason": "No debug mode enabled in GitHub Actions runner configuration",
    },
    {
        "rule_id": "NGA-015", "severity": "LOW",
        "title": "Container missing resource limits",
        "checks": "CONTAINER_IMAGE / DEPLOYMENT nodes for CPU/memory resource limits",
        "pass_reason": "All containers have CPU and memory resource limits defined",
    },
    {
        "rule_id": "NGA-016", "severity": "LOW",
        "title": "Container image uses 'latest' tag",
        "checks": "CONTAINER_IMAGE nodes for unversioned 'latest' image tag",
        "pass_reason": "All container images use pinned version tags",
    },
    {
        "rule_id": "NGA-017", "severity": "LOW",
        "title": "Container missing health check",
        "checks": "CONTAINER_IMAGE / DEPLOYMENT nodes for health check configuration",
        "pass_reason": "All containers have health checks configured",
    },
    {
        "rule_id": "NGA-018", "severity": "LOW",
        "title": "Shared datastore without IAM isolation",
        "checks": "DATASTORE nodes shared across AGENT/TOOL boundaries for IAM isolation",
        "pass_reason": "All shared datastores have IAM isolation or are not cross-boundary",
    },
    {
        "rule_id": "NGA-019", "severity": "HIGH",
        "title": "Unguarded write path to sensitive datastore",
        "checks": "AGENT→[CALLS→TOOL]→ACCESSES[write]→DATASTORE[PII/PHI] paths with no guardrail",
        "pass_reason": "No unguarded write paths to PII/PHI datastores detected",
    },
    {
        "rule_id": "NGA-020", "severity": "MEDIUM",
        "title": "Unguarded agent delegation chain",
        "checks": "AGENT→DELEGATES_TO→AGENT edges where neither side has guardrail coverage",
        "pass_reason": "All agent delegation chains have at least one guardrail boundary",
    },
]


# ── Pass evidence builder ─────────────────────────────────────────────────────


def _build_pass_evidence(
    rule_idx: int,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Return SBOM-derived evidence for a rule that passed (parallel to _RULE_META[rule_idx]).

    Called only when the corresponding rule returns no findings, so this function
    documents *what was examined* and *what was found* rather than what failed.
    All helpers (_depl_meta, _node_extras, etc.) are reused as-is.
    """
    rule_id = _RULE_META[rule_idx]["rule_id"]

    if rule_id == "NGA-001":
        dc_labels: list[str] = summary.get("data_classification") or []
        model_nodes = [n for n in nodes if n.get("component_type") in _MODEL_TYPES]
        return {
            "data_classification_labels": dc_labels or ["none"],
            "model_nodes_checked": [n.get("name", "") for n in model_nodes],
            "phi_pii_detected": _has_phi_pii(dc_labels),
        }

    if rule_id == "NGA-002":
        return {
            "guardrail_nodes": [n.get("name", "") for n in nodes if n.get("component_type") in _GUARDRAIL_TYPES],
            "model_nodes": [n.get("name", "") for n in nodes if n.get("component_type") in _MODEL_TYPES],
            "agent_nodes": [n.get("name", "") for n in nodes if n.get("component_type") in _AGENT_TYPES],
        }

    if rule_id == "NGA-003":
        return {
            "deployment_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") == "DEPLOYMENT"],
            "secret_stores_configured": summary.get("secret_stores") or [],
            "secrets_in_env_found": "secrets_in_env_vars" in (summary.get("security_findings") or []),
        }

    if rule_id == "NGA-004":
        return {
            "container_or_deployment_nodes_checked": [
                n.get("name", "") for n in nodes
                if n.get("component_type") in ("DEPLOYMENT", "CONTAINER_IMAGE")
            ],
            "runs_as_root_detected": False,
        }

    if rule_id == "NGA-005":
        dc_labels = summary.get("data_classification") or []
        return {
            "data_classification_labels": dc_labels or ["none"],
            "datastore_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _DATASTORE_TYPES],
            "unencrypted_datastores_found": 0,
        }

    if rule_id == "NGA-006":
        return {
            "api_endpoints_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _API_ENDPOINT_TYPES],
            "auth_nodes_found": [n.get("name", "") for n in nodes if n.get("component_type") == "AUTH"],
        }

    if rule_id == "NGA-007":
        depl_ids = {n["id"] for n in nodes if n.get("component_type") in _DEPLOYMENT_TYPES}
        iam_attached: list[str] = []
        for e in edges:
            if e.get("source") in depl_ids:
                tgt = next((n for n in nodes if n["id"] == e.get("target")), None)
                if tgt and tgt.get("component_type") in _IAM_TYPES:
                    iam_attached.append(tgt.get("name", ""))
            elif e.get("target") in depl_ids:
                src = next((n for n in nodes if n["id"] == e.get("source")), None)
                if src and src.get("component_type") in _IAM_TYPES:
                    iam_attached.append(src.get("name", ""))
        return {
            "deployment_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _DEPLOYMENT_TYPES],
            "iam_nodes_checked": list(set(iam_attached)),
            "overpermissive_roles_found": 0,
        }

    if rule_id == "NGA-008":
        trusted = _DEFAULT_TRUSTED_REGISTRIES | set(summary.get("trusted_registries") or [])
        return {
            "model_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _MODEL_TYPES],
            "trusted_registries": sorted(trusted),
        }

    if rule_id == "NGA-009":
        frameworks = [f.lower() for f in (summary.get("frameworks") or [])]
        return {
            "agent_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _AGENT_TYPES],
            "audit_libraries_found": [lib for lib in _AUDIT_LIBS if lib in frameworks],
            "log_paths_found": summary.get("log_paths") or [],
        }

    if rule_id == "NGA-010":
        return {
            "github_actions_content_present": bool(summary.get("github_actions_content")),
            "pull_request_target_injection_found": False,
        }

    if rule_id == "NGA-011":
        return {
            "github_actions_content_present": bool(summary.get("github_actions_content")),
            "env_injection_patterns_found": False,
        }

    if rule_id == "NGA-012":
        tool_names = [n.get("name", "") for n in nodes if n.get("component_type") == "TOOL"]
        irreversible_tools = [
            n.get("name", "") for n in nodes
            if n.get("component_type") == "TOOL"
            and _IRREVERSIBLE_TOOL_PATTERNS.search(n.get("name", "") + " " + str(n.get("metadata", {})))
        ]
        return {
            "agents_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _AGENT_TYPES],
            "tools_checked": tool_names,
            "irreversible_tools_found": irreversible_tools,
        }

    if rule_id == "NGA-013":
        k8s = [
            n.get("name", "") for n in nodes
            if n.get("component_type") in _DEPLOYMENT_TYPES
            and (
                _depl_meta(n).get("deployment_target", "").lower() in ("kubernetes", "k8s")
                or (n.get("metadata") or {}).get("iac_format", "").lower() == "kubernetes"
            )
        ]
        return {"k8s_deployment_nodes_checked": k8s}

    if rule_id == "NGA-014":
        return {
            "github_actions_content_present": bool(summary.get("github_actions_content")),
            "debug_mode_patterns_found": False,
        }

    if rule_id == "NGA-015":
        return {
            "deployment_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") == "DEPLOYMENT"],
            "all_have_resource_limits": True,
        }

    if rule_id == "NGA-016":
        return {
            "container_image_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _CONTAINER_TYPES],
            "all_images_version_pinned": True,
        }

    if rule_id == "NGA-017":
        return {
            "deployment_and_container_nodes_checked": [
                n.get("name", "") for n in nodes
                if n.get("component_type") in (_DEPLOYMENT_TYPES | _CONTAINER_TYPES)
            ],
            "all_have_health_check": True,
        }

    if rule_id == "NGA-018":
        return {
            "datastore_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _DATASTORE_TYPES],
            "agent_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _AGENT_TYPES],
            "iam_nodes_found": [n.get("name", "") for n in nodes if n.get("component_type") in _IAM_TYPES],
        }

    if rule_id == "NGA-019":
        return {
            "agent_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _AGENT_TYPES],
            "datastore_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _DATASTORE_TYPES],
            "requires_graph": True,
        }

    if rule_id == "NGA-020":
        return {
            "agent_nodes_checked": [n.get("name", "") for n in nodes if n.get("component_type") in _AGENT_TYPES],
            "requires_graph": True,
        }

    return {}


# ── OSV / Grype finding converters ───────────────────────────────────────────

_RE_FIXED_VERSION = re.compile(r"<([^\s,;]+)")


def _extract_fixed_version(affected_versions: str) -> str | None:
    """Extract the first fixed version from a range string such as '>=1.0,<2.0'."""
    m = _RE_FIXED_VERSION.search(affected_versions or "")
    return m.group(1) if m else None


def _osv_to_finding(osv: dict[str, Any]) -> dict[str, Any]:
    """Convert an osv_client result dict to the standard finding shape."""
    cve_ids = osv.get("cve_ids") or []
    adv_id = osv.get("advisory_id", "")
    title = f"Known vulnerability in {osv.get('dep_name', '?')} ({adv_id})"
    if cve_ids:
        title += f" [{', '.join(cve_ids[:2])}]"
    affected_versions = osv.get("affected_versions", "see advisory")
    fixed_version = _extract_fixed_version(affected_versions)
    fixed_note = f"  Fixed in: {fixed_version}." if fixed_version else ""
    return {
        "rule_id": adv_id,
        "severity": osv.get("severity", "UNKNOWN"),
        "title": title,
        "description": (
            f"{osv.get('summary', adv_id)}  "
            f"Affected versions: {affected_versions}.{fixed_note}  "
            f"Package: {osv.get('dep_name')} {osv.get('dep_version', '')}."
        ),
        "affected": [osv.get("purl", osv.get("dep_name", "?"))],
        "remediation": (
            f"Upgrade {osv.get('dep_name')} to a version outside the affected range"
            + (f" (fix available: {fixed_version})" if fixed_version else "")
            + f". See {osv.get('url', 'https://osv.dev')} for details."
        ),
        "source": "osv",
        "advisory_url": osv.get("url"),
        "cve_ids": cve_ids,
        "fixed_version": fixed_version,
    }


def _grype_to_finding(grype: dict[str, Any]) -> dict[str, Any]:
    """Convert a grype_client result dict to the standard finding shape."""
    cve_ids = grype.get("cve_ids") or []
    adv_id = grype.get("advisory_id", "")
    title = f"Known vulnerability in {grype.get('dep_name', '?')} ({adv_id})"
    if cve_ids:
        title += f" [{', '.join(cve_ids[:2])}]"
    target = grype.get("scan_target", "")
    target_note = f" (image: {target})" if target and target != "sbom" else ""
    affected_versions = grype.get("affected_versions", "see advisory")
    fixed_version = (
        affected_versions.lstrip("<").strip()
        if isinstance(affected_versions, str) and affected_versions.startswith("<")
        else _extract_fixed_version(affected_versions)
    )
    fixed_note = f"  Fixed in: {fixed_version}." if fixed_version else ""
    return {
        "rule_id": adv_id,
        "severity": grype.get("severity", "UNKNOWN"),
        "title": title,
        "description": (
            f"{grype.get('summary', adv_id)}  "
            f"Affected versions: {affected_versions}.{fixed_note}  "
            f"Package: {grype.get('dep_name')} {grype.get('dep_version', '')}."
            f"{target_note}"
        ),
        "affected": [grype.get("purl", grype.get("dep_name", "?"))],
        "remediation": (
            f"Upgrade {grype.get('dep_name')} to a version outside the affected range"
            + (f" (fix available: {fixed_version})" if fixed_version else "")
            + f". See {grype.get('url', 'https://github.com/anchore/grype')} for details."
        ),
        "source": "grype",
        "advisory_url": grype.get("url"),
        "cve_ids": cve_ids,
        "fixed_version": fixed_version,
    }


# ── Infrastructure rule N/A detection ────────────────────────────────────────


def _rule_is_not_applicable(rule_id: str, nodes: list[dict[str, Any]]) -> bool:
    """Return True when an infrastructure rule cannot fire because relevant nodes are absent.

    Used by the verbose audit loop to emit "N/A" instead of "PASS" for rules that
    check container/K8s components that simply don't exist in this SBOM.
    """
    if rule_id == "NGA-004":
        return not any(n.get("component_type") in ("DEPLOYMENT", "CONTAINER_IMAGE") for n in nodes)
    if rule_id == "NGA-013":
        return not any(
            n.get("component_type") == "DEPLOYMENT"
            and _depl_meta(n).get("deployment_target", "").lower() in ("kubernetes", "k8s")
            for n in nodes
        )
    if rule_id == "NGA-015":
        return not any(n.get("component_type") == "DEPLOYMENT" for n in nodes)
    if rule_id == "NGA-016":
        return not any(n.get("component_type") == "CONTAINER_IMAGE" for n in nodes)
    if rule_id == "NGA-017":
        return not any(n.get("component_type") in ("DEPLOYMENT", "CONTAINER_IMAGE") for n in nodes)
    return False


# ── Plugin ────────────────────────────────────────────────────────────────────


class NgaRulesPlugin(AnalysisPlugin):
    """Run all NGA structural rules + optional OSV/Grype dep scans."""

    name = "nga_rules"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        nodes = sbom.get("nodes") or []
        edges = sbom.get("edges") or []
        summary = sbom.get("summary") or {}
        deps = sbom.get("deps") or []
        provider = config.get("provider", "all")
        timeout = float(config.get("timeout", 15.0))

        # Phase 1: structural NGA rules
        graph: AnalysisGraph | None = config.get("sbom_graph")
        ctx = {"nodes": nodes, "edges": edges, "summary": summary, "graph": graph}
        findings: list[dict[str, Any]] = []
        verbose = bool(config.get("verbose"))
        rule_audit: list[dict[str, Any]] = []
        for i, rule in enumerate(_RULES):
            try:
                rule_findings = rule(**ctx)
                findings.extend(rule_findings)
                if verbose:
                    meta = _RULE_META[i]
                    is_pass = not rule_findings
                    rule_id_str = meta["rule_id"]
                    not_applicable = is_pass and _rule_is_not_applicable(rule_id_str, nodes)
                    if not_applicable:
                        audit_status = "N/A"
                    elif is_pass:
                        audit_status = "PASS"
                    else:
                        audit_status = "FAIL"
                    rule_audit.append({
                        "rule_id": rule_id_str,
                        "severity": meta["severity"],
                        "title": meta["title"],
                        "checks": meta["checks"],
                        "status": audit_status,
                        "finding_count": len(rule_findings),
                        "pass_reason": meta["pass_reason"] if is_pass and not not_applicable else "",
                        "pass_evidence": _build_pass_evidence(i, nodes, edges, summary) if (is_pass and not not_applicable) else {},
                        "affected": list({
                            f.get("affected_component") or ""
                            for f in rule_findings
                            if f.get("affected_component")
                        }),
                    })
            except Exception as exc:
                _log.warning("NGA rule %s raised an error and was skipped: %s", rule.__name__, exc)
                if verbose:
                    meta = _RULE_META[i]
                    rule_audit.append({
                        "rule_id": meta["rule_id"],
                        "severity": meta["severity"],
                        "title": meta["title"],
                        "checks": meta["checks"],
                        "status": "ERROR",
                        "finding_count": 0,
                        "pass_reason": f"Rule raised an error: {exc}",
                        "affected": [],
                    })
        _log.info("NGA structural rules: %d finding(s)", len(findings))

        # Phase 2: OSV dep scan
        osv_findings: list[dict[str, Any]] = []
        if provider in ("osv", "all"):
            from nuguard.analysis.osv_client import query_osv
            _log.info("Querying OSV for %d dep(s)", len(deps))
            for osv in query_osv(deps, timeout=timeout):
                osv_findings.append(_osv_to_finding(osv))
            _log.info("OSV: %d advisory finding(s)", len(osv_findings))

        # Phase 3: Grype scan
        grype_findings: list[dict[str, Any]] = []
        if provider in ("grype", "all"):
            from nuguard.analysis.grype_client import query_grype_images, query_grype_sbom
            grype_timeout = float(config.get("grype_timeout", 60.0))
            _log.info("Running grype sbom scan")
            for g in query_grype_sbom(sbom, timeout=grype_timeout):
                grype_findings.append(_grype_to_finding(g))
            container_nodes = [n for n in nodes if n.get("component_type") == "CONTAINER_IMAGE"]
            if container_nodes:
                for g in query_grype_images(container_nodes, timeout=grype_timeout):
                    grype_findings.append(_grype_to_finding(g))
            _log.info("Grype: %d finding(s)", len(grype_findings))

        all_findings = findings + osv_findings + grype_findings
        all_findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.get("severity", "INFO"), 99))

        counts: dict[str, int] = {}
        for f in all_findings:
            sev = f.get("severity", "UNKNOWN")
            counts[sev] = counts.get(sev, 0) + 1

        confirmed_critical = any(
            f.get("source") in ("osv", "grype") and f.get("severity") in ("CRITICAL", "HIGH")
            for f in (osv_findings + grype_findings)
        )
        if confirmed_critical:
            status = "failed"
        elif all_findings:
            status = "warning"
        else:
            status = "ok"

        msg_parts = [f"{v} {k}" for k, v in counts.items() if v]
        return AnalysisResult(
            status=status,
            plugin=self.name,
            message=(f"Found {len(all_findings)} finding(s): " + ", ".join(msg_parts))
            if all_findings else "No vulnerabilities detected",
            findings=all_findings,
            details={
                "provider": provider,
                "summary": {
                    "total": len(all_findings),
                    "structural": len(findings),
                    "dep_advisories": len(osv_findings) + len(grype_findings),
                    **{k.lower(): v for k, v in counts.items()},
                },
                **({"rule_audit": rule_audit} if rule_audit else {}),
            },
        )
