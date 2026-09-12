"""Adapters for exported low-code and no-code AI workflows.

The adapters in this module intentionally share one normalization layer.  Each
platform parser is responsible only for recognizing its native export shape and
converting it into ``_Workflow`` / ``_WorkflowNode`` records.  The common
emitter then produces NuGuard's existing component and relationship types so
policy, analysis, behavior target discovery, and red-team target discovery can
consume the result without platform-specific branches.

Supported export formats:

* n8n workflow JSON
* Langflow flow JSON
* Flowise chatflow/agentflow JSON
* Microsoft Copilot Studio ``.mcs.yml`` topics

Raw credentials are never copied into detection metadata or evidence snippets.
Credential names/IDs and the names of secret-bearing fields are retained, but
secret values are replaced before prompt or URL metadata is emitted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from nuguard.common.logging import get_logger

from ..normalization import canonicalize_text
from ..types import ComponentType
from .base import ComponentDetection, RelationshipHint

_log = get_logger(__name__)

_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"password|passwd|authorization|private[_-]?key|connection[_-]?string|secret)",
    re.IGNORECASE,
)
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"password|authorization|secret)\b(\s*[:=]\s*)([^\s,;\"']+|\"[^\"]*\"|'[^']*')"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OPENAI_KEY_RE = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b")
_URL_KEY_RE = re.compile(r"(?i)(?:key|token|secret|password|signature|sig|code)")
_HIGH_ENTROPY_PATH_SEGMENT_RE = re.compile(r"^(?:sk-[A-Za-z0-9_-]+|[A-Za-z0-9_-]{40,})$")
_PROMPT_KEY_RE = re.compile(
    r"(?:prompt|instruction|system(?:message|prompt)?|message|template|activity|"
    r"persona|goal|backstory)",
    re.IGNORECASE,
)
_MODEL_KEY_RE = re.compile(
    r"^(?:model|modelname|model_name|modelid|model_id|deployment|"
    r"deploymentname|deployment_name)$",
    re.IGNORECASE,
)
_PROVIDER_KEY_RE = re.compile(
    r"^(?:provider|vendor|service|apiProvider)$",
    re.IGNORECASE,
)
_ENDPOINT_KEY_RE = re.compile(
    r"^(?:path|webhookPath|endpoint|url|webhookUrl)$",
    re.IGNORECASE,
)
_METHOD_KEY_RE = re.compile(
    r"^(?:method|httpMethod)$",
    re.IGNORECASE,
)

_MODEL_MARKERS = (
    "openai",
    "anthropic",
    "azure openai",
    "azureopenai",
    "chat model",
    "chatmodel",
    "language model",
    "llm",
    "gemini",
    "vertex ai",
    "vertexai",
    "bedrock",
    "ollama",
    "mistral",
    "groq",
    "cohere",
    "huggingface",
)
_AGENT_MARKERS = (
    "agent",
    "assistant",
    "autonomous",
    "supervisor",
    "multiagent",
    "multi-agent",
    "planner",
)
_TOOL_MARKERS = (
    "tool",
    "function",
    "connector",
    "action",
    "http request",
    "httprequest",
    "api call",
    "invoke",
    "code",
    "python",
    "javascript",
    "shell",
    "execute command",
    "email",
    "slack",
    "teams",
    "search",
    "browser",
    "calendar",
)
_DATASTORE_MARKERS = (
    "vector store",
    "vectorstore",
    "pinecone",
    "qdrant",
    "chroma",
    "weaviate",
    "milvus",
    "postgres",
    "mysql",
    "mariadb",
    "sql server",
    "mssql",
    "mongodb",
    "mongo db",
    "redis",
    "supabase",
    "cosmos db",
    "cosmosdb",
    "dataverse",
    "sharepoint",
    "knowledge base",
    "knowledgebase",
    "memory",
)
_GUARDRAIL_MARKERS = (
    "guardrail",
    "moderation",
    "content safety",
    "contentsafety",
    "safety classifier",
    "output parser",
    "validator",
    "human approval",
    "approval",
    "human in the loop",
    "human-in-the-loop",
)
_HITL_MARKERS = (
    "human approval",
    "approval",
    "human in the loop",
    "human-in-the-loop",
    "manual review",
)
_ENDPOINT_MARKERS = (
    "webhook",
    "chat trigger",
    "chattrigger",
    "form trigger",
    "formtrigger",
    "request trigger",
    "when a request is received",
)
_WRITE_MARKERS = (
    "insert",
    "update",
    "delete",
    "upsert",
    "write",
    "create record",
    "send email",
    "post message",
    "publish",
)


@dataclass(frozen=True)
class _WorkflowNode:
    node_id: str
    name: str
    type_name: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    credentials: Any = None
    line: int = 1


@dataclass(frozen=True)
class _Workflow:
    platform: str
    name: str
    nodes: tuple[_WorkflowNode, ...]
    edges: tuple[tuple[str, str], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _try_json(content: str) -> Any:
    try:
        return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        _log.debug("workflow export JSON parse error: %s", exc)
        return None


def _try_yaml_all(content: str) -> list[Any]:
    try:
        import yaml  # type: ignore[import-untyped]

        return list(yaml.safe_load_all(content))
    except Exception as exc:  # noqa: BLE001
        _log.debug("Copilot Studio YAML parse error: %s", exc)
        return []


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    return default


def _redact_text(value: str, *, limit: int = 2000) -> str:
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    value = _OPENAI_KEY_RE.sub("[REDACTED]", value)
    value = _INLINE_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )
    if len(value) > limit:
        return value[:limit] + "..."
    return value


def _safe_url(value: str) -> str:
    value = _redact_text(value, limit=2048)
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    safe_query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        safe_query.append(
            (
                key,
                "[REDACTED]" if _URL_KEY_RE.search(key) else item,
            )
        )

    safe_path = "/".join(
        "[REDACTED]" if _HIGH_ENTROPY_PATH_SEGMENT_RE.fullmatch(part) else part
        for part in parsed.path.split("/")
    )

    netloc = parsed.netloc
    if parsed.hostname is not None:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port is not None else host

    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            safe_path,
            urlencode(safe_query),
            "",
        )
    )


def _find_line(content: str, *needles: str) -> int:
    usable = [needle for needle in needles if needle]
    if not usable:
        return 1

    lines = content.splitlines()
    for needle in usable:
        quoted = json.dumps(needle)
        for index, line in enumerate(lines, start=1):
            if quoted in line or needle in line:
                return index

    return 1


def _mapping(value: Any) -> dict[str, Any]:
    """Return a string-keyed copy of a mapping or an empty mapping."""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _walk(
    value: Any,
    *,
    max_depth: int = 12,
) -> Iterable[tuple[str, Any]]:
    stack: list[tuple[str, Any, int]] = [("", value, 0)]

    while stack:
        key, item, depth = stack.pop()

        if depth > max_depth:
            continue

        yield key, item

        if isinstance(item, Mapping):
            children = list(item.items())

            for child_key, child in reversed(children):
                stack.append(
                    (
                        str(child_key),
                        child,
                        depth + 1,
                    )
                )

        elif isinstance(item, Sequence) and not isinstance(
            item,
            (str, bytes, bytearray),
        ):
            for child in reversed(item):
                stack.append(
                    (
                        key,
                        child,
                        depth + 1,
                    )
                )


def _secret_keys(value: Any) -> list[str]:
    found: set[str] = set()

    for key, _item in _walk(value):
        if key and _SECRET_KEY_RE.search(key):
            found.add(key)

    return sorted(
        found,
        key=str.casefold,
    )


def _credential_references(
    value: Any,
) -> list[dict[str, str]]:
    """Return credential identifiers without retaining secret values."""
    references: list[dict[str, str]] = []

    if not value:
        return references

    items: list[tuple[str, Any]]

    if isinstance(value, Mapping):
        items = [(str(key), item) for key, item in value.items()]

    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        items = [("credential", item) for item in value]

    else:
        items = [("credential", value)]

    for credential_type, item in items:
        reference: dict[str, str] = {
            "type": _redact_text(
                credential_type,
                limit=120,
            )
        }

        item_mapping = _mapping(item)

        if item_mapping:
            for key in (
                "name",
                "id",
                "credentialName",
                "credentialId",
                "connectionName",
                "connectionId",
                "type",
            ):
                candidate = item_mapping.get(key)
                text = _text(candidate)

                if text and not _SECRET_KEY_RE.search(key):
                    reference[key] = _redact_text(
                        text,
                        limit=160,
                    )

        elif isinstance(item, (str, int)) and not _SECRET_KEY_RE.search(credential_type):
            reference["name"] = _redact_text(
                str(item),
                limit=160,
            )

        references.append(reference)

    unique: dict[
        tuple[tuple[str, str], ...],
        dict[str, str],
    ] = {}

    for reference in references:
        unique[tuple(sorted(reference.items()))] = reference

    return list(unique.values())


def _values_for_key(
    value: Any,
    pattern: re.Pattern[str],
) -> list[str]:
    values: list[str] = []

    for key, item in _walk(value):
        if not key or not pattern.search(key):
            continue

        if isinstance(
            item,
            (str, int, float),
        ):
            text = _text(item)

            if text:
                values.append(text)

        elif isinstance(item, Mapping):
            item_mapping = _mapping(item)

            for candidate_key in (
                "value",
                "default",
                "text",
                "content",
            ):
                candidate = item_mapping.get(candidate_key)
                text = _text(candidate)

                if text:
                    values.append(text)

    return values


def _prompt_values(
    value: Any,
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for key, item in _walk(value):
        if not key or not _PROMPT_KEY_RE.search(key):
            continue

        candidates: list[str] = []

        if isinstance(item, str):
            candidates.append(item)

        elif isinstance(item, Mapping):
            item_mapping = _mapping(item)

            for candidate_key in (
                "value",
                "default",
                "text",
                "content",
                "template",
            ):
                candidate = item_mapping.get(candidate_key)

                if isinstance(candidate, str):
                    candidates.append(candidate)

        for candidate in candidates:
            candidate = _redact_text(candidate.strip())

            if len(candidate) < 8:
                continue

            token = (
                key.casefold(),
                candidate,
            )

            if token in seen:
                continue

            seen.add(token)
            found.append(
                (
                    key,
                    candidate,
                )
            )

    return found


def _first(
    values: Iterable[str],
    default: str = "",
) -> str:
    for value in values:
        if value:
            return value

    return default


def _contains_any(
    haystack: str,
    needles: Sequence[str],
) -> bool:
    return any(needle in haystack for needle in needles)


def _node_text(
    node: _WorkflowNode,
) -> str:
    bits = [
        node.name,
        node.type_name,
    ]

    for key, value in _walk(
        node.parameters,
        max_depth=5,
    ):
        if (
            key
            and not _SECRET_KEY_RE.search(key)
            and isinstance(
                value,
                (str, int, float, bool),
            )
        ):
            bits.append(str(value))

    return " ".join(bits).casefold()


def _canonical(
    platform: str,
    kind: str,
    workflow: str,
    item: str = "",
) -> str:
    parts = [
        platform,
        kind,
        workflow,
    ]

    if item:
        parts.append(item)

    return canonicalize_text(":".join(parts))


def _relationship(
    source: tuple[str, ComponentType],
    target: tuple[str, ComponentType],
    relationship_type: str,
    *,
    access_type: str | None = None,
) -> RelationshipHint:
    return RelationshipHint(
        source_canonical=source[0],
        source_type=source[1],
        target_canonical=target[0],
        target_type=target[1],
        relationship_type=relationship_type,
        access_type=access_type,
    )


def _detection(
    *,
    component_type: ComponentType,
    canonical: str,
    display: str,
    adapter_name: str,
    priority: int,
    confidence: float,
    metadata: Mapping[str, Any],
    file_path: str,
    line: int,
    snippet: str,
    relationships: (list[RelationshipHint] | None) = None,
) -> ComponentDetection:
    return ComponentDetection(
        component_type=component_type,
        canonical_name=canonical,
        display_name=display,
        adapter_name=adapter_name,
        priority=priority,
        confidence=confidence,
        metadata=dict(metadata),
        file_path=file_path,
        line=max(1, line),
        snippet=_redact_text(
            snippet,
            limit=180,
        ),
        evidence_kind="workflow_export",
        relationships=relationships or [],
    )


def _provider_from_text(
    text: str,
) -> str:
    providers = (
        "openai",
        "anthropic",
        "azure openai",
        "gemini",
        "vertex ai",
        "bedrock",
        "ollama",
        "mistral",
        "groq",
        "cohere",
        "huggingface",
    )

    for provider in providers:
        if provider in text:
            return provider

    return ""


def _datastore_type(
    text: str,
) -> str:
    if any(
        marker in text
        for marker in (
            "vector",
            "pinecone",
            "qdrant",
            "chroma",
            "weaviate",
            "milvus",
        )
    ):
        return "vector"

    if any(
        marker in text
        for marker in (
            "redis",
            "memory",
        )
    ):
        return "kv"

    if any(
        marker in text
        for marker in (
            "knowledge",
            "sharepoint",
        )
    ):
        return "knowledge_base"

    return "relational"


def _privilege_scope(
    text: str,
) -> list[str]:
    scopes: list[str] = []

    if any(
        marker in text
        for marker in (
            "code",
            "python",
            "javascript",
            "shell",
            "execute command",
        )
    ):
        scopes.append("code_execution")

    if any(
        marker in text
        for marker in (
            "email",
            "outlook",
            "gmail",
        )
    ):
        scopes.append("email_out")

    if any(
        marker in text
        for marker in (
            "twitter",
            "x.com",
            "facebook",
            "linkedin",
            "social",
        )
    ):
        scopes.append("social_media_out")

    if any(
        marker in text
        for marker in (
            "write",
            "insert",
            "update",
            "delete",
            "upsert",
            "create record",
        )
    ):
        scopes.append("db_write")

    if any(
        marker in text
        for marker in (
            "file",
            "filesystem",
            "dropbox",
            "onedrive",
            "sharepoint",
        )
    ):
        scopes.append("filesystem_write")

    if any(
        marker in text
        for marker in (
            "http",
            "api call",
            "connector",
            "webhook",
            "browser",
        )
    ):
        scopes.append("network_out")

    return sorted(set(scopes))


def _concrete_endpoint(
    value: str,
) -> str:
    """Normalize only concrete paths and URLs."""
    value = value.strip()

    if not value or "{{" in value or "${" in value or "$(" in value:
        return ""

    safe = _safe_url(value)

    if safe.startswith(("http://", "https://", "/")):
        return safe

    if " " not in safe and "\n" not in safe:
        return f"/{safe.lstrip('/')}"

    return ""


@dataclass
class _EmissionState:
    detections: list[ComponentDetection] = field(default_factory=list)
    classified: dict[
        str,
        tuple[str, ComponentType],
    ] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    models: list[tuple[str, ComponentType]] = field(default_factory=list)
    tools: list[tuple[str, ComponentType]] = field(default_factory=list)
    datastores: list[tuple[str, ComponentType]] = field(default_factory=list)
    endpoints: list[tuple[str, ComponentType]] = field(default_factory=list)
    guardrails: list[tuple[str, ComponentType]] = field(default_factory=list)
    prompts: list[tuple[str, ComponentType]] = field(default_factory=list)
    auth_by_node: dict[
        str,
        tuple[str, ComponentType],
    ] = field(default_factory=dict)
    access_by_node: dict[str, str] = field(default_factory=dict)
    text_by_node: dict[str, str] = field(default_factory=dict)
    has_hitl: bool = False


def _classify_workflow_nodes(
    workflow: _Workflow,
    *,
    file_path: str,
    adapter_name: str,
    priority: int,
) -> _EmissionState:
    platform = workflow.platform
    workflow_name = _redact_text(
        workflow.name or Path(file_path).stem,
        limit=240,
    )
    state = _EmissionState()

    for node in workflow.nodes:
        state.aliases[node.node_id] = node.node_id
        state.aliases[node.name] = node.node_id

        text = _node_text(node)
        state.text_by_node[node.node_id] = text

        item_name = _redact_text(
            node.name or node.node_id or node.type_name,
            limit=240,
        )

        base_metadata: dict[str, Any] = {
            "platform": platform,
            "workflow": workflow_name,
            "platform_node_id": _redact_text(
                node.node_id,
                limit=160,
            ),
            "platform_node_type": (
                _redact_text(
                    node.type_name,
                    limit=200,
                )
            ),
        }

        secret_keys = _secret_keys(
            {
                "parameters": node.parameters,
                "credentials": node.credentials,
            }
        )
        credential_refs = _credential_references(node.credentials)

        if secret_keys:
            base_metadata["secret_keys_present"] = secret_keys

        node_ref: tuple[str, ComponentType] | None = None

        is_endpoint_node = _contains_any(
            text,
            _ENDPOINT_MARKERS,
        )

        if is_endpoint_node:
            raw_endpoint = _first(
                _values_for_key(
                    node.parameters,
                    _ENDPOINT_KEY_RE,
                )
            )
            endpoint = _concrete_endpoint(raw_endpoint)

            if endpoint:
                method = _first(
                    _values_for_key(
                        node.parameters,
                        _METHOD_KEY_RE,
                    ),
                    "POST",
                ).upper()

                canonical = _canonical(
                    platform,
                    "endpoint",
                    workflow_name,
                    item_name,
                )
                node_ref = (
                    canonical,
                    ComponentType.API_ENDPOINT,
                )
                state.endpoints.append(node_ref)

                state.detections.append(
                    _detection(
                        component_type=(ComponentType.API_ENDPOINT),
                        canonical=canonical,
                        display=(f"{method} {endpoint}"),
                        adapter_name=adapter_name,
                        priority=priority,
                        confidence=0.94,
                        metadata={
                            **base_metadata,
                            "endpoint": endpoint,
                            "method": method,
                            "endpoint_kind": (node.type_name),
                            "transport": "http",
                            "accepts_user_input": True,
                            "auth_required": bool(credential_refs),
                        },
                        file_path=file_path,
                        line=node.line,
                        snippet=(f"{node.type_name}: {item_name}"),
                    )
                )

        elif _contains_any(
            text,
            _GUARDRAIL_MARKERS,
        ):
            canonical = _canonical(
                platform,
                "guardrail",
                workflow_name,
                item_name,
            )
            node_ref = (
                canonical,
                ComponentType.GUARDRAIL,
            )
            state.guardrails.append(node_ref)

            hitl = _contains_any(
                text,
                _HITL_MARKERS,
            )
            state.has_hitl = state.has_hitl or hitl

            state.detections.append(
                _detection(
                    component_type=(ComponentType.GUARDRAIL),
                    canonical=canonical,
                    display=item_name,
                    adapter_name=adapter_name,
                    priority=priority,
                    confidence=0.90,
                    metadata={
                        **base_metadata,
                        "guardrail_type": (node.type_name),
                        "human_approval": hitl,
                    },
                    file_path=file_path,
                    line=node.line,
                    snippet=(f"{node.type_name}: {item_name}"),
                )
            )

        elif _contains_any(
            text,
            _DATASTORE_MARKERS,
        ):
            canonical = _canonical(
                platform,
                "datastore",
                workflow_name,
                item_name,
            )
            node_ref = (
                canonical,
                ComponentType.DATASTORE,
            )
            state.datastores.append(node_ref)

            access_type = (
                "readwrite"
                if _contains_any(
                    text,
                    _WRITE_MARKERS,
                )
                else "read"
            )
            state.access_by_node[node.node_id] = access_type

            state.detections.append(
                _detection(
                    component_type=(ComponentType.DATASTORE),
                    canonical=canonical,
                    display=item_name,
                    adapter_name=adapter_name,
                    priority=priority,
                    confidence=0.91,
                    metadata={
                        **base_metadata,
                        "datastore_type": (_datastore_type(text)),
                        "access_type": (access_type),
                    },
                    file_path=file_path,
                    line=node.line,
                    snippet=(f"{node.type_name}: {item_name}"),
                )
            )

        elif _contains_any(
            text,
            _MODEL_MARKERS,
        ) or bool(
            _values_for_key(
                node.parameters,
                _MODEL_KEY_RE,
            )
        ):
            model_name = _first(
                _values_for_key(
                    node.parameters,
                    _MODEL_KEY_RE,
                ),
                item_name,
            )
            model_name = _redact_text(
                model_name,
                limit=240,
            )

            provider = _first(
                _values_for_key(
                    node.parameters,
                    _PROVIDER_KEY_RE,
                ),
                _provider_from_text(text),
            )
            provider = _redact_text(
                provider,
                limit=120,
            )

            canonical = _canonical(
                platform,
                "model",
                workflow_name,
                f"{item_name}:{model_name}",
            )
            node_ref = (
                canonical,
                ComponentType.MODEL,
            )
            state.models.append(node_ref)

            state.detections.append(
                _detection(
                    component_type=(ComponentType.MODEL),
                    canonical=canonical,
                    display=model_name,
                    adapter_name=adapter_name,
                    priority=priority,
                    confidence=0.92,
                    metadata={
                        **base_metadata,
                        "model": model_name,
                        "model_name": model_name,
                        "provider": provider,
                    },
                    file_path=file_path,
                    line=node.line,
                    snippet=(f"{node.type_name}: {item_name}"),
                )
            )

        elif _contains_any(
            text,
            _AGENT_MARKERS,
        ):
            canonical = _canonical(
                platform,
                "agent",
                workflow_name,
                item_name,
            )
            node_ref = (
                canonical,
                ComponentType.AGENT,
            )

            state.detections.append(
                _detection(
                    component_type=(ComponentType.AGENT),
                    canonical=canonical,
                    display=item_name,
                    adapter_name=adapter_name,
                    priority=priority,
                    confidence=0.88,
                    metadata={
                        **base_metadata,
                        "agent_kind": (node.type_name),
                    },
                    file_path=file_path,
                    line=node.line,
                    snippet=(f"{node.type_name}: {item_name}"),
                )
            )

        elif _contains_any(
            text,
            _TOOL_MARKERS,
        ):
            canonical = _canonical(
                platform,
                "tool",
                workflow_name,
                item_name,
            )
            node_ref = (
                canonical,
                ComponentType.TOOL,
            )
            state.tools.append(node_ref)

            scopes = _privilege_scope(text)
            metadata: dict[str, Any] = {
                **base_metadata,
                "tool_type": node.type_name,
                "side_effecting": bool(
                    scopes
                    or _contains_any(
                        text,
                        _WRITE_MARKERS,
                    )
                ),
            }

            if scopes:
                metadata["privilege_scope"] = scopes[0]
                metadata["privilege_scopes"] = scopes
                metadata["high_privilege"] = True

            outbound = _first(
                _values_for_key(
                    node.parameters,
                    _ENDPOINT_KEY_RE,
                )
            )

            if outbound:
                metadata["outbound_endpoint"] = _safe_url(outbound)
                metadata["accepts_external_url"] = (
                    "{{" in outbound or "${" in outbound or "$(" in outbound
                )
                metadata["ssrf_possible"] = bool(metadata["accepts_external_url"])

            state.detections.append(
                _detection(
                    component_type=(ComponentType.TOOL),
                    canonical=canonical,
                    display=item_name,
                    adapter_name=adapter_name,
                    priority=priority,
                    confidence=0.88,
                    metadata=metadata,
                    file_path=file_path,
                    line=node.line,
                    snippet=(f"{node.type_name}: {item_name}"),
                )
            )

        if node_ref is not None:
            state.classified[node.node_id] = node_ref

        prompt_values = _prompt_values(node.parameters)

        for prompt_index, (
            role,
            prompt,
        ) in enumerate(
            prompt_values,
            start=1,
        ):
            prompt_canonical = _canonical(
                platform,
                "prompt",
                workflow_name,
                (f"{item_name}:{role}:{prompt_index}"),
            )
            prompt_ref = (
                prompt_canonical,
                ComponentType.PROMPT,
            )
            state.prompts.append(prompt_ref)

            state.detections.append(
                _detection(
                    component_type=(ComponentType.PROMPT),
                    canonical=prompt_canonical,
                    display=(f"{item_name} {role}"),
                    adapter_name=adapter_name,
                    priority=priority,
                    confidence=0.87,
                    metadata={
                        **base_metadata,
                        "role": role,
                        "content": prompt,
                        "char_count": len(prompt),
                        "is_template": ("{{" in prompt or "${" in prompt or "$(" in prompt),
                    },
                    file_path=file_path,
                    line=node.line,
                    snippet=(f"{role}: {prompt[:100]}"),
                )
            )

        if credential_refs or secret_keys:
            auth_canonical = _canonical(
                platform,
                "auth",
                workflow_name,
                item_name,
            )
            auth_ref = (
                auth_canonical,
                ComponentType.AUTH,
            )
            state.auth_by_node[node.node_id] = auth_ref

            auth_type = (
                credential_refs[0].get(
                    "type",
                    "credential_reference",
                )
                if credential_refs
                else ("inline_secret_reference")
            )

            state.detections.append(
                _detection(
                    component_type=(ComponentType.AUTH),
                    canonical=auth_canonical,
                    display=(f"{item_name} credentials"),
                    adapter_name=adapter_name,
                    priority=priority,
                    confidence=(0.86 if credential_refs else 0.70),
                    metadata={
                        **base_metadata,
                        "auth_type": auth_type,
                        "credential_references": (credential_refs),
                        "secret_keys_present": (secret_keys),
                        "secret_values_retained": (False),
                    },
                    file_path=file_path,
                    line=node.line,
                    snippet=(f"credential reference for {item_name}"),
                )
            )

    return state


def _workflow_relationships(
    workflow: _Workflow,
    *,
    state: _EmissionState,
    agent_ref: tuple[str, ComponentType],
) -> list[RelationshipHint]:
    relationships: list[RelationshipHint] = []

    for target in state.models:
        relationships.append(
            _relationship(
                agent_ref,
                target,
                "USES",
            )
        )

    for target in state.prompts:
        relationships.append(
            _relationship(
                agent_ref,
                target,
                "USES",
            )
        )

    for target in state.tools:
        relationships.append(
            _relationship(
                agent_ref,
                target,
                "CALLS",
            )
        )

    datastore_ids = {
        reference: node_id
        for node_id, reference in state.classified.items()
        if reference[1] == ComponentType.DATASTORE
    }

    for target in state.datastores:
        node_id = datastore_ids.get(
            target,
            "",
        )
        relationships.append(
            _relationship(
                agent_ref,
                target,
                "ACCESSES",
                access_type=(
                    state.access_by_node.get(
                        node_id,
                        "read",
                    )
                ),
            )
        )

    for endpoint in state.endpoints:
        relationships.append(
            _relationship(
                endpoint,
                agent_ref,
                "CALLS",
            )
        )

    for node_id, auth_ref in state.auth_by_node.items():
        protected_target = state.classified.get(node_id)

        if protected_target is not None:
            relationships.append(
                _relationship(
                    auth_ref,
                    protected_target,
                    "PROTECTS",
                )
            )

    for guardrail in state.guardrails:
        relationships.append(
            _relationship(
                guardrail,
                agent_ref,
                "PROTECTS",
            )
        )

        for target in [
            *state.models,
            *state.endpoints,
        ]:
            relationships.append(
                _relationship(
                    guardrail,
                    target,
                    "PROTECTS",
                )
            )

    for raw_source, raw_target in workflow.edges:
        source_id = state.aliases.get(
            raw_source,
            raw_source,
        )
        target_id = state.aliases.get(
            raw_target,
            raw_target,
        )

        edge_source = state.classified.get(source_id)
        edge_target = state.classified.get(target_id)

        if edge_source is None or edge_target is None:
            continue

        if edge_source[1] == ComponentType.TOOL and edge_target[1] == ComponentType.DATASTORE:
            edge_text = " ".join(
                (
                    state.text_by_node.get(
                        source_id,
                        "",
                    ),
                    state.text_by_node.get(
                        target_id,
                        "",
                    ),
                )
            )
            access_type = (
                "readwrite"
                if _contains_any(
                    edge_text,
                    _WRITE_MARKERS,
                )
                else "read"
            )
            relationships.append(
                _relationship(
                    edge_source,
                    edge_target,
                    "ACCESSES",
                    access_type=access_type,
                )
            )

        elif edge_source[1] == ComponentType.AGENT and edge_target[1] == ComponentType.MODEL:
            relationships.append(
                _relationship(
                    edge_source,
                    edge_target,
                    "USES",
                )
            )

        elif edge_source[1] == ComponentType.AGENT and edge_target[1] == ComponentType.TOOL:
            relationships.append(
                _relationship(
                    edge_source,
                    edge_target,
                    "CALLS",
                )
            )

        elif edge_source[1] == ComponentType.API_ENDPOINT and edge_target[1] == ComponentType.AGENT:
            relationships.append(
                _relationship(
                    edge_source,
                    edge_target,
                    "CALLS",
                )
            )

        elif edge_source[1] == ComponentType.GUARDRAIL:
            relationships.append(
                _relationship(
                    edge_source,
                    edge_target,
                    "PROTECTS",
                )
            )

    deduplicated: list[RelationshipHint] = []
    seen: set[
        tuple[
            str,
            str,
            str,
            str | None,
        ]
    ] = set()

    for relationship in relationships:
        key = (
            relationship.source_canonical,
            relationship.target_canonical,
            relationship.relationship_type,
            relationship.access_type,
        )

        if key in seen:
            continue

        seen.add(key)
        deduplicated.append(relationship)

    return deduplicated


def _emit_workflow(
    workflow: _Workflow,
    *,
    content: str,
    file_path: str,
    adapter_name: str,
    priority: int,
) -> list[ComponentDetection]:
    platform = workflow.platform
    workflow_name = _redact_text(
        workflow.name or Path(file_path).stem,
        limit=240,
    )

    framework_canonical = _canonical(
        platform,
        "framework",
        platform,
    )
    agent_canonical = _canonical(
        platform,
        "workflow",
        workflow_name,
    )
    agent_ref = (
        agent_canonical,
        ComponentType.AGENT,
    )

    state = _classify_workflow_nodes(
        workflow,
        file_path=file_path,
        adapter_name=adapter_name,
        priority=priority,
    )

    relationships = _workflow_relationships(
        workflow,
        state=state,
        agent_ref=agent_ref,
    )

    workflow_line = _find_line(
        content,
        workflow_name,
    )

    framework = _detection(
        component_type=(ComponentType.FRAMEWORK),
        canonical=framework_canonical,
        display=platform,
        adapter_name=adapter_name,
        priority=priority,
        confidence=0.98,
        metadata={
            "framework": platform,
            "platform": platform,
            "export_format": True,
        },
        file_path=file_path,
        line=workflow_line,
        snippet=(f"{platform} workflow export"),
    )

    agent = _detection(
        component_type=ComponentType.AGENT,
        canonical=agent_canonical,
        display=workflow_name,
        adapter_name=adapter_name,
        priority=priority,
        confidence=0.95,
        metadata={
            "platform": platform,
            "workflow": workflow_name,
            "export_format": True,
            "human_approval": (state.has_hitl),
            "hitl": state.has_hitl,
            "live_target_required": True,
            "source_node_count": len(workflow.nodes),
        },
        file_path=file_path,
        line=workflow_line,
        snippet=(f"{platform} workflow: {workflow_name}"),
        relationships=relationships,
    )

    return [
        framework,
        agent,
        *state.detections,
    ]


class N8nWorkflowAdapter:
    """Normalize native n8n workflow JSON exports."""

    name = "n8n_workflow_export"
    priority = 26

    def scan(
        self,
        content: str,
        rel_path: str,
    ) -> list[ComponentDetection]:
        data = _try_json(content)

        documents = data if isinstance(data, list) else [data]

        detections: list[ComponentDetection] = []

        for document in documents:
            workflow = self._parse(
                document,
                content,
                rel_path,
            )

            if workflow is None:
                continue

            detections.extend(
                _emit_workflow(
                    workflow,
                    content=content,
                    file_path=rel_path,
                    adapter_name=self.name,
                    priority=self.priority,
                )
            )

        return detections

    @staticmethod
    def _parse(
        data: Any,
        content: str,
        rel_path: str,
    ) -> _Workflow | None:
        data_mapping = _mapping(data)

        if not data_mapping:
            return None

        nodes_value = data_mapping.get("nodes")
        connections_value = data_mapping.get("connections")

        if not isinstance(nodes_value, list) or not isinstance(
            connections_value,
            Mapping,
        ):
            return None

        has_native_node = False

        for raw_node in nodes_value:
            node_mapping = _mapping(raw_node)
            node_type = _text(node_mapping.get("type"))

            if node_type.startswith(
                (
                    "n8n-nodes-",
                    "@n8n/",
                )
            ):
                has_native_node = True
                break

        if not has_native_node:
            return None

        nodes: list[_WorkflowNode] = []

        for index, raw_node in enumerate(
            nodes_value,
            start=1,
        ):
            node_mapping = _mapping(raw_node)

            if not node_mapping:
                continue

            node_id = _text(
                node_mapping.get("id"),
                f"node-{index}",
            )
            name = _text(
                node_mapping.get("name"),
                node_id,
            )
            type_name = _text(
                node_mapping.get("type"),
                "n8n-node",
            )
            parameters = _mapping(node_mapping.get("parameters"))

            nodes.append(
                _WorkflowNode(
                    node_id=node_id,
                    name=name,
                    type_name=type_name,
                    parameters=parameters,
                    credentials=(node_mapping.get("credentials")),
                    line=_find_line(
                        content,
                        name,
                        node_id,
                    ),
                )
            )

        edges: list[tuple[str, str]] = []

        connections = _mapping(connections_value)

        for source, raw_groups in connections.items():
            groups = _mapping(raw_groups)

            for raw_branches in groups.values():
                if not isinstance(
                    raw_branches,
                    list,
                ):
                    continue

                for raw_branch in raw_branches:
                    if not isinstance(
                        raw_branch,
                        list,
                    ):
                        continue

                    for raw_link in raw_branch:
                        link = _mapping(raw_link)
                        target = _text(link.get("node"))

                        if target:
                            edges.append(
                                (
                                    source,
                                    target,
                                )
                            )

        return _Workflow(
            platform="n8n",
            name=_text(
                data_mapping.get("name"),
                Path(rel_path).stem,
            ),
            nodes=tuple(nodes),
            edges=tuple(edges),
            metadata={
                "active": bool(
                    data_mapping.get(
                        "active",
                        False,
                    )
                )
            },
        )


class LangflowWorkflowAdapter:
    """Normalize Langflow flow JSON exports."""

    name = "langflow_workflow_export"
    priority = 26

    def scan(
        self,
        content: str,
        rel_path: str,
    ) -> list[ComponentDetection]:
        parsed = _try_json(content)
        data = _mapping(parsed)

        if not data:
            return []

        data_value = data.get("data")
        graph = (
            _mapping(data_value)
            if isinstance(
                data_value,
                Mapping,
            )
            else data
        )

        nodes_value = graph.get("nodes")
        edges_value = graph.get("edges")

        if not isinstance(nodes_value, list) or not isinstance(
            edges_value,
            list,
        ):
            return []

        has_langflow_node = False

        for raw_node in nodes_value:
            node = _mapping(raw_node)
            node_data = _mapping(node.get("data"))

            if not node_data:
                continue

            inner = _mapping(node_data.get("node"))
            template = _mapping(node_data.get("template"))

            if inner:
                has_langflow_node = True
                break

            if template and any(
                key in node_data
                for key in (
                    "type",
                    "display_name",
                    "base_classes",
                )
            ):
                has_langflow_node = True
                break

        if not has_langflow_node:
            return []

        nodes: list[_WorkflowNode] = []

        for index, raw_node in enumerate(
            nodes_value,
            start=1,
        ):
            node = _mapping(raw_node)

            if not node:
                continue

            node_data = _mapping(node.get("data"))
            inner = _mapping(node_data.get("node"))

            node_id = _text(
                node.get("id"),
                f"node-{index}",
            )
            name = _first(
                (
                    _text(node_data.get("display_name")),
                    _text(node_data.get("label")),
                    _text(inner.get("display_name")),
                    _text(inner.get("name")),
                    node_id,
                )
            )
            type_name = _first(
                (
                    _text(node_data.get("type")),
                    _text(inner.get("type")),
                    _text(node.get("type")),
                    name,
                )
            )

            parameters: dict[str, Any] = dict(node_data)

            if inner:
                parameters["node"] = inner

            nodes.append(
                _WorkflowNode(
                    node_id=node_id,
                    name=name,
                    type_name=type_name,
                    parameters=parameters,
                    credentials=(node_data.get("credentials")),
                    line=_find_line(
                        content,
                        node_id,
                        name,
                    ),
                )
            )

        edges: list[tuple[str, str]] = []

        for raw_edge in edges_value:
            edge = _mapping(raw_edge)
            source = _text(edge.get("source"))
            target = _text(edge.get("target"))

            if source and target:
                edges.append(
                    (
                        source,
                        target,
                    )
                )

        workflow = _Workflow(
            platform="langflow",
            name=_first(
                (
                    _text(data.get("name")),
                    _text(data.get("display_name")),
                    Path(rel_path).stem,
                )
            ),
            nodes=tuple(nodes),
            edges=tuple(edges),
        )

        return _emit_workflow(
            workflow,
            content=content,
            file_path=rel_path,
            adapter_name=self.name,
            priority=self.priority,
        )


class FlowiseWorkflowAdapter:
    """Normalize Flowise chatflow and agentflow exports."""

    name = "flowise_workflow_export"
    priority = 26

    def scan(
        self,
        content: str,
        rel_path: str,
    ) -> list[ComponentDetection]:
        parsed = _try_json(content)
        data = _mapping(parsed)

        if not data:
            return []

        had_flow_data = "flowData" in data
        graph_value: Any = data.get(
            "flowData",
            data,
        )

        if isinstance(graph_value, str):
            graph_value = _try_json(graph_value)

        graph = _mapping(graph_value)

        if not graph:
            return []

        nodes_value = graph.get("nodes")
        edges_value = graph.get("edges")

        if not isinstance(nodes_value, list) or not isinstance(
            edges_value,
            list,
        ):
            return []

        has_flowise_node = False

        for raw_node in nodes_value:
            node = _mapping(raw_node)
            node_data = _mapping(node.get("data"))

            if (
                "inputAnchors" in node_data
                and "outputAnchors" in node_data
                and any(
                    key in node_data
                    for key in (
                        "name",
                        "label",
                        "type",
                    )
                )
            ):
                has_flowise_node = True
                break

        if not had_flow_data and not has_flowise_node:
            return []

        nodes: list[_WorkflowNode] = []

        for index, raw_node in enumerate(
            nodes_value,
            start=1,
        ):
            node = _mapping(raw_node)

            if not node:
                continue

            node_data = _mapping(node.get("data"))
            inputs = _mapping(node_data.get("inputs"))

            node_id = _text(
                node.get("id"),
                f"node-{index}",
            )
            name = _first(
                (
                    _text(node_data.get("label")),
                    _text(node_data.get("name")),
                    node_id,
                )
            )
            type_name = _first(
                (
                    _text(node_data.get("name")),
                    _text(node_data.get("type")),
                    _text(node.get("type")),
                    name,
                )
            )

            parameters: dict[str, Any] = dict(node_data)

            if inputs:
                parameters["inputs"] = inputs

            credentials = (
                node_data.get("credentials")
                or inputs.get("credential")
                or inputs.get("credentials")
            )

            nodes.append(
                _WorkflowNode(
                    node_id=node_id,
                    name=name,
                    type_name=type_name,
                    parameters=parameters,
                    credentials=credentials,
                    line=_find_line(
                        content,
                        node_id,
                        name,
                    ),
                )
            )

        edges: list[tuple[str, str]] = []

        for raw_edge in edges_value:
            edge = _mapping(raw_edge)
            source = _text(edge.get("source"))
            target = _text(edge.get("target"))

            if source and target:
                edges.append(
                    (
                        source,
                        target,
                    )
                )

        workflow = _Workflow(
            platform="flowise",
            name=_first(
                (
                    _text(data.get("name")),
                    _text(data.get("chatflowName")),
                    Path(rel_path).stem,
                )
            ),
            nodes=tuple(nodes),
            edges=tuple(edges),
        )

        return _emit_workflow(
            workflow,
            content=content,
            file_path=rel_path,
            adapter_name=self.name,
            priority=self.priority,
        )


_COPILOT_CHILD_KEYS = (
    "actions",
    "elseActions",
    "defaultActions",
    "steps",
    "cases",
    "conditionItems",
)


def _copilot_actions(
    value: Any,
) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        value_mapping = _mapping(value)

        if "kind" in value_mapping or "$kind" in value_mapping:
            yield value_mapping

        for key, child in value_mapping.items():
            if key in _COPILOT_CHILD_KEYS or key in (
                "beginDialog",
                "dialog",
            ):
                yield from _copilot_actions(child)

    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for item in value:
            yield from _copilot_actions(item)


class CopilotStudioYAMLAdapter:
    """Normalize Copilot Studio AdaptiveDialog topic YAML."""

    name = "copilot_studio_export"
    priority = 26

    def scan(
        self,
        content: str,
        rel_path: str,
    ) -> list[ComponentDetection]:
        documents = _try_yaml_all(content)
        detections: list[ComponentDetection] = []

        lower_path = rel_path.casefold()
        strict_path = lower_path.endswith(
            (
                ".mcs.yml",
                ".mcs.yaml",
            )
        )

        for raw_document in documents:
            document = _mapping(raw_document)

            if not document:
                continue

            kind = _text(document.get("kind"))

            if kind != "AdaptiveDialog" and not strict_path:
                continue

            if kind != "AdaptiveDialog" or "beginDialog" not in document:
                continue

            actions = list(_copilot_actions(document.get("beginDialog")))
            nodes: list[_WorkflowNode] = []

            for index, action_value in enumerate(
                actions,
                start=1,
            ):
                action = _mapping(action_value)

                type_name = _first(
                    (
                        _text(action.get("kind")),
                        _text(action.get("$kind")),
                        "CopilotAction",
                    )
                )
                node_id = _first(
                    (
                        _text(action.get("id")),
                        f"action-{index}",
                    )
                )
                name = _first(
                    (
                        _text(action.get("displayName")),
                        _text(action.get("name")),
                        _text(action.get("topic")),
                        node_id,
                    )
                )

                credentials = action.get("connectionReference") or action.get("connection")

                nodes.append(
                    _WorkflowNode(
                        node_id=node_id,
                        name=name,
                        type_name=type_name,
                        parameters=action,
                        credentials=credentials,
                        line=_find_line(
                            content,
                            node_id,
                            type_name,
                        ),
                    )
                )

            if not nodes:
                continue

            edges = tuple(
                (
                    nodes[index].node_id,
                    nodes[index + 1].node_id,
                )
                for index in range(len(nodes) - 1)
            )

            file_name = Path(rel_path).name
            fallback_name = file_name.removesuffix(".mcs.yml").removesuffix(".mcs.yaml")

            workflow = _Workflow(
                platform="copilot-studio",
                name=_first(
                    (
                        _text(document.get("displayName")),
                        _text(document.get("id")),
                        fallback_name,
                    )
                ),
                nodes=tuple(nodes),
                edges=edges,
            )

            detections.extend(
                _emit_workflow(
                    workflow,
                    content=content,
                    file_path=rel_path,
                    adapter_name=self.name,
                    priority=self.priority,
                )
            )

        return detections


__all__ = [
    "CopilotStudioYAMLAdapter",
    "FlowiseWorkflowAdapter",
    "LangflowWorkflowAdapter",
    "N8nWorkflowAdapter",
]
