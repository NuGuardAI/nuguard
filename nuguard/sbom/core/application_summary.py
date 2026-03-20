"""
Application Summary Utilities for AIBOM scans.

Builds scan-level metadata and node-level asset summaries from extracted
files and nodes. Uses deterministic extraction by default and supports
optional LLM refinement via an injected LLMClient.

Standalone module: no dependency on backend services.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from xelo.models import Node

if TYPE_CHECKING:
    from xelo.llm_client import LLMClient

# ---------------------------------------------------------------------------
# Pattern constants
# ---------------------------------------------------------------------------

_ENDPOINT_PATTERNS = [
    re.compile(
        r"@(?:app|router)\.(?:get|post|put|patch|delete|options|head)\(\s*[\"']([^\"']+)[\"']"
    ),
    re.compile(r"@(?:app|blueprint)\.route\(\s*[\"']([^\"']+)[\"']"),
    re.compile(r"\b(?:app|router)\.(?:get|post|put|patch|delete|use)\(\s*[\"']([^\"']+)[\"']"),
]

_URL_PATTERN = re.compile(r"https?://[^\s\"'`<>]+")
_AWS_ACCOUNT_PATTERN = re.compile(r"\b\d{12}\b")
_AZURE_SUB_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_REGION_PATTERN = re.compile(
    r"\b(?:us|eu|ap|sa|ca|af|me|northamerica|southamerica|europe|asia|australia)[-_][a-z0-9-]+\b",
    re.IGNORECASE,
)
_ENV_PATTERN = re.compile(
    r"\b(dev|development|stage|staging|prod|production|test|qa|uat)\b",
    re.IGNORECASE,
)
_VOICE_PATTERN = re.compile(
    r"\b(twilio|webrtc|tts|stt|whisper|microphone|pyaudio|sounddevice|vosk|speechrecognition|pyttsx)\b",
    re.IGNORECASE,
)
_IMAGE_PATTERN = re.compile(
    r"\b(vision|ocr|opencv|pillow|pil|cv2|dall.e|stable.diffusion|image.generation)\b",
    re.IGNORECASE,
)
_VIDEO_PATTERN = re.compile(
    r"\b(webcam|ffmpeg|hls|rtsp|VideoCapture|VideoWriter|VideoFileClip|gstreamer|libav)\b",
    re.IGNORECASE,
)
_MODALITY_MATCH_THRESHOLD = 2

_AGENTIC_FRAMEWORKS = {
    "langgraph",
    "langchain",
    "semantic-kernel",
    "semantic_kernel",
    "autogen",
    "crewai",
    "openai-agents",
    "openai-agents-sdk",
    "openai-agents-ts",
    "google-adk",
    "bedrock-agents",
    "llamaindex",
    "llama-index",
    # Additional adapters (underscore variants handled via normalisation in _is_agentic_framework)
    "agno",
    "aws-bedrock",
    "azure-ai-agents",
    "azure-ai-agent-service",
    "bedrock-agentcore",
    "guardrails-ai",
    "mcp-server",
    "langchain-js",
    "langgraph-js",
}
_FRAMEWORK_EXCLUDES = {
    "inline",
    "openai",
    "anthropic",
    "gemini",
    "azure",
    "aws",
    "gcp",
    "huggingface",
    "vercel-ai",
}
_DEPLOYMENT_FILE_HINTS = (
    ".github/workflows/",
    "docker",
    "kubernetes",
    "/k8s/",
    "terraform",
    "infra/",
    "deployment",
    "helm",
    "nginx",
    "compose",
    "vercel",
    "netlify",
    "cloudrun",
)
_DOC_HOST_BLOCKLIST = {
    "aka.ms",
    "docs.github.com",
    "learn.microsoft.com",
    "docs.python.org",
    "readthedocs.io",
}
_DOC_PATH_HINTS = ("/docs/", "/documentation/", "workflowconfig")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _uniq(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result


def _canonicalize_url(raw: str) -> str | None:
    v = (raw or "").strip()
    if not v or not v.startswith(("http://", "https://")):
        return None
    if any(tok in v for tok in ("${", "{{", "$", "<", ">")):
        return None
    try:
        parsed = urlparse(v)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    path = (parsed.path or "").lower()
    if host in _DOC_HOST_BLOCKLIST or any(h in path for h in _DOC_PATH_HINTS):
        return None
    normalized = parsed._replace(scheme=parsed.scheme.lower(), netloc=host, fragment="")
    return urlunparse(normalized).rstrip("/") or None


def _is_agentic_framework(value: str) -> bool:
    n = (
        value.strip().lower().replace("_", "-")
    )  # normalise underscore variants (e.g. openai_agents → openai-agents)
    if not n or n in _FRAMEWORK_EXCLUDES:
        return False
    # Strip language suffix emitted by TS _fw_node() (e.g. "agno-ts" → "agno",
    # "langgraph-js" already matches via substring so this mainly helps the
    # purely-import-detected case where only a FRAMEWORK node is produced).
    n_base = re.sub(r"-(ts|js)$", "", n)
    return (
        n in _AGENTIC_FRAMEWORKS
        or n_base in _AGENTIC_FRAMEWORKS
        or any(tok in n for tok in ("langgraph", "semantic", "autogen", "crewai", "agents-sdk"))
    )


def _node_type_str(node: Node) -> str:
    return node.component_type.value.upper()


# ---------------------------------------------------------------------------
# Extraction utilities
# ---------------------------------------------------------------------------


def extract_api_endpoints(files: Sequence[tuple[str, str]]) -> list[str]:
    """Extract API route paths from source files."""
    endpoints: list[str] = []
    for path, content in files:
        if not path.lower().endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
            continue
        for pattern in _ENDPOINT_PATTERNS:
            endpoints.extend(pattern.findall(content or ""))
    return _uniq(ep for ep in endpoints if ep and ep != "/")


def extract_deployment_context(files: Sequence[tuple[str, str]]) -> dict[str, list[str]]:
    """Extract deployment/platform metadata from IaC and workflow files."""
    platforms: list[str] = []
    accounts: list[str] = []
    projects: list[str] = []
    regions: list[str] = []
    environments: list[str] = []
    deployment_urls: list[str] = []

    for path, content in files:
        lower_path = path.lower()
        text = content or ""
        text_lower = text.lower()

        if "azure" in lower_path or "azure" in text_lower:
            platforms.append("Azure")
        if "aws" in lower_path or "bedrock" in text_lower or "eks" in text_lower:
            platforms.append("AWS")
        if (
            "gcp" in lower_path
            or "google cloud" in text_lower
            or "gcloud" in text_lower
            or "cloud run" in text_lower
            or "cloudrun" in text_lower
            or "vertex ai" in text_lower
            or "google_cloud_project" in text_lower
        ):
            platforms.append("GCP")
        if ".github/workflows/" in lower_path:
            platforms.append("GitHub Actions")
        if "kubernetes" in lower_path or "/k8s/" in lower_path or "apiVersion:" in text:
            platforms.append("Kubernetes")

        accounts.extend(_AWS_ACCOUNT_PATTERN.findall(text))
        accounts.extend(_AZURE_SUB_PATTERN.findall(text))
        for key in ["project_id", "project", "resource_group", "subscription", "account_id"]:
            for m in re.findall(
                rf"{key}\s*[:=]\s*[\"']?([a-zA-Z0-9._-]+)", text, flags=re.IGNORECASE
            ):
                projects.append(m)
        regions.extend(_REGION_PATTERN.findall(text))
        environments.extend(_ENV_PATTERN.findall(text))
        if any(hint in lower_path for hint in _DEPLOYMENT_FILE_HINTS):
            deployment_urls.extend(_URL_PATTERN.findall(text))

    canonical_urls = _uniq(u for u in (_canonicalize_url(u) for u in deployment_urls) if u)
    return {
        "deployment_platforms": _uniq(platforms),
        "subscription_account_project": _uniq(accounts + projects),
        "regions": _uniq(regions),
        "environments": _uniq(e.lower() for e in environments),
        "deployment_urls": canonical_urls,
    }


def infer_modalities_support(
    nodes: Sequence[Node],
    files: Sequence[tuple[str, str]],
) -> dict[str, bool]:
    """Infer supported input/output modalities from nodes and file content."""
    combined = " ".join((content or "") for _, content in files[:200]).lower()

    voice = len(_VOICE_PATTERN.findall(combined)) >= _MODALITY_MATCH_THRESHOLD
    image = len(_IMAGE_PATTERN.findall(combined)) >= _MODALITY_MATCH_THRESHOLD
    video = len(_VIDEO_PATTERN.findall(combined)) >= _MODALITY_MATCH_THRESHOLD

    for node in nodes:
        extras = node.metadata.extras
        modality = str(extras.get("modality") or "").lower()
        caps_raw = extras.get("capabilities") or []
        capabilities = (
            " ".join(str(v).lower() for v in caps_raw) if isinstance(caps_raw, list) else ""
        )
        probe = f"{modality} {capabilities}"
        voice = voice or any(
            k in probe for k in ("tts", "stt", "microphone", "twilio", "webrtc", "whisper")
        )
        image = image or any(k in probe for k in ("vision", "ocr", "image_generation"))
        video = video or any(k in probe for k in ("webcam", "ffmpeg", "rtsp", "videocapture"))

    return {"text": True, "voice": voice, "image": image, "video": video}


# ---------------------------------------------------------------------------
# Deterministic summary builders
# ---------------------------------------------------------------------------


def build_deterministic_use_case_summary(
    nodes: Sequence[Node],
    modality_support: dict[str, bool],
) -> str:
    agent_count = sum(1 for n in nodes if _node_type_str(n) == "AGENT")
    tool_count = sum(1 for n in nodes if _node_type_str(n) == "TOOL")
    guardrail_count = sum(1 for n in nodes if _node_type_str(n) == "GUARDRAIL")

    node_names = [n.name.lower() for n in nodes if _node_type_str(n) in {"AGENT", "TOOL"}]
    phrase_map = {
        "voice": "Voice interaction",
        "mcp": "MCP tool integration",
        "git": "git repository management",
        "faq": "FAQ question answering",
        "web": "web search and retrieval",
        "specialist": "specialist recommendation workflows",
        "search": "search-based retrieval",
        "triage": "request triage and routing",
        "support": "customer support assistance",
    }
    phrases = _uniq(
        phrase for key, phrase in phrase_map.items() if any(key in n for n in node_names)
    )[:3]
    use_case = ", ".join(phrases) if phrases else "general agentic task orchestration"

    return (
        f"This application implements an agentic AI workflow with {agent_count} agent(s), "
        f"{tool_count} tool integration(s), and {guardrail_count} guardrail control(s). "
        f"Detected use cases include {use_case}. "
        f"Multi-modal support: Voice {'supported' if modality_support.get('voice') else 'not supported'}, "
        f"Images {'supported' if modality_support.get('image') else 'not supported'}, "
        f"Video {'supported' if modality_support.get('video') else 'not supported'}."
    )


def extract_iac_security_context(nodes: Sequence[Node]) -> dict[str, Any]:
    """Aggregate security and resilience metadata from IaC/Dockerfile node metadata.

    Scans DEPLOYMENT, CONTAINER_IMAGE, and IAM nodes for typed fields
    (``secret_store``, ``encryption_at_rest``, ``runs_as_root``, etc.) and
    returns a dict suitable for merging into the scan summary.
    """
    secret_stores: list[str] = []
    azones: list[str] = []
    encryption_at_rest_coverage = False
    security_findings: list[str] = []
    iam_principals: list[str] = []
    service_accounts: list[str] = []

    _findings_set: set[str] = set()

    for node in nodes:
        nt = _node_type_str(node)
        meta = node.metadata

        if nt in ("DEPLOYMENT", "CONTAINER_IMAGE"):
            # Secret stores
            store = meta.secret_store or meta.extras.get("secret_store")
            if store and isinstance(store, str):
                secret_stores.append(store)

            # AZs
            az = meta.availability_zones or meta.extras.get("availability_zones")
            if isinstance(az, list):
                azones.extend(str(z) for z in az)

            # Encryption at rest
            enc = meta.encryption_at_rest
            if enc is None:
                enc = meta.extras.get("encryption_at_rest")
            if enc is True:
                encryption_at_rest_coverage = True

        if nt == "CONTAINER_IMAGE":
            rar = meta.extras.get("runs_as_root")
            if rar is None:
                rar = meta.runs_as_root
            if rar is True and "container_runs_as_root" not in _findings_set:
                _findings_set.add("container_runs_as_root")
                security_findings.append("container_runs_as_root")

            hc = meta.extras.get("has_health_check")
            if hc is None:
                hc = meta.has_health_check
            if hc is False and "missing_health_check" not in _findings_set:
                _findings_set.add("missing_health_check")
                security_findings.append("missing_health_check")

            findings = meta.extras.get("security_findings") or []
            for f in findings:
                if f not in _findings_set:
                    _findings_set.add(f)
                    security_findings.append(f)

        if nt == "DEPLOYMENT":
            rl = meta.has_resource_limits
            if rl is None:
                rl = meta.extras.get("has_resource_limits")
            if rl is False and "no_resource_limits" not in _findings_set:
                _findings_set.add("no_resource_limits")
                security_findings.append("no_resource_limits")

            rar = meta.runs_as_root
            if rar is None:
                rar = meta.extras.get("runs_as_root")
            if rar is True and "container_runs_as_root" not in _findings_set:
                _findings_set.add("container_runs_as_root")
                security_findings.append("container_runs_as_root")

        if nt == "IAM":
            principal = meta.principal or meta.extras.get("principal")
            iam_type = meta.iam_type or meta.extras.get("iam_type")
            if principal and isinstance(principal, str):
                iam_principals.append(principal)
            if iam_type in ("service_account", "managed_identity"):
                if principal and isinstance(principal, str):
                    service_accounts.append(principal)
            # Flag overly-permissive IAM (wildcard permissions)
            perms = meta.permissions or meta.extras.get("permissions") or []
            if isinstance(perms, list) and any("*" in str(p) for p in perms):
                if "overly_permissive_iam" not in _findings_set:
                    _findings_set.add("overly_permissive_iam")
                    security_findings.append("overly_permissive_iam")

    return {
        "secret_stores": _uniq(secret_stores),
        "availability_zones": _uniq(azones),
        "encryption_at_rest_coverage": encryption_at_rest_coverage,
        "security_findings": security_findings,
        "iam_principals": _uniq(iam_principals),
        "service_accounts": _uniq(service_accounts),
    }


def build_scan_summary(
    nodes: Sequence[Node],
    files: Sequence[tuple[str, str]],
    source_ref: str | None = None,
    branch: str | None = None,
    dc_metadata: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build scan-level summary for reporting."""
    node_types: dict[str, int] = {}
    frameworks: list[str] = []

    for node in nodes:
        nt = _node_type_str(node)
        node_types[nt] = node_types.get(nt, 0) + 1
        framework = node.metadata.framework or node.metadata.extras.get("framework")
        if isinstance(framework, str) and _is_agentic_framework(framework):
            frameworks.append(framework)

    deployment = extract_deployment_context(files)
    endpoints = extract_api_endpoints(files)
    modality_support = infer_modalities_support(nodes, files)
    use_case_summary = build_deterministic_use_case_summary(nodes, modality_support)
    modalities = [k.upper() for k, enabled in modality_support.items() if enabled]

    # Data classification: collect from typed fields on DATASTORE nodes, then fall back
    # to raw dc_metadata for repos where no DATASTORE node was detected.
    all_labels: set[str] = set()
    classified_tables: list[str] = []
    for node in nodes:
        dc = node.metadata.data_classification or node.metadata.extras.get("data_classification")
        if dc and isinstance(dc, list):
            all_labels.update(dc)
        ct = node.metadata.classified_tables or []
        classified_tables.extend(ct)
    if not all_labels:
        for meta in dc_metadata or []:
            all_labels.update(meta.get("data_classification") or [])
            table = meta.get("table_name") or meta.get("model_name")
            if table:
                classified_tables.append(table)

    iac_security = extract_iac_security_context(nodes)

    return {
        "source_ref": source_ref,
        "branch": branch,
        "node_type_counts": node_types,
        "frameworks": _uniq(frameworks),
        "api_endpoints": endpoints[:200],
        "use_case_summary": use_case_summary,
        "modalities": modalities,
        "modality_support": modality_support,
        "data_classification": sorted(all_labels),
        "classified_tables": sorted(classified_tables),
        **deployment,
        **iac_security,
    }


