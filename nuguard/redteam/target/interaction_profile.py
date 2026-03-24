"""Deterministic target interaction profile discovery and selection.

Profiles model how NuGuard should interact with a specific chat-capable endpoint.
This is intentionally static and metadata-driven (no LLM inference).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType

_ID_FIELD_PRIORITY = (
    "access_token",
    "token",
    "session_id",
    "thread_id",
    "conversation_id",
    "chat_id",
    "run_id",
)


@dataclass(frozen=True)
class StateExtractor:
    """Defines how to extract state from a step response."""

    source: str  # json | header | cookie
    key: str
    state_key: str


@dataclass(frozen=True)
class InteractionStep:
    """A single HTTP interaction step used by a profile."""

    name: str
    method: str
    path: str
    role: str = "generic"
    body_template: dict[str, Any] | None = None
    header_templates: dict[str, str] = field(default_factory=dict)
    query_templates: dict[str, str] = field(default_factory=dict)
    extractors: tuple[StateExtractor, ...] = ()
    extract_key: str | None = None


@dataclass(frozen=True)
class TargetInteractionProfile:
    """Interaction contract for a single chat-capable endpoint."""

    profile_id: str
    endpoint_node_id: str
    display_name: str
    chat_path: str
    chat_payload_key: str
    chat_payload_list: bool
    response_text_key: str | None
    flow_steps: tuple[InteractionStep, ...] = ()
    bootstrap_step: InteractionStep | None = None
    session_header_name: str | None = None
    session_id_json_key: str | None = None


def _role_tags(node: Node) -> set[str]:
    meta = node.metadata
    if not meta:
        return set()
    explicit = {t.lower() for t in (meta.interaction_role_tags or [])}
    if explicit:
        return explicit

    inferred: set[str] = set()
    path = (meta.endpoint or "").lower()
    name = node.name.lower()
    text = f"{name} {path}"
    if any(k in text for k in ("login", "signin", "token", "oauth", "auth")):
        inferred.add("auth_token")
    if any(k in text for k in ("session", "thread", "conversation", "new-chat")):
        inferred.add("session_bootstrap")
    if "cookie" in text:
        inferred.add("cookie_session")
    return inferred


def _response_id_map(node: Node) -> dict[str, str]:
    meta = node.metadata
    if not meta:
        return {}
    return {k: v for k, v in (meta.response_id_map or {}).items() if isinstance(v, str) and v}


def _request_state_headers(node: Node) -> dict[str, str]:
    meta = node.metadata
    if not meta:
        return {}
    return {
        k: v
        for k, v in (meta.request_state_headers or {}).items()
        if isinstance(k, str) and isinstance(v, str) and k and v
    }


def _api_post_nodes(sbom: AiSbomDocument) -> list[Node]:
    nodes: list[Node] = []
    for node in sbom.nodes:
        if node.component_type != ComponentType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta or not meta.endpoint:
            continue
        method = (meta.method or "POST").upper()
        if method != "POST":
            continue
        nodes.append(node)
    return nodes


def _id_like_fields(node: Node) -> list[str]:
    meta = node.metadata
    if not meta:
        return []
    fields = set(meta.path_params or [])
    fields.update((meta.request_body_schema or {}).keys())
    lowered = {f.lower() for f in fields}
    return [k for k in _ID_FIELD_PRIORITY if k in lowered]


def _session_header_for(key: str) -> str:
    return "X-" + key.replace("_", "-").title()


def _build_chat_step(chat_node: Node, session_key: str | None = None) -> InteractionStep:
    meta = chat_node.metadata
    assert meta is not None
    chat_path = meta.endpoint or "/chat"
    chat_payload_key = meta.chat_payload_key or "message"
    chat_payload_list = bool(meta.chat_payload_list)

    payload_value: Any = ["{{payload}}"] if chat_payload_list else "{{payload}}"
    body_template: dict[str, Any] = {chat_payload_key: payload_value}

    for state_key, field_name in (meta.request_state_body or {}).items():
        body_template[field_name] = f"{{{{state.{state_key}}}}}"

    header_templates: dict[str, str] = {}
    for state_key, header_name in _request_state_headers(chat_node).items():
        header_templates[header_name] = f"{{{{state.{state_key}}}}}"

    # Backward-compatible fallback for session bootstrap profiles.
    if session_key and not header_templates:
        header_templates[_session_header_for(session_key)] = f"{{{{state.{session_key}}}}}"

    # Auth-token modeled flow fallback: bind Authorization when token-like key exists.
    if "Authorization" not in header_templates:
        for key in ("access_token", "token"):
            if key in body_template:
                continue
            header_templates.setdefault("Authorization", f"Bearer {{{{state.{key}}}}}")
            break

    return InteractionStep(
        name=chat_node.name,
        method=(meta.method or "POST").upper(),
        path=chat_path,
        role="chat",
        body_template=body_template,
        header_templates=header_templates,
        query_templates={
            qfield: f"{{{{state.{skey}}}}}"
            for skey, qfield in (meta.request_state_query or {}).items()
        },
    )


def _build_pre_step(node: Node, chat_node: Node) -> InteractionStep | None:
    meta = node.metadata
    if not meta:
        return None
    path = meta.endpoint or ""
    if not path:
        return None

    tags = _role_tags(node)
    role = "generic"
    if "auth_token" in tags:
        role = "auth_token"
    elif "cookie_session" in tags:
        role = "cookie_session"
    elif "session_bootstrap" in tags:
        role = "session_bootstrap"

    id_map = _response_id_map(node)
    extractors: list[StateExtractor] = []

    # Strong defaults for modeled auth/session/cookie flows.
    if role == "auth_token":
        token_key = id_map.get("access_token") or id_map.get("token") or "access_token"
        extractors.append(StateExtractor(source="json", key=token_key, state_key="access_token"))
    elif role == "cookie_session":
        cookie_name = id_map.get("session_cookie") or "sessionid"
        extractors.append(StateExtractor(source="cookie", key=cookie_name, state_key="session_cookie"))
    elif role == "session_bootstrap":
        session_key = (
            id_map.get("session_id")
            or _bootstrap_session_key(chat_node, node)
            or "session_id"
        )
        extractors.append(StateExtractor(source="json", key=session_key, state_key=session_key))

    for state_key, response_key in id_map.items():
        if any(e.state_key == state_key for e in extractors):
            continue
        extractors.append(
            StateExtractor(source="json", key=response_key, state_key=state_key)
        )

    body_template: dict[str, Any] = {}
    for state_key, field_name in (meta.request_state_body or {}).items():
        body_template[field_name] = f"{{{{state.{state_key}}}}}"

    return InteractionStep(
        name=node.name,
        method=(meta.method or "POST").upper(),
        path=path,
        role=role,
        body_template=body_template or {},
        header_templates={
            hname: f"{{{{state.{skey}}}}}"
            for skey, hname in _request_state_headers(node).items()
        },
        query_templates={
            qfield: f"{{{{state.{skey}}}}}"
            for skey, qfield in (meta.request_state_query or {}).items()
        },
        extractors=tuple(extractors),
    )


def _path_affinity(pre_path: str, chat_path: str) -> tuple[int, int]:
    pre_parts = [p for p in pre_path.split("/") if p]
    chat_parts = [p for p in chat_path.split("/") if p]
    shared = 0
    for left, right in zip(pre_parts, chat_parts):
        if left != right:
            break
        shared += 1
    # Stable tie-breaker: shorter path first.
    return shared, -len(pre_parts)


def _select_pre_steps(chat_node: Node, candidates: list[Node]) -> list[InteractionStep]:
    chat_path = (chat_node.metadata.endpoint if chat_node.metadata else "") or "/chat"
    built: list[InteractionStep] = []
    for node in candidates:
        step = _build_pre_step(node, chat_node)
        if step is None:
            continue
        built.append(step)

    if not built:
        return []

    # Keep at most one step per modeled role, preferring same-path affinity.
    by_role: dict[str, list[InteractionStep]] = {}
    for step in built:
        by_role.setdefault(step.role, []).append(step)

    chosen: list[InteractionStep] = []
    for role in ("auth_token", "cookie_session", "session_bootstrap"):
        options = by_role.get(role, [])
        if not options:
            continue
        options.sort(key=lambda s: _path_affinity(s.path, chat_path), reverse=True)
        chosen.append(options[0])

    return chosen


def _bootstrap_session_key(chat_node: Node, bootstrap_node: Node) -> str | None:
    # Strongest signal: chat endpoint expects a known id-like field.
    chat_id_fields = _id_like_fields(chat_node)
    if chat_id_fields:
        return chat_id_fields[0]

    chat_path = (chat_node.metadata.endpoint or "").lower()
    boot_path = (bootstrap_node.metadata.endpoint or "").lower()

    # Path token heuristic for bootstrap pairing.
    if "session" in chat_path and "session" in boot_path:
        return "session_id"
    if "thread" in chat_path and "thread" in boot_path:
        return "thread_id"
    if "conversation" in chat_path and "conversation" in boot_path:
        return "conversation_id"
    return None


def discover_interaction_profiles(sbom: AiSbomDocument) -> list[TargetInteractionProfile]:
    """Discover direct and bootstrap chat interaction profiles from SBOM metadata."""
    post_api_nodes = _api_post_nodes(sbom)
    chat_nodes = [n for n in post_api_nodes if n.metadata and n.metadata.chat_payload_key]
    bootstrap_nodes = [n for n in post_api_nodes if not (n.metadata and n.metadata.chat_payload_key)]

    profiles: list[TargetInteractionProfile] = []

    for chat_node in chat_nodes:
        meta = chat_node.metadata
        assert meta is not None
        chat_path = meta.endpoint or "/chat"
        chat_payload_key = meta.chat_payload_key or "message"
        chat_payload_list = bool(meta.chat_payload_list)
        response_text_key = meta.response_text_key

        chat_step = _build_chat_step(chat_node)
        profiles.append(
            TargetInteractionProfile(
                profile_id=f"direct:{chat_node.id}",
                endpoint_node_id=str(chat_node.id),
                display_name=f"{chat_node.name} ({chat_path})",
                chat_path=chat_path,
                chat_payload_key=chat_payload_key,
                chat_payload_list=chat_payload_list,
                response_text_key=response_text_key,
                flow_steps=(chat_step,),
            )
        )

        pre_steps = _select_pre_steps(chat_node, bootstrap_nodes)
        if pre_steps:
            flow_steps = tuple(pre_steps + [_build_chat_step(chat_node)])
            id_extractors = [e for s in pre_steps for e in s.extractors if e.state_key in _ID_FIELD_PRIORITY]
            session_extractor = next(
                (e for e in id_extractors if e.state_key in ("session_id", "thread_id", "conversation_id")),
                None,
            )
            profiles.append(
                TargetInteractionProfile(
                    profile_id=(
                        f"flow:{'+'.join(s.name.replace(' ', '_').lower() for s in pre_steps)}"
                        f"->{chat_node.id}"
                    ),
                    endpoint_node_id=str(chat_node.id),
                    display_name=f"{chat_node.name} ({chat_path}) via modeled flow",
                    chat_path=chat_path,
                    chat_payload_key=chat_payload_key,
                    chat_payload_list=chat_payload_list,
                    response_text_key=response_text_key,
                    flow_steps=flow_steps,
                    bootstrap_step=(
                        pre_steps[0]
                        if pre_steps and pre_steps[0].role == "session_bootstrap"
                        else None
                    ),
                    session_id_json_key=(session_extractor.state_key if session_extractor else None),
                    session_header_name=(
                        _session_header_for(session_extractor.state_key)
                        if session_extractor
                        else None
                    ),
                )
            )

        for bootstrap_node in bootstrap_nodes:
            bootstrap_meta = bootstrap_node.metadata
            assert bootstrap_meta is not None
            session_key = _bootstrap_session_key(chat_node, bootstrap_node)
            if not session_key:
                continue
            bootstrap_path = bootstrap_meta.endpoint or ""
            bootstrap_step = InteractionStep(
                name=bootstrap_node.name,
                method="POST",
                path=bootstrap_path,
                role="session_bootstrap",
                body_template={},
                extractors=(
                    StateExtractor(source="json", key=session_key, state_key=session_key),
                ),
                extract_key=session_key,
            )
            profiles.append(
                TargetInteractionProfile(
                    profile_id=f"bootstrap:{bootstrap_node.id}->{chat_node.id}",
                    endpoint_node_id=str(chat_node.id),
                    display_name=(
                        f"{chat_node.name} ({chat_path}) via {bootstrap_node.name} ({bootstrap_path})"
                    ),
                    chat_path=chat_path,
                    chat_payload_key=chat_payload_key,
                    chat_payload_list=chat_payload_list,
                    response_text_key=response_text_key,
                    flow_steps=(bootstrap_step, _build_chat_step(chat_node, session_key=session_key)),
                    bootstrap_step=bootstrap_step,
                    session_id_json_key=session_key,
                    session_header_name=_session_header_for(session_key),
                )
            )

    return profiles


def select_interaction_profile(
    profiles: list[TargetInteractionProfile],
    endpoint_id: str | None = None,
    endpoint_name: str | None = None,
    endpoint_path: str | None = None,
) -> TargetInteractionProfile | None:
    """Select one interaction profile deterministically.

    Raises ValueError when selection is ambiguous or references unknown candidates.
    """
    if not profiles:
        return None

    selected = profiles
    if endpoint_id:
        selected = [
            p
            for p in selected
            if p.endpoint_node_id == endpoint_id or p.profile_id == endpoint_id
        ]
        if not selected:
            raise ValueError(f"No interaction profile matches endpoint_id={endpoint_id!r}")

    if endpoint_name:
        needle = endpoint_name.lower().strip()
        selected = [p for p in selected if needle in p.display_name.lower()]
        if not selected:
            raise ValueError(f"No interaction profile matches endpoint_name={endpoint_name!r}")

    if endpoint_path:
        selected = [p for p in selected if p.chat_path == endpoint_path]
        if not selected:
            raise ValueError(f"No interaction profile matches endpoint_path={endpoint_path!r}")

    if len(selected) == 1:
        return selected[0]

    # If multiple profiles share one endpoint, prefer bootstrap profile.
    endpoint_ids = {p.endpoint_node_id for p in selected}
    if len(endpoint_ids) == 1:
        bootstrap = [p for p in selected if p.bootstrap_step is not None]
        if len(bootstrap) == 1:
            return bootstrap[0]
        if len(selected) == 1:
            return selected[0]

    lines = "\n".join(
        f"- {p.profile_id}: {p.display_name} [path={p.chat_path}]"
        for p in selected
    )
    raise ValueError(
        "Multiple interaction profiles match. Set redteam.endpoint_id, "
        "redteam.endpoint_name, or redteam.target_endpoint to disambiguate:\n"
        f"{lines}"
    )