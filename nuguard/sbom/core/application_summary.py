"""
Application Summary Utilities for AIBOM scans.

Builds scan-level metadata and node-level asset summaries from extracted
files and nodes. Uses deterministic extraction by default and supports
optional LLM refinement via an injected LLMClient.

Standalone module: no dependency on backend services.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from nuguard.common.soft_reject import (
    is_llm_soft_rejected,
    iter_effective_nodes,
    partition_node_counts,
)

from ..adapters._test_paths import looks_like_test_path
from ..models import InstrumentationDetail, Node, TestingDetail
from .route_patterns import ROUTE_PATTERNS

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient

# ---------------------------------------------------------------------------
# Pattern constants
# ---------------------------------------------------------------------------

_ENDPOINT_PATTERNS = ROUTE_PATTERNS

_URL_PATTERN = re.compile(r"https?://[^\s\"'`<>,]+")
# GitHub Actions env vars that encode well-known Azure hostname patterns, e.g.
#   AZURE_WEBAPP_NAME: my-backend  →  https://my-backend.azurewebsites.net
# The template expression ${{ env.AZURE_WEBAPP_NAME }}.azurewebsites.net is
# filtered by _canonicalize_url, so we reconstruct the URL explicitly.
_GHA_AZURE_WEBAPP_NAME_RE = re.compile(
    r"AZURE_WEBAPP_NAME\s*:\s*['\"]?([A-Za-z0-9][A-Za-z0-9-]{0,59})['\"]?",
    re.IGNORECASE,
)
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

# ---------------------------------------------------------------------------
# Streaming detection patterns
# ---------------------------------------------------------------------------

# Frameworks that always expose a native streaming endpoint.
# Presence of any of these in summary.frameworks is sufficient to infer streaming.
_STREAMING_FRAMEWORKS: frozenset[str] = frozenset({"google-adk", "google_adk"})

# Their canonical streaming endpoint path (registered in streaming_endpoints).
_FRAMEWORK_STREAMING_ENDPOINTS: dict[str, str] = {
    "google-adk": "/run_sse",
    "google_adk": "/run_sse",
}

# Source-code tokens that indicate streaming output from the application itself
# (not just upstream streaming to an LLM).
_STREAMING_SOURCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bStreamingResponse\b"),
    re.compile(r"\bEventSourceResponse\b"),
    re.compile(r"['\"]text/event-stream['\"]"),
    re.compile(r"\bastream\s*\("),  # LangChain .astream()
    re.compile(r"\bstream_events\s*\("),  # LangGraph .stream_events()
    re.compile(r"\bstream\s*=\s*True"),  # litellm / openai stream=True passed to caller
    re.compile(r"/run_sse"),  # ADK SSE path reference
    re.compile(r"\bserver.sent.events?\b", re.IGNORECASE),
    re.compile(r"\bAsyncGenerator\b.*\bstr\b"),  # async generator route returning str chunks
]

# Decorator + return-type patterns that identify a specific route as streaming
_STREAMING_ROUTE_RETURN_TYPES: frozenset[str] = frozenset(
    {
        "StreamingResponse",
        "EventSourceResponse",
    }
)

# Route path suffixes / names strongly associated with SSE output
_STREAMING_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"/(stream|sse|run_sse|events?)(/|$)", re.IGNORECASE),
    re.compile(r"/chat/stream", re.IGNORECASE),
]


def detect_streaming_output(
    nodes: "Sequence[Node]",
    files: "Sequence[tuple[str, str]]",
    frameworks: list[str],
) -> tuple[bool, list[str]]:
    """Detect whether the application exposes streaming output endpoints.

    Detection works in three layers, from most to least authoritative:

    1. **Framework inference** — frameworks like Google ADK always expose
       ``/run_sse``; detecting the framework is sufficient evidence.
    2. **SBOM node transport** — ``API_ENDPOINT`` nodes with
       ``metadata.transport == 'sse'`` or ``'streamable-http'`` carry their
       own endpoint path.
    3. **Source-code pattern scan** — searches ``.py`` / ``.ts`` files for
       ``StreamingResponse``, ``EventSourceResponse``, ``text/event-stream``,
       ``.astream()``, etc.  When a match is found nearby a route decorator
       the route path is extracted; otherwise the file is treated as
       unanchored evidence (``uses_streaming`` set True without a specific
       path).

    Returns:
        ``(uses_streaming, streaming_endpoints)`` where *streaming_endpoints*
        is a deduplicated list of route paths serving SSE output.
    """
    streaming_endpoints: list[str] = []

    # Layer 1 — framework-level inference
    norm_frameworks = {f.strip().lower().replace("_", "-") for f in frameworks}
    for fw in norm_frameworks:
        ep = _FRAMEWORK_STREAMING_ENDPOINTS.get(fw)
        if ep:
            streaming_endpoints.append(ep)

    # Layer 2 — SBOM node transport metadata
    node_streaming = False
    for node in nodes:
        t = (node.metadata.transport or "").lower()
        if t in ("sse", "streamable-http"):
            node_streaming = True
            ep = node.metadata.endpoint or ""
            if ep:
                streaming_endpoints.append(ep)

    # Layer 3 — source-code pattern scan
    file_has_streaming = False
    for path, content in files:
        if not path.lower().endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
            continue
        text = content or ""
        for pat in _STREAMING_SOURCE_PATTERNS:
            if pat.search(text):
                file_has_streaming = True
                # Try to extract the route path from the same file
                _extract_streaming_route_paths(text, streaming_endpoints)
                break

    uses_streaming = bool(streaming_endpoints) or node_streaming or file_has_streaming
    return uses_streaming, _uniq(streaming_endpoints)


def _extract_streaming_route_paths(source: str, out: "list[str]") -> None:
    """Append route paths that appear to serve SSE in *source* to *out*.

    Heuristic: scan for route decorators and check if the function body
    or return annotation contains a streaming indicator within the next
    30 lines.  Relies on textual proximity rather than full AST analysis
    for speed.
    """
    lines = source.splitlines()
    for i, line in enumerate(lines):
        # Find route decorator (captures path like '/stream' or '/run_sse')
        m = re.search(
            r"@\w+\.\w+\(\s*[\"']([^\"']+)[\"']",
            line,
        )
        if m is None:
            continue
        route_path = m.group(1)
        # Quick check: is this path itself a known streaming path?
        if any(p.search(route_path) for p in _STREAMING_PATH_PATTERNS):
            out.append(route_path)
            continue
        # Check the next 30 lines for streaming return types or patterns
        window = "\n".join(lines[i : i + 30])
        if any(t in window for t in _STREAMING_ROUTE_RETURN_TYPES):
            out.append(route_path)
        elif re.search(r"['\"]text/event-stream['\"]", window):
            out.append(route_path)
        elif re.search(r"\bastream\s*\(", window):
            out.append(route_path)


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
            for match in pattern.finditer(content or ""):
                endpoints.append(match.group("path"))
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
        # Normalize separators so workflow-path hints match on Windows too.
        # Scanned file paths can use backslashes on Windows; the hint set uses
        # POSIX style (".github/workflows/", "/k8s/", "infra/"). Comparing the
        # raw lower_path on Windows would miss these matches and silently drop
        # deployment URLs discovered from workflow content. See issue #234.
        normalized_path = path.replace("\\", "/")
        lower_path = normalized_path.lower()
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
        # Cloud Run is more specific than the generic "GCP" marker above.
        # All the following are Cloud Run-specific signals (issue #220):
        #   - the official ``google-github-actions/deploy-cloudrun`` GH Action,
        #   - ``run.googleapis.com`` annotations (Cloud Run runtime API),
        #   - ``*.a.run.app`` URLs (Cloud Run default domain),
        #   - the ``cloudrun`` path substring (e.g. ``cloudrun/service.yaml``).
        # The Knative service API (``serving.knative.dev``) is intentionally
        # NOT a Cloud Run trigger: Knative Serving is open source and
        # commonly self-hosted on plain GKE/EKS/AKS, so a manifest using
        # that API alone is not necessarily Cloud Run (issue #220 nit).
        has_cloud_run_signal = (
            "deploy-cloudrun" in text_lower
            or "run.googleapis.com" in text_lower
            or ".a.run.app" in text_lower
            or "cloudrun" in lower_path
        )
        if has_cloud_run_signal:
            platforms.append("Cloud Run")
            # Cloud Run is a GCP product; ensure the coarse "GCP" marker
            # also fires so downstream consumers relying on GCP don't
            # silently break when a repo only references Cloud Run signals.
            if "GCP" not in platforms:
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
            # GitHub Actions workflows rarely contain literal deployed URLs because
            # they build them from env vars (e.g. ${{ env.AZURE_WEBAPP_NAME }}.azurewebsites.net).
            # Reconstruct well-known Azure App Service URLs from the declared env var value.
            if ".github/workflows/" in lower_path:
                for m in _GHA_AZURE_WEBAPP_NAME_RE.finditer(text):
                    deployment_urls.append(f"https://{m.group(1)}.azurewebsites.net")

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
        "github_actions_content": "\n---\n".join(
            meta.extras.get("workflow_content", "")
            for node in nodes
            for meta in [node.metadata]
            if meta.extras.get("iac_format") == "github_actions"
            and meta.extras.get("workflow_content")
        ),
        "workflow_security_findings": list(
            {
                (f["rule_signal"], f.get("line", 0)): f
                for node in nodes
                for meta in [node.metadata]
                if meta.extras.get("iac_format") == "github_actions"
                for f in meta.extras.get("workflow_security_findings", [])
            }.values()
        ),
        "k8s_network_policy_namespaces": sorted(
            {
                meta.extras.get("k8s_namespace", "")
                for node in nodes
                for meta in [node.metadata]
                if meta.extras.get("iac_format") == "kubernetes"
                and meta.extras.get("is_network_policy_namespace") is True
                and meta.extras.get("k8s_namespace")
            }
        ),
    }


def build_scan_summary(
    nodes: Sequence[Node],
    files: Sequence[tuple[str, str]],
    source_ref: str | None = None,
    branch: str | None = None,
    dc_metadata: list[dict[str, Any]] | None = None,
    instrumentation_evidence: tuple[bool, set[str]] | None = None,
) -> dict[str, Any]:
    """Build scan-level summary for reporting."""
    from .app_env_detector import detect_app_env

    all_nodes = tuple(nodes)  # kept for node_endpoint_paths below — see its comment
    node_count_partition = partition_node_counts(nodes)
    nodes = tuple(iter_effective_nodes(nodes))
    node_types = node_count_partition.effective
    frameworks: list[str] = []

    for node in nodes:
        framework = node.metadata.framework or node.metadata.extras.get("framework")
        if isinstance(framework, str) and _is_agentic_framework(framework):
            frameworks.append(framework)

    deployment = extract_deployment_context(files)
    # API_ENDPOINT nodes are the authoritative, deduped source (they're what
    # `analyze`/`redteam` actually consume); the regex sweep over `files` is
    # kept as a supplementary source so a route that — despite sharing regex
    # patterns with the node-building adapters — still isn't backed by a node
    # doesn't silently disappear from the summary.
    # Unlike the rest of this summary (frameworks, data classification, etc.),
    # endpoint discovery is validated live by a probe before anything is
    # trusted — a bulk-catalog-collapsed-but-real node (see
    # is_llm_soft_rejected's docstring) is a safe candidate to surface here
    # even though it's excluded from counts/findings elsewhere. An
    # LLM-verified-fabricated node must still be excluded, so this draws from
    # all_nodes (pre-iter_effective_nodes) rather than the filtered `nodes`.
    node_endpoint_paths = [
        node.metadata.endpoint
        for node in all_nodes
        if _node_type_str(node) == "API_ENDPOINT"
        and node.metadata.endpoint
        and not is_llm_soft_rejected(node)
    ]
    endpoints = _uniq(node_endpoint_paths + extract_api_endpoints(files))
    modality_support = infer_modalities_support(nodes, files)
    use_case_summary = build_deterministic_use_case_summary(nodes, modality_support)
    modalities = [k.upper() for k, enabled in modality_support.items() if enabled]

    frameworks_list: list[str] = _uniq(frameworks)
    uses_streaming, streaming_endpoints = detect_streaming_output(nodes, files, frameworks_list)

    # App-launch discovery: startup commands, .env files, local/staging/prod URLs
    app_env = detect_app_env(files)
    # Merge any newly discovered deployment URLs into the IaC-detected list
    all_deployment_urls = _uniq(
        deployment.get("deployment_urls", []) + app_env["staging_urls"] + app_env["production_urls"]
    )

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

    # App-wide instrumentation and testing summaries
    dep_names_all: set[str] = set()
    for node in nodes:
        for dep in node.metadata.dependency_names or []:
            dep_names_all.add(dep.lower().replace("-", "_"))
    app_instrumentation = _detect_app_instrumentation(
        nodes,
        files,
        dep_names_all,
        instrumentation_evidence=instrumentation_evidence,
    )
    app_testing = _detect_app_testing(nodes, files, dep_names_all)

    return {
        "source_ref": source_ref,
        "branch": branch,
        "node_type_counts": node_types,
        "node_type_counts_soft_rejected": node_count_partition.soft_rejected,
        "frameworks": frameworks_list,
        "api_endpoints": endpoints[:200],
        "use_case_summary": use_case_summary,
        "modalities": modalities,
        "modality_support": modality_support,
        "uses_streaming": uses_streaming,
        "streaming_endpoints": streaming_endpoints,
        "data_classification": sorted(all_labels),
        "classified_tables": sorted(classified_tables),
        # App-launch discovery
        "startup_commands": app_env["startup_commands"],
        "env_files": app_env["env_files"],
        "env_var_keys": app_env["env_var_keys"],
        "_env_vars": app_env["env_vars"],  # underscore prefix = not in ScanSummary model
        "local_url": app_env["local_url"],
        "staging_urls": app_env["staging_urls"],
        "production_urls": app_env["production_urls"],
        "log_paths": app_env["log_paths"],
        **{**deployment, "deployment_urls": all_deployment_urls},
        **iac_security,
        # 1.5.0 additions
        "instrumentation": app_instrumentation,
        "testing": app_testing,
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
        from ..llm_client import complete_structured

        result = await asyncio.wait_for(
            complete_structured(llm_client, system, user_prompt, schema),
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
        from ..llm_client import complete_structured

        result = await asyncio.wait_for(
            complete_structured(llm_client, system, user_prompt, schema),
            timeout=15.0,
        )
        candidate = (result or {}).get("summary")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:700]
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        pass
    return base_summary


# ---------------------------------------------------------------------------
# App-wide instrumentation detection
# ---------------------------------------------------------------------------

_APP_INSTRUMENTATION_IMPORTS: dict[str, str] = {
    "opentelemetry": "opentelemetry",
    "opentelemetry_api": "opentelemetry",
    "opentelemetry_sdk": "opentelemetry",
    "ddtrace": "datadog",
    "datadog": "datadog",
    "prometheus_client": "prometheus",
    "prometheus": "prometheus",
    "structlog": "structlog",
    "sentry_sdk": "sentry",
    "newrelic": "new_relic",
    "elastic_apm": "elastic_apm",
    "jaeger": "jaeger",
}

_LOG_LEVEL_RE = re.compile(r"logging\.basicConfig\s*\(.*?level\s*=\s*logging\.(\w+)", re.DOTALL)
_LANGSMITH_RUNTIME_SYMBOLS = {
    "ensure_traceable",
    "LangChainTracer",
    "RunTree",
    "get_current_run_tree",
    "trace",
    "traceable",
    "tracing_context",
    "wrap_anthropic",
    "wrap_gemini",
    "wrap_openai",
}
_LANGSMITH_EXAMPLE_PARTS = {"example", "examples", "demo", "demos", "sample", "samples"}
_LANGSMITH_NON_RUNTIME_PARTS = _LANGSMITH_EXAMPLE_PARTS | {
    "doc",
    "docs",
    "fixture",
    "fixtures",
}
_LANGSMITH_TEST_CONFIG_NAMES = {
    ".env.e2e",
    ".env.spec",
    ".env.test",
    ".env.testing",
    "conftest.py",
    "pytest.ini",
    "tox.ini",
}
_LANGSMITH_TEST_CONFIG_PREFIXES = (
    ".env.e2e.",
    ".env.spec.",
    ".env.test.",
    ".env.testing.",
    "cypress.config.",
    "jest.config.",
    "playwright.config.",
    "test-env",
    "test.env",
    "vitest.config.",
)
_LANGSMITH_RUNTIME_MODULES = {"run_helpers", "run_trees", "wrappers"}
_PYTHON_LANGSMITH_EXPORTS: dict[str, set[str]] = {
    "langsmith": {
        "RunTree",
        "get_current_run_tree",
        "trace",
        "traceable",
        "tracing_context",
    }
    | _LANGSMITH_RUNTIME_MODULES,
    "langsmith.run_helpers": {
        "ensure_traceable",
        "get_current_run_tree",
        "trace",
        "traceable",
        "tracing_context",
    },
    "langsmith.run_trees": {"RunTree"},
    "langsmith.wrappers": {"wrap_anthropic", "wrap_gemini", "wrap_openai"},
    "langchain_core": {"tracers"},
    "langchain_core.tracers": {"LangChainTracer"},
    "langchain_core.tracers.langchain": {"LangChainTracer"},
}
_PYTHON_LANGSMITH_MODULES = {
    "langsmith",
    "langsmith.run_helpers",
    "langsmith.run_trees",
    "langsmith.wrappers",
    "langchain_core",
    "langchain_core.tracers",
    "langchain_core.tracers.langchain",
}
_JAVASCRIPT_LANGSMITH_EXPORTS: dict[str, set[str]] = {
    "langsmith": {"RunTree", "traceable"},
    "langsmith/run_trees": {"RunTree"},
    "langsmith/traceable": {"traceable"},
    "langsmith/wrappers": {"wrapOpenAI", "wrapSDK"},
    "langsmith/wrappers/anthropic": {"wrapAnthropic"},
    "langsmith/wrappers/gemini": {"wrapGemini"},
}
_JAVASCRIPT_LANGSMITH_CALLABLES = {
    "traceable",
    "wrapAnthropic",
    "wrapGemini",
    "wrapOpenAI",
    "wrapSDK",
}
_LANGSMITH_TRACING_KEYS = {
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
}


def _langsmith_path_is_non_runtime(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = [part.lower() for part in normalized.split("/") if part]
    filename = parts[-1] if parts else ""
    stem, _, suffix = filename.rpartition(".")
    dotenv_template = filename.startswith(".env.") and bool(
        set(filename.split(".")[2:])
        & (_LANGSMITH_EXAMPLE_PARTS | {"dist", "template", "templates"})
    )
    pytest_filename = suffix in {"py", "pyw"} and (
        filename.startswith("test_") or stem.endswith("_test")
    )
    test_config_filename = suffix in {"json", "toml", "yaml", "yml"} and (
        stem in {"e2e", "spec", "specs", "test", "tests"}
        or stem.startswith(
            (
                "e2e-",
                "e2e_",
                "integration-test",
                "integration_test",
                "test-",
                "test_",
                "tests-",
                "tests_",
                "unit-test",
                "unit_test",
            )
        )
    )
    return (
        looks_like_test_path(normalized.lower())
        or bool(set(parts) & _LANGSMITH_NON_RUNTIME_PARTS)
        or stem in _LANGSMITH_EXAMPLE_PARTS
        or re.search(
            r"(?:^|[._-])(?:example|demo|sample)s?(?:[._-]|$)", stem
        )
        is not None
        or pytest_filename
        or test_config_filename
        or filename in _LANGSMITH_TEST_CONFIG_NAMES
        or filename.startswith(_LANGSMITH_TEST_CONFIG_PREFIXES)
        or filename.endswith(".example")
        or ".example." in filename
        or dotenv_template
    )


def _truthy_tracing_value(expr: ast.expr) -> bool:
    if not isinstance(expr, ast.Constant):
        return False
    value = expr.value
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "on"}


def _subscript_string_key(target: ast.expr) -> str | None:
    if isinstance(target, ast.Subscript):
        slice_value = target.slice
        if isinstance(slice_value, ast.Constant) and isinstance(slice_value.value, str):
            return slice_value.value
    return None


class _PythonBoundNameCollector(ast.NodeVisitor):
    """Collect names local to one Python scope without entering child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.names.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)