def build_deterministic_asset_summary(
    node: Node,
    scan_summary: dict[str, Any] | None = None,
) -> str:
    """Generate a concise deterministic summary for a single asset node."""
    nt = _node_type_str(node)
    extras = node.metadata.extras
    parts: list[str] = [f"{nt} '{node.name}'"]

    role = extras.get("role") or extras.get("prompt_type") or extras.get("agent_type")
    if isinstance(role, str) and role.strip():
        parts.append(f"role/type: {role}")

    provider = (
        node.metadata.extras.get("provider") or node.metadata.framework or extras.get("namespace")
    )
    if isinstance(provider, str) and provider.strip():
        parts.append(f"provider/framework: {provider}")

    model = node.metadata.model_name or extras.get("model_name")
    if isinstance(model, str) and model.strip():
        parts.append(f"model: {model}")

    endpoint = extras.get("api_endpoint") or extras.get("endpoint") or extras.get("url")
    if isinstance(endpoint, str) and endpoint.strip() and not endpoint.startswith("$"):
        parts.append(f"endpoint: {endpoint}")

    if scan_summary:
        plats = scan_summary.get("deployment_platforms") or []
        if isinstance(plats, list) and plats:
            parts.append(f"deployment: {', '.join(plats[:2])}")

    return "; ".join(parts)[:600]


