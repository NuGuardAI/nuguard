"""NuGuard AI (NGA) structural analysis rules for AI SBOMs.

Runs deterministic, offline checks against the SBOM graph and metadata.
Every finding carries a stable rule ID (NGA-xxx), severity, affected
components, and a remediation hint.

NGA-001  PII/PHI data handled by external LLM providers        CRITICAL
NGA-002  Insufficient guardrail coverage                        HIGH (two sub-checks)
NGA-003  Secrets exposed as env vars or no secret store         HIGH
NGA-004  Containers / K8s workloads running as root             HIGH
NGA-005  AI workloads without CPU/memory resource limits        LOW
NGA-006  Unencrypted datastore containing PII/PHI              HIGH
NGA-007  Missing authentication on external AI API endpoint     HIGH
NGA-008  (retired — absorbed into NGA-002 sub-check B)
NGA-009  Overly permissive IAM role for AI workload             HIGH
NGA-010  No network policy for AI workload in K8s               MEDIUM
NGA-011  LLM model weight loaded from untrusted registry        HIGH
NGA-012  Container image using latest tag                       LOW
NGA-013  AI workload missing health check                       LOW
NGA-014  Multiple AI agents sharing a datastore, no IAM isolation  LOW
NGA-015  AI application has no audit logging enabled            HIGH
NGA-016  GitHub Actions: pull_request_target with untrusted injection  HIGH
NGA-017  GitHub Actions: GITHUB_ENV written from untrusted input  HIGH
NGA-018  GitHub Actions: ACTIONS_RUNNER_DEBUG secret exposed    MEDIUM
NGA-019  Agent pipeline lacks HITL approval for high-risk actions  HIGH
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin

_log = logging.getLogger("analysis.nga_rules")

# ── Severity ordering ────────────────────────────────────────────────────────
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

# ── External LLM providers whose APIs leave your trust boundary ───────────────
_EXTERNAL_PROVIDERS = {
    "openai", "anthropic", "google", "cohere", "mistral",
    "deepseek", "ai21", "amazon", "azure",
}

# ── Trusted model registries (NGA-011) ───────────────────────────────────────
_DEFAULT_TRUSTED_REGISTRIES = {"huggingface.co", "ollama.ai"}

# ── Irreversible tool name patterns (NGA-019) ─────────────────────────────────
_IRREVERSIBLE_TOOL_PATTERNS = re.compile(
    r"send[_\-]?email|delete[_\-]?|drop[_\-]?|execute[_\-]?sql|"
    r"write[_\-]?|charge[_\-]?|create[_\-]?payment|pay[_\-]?|"
    r"post[_\-]?|publish[_\-]?|rm[_\-]?|remove[_\-]?|destroy[_\-]?",
    re.IGNORECASE,
)

# ── HITL pattern indicators in AGENT metadata (NGA-019) ──────────────────────
_HITL_PATTERNS = {
    "interrupt", "interrupt_before", "interrupt_after",
    "human_input", "requires_action", "HumanApprovalCallbackHandler",
    "hitl", "human_in_the_loop", "human_approval",
}

# ── GitHub Actions patterns (NGA-016/017/018) ─────────────────────────────────
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
        "has_resource_limits", "ha_mode", "availability_zones",
        "iam_type", "permissions", "iam_scope", "trust_principals",
        "base_image", "image_name", "image_tag",
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
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """CRITICAL — PHI/PII data present while external LLM providers are used."""
    dc_labels: list[str] = summary.get("data_classification") or []
    if not _has_phi_pii(dc_labels):
        return []

    external_models: list[str] = []
    for n in nodes:
        if n.get("component_type", "") not in _MODEL_TYPES:
            continue
        extras = _node_extras(n)
        provider = (extras.get("provider") or "").lower()
        if any(ep in provider for ep in _EXTERNAL_PROVIDERS):
            external_models.append(n.get("name", ""))

    if not external_models:
        return []

    phi_tables: list[str] = summary.get("classified_tables") or []
    return [
        _finding(
            "NGA-001", "CRITICAL",
            "PII/PHI data handled by external LLM providers",
            f"The SBOM contains {', '.join(sorted(set(dc_labels)))} data "
            f"({len(phi_tables)} classified table(s)) and calls external LLM "
            f"provider(s): {', '.join(external_models)}. Regulated data (PII/PHI) may be "
            "transmitted outside your trust boundary, potentially violating applicable "
            "data protection regulations.",
            external_models,
            "Ensure regulated data is stripped or anonymised before being included in prompts "
            "sent to external providers. Consider a self-hosted model for sensitive workloads "
            "or establish a data processing agreement (DPA) with each provider.",
        )
    ]


# ── NGA-002 ──────────────────────────────────────────────────────────────────


def _rule_nga002_insufficient_guardrails(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — Insufficient guardrail coverage (two sub-checks).

    Sub-check A: LLM MODEL node with no reachable GUARDRAIL node in the graph.
    Sub-check B: AGENT with outbound external API_ENDPOINT edge and no GUARDRAIL.
    """
    findings: list[dict[str, Any]] = []
    guardrail_ids = {n["id"] for n in nodes if n.get("component_type") in _GUARDRAIL_TYPES}

    # Sub-check A: MODEL with no guardrail anywhere in graph
    model_nodes = [n for n in nodes if n.get("component_type") in _MODEL_TYPES]
    if model_nodes and not guardrail_ids:
        findings.append(_finding(
            "NGA-002", "HIGH",
            "LLM models with no output guardrail (sub-check A)",
            f"{len(model_nodes)} LLM model node(s) produce output with no output-validation "
            "or guardrail step detected anywhere in the SBOM graph.",
            [n.get("name", "") for n in model_nodes],
            "Implement structured output parsing and validation. Add a GUARDRAIL component "
            "(response classifier, PII filter, or output validator) between model output "
            "and downstream consumers.",
        ))

    # Sub-check B: AGENT with outbound API_ENDPOINT edge and no guardrail
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
            findings.append(_finding(
                "NGA-002", "HIGH",
                "Internet-capable agent with no output guardrail (sub-check B)",
                f"{len(agent_nodes_outbound)} agent(s) make outbound API calls with no output "
                "guardrail detected. Internet-capable agents without output filtering can "
                "exfiltrate data or be manipulated by adversarial external content.",
                [n.get("name", "") for n in agent_nodes_outbound],
                "Add an output guardrail or content filter between the agent and any external "
                "API endpoints it calls. Log all outbound requests for audit purposes.",
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


def _rule_nga005_no_resource_limits(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """LOW — AI workloads deployed without CPU/memory resource limits."""
    security_findings: list[str] = summary.get("security_findings") or []
    depl_nodes = [n for n in nodes if n.get("component_type") == "DEPLOYMENT"]
    if not depl_nodes:
        return []
    limited_nodes = [n for n in depl_nodes if _depl_meta(n).get("no_resource_limits") is True]
    if not limited_nodes and "no_resource_limits" not in security_findings:
        return []
    affected = limited_nodes if limited_nodes else depl_nodes
    return [
        _finding(
            "NGA-005", "LOW",
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


# ── NGA-006 ──────────────────────────────────────────────────────────────────


def _rule_nga006_unencrypted_pii_datastore(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
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

    return [
        _finding(
            "NGA-006", "HIGH",
            "Unencrypted datastore containing PII/PHI",
            f"{len(unencrypted)} datastore(s) containing PII/PHI data have "
            "'encryption_at_rest=false'. Unencrypted data stores expose sensitive data "
            "if underlying storage media is accessed or storage backups are leaked.",
            [n.get("name", "") for n in unencrypted],
            "Enable encryption at rest for all datastores containing PII or PHI. "
            "For managed databases (RDS, Cloud SQL, Cosmos DB) enable the built-in "
            "encryption option. Rotate encryption keys on a schedule and store them "
            "in a dedicated key management service (KMS).",
        )
    ]


# ── NGA-007 ──────────────────────────────────────────────────────────────────


def _rule_nga007_missing_auth_on_api_endpoint(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — Missing authentication on external AI API endpoint."""
    auth_ids = {n["id"] for n in nodes if n.get("component_type") == "AUTH"}
    guardrail_ids = {n["id"] for n in nodes if n.get("component_type") in _GUARDRAIL_TYPES}
    protected_targets = {e.get("target") for e in edges if e.get("source") in auth_ids | guardrail_ids}

    unprotected = [
        n for n in nodes
        if n.get("component_type") in _API_ENDPOINT_TYPES
        and n["id"] not in protected_targets
        and (_node_extras(n).get("no_auth_required") is True
             or not any(e.get("target") == n["id"] for e in edges))
    ]
    if not unprotected:
        return []

    return [
        _finding(
            "NGA-007", "HIGH",
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


# ── NGA-009 ──────────────────────────────────────────────────────────────────


def _rule_nga009_overly_permissive_iam(
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
            "NGA-009", "HIGH",
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


# ── NGA-010 ──────────────────────────────────────────────────────────────────


def _rule_nga010_no_k8s_network_policy(
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
    ]
    if not k8s_deployments:
        return []

    no_netpol = [
        n for n in k8s_deployments
        if not _depl_meta(n).get("has_network_policy")
    ]
    if not no_netpol:
        return []

    return [
        _finding(
            "NGA-010", "MEDIUM",
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


# ── NGA-011 ──────────────────────────────────────────────────────────────────


def _rule_nga011_untrusted_model_registry(
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
            "NGA-011", "HIGH",
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


# ── NGA-012 ──────────────────────────────────────────────────────────────────


def _rule_nga012_latest_image_tag(
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
            "NGA-012", "LOW",
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


# ── NGA-013 ──────────────────────────────────────────────────────────────────


def _rule_nga013_missing_health_check(
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
            "NGA-013", "LOW",
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


# ── NGA-014 ──────────────────────────────────────────────────────────────────


def _rule_nga014_shared_datastore_no_iam_isolation(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **_: Any
) -> list[dict[str, Any]]:
    """LOW — Multiple AI agents sharing a single datastore with no IAM isolation."""
    datastore_ids = {n["id"] for n in nodes if n.get("component_type") in _DATASTORE_TYPES}
    agent_ids = {n["id"] for n in nodes if n.get("component_type") in _AGENT_TYPES}
    iam_ids = {n["id"] for n in nodes if n.get("component_type") in _IAM_TYPES}

    # Map each datastore to the set of agents that access it
    ds_to_agents: dict[str, set[str]] = {ds_id: set() for ds_id in datastore_ids}
    for e in edges:
        src, tgt = e.get("source", ""), e.get("target", "")
        if src in agent_ids and tgt in datastore_ids:
            ds_to_agents[tgt].add(src)
        elif tgt in agent_ids and src in datastore_ids:
            ds_to_agents[src].add(tgt)

    # Shared datastores (2+ agents), with no IAM node in graph
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
            "NGA-014", "LOW",
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


# ── NGA-015 ──────────────────────────────────────────────────────────────────

_AUDIT_LIBS = {
    "opentelemetry", "langfuse", "arize", "whylogs",
    "mlflow", "wandb", "helicone",
}
_AUDIT_MIDDLEWARE = {
    "RequestLoggingMiddleware", "AuditLogHandler",
    "RotatingFileHandler", "TimedRotatingFileHandler",
}


def _rule_nga015_no_audit_logging(
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

    if has_audit_lib or has_log_paths or has_middleware:
        return []

    return [
        _finding(
            "NGA-015", "HIGH",
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


# ── NGA-016 ──────────────────────────────────────────────────────────────────


def _rule_nga016_pr_target_injection(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — GitHub Actions: pull_request_target with untrusted context injection."""
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
            "NGA-016", "HIGH",
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


# ── NGA-017 ──────────────────────────────────────────────────────────────────


def _rule_nga017_github_env_injection(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — GitHub Actions: GITHUB_ENV written from untrusted input."""
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
            "NGA-017", "HIGH",
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


# ── NGA-018 ──────────────────────────────────────────────────────────────────


def _rule_nga018_actions_runner_debug(
    nodes: list[dict[str, Any]], summary: dict[str, Any], **_: Any
) -> list[dict[str, Any]]:
    """MEDIUM — GitHub Actions: ACTIONS_RUNNER_DEBUG secret exposed."""
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
            "NGA-018", "MEDIUM",
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


# ── NGA-019 ──────────────────────────────────────────────────────────────────


def _rule_nga019_missing_hitl(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **_: Any
) -> list[dict[str, Any]]:
    """HIGH — Agent pipeline lacks human-in-the-loop approval for high-risk actions."""
    nodes_by_id = {n["id"]: n for n in nodes}
    tool_ids = {n["id"] for n in nodes if n.get("component_type") == "TOOL"}
    agent_nodes = [n for n in nodes if n.get("component_type") in _AGENT_TYPES]

    risky_agents = []
    for agent in agent_nodes:
        # Check if this agent has HITL patterns in its metadata
        meta = agent.get("metadata") or {}
        extras = meta.get("extras") or {}
        agent_str = str(meta) + str(extras)
        has_hitl = any(pattern.lower() in agent_str.lower() for pattern in _HITL_PATTERNS)
        if has_hitl:
            continue

        # Find reachable tool nodes
        reachable = _nodes_reachable_from(agent["id"], edges, nodes_by_id)
        reachable_tools = [
            nodes_by_id[nid] for nid in reachable
            if nid in tool_ids and nid in nodes_by_id
        ]

        # Check if any reachable tool is irreversible
        has_irreversible = any(
            _IRREVERSIBLE_TOOL_PATTERNS.search(t.get("name", "") + " " + str(t.get("metadata", {})))
            for t in reachable_tools
        )
        if has_irreversible:
            risky_agents.append(agent)

    if not risky_agents:
        return []

    return [
        _finding(
            "NGA-019", "HIGH",
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


# ── Rule registry ─────────────────────────────────────────────────────────────

_RULES: list[Callable[..., list[dict[str, Any]]]] = [
    _rule_nga001_phi_to_external_llm,
    _rule_nga002_insufficient_guardrails,
    _rule_nga003_secrets_in_env,
    _rule_nga004_runs_as_root,
    _rule_nga005_no_resource_limits,
    _rule_nga006_unencrypted_pii_datastore,
    _rule_nga007_missing_auth_on_api_endpoint,
    _rule_nga009_overly_permissive_iam,
    _rule_nga010_no_k8s_network_policy,
    _rule_nga011_untrusted_model_registry,
    _rule_nga012_latest_image_tag,
    _rule_nga013_missing_health_check,
    _rule_nga014_shared_datastore_no_iam_isolation,
    _rule_nga015_no_audit_logging,
    _rule_nga016_pr_target_injection,
    _rule_nga017_github_env_injection,
    _rule_nga018_actions_runner_debug,
    _rule_nga019_missing_hitl,
]


# ── OSV / Grype finding converters ───────────────────────────────────────────


def _osv_to_finding(osv: dict[str, Any]) -> dict[str, Any]:
    """Convert an osv_client result dict to the standard finding shape."""
    cve_ids = osv.get("cve_ids") or []
    adv_id = osv.get("advisory_id", "")
    title = f"Known vulnerability in {osv.get('dep_name', '?')} ({adv_id})"
    if cve_ids:
        title += f" [{', '.join(cve_ids[:2])}]"
    return {
        "rule_id": adv_id,
        "severity": osv.get("severity", "UNKNOWN"),
        "title": title,
        "description": (
            f"{osv.get('summary', adv_id)}  "
            f"Affected versions: {osv.get('affected_versions', 'see advisory')}.  "
            f"Package: {osv.get('dep_name')} {osv.get('dep_version', '')}."
        ),
        "affected": [osv.get("purl", osv.get("dep_name", "?"))],
        "remediation": (
            f"Upgrade {osv.get('dep_name')} to a version outside the affected range. "
            f"See {osv.get('url', 'https://osv.dev')} for details."
        ),
        "source": "osv",
        "advisory_url": osv.get("url"),
        "cve_ids": cve_ids,
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
    return {
        "rule_id": adv_id,
        "severity": grype.get("severity", "UNKNOWN"),
        "title": title,
        "description": (
            f"{grype.get('summary', adv_id)}  "
            f"Affected versions: {grype.get('affected_versions', 'see advisory')}.  "
            f"Package: {grype.get('dep_name')} {grype.get('dep_version', '')}."
            f"{target_note}"
        ),
        "affected": [grype.get("purl", grype.get("dep_name", "?"))],
        "remediation": (
            f"Upgrade {grype.get('dep_name')} to a version outside the affected range. "
            f"See {grype.get('url', 'https://github.com/anchore/grype')} for details."
        ),
        "source": "grype",
        "advisory_url": grype.get("url"),
        "cve_ids": cve_ids,
    }


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
        ctx = {"nodes": nodes, "edges": edges, "summary": summary}
        findings: list[dict[str, Any]] = []
        for rule in _RULES:
            try:
                findings.extend(rule(**ctx))
            except Exception as exc:
                _log.warning("NGA rule %s raised an error and was skipped: %s", rule.__name__, exc)
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
            },
        )