def _python_scope_bindings(
    body: Sequence[ast.stmt], arguments: ast.arguments | None = None
) -> tuple[set[str], set[str], set[str]]:
    collector = _PythonBoundNameCollector()
    for statement in body:
        collector.visit(statement)
    if arguments is not None:
        collector.names.update(
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        )
        if arguments.vararg:
            collector.names.add(arguments.vararg.arg)
        if arguments.kwarg:
            collector.names.add(arguments.kwarg.arg)
    return (
        collector.names - collector.global_names - collector.nonlocal_names,
        collector.global_names,
        collector.nonlocal_names,
    )


class _PythonInstrumentationVisitor(ast.NodeVisitor):
    """Resolve instrumentation calls against live bindings in lexical scopes."""

    def __init__(self) -> None:
        self.scopes: list[tuple[dict[str, tuple[str, ...]], set[str]]] = [({}, set())]
        self.member_invalidations: list[set[tuple[str, tuple[str, ...]]]] = [set()]
        self.scope_kinds: list[str] = ["module"]
        self.scope_globals: list[set[str]] = [set()]
        self.scope_nonlocals: list[set[str]] = [set()]
        self.langsmith_active = False
        self.active_tracing_tools: set[str] = set()

    @property
    def bindings(self) -> dict[str, tuple[str, ...]]:
        return self.scopes[-1][0]

    def _resolve_with_scope(self, name: str) -> tuple[tuple[str, ...], int] | None:
        if name in self.scope_globals[-1]:
            binding = self.scopes[0][0].get(name)
            return (binding, 0) if binding is not None else None
        if name in self.scope_nonlocals[-1]:
            index = self._nonlocal_scope_index(name)
            if index is None:
                return None
            binding = self.scopes[index][0].get(name)
            return (binding, index) if binding is not None else None
        skip_class_scopes = self.scope_kinds[-1] in {"function", "lambda", "comprehension"}
        for index in range(len(self.scopes) - 1, -1, -1):
            if skip_class_scopes and self.scope_kinds[index] == "class":
                continue
            bindings, local_names = self.scopes[index]
            if name in bindings:
                return bindings[name], index
            if name in local_names:
                return None
        return None

    def _resolve(self, name: str) -> tuple[str, ...] | None:
        resolved = self._resolve_with_scope(name)
        return resolved[0] if resolved is not None else None

    def _nonlocal_scope_index(self, name: str) -> int | None:
        for index in range(len(self.scopes) - 2, 0, -1):
            if self.scope_kinds[index] not in {"function", "lambda"}:
                continue
            bindings, local_names = self.scopes[index]
            if name in bindings or name in local_names:
                return index
        return None

    def _binding_scope_index(self, name: str) -> int:
        if name in self.scope_globals[-1]:
            return 0
        if name in self.scope_nonlocals[-1]:
            index = self._nonlocal_scope_index(name)
            if index is not None:
                return index
        return len(self.scopes) - 1

    def _bind(self, name: str, binding: tuple[str, ...]) -> None:
        scope_index = self._binding_scope_index(name)
        self.scopes[scope_index][0][name] = binding
        for invalidation_index in {scope_index, len(self.scopes) - 1}:
            self.member_invalidations[invalidation_index] = {
                item
                for item in self.member_invalidations[invalidation_index]
                if item[0] != name
            }

    def _unbind_name(self, name: str) -> None:
        scope_index = self._binding_scope_index(name)
        self.scopes[scope_index][0].pop(name, None)
        self.scopes[scope_index][1].add(name)

    def _builtin_is_live(self, name: str) -> bool:
        if name in self.scope_globals[-1]:
            bindings, local_names = self.scopes[0]
            return name not in bindings and name not in local_names
        if name in self.scope_nonlocals[-1]:
            index = self._nonlocal_scope_index(name)
            return index is None
        skip_class_scopes = self.scope_kinds[-1] in {"function", "lambda", "comprehension"}
        for index in range(len(self.scopes) - 1, -1, -1):
            if skip_class_scopes and self.scope_kinds[index] == "class":
                continue
            bindings, local_names = self.scopes[index]
            if name in bindings or name in local_names:
                return False
        return True

    def _invalidate_member(self, target: ast.expr, members: tuple[str, ...]) -> None:
        attributes: list[str] = []
        root: ast.expr = target
        while isinstance(root, ast.Attribute):
            attributes.append(root.attr)
            root = root.value
        if not isinstance(root, ast.Name):
            return
        binding = self._resolve(root.id)
        if binding and binding[0] in {
            "langsmith_module",
            "langsmith_symbol",
            "trace_module",
            "trace_symbol",
        }:
            self.member_invalidations[-1].add(
                (root.id, (*tuple(reversed(attributes)), *members))
            )

    def _unbind_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self._unbind_name(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._unbind_target(item)
        elif isinstance(target, ast.Starred):
            self._unbind_target(target.value)
        elif isinstance(target, ast.Attribute):
            self._invalidate_member(target, ())

    def _member_is_invalidated(
        self,
        name: str,
        attributes: tuple[str, ...],
        binding_scope: int,
    ) -> bool:
        skip_class_scopes = self.scope_kinds[-1] in {"function", "lambda", "comprehension"}
        for index in range(len(self.member_invalidations) - 1, binding_scope - 1, -1):
            if skip_class_scopes and self.scope_kinds[index] == "class":
                continue
            for invalid_name, invalid_path in self.member_invalidations[index]:
                if invalid_name == name and attributes[: len(invalid_path)] == invalid_path:
                    return True
        return False

    def _resolved_path(self, expr: ast.expr) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        attributes: list[str] = []
        while isinstance(expr, ast.Attribute):
            attributes.append(expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name):
            return None
        resolved_attributes = tuple(reversed(attributes))
        resolved = self._resolve_with_scope(expr.id)
        if resolved is None:
            return None
        binding, binding_scope = resolved
        if self._member_is_invalidated(expr.id, resolved_attributes, binding_scope):
            return None
        return binding, resolved_attributes

    @staticmethod
    def _langsmith_module_path(
        binding: tuple[str, ...], attributes: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        module = binding[1]
        module_parts = tuple(module.split("."))[1:]
        if module in {"langsmith", "langchain_core"} and attributes:
            submodule = f"{module}.{attributes[0]}"
            if attributes[0] in {"run_helpers", "run_trees", "tracers", "wrappers"} and not any(
                loaded == submodule or loaded.startswith(f"{submodule}.")
                for loaded in binding[2:]
            ):
                return None
        return (*module_parts, *attributes)

    def _check_langsmith_expr(self, expr: ast.expr, *, decorator: bool = False) -> None:
        resolved = self._resolved_path(expr)
        if resolved is None:
            return
        binding, attributes = resolved
        if binding[0] == "langsmith_symbol":
            role = binding[1]
            if not attributes and role in _LANGSMITH_RUNTIME_SYMBOLS - {"tracing_context"}:
                if not decorator or role in {"ensure_traceable", "traceable"}:
                    self.langsmith_active = True
            return
        if binding[0] != "langsmith_module":
            return
        path = self._langsmith_module_path(binding, attributes)
        if path is None:
            return
        runtime_paths = {
            ("LangChainTracer",),
            ("RunTree",),
            ("get_current_run_tree",),
            ("trace",),
            ("traceable",),
            ("run_helpers", "ensure_traceable"),
            ("run_helpers", "get_current_run_tree"),
            ("run_helpers", "trace"),
            ("run_helpers", "traceable"),
            ("run_trees", "RunTree"),
            ("wrappers", "wrap_anthropic"),
            ("wrappers", "wrap_gemini"),
            ("wrappers", "wrap_openai"),
            ("tracers", "LangChainTracer"),
            ("tracers", "langchain", "LangChainTracer"),
        }
        if path in runtime_paths and (
            not decorator or path[-1] in {"ensure_traceable", "traceable"}
        ):
            self.langsmith_active = True

    def _check_langsmith_call(self, node: ast.Call) -> None:
        """Recognize LangSmith calls whose arguments determine activation."""
        resolved = self._resolved_path(node.func)
        if resolved is None:
            return
        binding, attributes = resolved
        if binding[0] == "langsmith_symbol":
            path = (binding[1], *attributes)
        elif binding[0] == "langsmith_module":
            path = self._langsmith_module_path(binding, attributes)
            if path is None:
                return
        else:
            return
        if path not in {
            ("tracing_context",),
            ("run_helpers", "tracing_context"),
        }:
            return
        enabled = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "enabled"),
            None,
        )
        if enabled is not None and _truthy_tracing_value(enabled):
            self.langsmith_active = True

    def _check_tracing_expr(self, expr: ast.expr) -> None:
        resolved = self._resolved_path(expr)
        if resolved is None:
            return
        binding, attributes = resolved
        if binding[0] == "trace_symbol":
            tool, role = binding[1], binding[2]
            direct_roles = _ACTIVE_TRACE_MARKERS
            member_paths: dict[str, dict[str, set[tuple[str, ...]]]] = {
                "opentelemetry": {
                    "trace": {
                        ("get_tracer",),
                        ("set_tracer_provider",),
                        ("start_as_current_span",),
                        ("start_span",),
                    }
                },
                "datadog": {
                    "tracer": {
                        ("configure",),
                        ("start_span",),
                        ("trace",),
                        ("wrap",),
                    }
                },
                "jaeger": {},
            }
            active = not attributes and (
                role in direct_roles[tool]
                or (tool == "opentelemetry" and role.endswith("Instrumentor"))
            )
            active = active or attributes in member_paths[tool].get(role, set())
            if (
                tool == "opentelemetry"
                and role.endswith("Instrumentor")
                and attributes in {("instrument",), ("instrument_app",)}
            ):
                active = True
            if active:
                self.active_tracing_tools.add(tool)
            return
        if binding[0] == "trace_module" and attributes:
            tool, module = binding[1], binding[2]
            root, *suffix = module.lower().replace("-", "_").split(".")
            path = (*suffix, *attributes)
            canonical_paths = {
                "opentelemetry": {
                    ("trace", "get_tracer"),
                    ("trace", "set_tracer_provider"),
                    ("trace", "start_as_current_span"),
                    ("trace", "start_span"),
                    ("sdk", "trace", "TracerProvider"),
                },
                "datadog": {
                    ("patch",),
                    ("patch_all",),
                    ("tracer", "configure"),
                    ("tracer", "start_span"),
                    ("tracer", "trace"),
                    ("tracer", "wrap"),
                },
                "jaeger": {
                    ("Config",),
                    ("JaegerExporter",),
                    ("exporter", "jaeger", "JaegerExporter"),
                },
            }
            if (
                root in {"opentelemetry", "ddtrace", "datadog", "jaeger", "jaeger_client"}
                and path in canonical_paths[tool]
            ):
                self.active_tracing_tools.add(tool)

    def _check_environment_assignment(self, target: ast.expr, value: ast.expr) -> None:
        if not _truthy_tracing_value(value):
            return
        key = _subscript_string_key(target)
        if key not in _LANGSMITH_TRACING_KEYS or not isinstance(target, ast.Subscript):
            return
        resolved = self._resolved_path(target.value)
        if resolved is None:
            return
        binding, attributes = resolved
        if (binding[0] == "os_module" and attributes == ("environ",)) or (
            binding[0] == "os_environ" and not attributes
        ):
            self.langsmith_active = True

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".")[0]
            if alias.name in _PYTHON_LANGSMITH_MODULES:
                if alias.asname:
                    self._bind(local_name, ("langsmith_module", alias.name))
                else:
                    root_module = alias.name.split(".")[0]
                    existing = self._resolve(local_name)
                    loaded = {
                        *(
                            existing[2:]
                            if existing
                            and existing[0] == "langsmith_module"
                            and existing[1] == root_module
                            else ()
                        ),
                        *(() if alias.name == root_module else (alias.name,)),
                    }
                    self._bind(
                        local_name,
                        ("langsmith_module", root_module, *sorted(loaded)),
                    )
            elif alias.name == "os":
                self._bind(local_name, ("os_module", "os"))
            else:
                tool = _trace_tool_for_module(alias.name)
                if tool:
                    module = alias.name if alias.asname else alias.name.split(".")[0]
                    self._bind(local_name, ("trace_module", tool, module))
                else:
                    self._unbind_name(local_name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        langsmith_exports = _PYTHON_LANGSMITH_EXPORTS.get(module, set())
        tracing_tool = _trace_tool_for_module(module)
        for alias in node.names:
            if alias.name == "*":
                for bound_name in tuple(self.bindings):
                    self._unbind_name(bound_name)
                continue
            local_name = alias.asname or alias.name
            if alias.name in langsmith_exports and alias.name in _LANGSMITH_RUNTIME_SYMBOLS:
                self._bind(local_name, ("langsmith_symbol", alias.name))
            elif module == "langsmith" and alias.name in _LANGSMITH_RUNTIME_MODULES:
                self._bind(local_name, ("langsmith_module", f"langsmith.{alias.name}"))
            elif module == "langchain_core" and alias.name == "tracers":
                self._bind(local_name, ("langsmith_module", "langchain_core.tracers"))
            elif module == "os" and alias.name == "environ":
                self._bind(local_name, ("os_environ", "environ"))
            elif tracing_tool and _python_trace_export_is_supported(module, alias.name):
                self._bind(local_name, ("trace_symbol", tracing_tool, alias.name))
            else:
                self._unbind_name(local_name)

    def visit_Call(self, node: ast.Call) -> None:
        self._check_langsmith_expr(node.func)
        self._check_tracing_expr(node.func)
        self.generic_visit(node)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr"}
            and self._builtin_is_live(node.func.id)
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            self._invalidate_member(node.args[0], (node.args[1].value,))

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                self._check_langsmith_call(item.context_expr)
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._check_environment_assignment(target, node.value)
            self._unbind_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._check_environment_assignment(node.target, node.value)
        self._unbind_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._unbind_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._unbind_target(node.target)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._unbind_target(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._unbind_target(target)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._unbind_name(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name:
            self._unbind_name(node.name)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._unbind_name(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        for key in node.keys:
            self.visit(key)
        for pattern in node.patterns:
            self.visit(pattern)
        if node.rest:
            self._unbind_name(node.rest)

    def _visit_comprehension_scope(
        self,
        generators: list[ast.comprehension],
        result_nodes: tuple[ast.expr, ...],
    ) -> None:
        if not generators:
            return
        self.visit(generators[0].iter)
        collector = _PythonBoundNameCollector()
        for generator in generators:
            collector.visit(generator.target)
        self.scopes.append(({}, set(collector.names)))
        self.member_invalidations.append(set())
        self.scope_kinds.append("comprehension")
        self.scope_globals.append(set())
        self.scope_nonlocals.append(set())
        for index, generator in enumerate(generators):
            if index:
                self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for result in result_nodes:
            self.visit(result)
        self.scopes.pop()
        self.member_invalidations.pop()
        self.scope_kinds.pop()
        self.scope_globals.pop()
        self.scope_nonlocals.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension_scope(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension_scope(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension_scope(node.generators, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension_scope(node.generators, (node.elt,))

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            self._check_langsmith_expr(target, decorator=True)
            if isinstance(decorator, ast.Call):
                self._check_langsmith_call(decorator)
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        self._unbind_name(node.name)
        # A function body is deferred.  Global/nonlocal writes must affect name
        # resolution while that body is inspected, but must not escape into the
        # enclosing traversal as though the function had already run.
        enclosing_scopes = [
            (dict(bindings), set(local_names)) for bindings, local_names in self.scopes
        ]
        enclosing_member_invalidations = [set(items) for items in self.member_invalidations]
        local_names, global_names, nonlocal_names = _python_scope_bindings(
            node.body, node.args
        )
        self.scopes.append(({}, local_names))
        self.member_invalidations.append(set())
        self.scope_kinds.append("function")
        self.scope_globals.append(global_names)
        self.scope_nonlocals.append(nonlocal_names)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.member_invalidations.pop()
        self.scope_kinds.pop()
        self.scope_globals.pop()
        self.scope_nonlocals.pop()
        self.scopes[:] = enclosing_scopes
        self.member_invalidations[:] = enclosing_member_invalidations

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            self._check_langsmith_expr(target, decorator=True)
            if isinstance(decorator, ast.Call):
                self._check_langsmith_call(decorator)
            self.visit(decorator)
        self._unbind_name(node.name)
        local_names, global_names, nonlocal_names = _python_scope_bindings(node.body)
        self.scopes.append(({}, local_names))
        self.member_invalidations.append(set())
        self.scope_kinds.append("class")
        self.scope_globals.append(global_names)
        self.scope_nonlocals.append(nonlocal_names)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.member_invalidations.pop()
        self.scope_kinds.pop()
        self.scope_globals.pop()
        self.scope_nonlocals.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        local_names = {
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        }
        if node.args.vararg:
            local_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            local_names.add(node.args.kwarg.arg)
        collector = _PythonBoundNameCollector()
        collector.visit(node.body)
        local_names.update(collector.names)
        self.scopes.append(({}, local_names))
        self.member_invalidations.append(set())
        self.scope_kinds.append("lambda")
        self.scope_globals.append(set())
        self.scope_nonlocals.append(set())
        self.visit(node.body)
        self.scopes.pop()
        self.member_invalidations.pop()
        self.scope_kinds.pop()
        self.scope_globals.pop()
        self.scope_nonlocals.pop()


def _python_instrumentation_visitor(content: str) -> _PythonInstrumentationVisitor | None:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    visitor = _PythonInstrumentationVisitor()
    visitor.visit(tree)
    return visitor


def _python_has_active_langsmith(content: str) -> bool:
    """Detect executable LangSmith use with import provenance in Python AST."""
    visitor = _python_instrumentation_visitor(content)
    return bool(visitor and visitor.langsmith_active)


def _javascript_slash_starts_regex(masked: list[str], index: int) -> bool:
    """Use JavaScript lexical context to distinguish regex literals from division."""
    line_start = index
    while line_start > 0 and masked[line_start - 1] != "\n":
        line_start -= 1
    prefix = "".join(masked[line_start:index]).rstrip()
    if not prefix:
        return True
    if prefix[-1] in "([{=,:;!?&|+-*%^~<>\n":
        return True
    word_match = re.search(r"([A-Za-z_$][\w$]*)$", prefix)
    return bool(
        word_match
        and word_match.group(1)
        in {
            "await",
            "case",
            "delete",
            "do",
            "else",
            "in",
            "instanceof",
            "new",
            "return",
            "throw",
            "typeof",
            "void",
            "yield",
        }
    )


def _mask_javascript(content: str) -> tuple[str, str]:
    """Return comment-free and executable-only JavaScript lexical views."""
    comments = list(content)
    executable = list(content)
    state = "code"
    resume_state = "code"
    template_return_states: list[str] = []
    template_expression_depths: list[int] = []
    escaped = False
    regex_character_class = False
    index = 0
    while index < len(content):
        char = content[index]
        nxt = content[index + 1] if index + 1 < len(content) else ""
        if state in {"code", "template_expression"}:
            if (
                state == "template_expression"
                and char == "}"
                and template_expression_depths[-1] == 0
            ):
                comments[index] = executable[index] = " "
                template_expression_depths.pop()
                state = "template"
                index += 1
                continue
            if state == "template_expression" and char == "{":
                template_expression_depths[-1] += 1
            elif state == "template_expression" and char == "}":
                template_expression_depths[-1] -= 1
            if char == "/" and nxt == "/":
                comments[index] = comments[index + 1] = " "
                executable[index] = executable[index + 1] = " "
                resume_state = state
                state = "line_comment"
                index += 2
                continue
            if char == "/" and nxt == "*":
                comments[index] = comments[index + 1] = " "
                executable[index] = executable[index + 1] = " "
                resume_state = state
                state = "block_comment"
                index += 2
                continue
            if char == "/" and _javascript_slash_starts_regex(executable, index):
                comments[index] = executable[index] = " "
                resume_state = state
                state = "regex"
                escaped = False
                regex_character_class = False
                index += 1
                continue
            if char in {"'", '"'}:
                executable[index] = " "
                resume_state = state
                state = {"'": "single", '"': "double"}[char]
            elif char == "`":
                comments[index] = executable[index] = " "
                template_return_states.append(state)
                state = "template"
                escaped = False
        elif state == "line_comment":
            if char == "\n":
                state = resume_state
            else:
                comments[index] = executable[index] = " "
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                comments[index] = comments[index + 1] = " "
                executable[index] = executable[index + 1] = " "
                state = resume_state
                index += 2
                continue
            if char != "\n":
                comments[index] = executable[index] = " "
        elif state in {"single", "double"}:
            if char != "\n":
                executable[index] = " "
            quote = {"single": "'", "double": '"'}[state]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                state = resume_state
        elif state == "template":
            if char != "\n":
                comments[index] = executable[index] = " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "`":
                state = template_return_states.pop()
            elif char == "$" and nxt == "{":
                comments[index] = comments[index + 1] = " "
                executable[index] = executable[index + 1] = " "
                template_expression_depths.append(0)
                state = "template_expression"
                index += 2
                continue
        elif state == "regex":
            if char != "\n":
                comments[index] = executable[index] = " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "[":
                regex_character_class = True
            elif char == "]":
                regex_character_class = False
            elif char == "/" and not regex_character_class:
                state = "regex_flags"
        elif state == "regex_flags":
            if char.isalpha():
                comments[index] = executable[index] = " "
            else:
                state = resume_state
                continue
        index += 1
    return "".join(comments), "".join(executable)


def _javascript_restore_member_keys(comments_free: str, executable: str) -> str:
    """Restore quoted identifier keys only when they are executable member syntax."""
    restored = list(executable)
    member_key = re.compile(r"\[\s*([\"'])(?P<key>[A-Za-z_$][\w$]*)\1\s*\]")
    for match in member_key.finditer(comments_free):
        if not executable[match.start() : match.end()].strip():
            continue
        restored[match.start() : match.end()] = comments_free[match.start() : match.end()]
    return "".join(restored)


def _javascript_member_access(member: str) -> str:
    escaped = re.escape(member)
    return rf"(?:\s*\.\s*{escaped}|\s*\[\s*[\"']{escaped}[\"']\s*\])"


def _match_is_executable(match: re.Match[str], executable: str) -> bool:
    return bool(executable[match.start() : match.start() + len(match.group(0))].strip())


def _javascript_brace_pairs(executable: str) -> dict[int, int]:
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for index, char in enumerate(executable):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            pairs[stack.pop()] = index
    return pairs


def _javascript_direct_scope_view(source: str, start: int, end: int) -> str:
    """Preserve only text directly owned by one brace scope."""
    visible = list(" " * len(source))
    depth = 0
    body_start = start + 1 if start >= 0 else 0
    for index in range(body_start, end):
        char = source[index]
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            visible[index] = char
    return "".join(visible)


def _javascript_alias_is_live_at(
    executable: str,
    alias: str,
    declaration_span: tuple[int, int],
    use_position: int,
    members: tuple[str, ...] = (),
) -> bool:
    """Resolve an imported alias at one call site against enclosing JS scopes."""
    source = list(executable)
    source[declaration_span[0] : declaration_span[1]] = " " * (
        declaration_span[1] - declaration_span[0]
    )
    text = "".join(source)
    escaped = re.escape(alias)
    pairs = _javascript_brace_pairs(text)
    declaration_position = declaration_span[0]
    declaration_scopes = [(-1, len(text))]
    declaration_scopes.extend(
        (start, end)
        for start, end in pairs.items()
        if start < declaration_position < end
    )
    declaration_scope = max(declaration_scopes, key=lambda scope: scope[0])
    if not declaration_scope[0] < use_position < declaration_scope[1]:
        return False
    scopes = [(-1, len(text))]
    scopes.extend((start, end) for start, end in pairs.items() if start < use_position < end)
    declaration_patterns = (
        rf"\b(?:const|let|var|function|class)\s+{escaped}\b",
        rf"\b(?:const|let|var)\s*\{{[^}}]*\b(?:[A-Za-z_$][\w$]*\s*:\s*)?{escaped}\b",
        rf"\bimport\s+(?:\*\s+as\s+)?{escaped}\s+from\b",
        rf"\bimport\s*\{{[^}}]*\b(?:[A-Za-z_$][\w$]*\s+as\s+)?{escaped}\b[^}}]*\}}\s+from\b",
    )
    assignment_pattern = rf"\b{escaped}\s*(?:=(?!=)|\+=|-=|\*=|/=|%=|&&=|\|\|=|\?\?=)"
    parameter_patterns = (
        rf"\bfunction(?:\s+[A-Za-z_$][\w$]*)?\s*\([^)]*\b{escaped}\b[^)]*\)\s*$",
        rf"\([^)]*\b{escaped}\b[^)]*\)\s*(?::\s*[^=]+)?=>\s*$",
        rf"\b{escaped}\s*(?::\s*[^=]+)?=>\s*$",
        rf"\bcatch\s*\([^)]*\b{escaped}\b[^)]*\)\s*$",
        rf"(?:(?:public|private|protected|static|async|get|set)\s+)*"
        rf"(?:constructor|[A-Za-z_$][\w$]*)\s*\([^)]*\b{escaped}\b[^)]*\)"
        rf"\s*(?::\s*[^=]+)?$",
    )
    for start, end in scopes:
        direct = _javascript_direct_scope_view(text, start, end)
        if any(re.search(pattern, direct) for pattern in declaration_patterns):
            return False
        if re.search(assignment_pattern, direct[:use_position]):
            return False
        member_path = rf"\b{escaped}"
        for member in members:
            member_path += _javascript_member_access(member)
            member_write = (
                rf"(?:\bdelete\s+{member_path}(?![\w$])|"
                rf"{member_path}\s*(?:=(?!=)|\+=|-=|\*=|/=|%=|&&=|\|\|=|\?\?=))"
            )
            if re.search(member_write, direct[:use_position]):
                return False
        if start >= 0:
            header_start = max(
                0,
                text.rfind(";", 0, start) + 1,
                text.rfind("}", 0, start) + 1,
                text.rfind("{", 0, start) + 1,
            )
            header = text[header_start:start].strip()
            if any(re.search(pattern, header, re.DOTALL) for pattern in parameter_patterns):
                return False
    return True


def _javascript_bare_require_is_live_at(executable: str, position: int) -> bool:
    """True for an unshadowed CommonJS global ``require`` call only."""
    previous = position - 1
    while previous >= 0 and executable[previous].isspace():
        previous -= 1
    if previous >= 0 and (executable[previous] in ".$" or executable[previous].isalnum()):
        return False
    statement_start = max(
        executable.rfind(";", 0, position),
        executable.rfind("{", 0, position),
        executable.rfind("}", 0, position),
    )
    prefix = executable[statement_start + 1 : position]
    if re.search(
        r"(?:\([^)]*\brequire\b[^)]*\)|\brequire\b)\s*"
        r"(?::\s*[^=]+)?=>[^;{}]*$",
        prefix,
        re.DOTALL,
    ):
        return False
    return _javascript_alias_is_live_at(executable, "require", (0, 0), position)


def _javascript_named_call_is_declaration(executable: str, open_paren: int) -> bool:
    """Reject function/class/object method headers that resemble bare calls."""
    depth = 0
    close_paren: int | None = None
    for index in range(open_paren, len(executable)):
        char = executable[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                close_paren = index
                break
    if close_paren is None:
        return False
    tail = executable[close_paren + 1 :]
    if re.match(r"\s*(?::\s*[^\n{;=]+)?\s*\{", tail):
        return True
    return re.match(r"\s*:\s*[^\n{;=]+\s*;", tail) is not None


def _javascript_has_bound_call(
    executable: str,
    alias: str,
    members: tuple[str, ...] = (),
    declaration_span: tuple[int, int] | None = None,
) -> bool:
    path = rf"\b{re.escape(alias)}"
    for member in members:
        path += _javascript_member_access(member)
    for match in re.finditer(path + r"\s*\(", executable):
        if not members and _javascript_named_call_is_declaration(executable, match.end() - 1):
            continue
        if declaration_span is None or _javascript_alias_is_live_at(
            executable, alias, declaration_span, match.start(), members
        ):
            return True
    return False


def _javascript_has_bound_construction(
    executable: str,
    alias: str,
    members: tuple[str, ...] = (),
    declaration_span: tuple[int, int] | None = None,
) -> bool:
    path = rf"\bnew\s+{re.escape(alias)}"
    for member in members:
        path += _javascript_member_access(member)
    return any(
        declaration_span is None
        or _javascript_alias_is_live_at(
            executable, alias, declaration_span, match.start(), members
        )
        for match in re.finditer(path + r"\s*\(", executable)
    )


def _javascript_has_active_langsmith(content: str) -> bool:
    comments_free, executable = _mask_javascript(content)
    scope_view = _javascript_restore_member_keys(comments_free, executable)
    local_callables: dict[str, tuple[int, int]] = {}
    local_run_tree: dict[str, tuple[int, int]] = {}
    module_aliases: dict[str, tuple[str, tuple[int, int]]] = {}

    named_import_re = re.compile(
        r"\bimport\s*\{(?P<names>[^}]*)\}\s*from\s*"
        r"[\"'](?P<module>langsmith(?:/[^\"']*)?)[\"']"
    )
    for match in named_import_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        supported_exports = _JAVASCRIPT_LANGSMITH_EXPORTS.get(match.group("module"), set())
        for item in match.group("names").split(","):
            parts = re.split(r"\s+as\s+", item.strip())
            if (
                parts
                and parts[0].strip() in _JAVASCRIPT_LANGSMITH_CALLABLES
                and parts[0].strip() in supported_exports
            ):
                local_callables[parts[-1].strip()] = match.span()
            elif (
                parts
                and parts[0].strip() == "RunTree"
                and "RunTree" in supported_exports
            ):
                local_run_tree[parts[-1].strip()] = match.span()

    namespace_import_re = re.compile(
        r"\bimport\s*\*\s*as\s*(?P<alias>[A-Za-z_$][\w$]*)\s*from\s*"
        r"[\"'](?P<module>langsmith(?:/[^\"']*)?)[\"']"
    )
    for match in namespace_import_re.finditer(comments_free):
        if _match_is_executable(match, executable) and match.group(
            "module"
        ) in _JAVASCRIPT_LANGSMITH_EXPORTS:
            module_aliases[match.group("alias")] = (match.group("module"), match.span())

    destructured_require_re = re.compile(
        r"\b(?:const|let|var)\s*\{(?P<names>[^}]*)\}\s*=\s*require\(\s*"
        r"[\"'](?P<module>langsmith(?:/[^\"']*)?)[\"']\s*\)"
    )
    for match in destructured_require_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        require_position = comments_free.find("require", match.start(), match.end())
        if not _javascript_bare_require_is_live_at(scope_view, require_position):
            continue
        supported_exports = _JAVASCRIPT_LANGSMITH_EXPORTS.get(match.group("module"), set())
        for item in match.group("names").split(","):
            parts = item.strip().split(":", 1)
            if (
                parts
                and parts[0].strip() in _JAVASCRIPT_LANGSMITH_CALLABLES
                and parts[0].strip() in supported_exports
            ):
                local_callables[parts[-1].strip()] = match.span()
            elif (
                parts
                and parts[0].strip() == "RunTree"
                and "RunTree" in supported_exports
            ):
                local_run_tree[parts[-1].strip()] = match.span()

    module_require_re = re.compile(
        r"\b(?:const|let|var)\s+(?P<alias>[A-Za-z_$][\w$]*)\s*=\s*require\(\s*"
        r"[\"'](?P<module>langsmith(?:/[^\"']*)?)[\"']\s*\)(?!\s*[.\[])"
    )
    for match in module_require_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        require_position = comments_free.find("require", match.start(), match.end())
        if (
            _javascript_bare_require_is_live_at(scope_view, require_position)
            and match.group("module") in _JAVASCRIPT_LANGSMITH_EXPORTS
        ):
            module_aliases[match.group("alias")] = (match.group("module"), match.span())

    property_require_re = re.compile(
        r"\b(?:const|let|var)\s+(?P<alias>[A-Za-z_$][\w$]*)\s*=\s*(?P<require>require)\(\s*"
        r"[\"'](?P<module>langsmith(?:/[^\"']*)?)[\"']\s*\)"
        rf"\s*\.\s*(?P<member>{'|'.join(sorted(_JAVASCRIPT_LANGSMITH_CALLABLES))})\b"
    )
    for match in property_require_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        if not _javascript_bare_require_is_live_at(scope_view, match.start("require")):
            continue
        if match.group("member") in _JAVASCRIPT_LANGSMITH_EXPORTS.get(
            match.group("module"), set()
        ):
            local_callables[match.group("alias")] = match.span()

    direct_require_call_re = re.compile(
        r"\b(?P<require>require)\(\s*[\"'](?P<module>langsmith(?:/[^\"']*)?)[\"']\s*\)"
        rf"\s*\.\s*(?P<member>{'|'.join(sorted(_JAVASCRIPT_LANGSMITH_CALLABLES))})\s*\("
    )
    for match in direct_require_call_re.finditer(comments_free):
        if (
            _match_is_executable(match, executable)
            and _javascript_bare_require_is_live_at(scope_view, match.start("require"))
            and match.group("member")
            in _JAVASCRIPT_LANGSMITH_EXPORTS.get(match.group("module"), set())
        ):
            return True

    for alias, span in local_callables.items():
        decorator_active = any(
            _javascript_alias_is_live_at(scope_view, alias, span, match.start())
            for match in re.finditer(rf"@{re.escape(alias)}\b", scope_view)
        )
        if _javascript_has_bound_call(
            scope_view, alias, declaration_span=span
        ) or decorator_active:
            return True
    if any(
        _javascript_has_bound_construction(scope_view, alias, declaration_span=span)
        for alias, span in local_run_tree.items()
    ):
        return True
    for alias, (module, span) in module_aliases.items():
        supported_exports = _JAVASCRIPT_LANGSMITH_EXPORTS[module]
        for member in _JAVASCRIPT_LANGSMITH_CALLABLES:
            if member in supported_exports and _javascript_has_bound_call(
                scope_view, alias, (member,), declaration_span=span
            ):
                return True
        if "RunTree" in supported_exports and _javascript_has_bound_construction(
            scope_view, alias, ("RunTree",), declaration_span=span
        ):
            return True
    return False


def _javascript_has_truthy_langsmith_env(content: str) -> bool:
    comments_free, executable = _mask_javascript(content)
    scope_view = _javascript_restore_member_keys(comments_free, executable)
    process_aliases: dict[str, tuple[int, int]] = {"process": (0, 0)}
    env_aliases: dict[str, tuple[int, int]] = {}
    process_modules = {"node:process", "process"}

    import_re = re.compile(
        r"\bimport\s+(?:(?:\*\s+as\s+)?(?P<alias>[A-Za-z_$][\w$]*))\s+from\s*"
        r"[\"'](?P<module>node:process|process)[\"']"
    )
    for match in import_re.finditer(comments_free):
        if _match_is_executable(match, executable):
            process_aliases[match.group("alias")] = match.span()

    named_env_import_re = re.compile(
        r"\bimport\s*\{(?P<names>[^}]*)\}\s*from\s*"
        r"[\"'](?P<module>node:process|process)[\"']"
    )
    for match in named_env_import_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        for item in match.group("names").split(","):
            parts = re.split(r"\s+as\s+", item.strip())
            if parts and parts[0].strip() == "env":
                env_aliases[parts[-1].strip()] = match.span()

    require_re = re.compile(
        r"\b(?:const|let|var)\s+(?P<alias>[A-Za-z_$][\w$]*)\s*=\s*(?P<require>require)\(\s*"
        r"[\"'](?P<module>node:process|process)[\"']\s*\)"
    )
    for match in require_re.finditer(comments_free):
        if (
            match.group("module") in process_modules
            and _match_is_executable(match, executable)
            and _javascript_bare_require_is_live_at(scope_view, match.start("require"))
        ):
            process_aliases[match.group("alias")] = match.span()

    destructured_env_require_re = re.compile(
        r"\b(?:const|let|var)\s*\{(?P<names>[^}]*)\}\s*=\s*(?P<require>require)\(\s*"
        r"[\"'](?P<module>node:process|process)[\"']\s*\)"
    )
    for match in destructured_env_require_re.finditer(comments_free):
        if not _match_is_executable(match, executable) or not (
            _javascript_bare_require_is_live_at(scope_view, match.start("require"))
        ):
            continue
        for item in match.group("names").split(","):
            parts = item.strip().split(":", 1)
            if parts and parts[0].strip() == "env":
                env_aliases[parts[-1].strip()] = match.span()

    keys = "|".join(sorted(_LANGSMITH_TRACING_KEYS))
    for alias, span in process_aliases.items():
        assignment_re = re.compile(
            rf"\b{re.escape(alias)}{_javascript_member_access('env')}"
            rf"(?P<key_access>\.\s*(?P<dot_key>{keys})|"
            rf"\s*\[\s*[\"'](?P<bracket_key>{keys})[\"']\s*\])"
            r"\s*=\s*(?:[\"'](?i:true|1|yes|on)[\"']|(?i:true)|1)(?=\s|;|$)",
        )
        for match in assignment_re.finditer(comments_free):
            if (
                _match_is_executable(match, executable)
                and _javascript_alias_is_live_at(
                    scope_view, alias, span, match.start(), ("env",)
                )
            ):
                return True
    for alias, span in env_aliases.items():
        assignment_re = re.compile(
            rf"\b{re.escape(alias)}"
            rf"(?P<key_access>\.\s*(?P<dot_key>{keys})|"
            rf"\s*\[\s*[\"'](?P<bracket_key>{keys})[\"']\s*\])"
            r"\s*=\s*(?:[\"'](?i:true|1|yes|on)[\"']|(?i:true)|1)(?=\s|;|$)",
        )
        for match in assignment_re.finditer(comments_free):
            if (
                _match_is_executable(match, executable)
                and _javascript_alias_is_live_at(scope_view, alias, span, match.start())
            ):
                return True
    return False


def _dotenv_has_truthy_langsmith(content: str) -> bool:
    quote: str | None = None
    for raw_line in content.splitlines():
        if quote is not None:
            escaped = False
            for char in raw_line:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                    break
            continue
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"(?:export\s+)?(?P<key>LANGSMITH_TRACING(?:_V2)?|LANGCHAIN_TRACING_V2)\s*=\s*"
            r"(?P<value>[\"']?(?i:true|1|yes|on)[\"']?)\s*(?:#.*)?",
            line,
        )
        if match:
            return True
        assignment = line.split("=", 1)
        if len(assignment) == 2:
            value = assignment[1].lstrip()
            if value.startswith(("'", '"')) and value.count(value[0]) == 1:
                quote = value[0]
    return False


def _yaml_inline_env_is_truthy(value: str) -> bool:
    value = value.strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return False
    key = r"(?:LANGSMITH_TRACING(?:_V2)?|LANGCHAIN_TRACING_V2)"
    truthy = r"(?:true|1|yes|on)"
    map_entry = re.compile(
        rf"(?:^|\{{|,)\s*[\"']?{key}[\"']?\s*:\s*[\"']?(?i:{truthy})[\"']?\s*(?=,|\}})",
    )
    list_entry = re.compile(
        rf"(?:^|\[|,)\s*[\"']?{key}\s*=\s*(?i:{truthy})[\"']?\s*(?=,|\])",
    )
    return map_entry.search(value) is not None or list_entry.search(value) is not None


def _yaml_has_truthy_langsmith_env(content: str) -> bool:
    env_indent: int | None = None
    block_indent: int | None = None
    multiline_quote: str | None = None
    for raw_line in content.splitlines():
        visible: list[str] = []
        quote = multiline_quote
        escaped = False
        for char in raw_line:
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                    if multiline_quote is None:
                        visible.append(char)
                elif multiline_quote is None:
                    visible.append(char)
                continue
            if char == "#":
                break
            visible.append(char)
            if char in {"'", '"'}:
                quote = char
        multiline_quote = quote
        raw_line = "".join(visible)
        stripped = raw_line.lstrip()
        indent = len(raw_line) - len(stripped)
        if block_indent is not None:
            if not stripped or indent > block_indent:
                continue
            block_indent = None
        line = stripped.rstrip()
        if not line:
            continue
        if re.search(r":\s*[>|][-+]?\s*$", line):
            block_indent = indent
            continue
        if env_indent is not None and indent <= env_indent:
            env_indent = None
        inline_env = re.fullmatch(r"(?:environment|env)\s*:\s*(?P<value>.+)", line, re.IGNORECASE)
        if inline_env and _yaml_inline_env_is_truthy(inline_env.group("value")):
            return True
        if re.fullmatch(r"(?:environment|env)\s*:\s*", line, re.IGNORECASE):
            env_indent = indent
            continue
        if env_indent is None:
            continue
        match = re.fullmatch(
            r"(?:-\s*)?(?:[\"']?)(?P<key>LANGSMITH_TRACING(?:_V2)?|LANGCHAIN_TRACING_V2)(?:[\"']?)"
            r"\s*(?:=|:)\s*[\"']?(?i:true|1|yes|on)[\"']?\s*",
            line,
        )
        if match:
            return True
    return False


def _json_has_truthy_langsmith_env(content: str) -> bool:
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return False

    def visit(item: Any, in_env: bool = False) -> bool:
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                child_env = in_env or key_text.lower() in {
                    "env",
                    "environment",
                    "environmentvariables",
                }
                if in_env and key_text in _LANGSMITH_TRACING_KEYS:
                    if child is True or child == 1:
                        return True
                    if isinstance(child, str) and child.strip().lower() in {
                        "true",
                        "1",
                        "yes",
                        "on",
                    }:
                        return True
                if visit(child, child_env):
                    return True
        elif isinstance(item, list):
            return any(visit(child, in_env) for child in item)
        elif in_env and isinstance(item, str):
            match = re.fullmatch(
                r"(LANGSMITH_TRACING(?:_V2)?|LANGCHAIN_TRACING_V2)="
                r"(?i:true|1|yes|on)",
                item.strip(),
            )
            return match is not None
        return False

    return visit(value)


_ACTIVE_TRACE_MARKERS: dict[str, set[str]] = {
    "opentelemetry": {
        "TracerProvider",
        "get_tracer",
        "instrument",
        "set_tracer_provider",
        "start_as_current_span",
        "start_span",
    },
    "datadog": {"configure", "patch", "patch_all", "start_span", "trace", "wrap"},
    "jaeger": {"Config", "JaegerExporter", "initialize_tracer"},
}


def _trace_tool_for_module(module: str) -> str | None:
    normalized = module.lower().replace("-", "_")
    if normalized in {
        "opentelemetry.exporter.jaeger",
        "opentelemetry.exporter.jaeger.proto.grpc",
        "opentelemetry.exporter.jaeger.thrift",
    }:
        return "jaeger"
    if normalized in {"jaeger", "jaeger_client"}:
        return "jaeger"
    if normalized in {"ddtrace", "datadog"}:
        return "datadog"
    if normalized in {"opentelemetry", "opentelemetry.trace", "opentelemetry.sdk.trace"}:
        return "opentelemetry"
    if normalized.startswith("opentelemetry.instrumentation."):
        return "opentelemetry"
    return None


def _python_trace_export_is_supported(module: str, symbol: str) -> bool:
    """Require a role to come from the provider module that actually exports it."""
    normalized = module.lower().replace("-", "_")
    exports: dict[str, set[str]] = {
        "opentelemetry": {"trace"},
        "opentelemetry.trace": {
            "get_tracer",
            "set_tracer_provider",
            "start_as_current_span",
            "start_span",
        },
        "opentelemetry.sdk.trace": {"TracerProvider"},
        "ddtrace": {"patch", "patch_all", "tracer"},
        "datadog": {"configure", "tracer"},
        "jaeger": {"Config", "JaegerExporter"},
        "jaeger_client": {"Config"},
        "opentelemetry.exporter.jaeger": {"JaegerExporter"},
        "opentelemetry.exporter.jaeger.proto.grpc": {"JaegerExporter"},
        "opentelemetry.exporter.jaeger.thrift": {"JaegerExporter"},
    }
    if normalized.startswith("opentelemetry.instrumentation."):
        return symbol.endswith("Instrumentor")
    return symbol in exports.get(normalized, set())


def _python_active_tracing_tools(content: str) -> set[str]:
    visitor = _python_instrumentation_visitor(content)
    return visitor.active_tracing_tools if visitor else set()


def _javascript_active_tracing_tools(content: str) -> set[str]:
    comments_free, executable = _mask_javascript(content)
    scope_view = _javascript_restore_member_keys(comments_free, executable)
    local_roles: dict[str, tuple[str, str, str, tuple[int, int]]] = {}
    namespaces: dict[str, tuple[str, str, tuple[int, int]]] = {}

    def tool_for_module(module: str) -> str | None:
        if module == "@opentelemetry/exporter-jaeger":
            return "jaeger"
        if module in {
            "@opentelemetry/api",
            "@opentelemetry/sdk-node",
            "@opentelemetry/sdk-trace-base",
            "@opentelemetry/sdk-trace-node",
            "@opentelemetry/sdk-trace-web",
        }:
            return "opentelemetry"
        if module in {"dd-trace", "datadog"}:
            return "datadog"
        if module == "jaeger-client":
            return "jaeger"
        return None

    module_exports: dict[str, set[str]] = {
        "@opentelemetry/api": {"trace"},
        "@opentelemetry/exporter-jaeger": {"JaegerExporter"},
        "@opentelemetry/sdk-node": {"NodeSDK"},
        "@opentelemetry/sdk-trace-base": {"BasicTracerProvider"},
        "@opentelemetry/sdk-trace-node": {"NodeTracerProvider"},
        "@opentelemetry/sdk-trace-web": {"WebTracerProvider"},
        "dd-trace": {"default", "tracer"},
        "datadog": {"default", "tracer"},
        "jaeger-client": {"default", "initTracer"},
    }

    namespace_module_paths: dict[str, set[tuple[str, ...]]] = {
        "@opentelemetry/api": {
            ("trace", "getTracer"),
            ("trace", "setGlobalTracerProvider"),
        },
        "@opentelemetry/exporter-jaeger": {("JaegerExporter",)},
        "@opentelemetry/sdk-node": {("NodeSDK",)},
        "@opentelemetry/sdk-trace-base": {("BasicTracerProvider",)},
        "@opentelemetry/sdk-trace-node": {("NodeTracerProvider",)},
        "@opentelemetry/sdk-trace-web": {("WebTracerProvider",)},
        "dd-trace": {("init",), ("startSpan",), ("trace",), ("wrap",)},
        "datadog": {("init",), ("startSpan",), ("trace",), ("wrap",)},
        "jaeger-client": {("initTracer",)},
    }

    named_import_re = re.compile(
        r"\bimport\s*\{(?P<names>[^}]*)\}\s*from\s*[\"'](?P<module>[^\"']+)[\"']"
    )
    for match in named_import_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        tool = tool_for_module(match.group("module"))
        if not tool:
            continue
        for item in match.group("names").split(","):
            parts = re.split(r"\s+as\s+", item.strip())
            if parts and parts[0] and parts[0].strip() in module_exports[match.group("module")]:
                local_roles[parts[-1].strip()] = (
                    tool,
                    parts[0].strip(),
                    match.group("module"),
                    match.span(),
                )

    namespace_import_re = re.compile(
        r"\bimport\s*\*\s*as\s*(?P<alias>[A-Za-z_$][\w$]*)\s*from\s*"
        r"[\"'](?P<module>[^\"']+)[\"']"
    )
    for match in namespace_import_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        tool = tool_for_module(match.group("module"))
        if tool:
            namespaces[match.group("alias")] = (
                tool,
                match.group("module"),
                match.span(),
            )

    default_import_re = re.compile(
        r"\bimport\s+(?P<alias>[A-Za-z_$][\w$]*)\s+from\s*"
        r"[\"'](?P<module>[^\"']+)[\"']"
    )
    for match in default_import_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        tool = tool_for_module(match.group("module"))
        if tool:
            local_roles[match.group("alias")] = (
                tool,
                "default",
                match.group("module"),
                match.span(),
            )

    destructured_require_re = re.compile(
        r"\b(?:const|let|var)\s*\{(?P<names>[^}]*)\}\s*=\s*(?P<require>require)\(\s*"
        r"[\"'](?P<module>[^\"']+)[\"']\s*\)"
    )
    for match in destructured_require_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        if not _javascript_bare_require_is_live_at(scope_view, match.start("require")):
            continue
        tool = tool_for_module(match.group("module"))
        if not tool:
            continue
        for item in match.group("names").split(","):
            parts = item.strip().split(":", 1)
            if parts and parts[0] and parts[0].strip() in module_exports[match.group("module")]:
                local_roles[parts[-1].strip()] = (
                    tool,
                    parts[0].strip(),
                    match.group("module"),
                    match.span(),
                )

    module_require_re = re.compile(
        r"\b(?:const|let|var)\s+(?P<alias>[A-Za-z_$][\w$]*)\s*=\s*(?P<require>require)\(\s*"
        r"[\"'](?P<module>[^\"']+)[\"']\s*\)"
    )
    for match in module_require_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        if not _javascript_bare_require_is_live_at(scope_view, match.start("require")):
            continue
        tool = tool_for_module(match.group("module"))
        if tool:
            namespaces[match.group("alias")] = (
                tool,
                match.group("module"),
                match.span(),
            )

    direct_roles = {
        "opentelemetry": {
            "BasicTracerProvider",
            "NodeSDK",
            "NodeTracerProvider",
            "WebTracerProvider",
        },
        "datadog": set(),
        "jaeger": {"JaegerExporter", "default", "initTracer"},
    }
    member_markers = {
        "opentelemetry": {"getTracer", "register", "setGlobalTracerProvider", "startActiveSpan"},
        "datadog": {"init", "startSpan", "trace", "wrap"},
        "jaeger": {"init", "initTracer"},
    }
    active: set[str] = set()

    side_effect_esm_ddtrace_re = re.compile(r"\bimport\s*[\"']dd-trace/init(?:\.js)?[\"']\s*;?")
    if any(
        _match_is_executable(match, executable)
        for match in side_effect_esm_ddtrace_re.finditer(comments_free)
    ):
        active.add("datadog")

    side_effect_ddtrace_re = re.compile(
        r"\b(?P<require>require)\(\s*[\"']dd-trace/init(?:\.js)?[\"']\s*\)"
    )
    if any(
        _match_is_executable(match, executable)
        and _javascript_bare_require_is_live_at(scope_view, match.start("require"))
        for match in side_effect_ddtrace_re.finditer(comments_free)
    ):
        active.add("datadog")

    direct_require_re = re.compile(
        r"\b(?P<require>require)\(\s*[\"'](?P<module>[^\"']+)[\"']\s*\)"
        r"(?P<chain>(?:\s*\.\s*[A-Za-z_$][\w$]*)+)\s*\("
    )
    canonical_commonjs_chains = {
        "opentelemetry": {("trace", "getTracer")},
        "datadog": {("init",), ("startSpan",), ("trace",), ("wrap",)},
        "jaeger": {("initTracer",)},
    }
    for match in direct_require_re.finditer(comments_free):
        if not _match_is_executable(match, executable):
            continue
        if not _javascript_bare_require_is_live_at(scope_view, match.start("require")):
            continue
        tool = tool_for_module(match.group("module"))
        if not tool:
            continue
        chain = tuple(re.findall(r"[A-Za-z_$][\w$]*", match.group("chain")))
        module = match.group("module")
        if chain in canonical_commonjs_chains[tool] and chain in namespace_module_paths[module]:
            active.add(tool)

    local_member_paths: dict[str, dict[str, set[tuple[str, ...]]]] = {
        "opentelemetry": {
            "trace": {("getTracer",), ("setGlobalTracerProvider",)},
        },
        "datadog": {
            "default": {(marker,) for marker in member_markers["datadog"]},
            "tracer": {(marker,) for marker in member_markers["datadog"]},
        },
        "jaeger": {},
    }
    for alias, (tool, role, module, span) in local_roles.items():
        if role not in module_exports[module]:
            continue
        if role in direct_roles[tool] and _javascript_has_bound_call(
            scope_view, alias, declaration_span=span
        ):
            active.add(tool)
        if any(
            _javascript_has_bound_call(scope_view, alias, members, declaration_span=span)
            for members in local_member_paths[tool].get(role, set())
        ):
            active.add(tool)
    for alias, (tool, module, span) in namespaces.items():
        if any(
            _javascript_has_bound_call(scope_view, alias, members, declaration_span=span)
            for members in namespace_module_paths[module]
        ):
            active.add(tool)
    return active


def _instrumentation_evidence_for_file(path: str, content: str) -> tuple[bool, set[str]]:
    """Extract bounded instrumentation facts from one file without retaining it."""
    if _langsmith_path_is_non_runtime(path):
        return False, set()
    lower_path = path.lower()
    text = content or ""
    lower_text = text.lower()
    if not any(
        marker in lower_text
        for marker in (
            "@opentelemetry",
            "datadog",
            "dd-trace",
            "ddtrace",
            "jaeger",
            "langchain_core",
            "langchain_tracing",
            "langsmith",
            "opentelemetry",
        )
    ):
        return False, set()
    if lower_path.endswith((".py", ".pyw")):
        visitor = _python_instrumentation_visitor(text)
        return (
            bool(visitor and visitor.langsmith_active),
            set(visitor.active_tracing_tools) if visitor else set(),
        )
    if lower_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")):
        return (
            _javascript_has_active_langsmith(text)
            or _javascript_has_truthy_langsmith_env(text),
            _javascript_active_tracing_tools(text),
        )
    filename = lower_path.replace("\\", "/").rsplit("/", 1)[-1]
    if filename == ".env" or filename.startswith(".env."):
        return _dotenv_has_truthy_langsmith(text), set()
    if lower_path.endswith((".yaml", ".yml")):
        return _yaml_has_truthy_langsmith_env(text), set()
    if lower_path.endswith(".json"):
        return _json_has_truthy_langsmith_env(text), set()
    return False, set()


def _active_tracing_tools(files: Sequence[tuple[str, str]]) -> set[str]:
    active: set[str] = set()
    for path, content in files:
        active.update(_instrumentation_evidence_for_file(path, content)[1])
    return active


def _has_active_langsmith(files: Sequence[tuple[str, str]]) -> bool:
    """Return True only when source/config actively enables LangSmith tracing."""
    for path, content in files:
        if _instrumentation_evidence_for_file(path, content)[0]:
            return True
    return False


def _detect_app_instrumentation(
    nodes: Sequence[Node],
    files: Sequence[tuple[str, str]],
    dep_names: set[str],
    *,
    instrumentation_evidence: tuple[bool, set[str]] | None = None,
) -> InstrumentationDetail | None:
    """Build an app-wide InstrumentationDetail from node data and file contents."""
    tools: list[str] = []
    seen: set[str] = set()
    if instrumentation_evidence is None:
        langsmith_active = _has_active_langsmith(files)
        active_tracing_tools = _active_tracing_tools(files)
    else:
        langsmith_active, active_tracing_tools = instrumentation_evidence

    # Collect from per-node instrumentation already derived by the enricher
    for node in nodes:
        inst = node.metadata.instrumentation
        if inst:
            for t in inst.tools:
                if t == "langsmith" and not langsmith_active:
                    continue
                if t not in seen:
                    tools.append(t)
                    seen.add(t)

    # Scan dep names for additional tools not already covered
    for dep, tool in _APP_INSTRUMENTATION_IMPORTS.items():
        if dep in dep_names and tool not in seen:
            tools.append(tool)
            seen.add(tool)

    if langsmith_active and "langsmith" not in seen:
        tools.append("langsmith")
        seen.add("langsmith")

    for tool in sorted(active_tracing_tools):
        if tool not in seen:
            tools.append(tool)
            seen.add(tool)

    # Scan file contents for import patterns
    _import_re = re.compile(
        r"(?:import|from)\s+(opentelemetry|ddtrace|datadog|prometheus_client|structlog|sentry_sdk|newrelic|elastic_apm)\b"
    )
    log_level: str | None = None
    for _path, content in files:
        if not _path.endswith((".py",)):
            continue
        for m in _import_re.finditer(content):
            pkg = m.group(1)
            _tool = _APP_INSTRUMENTATION_IMPORTS.get(pkg)
            if _tool and _tool not in seen:
                tools.append(_tool)
                seen.add(_tool)
        if log_level is None:
            lm = _LOG_LEVEL_RE.search(content)
            if lm:
                log_level = lm.group(1)

    if not tools and log_level is None:
        return None
    return InstrumentationDetail(
        tools=tools,
        log_level=log_level,
        tracing_enabled=(bool(active_tracing_tools) or langsmith_active or None),
        metrics_enabled=any(t in seen for t in ("prometheus", "datadog", "elastic_apm")) or None,
    )


# ---------------------------------------------------------------------------
# App-wide testing detection
# ---------------------------------------------------------------------------

_TEST_FRAMEWORK_DEP_MAP: dict[str, str] = {
    "pytest": "pytest",
    "hypothesis": "hypothesis",
    "jest": "jest",
    "vitest": "vitest",
    "mocha": "mocha",
    "jasmine": "jasmine",
    "coverage": "coverage",
    "pytest_cov": "coverage",
}

_TEST_PATH_RE = re.compile(
    r"(?:^|/)tests?/|_test\.py$|test_[^/]+\.py$|__tests__/|\.spec\.[jt]sx?$|\.test\.[jt]sx?$",
    re.IGNORECASE,
)

_CI_PLATFORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.github/workflows/"), "github_actions"),
    (re.compile(r"\.gitlab-ci\.yml"), "gitlab_ci"),
    (re.compile(r"Jenkinsfile"), "jenkins"),
    (re.compile(r"\.circleci/config"), "circleci"),
    (re.compile(r"azure-pipelines\.yml"), "azure_pipelines"),
]

_QUALITY_GATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"codecov"), "codecov"),
    (re.compile(r"sonarqube|sonar-project"), "sonarqube"),
    (re.compile(r"snyk"), "snyk"),
    (re.compile(r"coveralls"), "coveralls"),
]


def _detect_app_testing(
    nodes: Sequence[Node],
    files: Sequence[tuple[str, str]],
    dep_names: set[str],
) -> TestingDetail | None:
    """Build an app-wide TestingDetail from nodes and file contents."""
    frameworks: list[str] = []
    seen_fw: set[str] = set()
    has_unit: bool = False
    has_integration: bool = False
    ci_cd: str | None = None
    quality_gates: list[str] = []
    seen_gates: set[str] = set()

    # Collect from per-node testing already derived by the enricher
    for node in nodes:
        tst = node.metadata.testing
        if tst:
            for fw in tst.test_frameworks:
                if fw not in seen_fw:
                    frameworks.append(fw)
                    seen_fw.add(fw)
            has_unit = has_unit or bool(tst.has_unit_tests)
            has_integration = has_integration or bool(tst.has_integration_tests)

    # Detect frameworks from dep names
    for dep, fw in _TEST_FRAMEWORK_DEP_MAP.items():
        if dep in dep_names and fw not in seen_fw:
            frameworks.append(fw)
            seen_fw.add(fw)

    # Scan file paths and contents
    for path, content in files:
        if _TEST_PATH_RE.search(path):
            has_unit = True
        if "integration" in path.lower() and _TEST_PATH_RE.search(path):
            has_integration = True
        # CI/CD platform detection
        if ci_cd is None:
            for pattern, platform in _CI_PLATFORM_PATTERNS:
                if pattern.search(path):
                    ci_cd = platform
                    # Scan workflow file for quality gate tools
                    for gate_pat, gate_name in _QUALITY_GATE_PATTERNS:
                        if gate_pat.search(content) and gate_name not in seen_gates:
                            quality_gates.append(gate_name)
                            seen_gates.add(gate_name)
                    break

    if not (has_unit or has_integration or frameworks or ci_cd):
        return None
    return TestingDetail(
        has_unit_tests=has_unit or None,
        has_integration_tests=has_integration or None,
        test_frameworks=frameworks,
        ci_cd_pipeline=ci_cd,
        quality_gates=quality_gates,
    )