# ---------------------------------------------------------------------------
# Optional LLM-refined summary functions
# ---------------------------------------------------------------------------


async def maybe_refine_use_case_summary_with_llm(
    scan_summary: dict[str, Any],
    nodes: Sequence[Node],
    files: Sequence[tuple[str, str]],
    llm_client: LLMClient | None = None,
) -> str:
    """Optionally generate a richer 2–3 sentence use-case summary via LLM.

    Falls back to the deterministic base summary when ``llm_client`` is None
    or on any error.
    """
    base = str(scan_summary.get("use_case_summary") or "").strip()
    if llm_client is None:
        return base

    try:
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        }
        top_nodes = [
            {
                "type": _node_type_str(n),
                "name": n.name,
                "framework": n.metadata.framework,
                "extras": {
                    k: v
                    for k, v in n.metadata.extras.items()
                    if k
                    in (
                        "provider",
                        "model_name",
                        "model_family",
                        "version",
                        "description",
                        "server_name",
                        "transport",
                        "auth_type",
                    )
                },
            }
            for n in nodes[:30]
        ]

        # Build MCP-specific context when an MCP server is present
        mcp_context = ""
        mcp_fw_nodes = [
            n
            for n in nodes
            if _node_type_str(n) == "FRAMEWORK"
            and "mcp" in str(n.metadata.extras.get("framework", "") or n.name).lower()
        ]
        if mcp_fw_nodes:
            mcp_lines: list[str] = []
            for mcp_node in mcp_fw_nodes:
                ex = mcp_node.metadata.extras
                server_name = ex.get("server_name") or mcp_node.name
                desc = ex.get("description", "")
                transport = ex.get("transport", "")
                tools = [
                    n.name
                    for n in nodes
                    if _node_type_str(n) == "TOOL"
                    and str(n.metadata.extras.get("framework", "")).lower()
                    in ("mcp-server", "mcp_server")
                ]
                auth_nodes = [
                    n.name
                    for n in nodes
                    if _node_type_str(n) == "AUTH"
                    and str(n.metadata.extras.get("framework", "")).lower()
                    in ("mcp-server", "mcp_server")
                ]
                line = f"MCP server '{server_name}'"
                if tools:
                    line += f" with tools: {', '.join(tools[:8])}"
                if transport:
                    line += f"; transport: {transport}"
                if auth_nodes:
                    line += f"; auth: {', '.join(auth_nodes[:3])}"
                if desc:
                    line += f". Description: {desc}"
                mcp_lines.append(line)
            mcp_context = "\nMCP server details: " + " | ".join(mcp_lines)

        user_prompt = (
            "Summarize this AI application's practical use cases in 2-3 concise sentences. "
            "Be factual and avoid speculation. Include what the system appears to do, who it serves, "
            "and notable capabilities. Mention modality support for voice/image/video explicitly. "
            "If an MCP server is present, include a brief description of the server, "
            "its exposed tools, transport, and auth mechanism.\n\n"
            f"Base summary: {base}\n"
            f"Modality support: {scan_summary.get('modality_support', {})}\n"
            f"Frameworks: {scan_summary.get('frameworks', [])}\n"
            f"Sample nodes (JSON): {top_nodes}\n"
            f"File sample count: {len(files)}{mcp_context}\n\n"
            'Respond with JSON: {"summary": "..."}'
        )
        system = "You are a technical writer producing concise AI system inventory summaries."
        result = await asyncio.wait_for(
            llm_client.complete_structured(system, user_prompt, schema),
            timeout=15.0,
        )
        candidate = (result or {}).get("summary")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:900]
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        pass
    return base


