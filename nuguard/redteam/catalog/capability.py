"""App capability profile — single source of truth for SBOM capability detection.

:class:`CapabilityDetector` reads the SBOM graph once and produces an immutable
:class:`AppCapabilityProfile` that is passed into builder factories and the
scenario selector.  This consolidates the scattered capability checks that were
previously spread across ``generator.py`` and ``sbom_driven.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType, RelationshipType

from .taxonomy import Capability as C

# ── Tool classification patterns (mirrors sbom_driven._CLASSIFIERS) ───────────
_WEB_PAT = re.compile(r"\b(?:url|web|browse|scrape|fetch|http|crawl|webhook)\b", re.IGNORECASE)
_SEARCH_PAT = re.compile(r"\b(?:search|googl|bing|serpapi|websearch|lookup)\b", re.IGNORECASE)
_EMAIL_PAT = re.compile(r"\b(?:email|send|notify|message|mail|smtp|comms|communication|messaging)\b", re.IGNORECASE)
_CALENDAR_PAT = re.compile(r"\b(?:calendar|event|schedule|appointment|meeting)\b", re.IGNORECASE)
_NAV_PAT = re.compile(r"\b(?:navigate|navigation|route|direction|map|gps|destination)\b", re.IGNORECASE)
_MEDIA_PAT = re.compile(r"\b(?:media|music|play|pause|volume|audio|radio|podcast)\b", re.IGNORECASE)
_CLIMATE_PAT = re.compile(r"\b(?:climate|hvac|temperature|heat|cool|fan|ac|air)\b", re.IGNORECASE)
_SHELL_PAT = re.compile(r"\b(?:execute|run|shell|bash|command|script|eval|terminal)\b", re.IGNORECASE)
_FS_PAT = re.compile(r"\b(?:read|write|file|path|disk|storage|filesystem|directory)\b", re.IGNORECASE)
_REPO_PAT = re.compile(r"\b(?:repo|git|commit|branch|pull.?request|github|gitlab)\b", re.IGNORECASE)
_CI_PAT = re.compile(r"\b(?:ci|cd|pipeline|workflow|action|jenkins|github.actions|circleci)\b", re.IGNORECASE)
_MCP_PAT = re.compile(r"\bmcp\b", re.IGNORECASE)
_MEMORY_PAT = re.compile(r"\b(?:memory|remember|persist|long.?term|preference|profile|store)\b", re.IGNORECASE)
_VECTOR_PAT = re.compile(r"\b(?:vector|embedding|semantic|pinecone|weaviate|chroma|qdrant|index)\b", re.IGNORECASE)
_WRITE_SCOPES = frozenset({"db_write", "filesystem_write", "code_execution", "email_out", "network_out"})
_WRITE_TOOL_INDICATORS = frozenset({
    "send", "write", "update", "delete", "create", "post", "put", "patch",
    "cancel", "submit", "publish", "upload", "notify", "message", "email",
    "transfer", "pay", "charge", "set", "save", "store",
})


def _tool_haystack(node: Node) -> str:
    name = node.name or ""
    desc = (node.metadata.description or "") if node.metadata else ""
    return f"{name} {desc}".lower()


def _is_write_tool(node: Node) -> bool:
    if node.metadata and node.metadata.privilege_scope in _WRITE_SCOPES:
        return True
    if node.metadata and node.metadata.high_privilege:
        return True
    hay = _tool_haystack(node)
    return any(ind in hay for ind in _WRITE_TOOL_INDICATORS)


def _is_egress_tool(node: Node) -> bool:
    hay = _tool_haystack(node)
    return bool(_WEB_PAT.search(hay) or _EMAIL_PAT.search(hay))


@dataclass(frozen=True)
class AppCapabilityProfile:
    """Immutable summary of what the target application can do.

    Built once per run by :class:`CapabilityDetector` and passed into builder
    factories and the scenario selector.  Each ``Capability`` flag corresponds
    to one or more SBOM signals (node types, edge patterns, metadata flags).
    """
    capabilities: frozenset[C]

    # Structured collections for factory use
    entry_agent_ids: tuple[str, ...]
    all_agent_ids: tuple[str, ...]
    write_sink_ids: tuple[str, ...]         # tool node IDs that are write-capable
    egress_sink_ids: tuple[str, ...]        # tool node IDs with external-egress
    mcp_tool_ids: tuple[str, ...]
    pii_fields: tuple[str, ...]             # merged from all datastores + policy
    tool_names: tuple[str, ...]             # all reachable tool names (for prompt context)
    tool_index: dict[str, str]              # node_id -> classified category
    domain: str                             # inferred domain (automotive, fintech, …)

    def satisfies(self, required: frozenset[C]) -> bool:
        """Return True iff every required capability is present."""
        return required <= self.capabilities

    def __repr__(self) -> str:
        caps = ", ".join(c.value for c in sorted(self.capabilities, key=lambda x: x.value))
        return f"AppCapabilityProfile({caps})"


class CapabilityDetector:
    """Builds an :class:`AppCapabilityProfile` from an :class:`AiSbomDocument`."""

    def __init__(
        self,
        sbom: AiSbomDocument,
        policy: object | None = None,  # CognitivePolicy | None — avoid heavy import
    ) -> None:
        self._sbom = sbom
        self._policy = policy
        self._node_by_id: dict[str, Node] = {str(n.id): n for n in sbom.nodes}
        self._outgoing: dict[str, dict[str, list[str]]] = {}
        for edge in sbom.edges:
            (self._outgoing
             .setdefault(str(edge.source), {})
             .setdefault(edge.relationship_type, [])
             .append(str(edge.target)))

    # ── Public entry point ────────────────────────────────────────────────────

    def build(self) -> AppCapabilityProfile:
        caps: set[C] = {C.CHAT}  # every conversational agent has CHAT

        agents = self._agents()
        entry_agents = self._entry_agents(agents)
        all_tools = self._all_reachable_tools(agents)
        tool_names = tuple(n.name or "" for n in all_tools if n.name)
        tool_index = {str(n.id): _tool_haystack(n) for n in all_tools}

        write_sinks: list[str] = []
        egress_sinks: list[str] = []
        mcp_tools: list[str] = []
        for tool in all_tools:
            hay = _tool_haystack(tool)
            if _is_write_tool(tool):
                caps.add(C.WRITE_SINK)
                write_sinks.append(str(tool.id))
            if _is_egress_tool(tool):
                caps.add(C.EXTERNAL_EGRESS_SINK)
                egress_sinks.append(str(tool.id))
            if _MCP_PAT.search(hay) or (tool.metadata and tool.metadata.mcp_server_url):
                caps.add(C.MCP_SERVER)
                mcp_tools.append(str(tool.id))
            if _WEB_PAT.search(hay):
                caps.add(C.WEB_FETCH)
            if _SEARCH_PAT.search(hay):
                caps.add(C.SEARCH)
            if _EMAIL_PAT.search(hay):
                caps.add(C.EMAIL_COMMS)
            if _CALENDAR_PAT.search(hay):
                caps.add(C.CALENDAR)
            if _NAV_PAT.search(hay):
                caps.add(C.NAVIGATION)
            if _MEDIA_PAT.search(hay):
                caps.add(C.MEDIA)
            if _CLIMATE_PAT.search(hay):
                caps.add(C.CLIMATE)
            if _SHELL_PAT.search(hay):
                caps.add(C.SHELL)
            if _FS_PAT.search(hay):
                caps.add(C.FILESYSTEM)
            if _REPO_PAT.search(hay):
                caps.add(C.REPO)
            if _CI_PAT.search(hay):
                caps.add(C.CI)
            if _MEMORY_PAT.search(hay):
                caps.add(C.MEMORY_STORE)
            if _VECTOR_PAT.search(hay):
                caps.add(C.VECTOR_STORE)
                caps.add(C.RAG)

        pii_fields = self._pii_fields()
        if pii_fields:
            caps.add(C.DATASTORE_PII)
            caps.add(C.SENSITIVE_CONTEXT)
        if self._has_phi():
            caps.add(C.DATASTORE_PHI)
            caps.add(C.SENSITIVE_CONTEXT)
        if self._has_pfi():
            caps.add(C.DATASTORE_PFI)
            caps.add(C.SENSITIVE_CONTEXT)
        if C.SENSITIVE_CONTEXT not in caps and entry_agents:
            # Even without a datastore, an agent with tool calls can access context
            if all_tools:
                caps.add(C.SENSITIVE_CONTEXT)

        if self._has_rag():
            caps.add(C.RAG)

        if self._has_multi_agent(agents):
            caps.add(C.MULTI_AGENT)

        if self._has_memory_store():
            caps.add(C.MEMORY_STORE)
        if self._has_session_summary():
            caps.add(C.SESSION_SUMMARY)

        if self._has_hitl_guard():
            caps.add(C.HITL_GUARD)

        # All tool nodes in the SBOM (not just edge-reachable ones — edges may be incomplete)
        all_sbom_tools = [n for n in self._sbom.nodes if n.component_type == ComponentType.TOOL]

        # DOCUMENT: detect from tool name/description keywords
        _DOCUMENT_TOOL_KEYS = (
            "document", "file", "ocr", "upload", "attachment", "pdf", "scan", "ingest",
        )
        if any(
            any(kw in _tool_haystack(t) for kw in _DOCUMENT_TOOL_KEYS)
            for t in all_sbom_tools
        ):
            caps.add(C.DOCUMENT)

        # MULTI_SESSION: detect from context_payload_fields or memory/session tool names
        _SESSION_FIELD_KEYS = ("session_id", "user_id", "conversation_id", "history", "profile")
        api_ep_nodes = [n for n in self._sbom.nodes if n.component_type == ComponentType.API_ENDPOINT]
        chat_ctx_fields = [
            f
            for ep in api_ep_nodes
            for f in (ep.metadata.context_payload_fields if ep.metadata else []) or []
        ]
        if any(any(kw in f.lower() for kw in _SESSION_FIELD_KEYS) for f in chat_ctx_fields):
            caps.add(C.MULTI_SESSION)
        _MULTI_SESSION_TOOL_KEYS = ("memory", "history", "profile", "session", "persist", "long-term")
        if any(
            any(kw in _tool_haystack(t) for kw in _MULTI_SESSION_TOOL_KEYS)
            for t in all_sbom_tools
        ):
            caps.add(C.MULTI_SESSION)

        # RENDERS_MARKDOWN: default True for all chat agents (most render markdown),
        # but annotate whether evidence was found from framework metadata.
        _MARKDOWN_FRAMEWORKS = ("react", "next", "streamlit", "gradio", "vue", "angular", "svelte")
        _summary_frameworks = list(getattr(self._sbom.summary, "frameworks", None) or [])
        _md_evidence = any(fw in " ".join(_summary_frameworks).lower() for fw in _MARKDOWN_FRAMEWORKS)
        caps.add(C.RENDERS_MARKDOWN)  # default-on; evidence captured above if needed

        domain = self._infer_domain(agents, all_tools)

        return AppCapabilityProfile(
            capabilities=frozenset(caps),
            entry_agent_ids=tuple(str(a.id) for a in entry_agents),
            all_agent_ids=tuple(str(a.id) for a in agents),
            write_sink_ids=tuple(write_sinks),
            egress_sink_ids=tuple(egress_sinks),
            mcp_tool_ids=tuple(mcp_tools),
            pii_fields=tuple(pii_fields),
            tool_names=tool_names,
            tool_index=tool_index,
            domain=domain,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _agents(self) -> list[Node]:
        return [n for n in self._sbom.nodes if n.component_type == ComponentType.AGENT]

    def _entry_agents(self, agents: list[Node]) -> list[Node]:
        """Agents directly reachable from an API_ENDPOINT node via CALLS/USES."""
        entry_ids: set[str] = set()
        for node in self._sbom.nodes:
            if node.component_type != ComponentType.API_ENDPOINT:
                continue
            for rel in (RelationshipType.CALLS, RelationshipType.USES):
                for tid in self._outgoing.get(str(node.id), {}).get(rel, []):
                    n = self._node_by_id.get(tid)
                    if n and n.component_type == ComponentType.AGENT:
                        entry_ids.add(str(n.id))
        # Heuristic fallback — name patterns
        if not entry_ids:
            for a in agents:
                if re.search(r"triage|router|entry|gateway|main|orchestrat|front.?door", a.name or "", re.IGNORECASE):
                    entry_ids.add(str(a.id))
        if not entry_ids and agents:
            entry_ids.add(str(agents[0].id))
        return [a for a in agents if str(a.id) in entry_ids]

    def _all_reachable_tools(self, agents: list[Node]) -> list[Node]:
        seen: set[str] = set()
        tools: list[Node] = []
        for agent in agents:
            for tid in self._outgoing.get(str(agent.id), {}).get(RelationshipType.CALLS, []):
                n = self._node_by_id.get(tid)
                if n and n.component_type == ComponentType.TOOL and tid not in seen:
                    seen.add(tid)
                    tools.append(n)
        return tools

    def _pii_fields(self) -> list[str]:
        fields: list[str] = []
        for n in self._sbom.nodes:
            if n.component_type == ComponentType.DATASTORE and n.metadata:
                fields.extend(n.metadata.pii_fields or [])
        if self._policy and hasattr(self._policy, "data_classification"):
            # Policy data_classification is a list of sensitivity strings
            for label in (self._policy.data_classification or []):
                if label not in fields:
                    fields.append(label)
        return fields

    def _has_phi(self) -> bool:
        return any(
            n.component_type == ComponentType.DATASTORE
            and n.metadata
            and n.metadata.phi_fields
            for n in self._sbom.nodes
        )

    def _has_pfi(self) -> bool:
        return any(
            n.component_type == ComponentType.DATASTORE
            and n.metadata
            and n.metadata.pfi_fields
            for n in self._sbom.nodes
        )

    def _has_rag(self) -> bool:
        for n in self._sbom.nodes:
            if n.component_type == ComponentType.DATASTORE:
                hay = _tool_haystack(n)
                if _VECTOR_PAT.search(hay) or (n.metadata and getattr(n.metadata, "datastore_type", "") in ("vector", "rag")):
                    return True
        return False

    def _has_multi_agent(self, agents: list[Node]) -> bool:
        # AGENT→AGENT CALLS edge
        agent_ids = {str(a.id) for a in agents}
        for aid in agent_ids:
            for tid in self._outgoing.get(aid, {}).get(RelationshipType.CALLS, []):
                if tid in agent_ids:
                    return True
        return len(agents) > 1

    def _has_memory_store(self) -> bool:
        for n in self._sbom.nodes:
            if n.component_type == ComponentType.DATASTORE:
                hay = _tool_haystack(n)
                if _MEMORY_PAT.search(hay):
                    return True
            if n.component_type == ComponentType.TOOL and n.metadata:
                hay = _tool_haystack(n)
                if _MEMORY_PAT.search(hay):
                    return True
        return False

    def _has_session_summary(self) -> bool:
        for n in self._sbom.nodes:
            hay = _tool_haystack(n)
            if re.search(r"summary|summariz", hay, re.IGNORECASE):
                return True
        return False

    def _has_hitl_guard(self) -> bool:
        for n in self._sbom.nodes:
            if n.component_type == ComponentType.GUARDRAIL:
                return True
        if self._policy and hasattr(self._policy, "hitl_triggers"):
            return bool(self._policy.hitl_triggers)
        return False

    def _infer_domain(self, agents: list[Node], tools: list[Node]) -> str:
        all_text = " ".join(
            ((a.metadata.system_prompt_excerpt or "") + " " + (a.name or "")) for a in agents
        ) + " " + " ".join(t.name or "" for t in tools)
        if re.search(r"health|medical|patient|clinical|ehr|hospital|diagnos", all_text, re.IGNORECASE):
            return "healthcare"
        if re.search(r"bank|financ|payment|credit|loan|invest|trading|wealth", all_text, re.IGNORECASE):
            return "fintech"
        if re.search(r"car|vehicle|driv|automotiv|navigation|route|climate|hvac", all_text, re.IGNORECASE):
            return "automotive"
        if re.search(r"flight|airline|booking|reservation|travel|hotel|itinerar", all_text, re.IGNORECASE):
            return "travel"
        if re.search(r"shop|ecommerce|cart|order|product|retail|merchant", all_text, re.IGNORECASE):
            return "ecommerce"
        if re.search(r"code|git|repo|deploy|ci|developer|engineer|devops", all_text, re.IGNORECASE):
            return "coding"
        return "general"