async def maybe_refine_asset_summary_with_llm(
    node: Node,
    base_summary: str,
    scan_summary: dict[str, Any] | None = None,
    llm_client: LLMClient | None = None,
) -> str:
    """Optionally refine a single asset summary via LLM.

    Controlled by the ``AISBOM_ENABLE_ASSET_SUMMARY_LLM`` environment variable
    (must be ``"true"``). Falls back to ``base_summary`` when disabled or on
    any error.
    """
    if os.getenv("AISBOM_ENABLE_ASSET_SUMMARY_LLM", "false").lower() != "true":
        return base_summary
    if llm_client is None:
        return base_summary

    try:
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        }
        payload = {
            "type": _node_type_str(node),
            "name": node.name,
            "framework": node.metadata.framework,
            "extras": node.metadata.extras,
            "scan_context": scan_summary or {},
        }
        user_prompt = (
            "Create a concise inventory summary for this AI asset. "
            "Use factual language, 1-2 sentences, include deployment/API context if present.\n"
            f"Base summary: {base_summary}\n\n"
            f"Asset JSON:\n{payload}\n\n"
            'Respond with JSON: {"summary": "..."}'
        )
        system = "You are a technical writer producing concise AI asset inventory summaries."
        result = await asyncio.wait_for(
            llm_client.complete_structured(system, user_prompt, schema),
            timeout=15.0,
        )
        candidate = (result or {}).get("summary")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:700]
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        pass
    return base_summary
