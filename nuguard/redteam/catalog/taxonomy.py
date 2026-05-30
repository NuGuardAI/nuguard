"""Closed taxonomy value sets for the red-team scenario catalog.

These enums mirror the dimension tables in
``docs/llm-runs/Red-team-new-design.md`` (the "Recommended dimensions" table and
the "Scenario-Pack Taxonomy").  They are ``str`` enums so they serialise as
plain strings in reports and JSON while still being snapshot-stable and
typo-proof (a bad value fails at import / spec-construction time, not silently).
"""
from __future__ import annotations

from enum import Enum


class ScenarioCategory(str, Enum):
    """Top-level catalog category (one per ID-series prefix)."""

    DATA_EXFILTRATION = "Data Exfiltration"          # D
    COVERT_EXFILTRATION = "Covert Exfiltration"      # C
    DESTRUCTIVE_ACTION = "Destructive Tool Actions"  # T
    AUTHORIZATION = "Authorization Failures"         # A
    INDIRECT_INJECTION = "Indirect Prompt Injection"  # I
    MCP_POISONING = "MCP and Tool Poisoning"         # M
    MEMORY_PERSISTENCE = "Memory and Persistence"    # P
    MULTI_AGENT_TRUST = "Multi-Agent Trust Abuse"    # G
    JAILBREAK = "Jailbreak and Policy Bypass"        # J
    EVASION = "Evasion and Robustness"               # E
    BUSINESS_LOGIC = "Business Logic and Safety"     # B
    CODING_AGENT = "Coding and Automation Agents"    # K


class DeliveryChannel(str, Enum):
    """How the attack payload reaches the model (delivery channel != objective)."""

    USER_PROMPT = "user_prompt"
    WEB_CONTENT = "web_content"
    EMAIL = "email"
    CALENDAR = "calendar"
    DOCUMENT = "document"
    SEARCH_RESULT = "search_result"
    TOOL_OUTPUT = "tool_output"
    MCP_METADATA = "mcp_metadata"
    MEMORY = "memory"
    API = "api"
    REPO = "repo"
    TERMINAL = "terminal"
    RESPONSE_RENDERING = "response_rendering"
    MULTI_SESSION = "multi_session"


class SourceTrust(str, Enum):
    """Trust level of the content that carries the attack."""

    TRUSTED_USER = "trusted_user"
    UNTRUSTED_USER = "untrusted_user"
    UNTRUSTED_EXTERNAL_CONTENT = "untrusted_external_content"
    UNTRUSTED_TOOL = "untrusted_tool"
    UNTRUSTED_AGENT = "untrusted_agent"
    STORED_UNTRUSTED_DATA = "stored_untrusted_data"


class SinkType(str, Enum):
    """Where a successful attack lands (the boundary it crosses)."""

    FINAL_ANSWER = "final_answer"
    TOOL_CALL = "tool_call"
    EXTERNAL_EGRESS = "external_egress"
    MEMORY_WRITE = "memory_write"
    BACKEND_READ = "backend_read"
    BACKEND_WRITE = "backend_write"
    HANDOFF = "handoff"
    FILE_DIFF = "file_diff"
    COMMAND_EXECUTION = "command_execution"


class EvidenceType(str, Enum):
    """Evidence required to confirm a finding for this scenario."""

    RESPONSE_QUOTE = "response_quote"
    CANARY = "canary"
    EGRESS_TRAP = "egress_trap"
    TOOL_TRACE = "tool_trace"
    BACKEND_TRACE = "backend_trace"
    MEMORY_TRACE = "memory_trace"
    AUTHZ_MISMATCH = "authz_mismatch"
    DIFF = "diff"
    COMMAND_TRACE = "command_trace"
    POLICY_EVAL = "policy_eval"
    RESPONSE_ARTIFACT = "response_artifact"


class SafeExecution(str, Enum):
    """How the scenario stays harmless against a live owned target."""

    TRACE_ONLY = "trace_only"
    DRY_RUN_TOOL = "dry_run_tool"
    EMULATED_TOOL = "emulated_tool"
    SYNTHETIC_TENANT = "synthetic_tenant"
    CANARY_ONLY = "canary_only"
    TRAP_ENDPOINT = "trap_endpoint"
    SANDBOX = "sandbox"


class Capability(str, Enum):
    """Application capabilities detected from the SBOM.

    A scenario spec lists the capabilities it requires; generation only emits
    the scenario when the target's :class:`AppCapabilityProfile` satisfies them.
    """

    # Data surfaces
    DATASTORE_PII = "datastore_pii"
    DATASTORE_PFI = "datastore_pfi"
    DATASTORE_PHI = "datastore_phi"
    SENSITIVE_CONTEXT = "sensitive_context"   # any PII/PFI/PHI reachable
    VECTOR_STORE = "vector_store"
    RAG = "rag"
    # External content / fetch surfaces
    WEB_FETCH = "web_fetch"
    SEARCH = "search"
    EMAIL_COMMS = "email_comms"
    CALENDAR = "calendar"
    DOCUMENT = "document"
    # Device / domain tools
    NAVIGATION = "navigation"
    MEDIA = "media"
    CLIMATE = "climate"
    # Agent / protocol topology
    MCP_SERVER = "mcp_server"
    MULTI_AGENT = "multi_agent"
    # Persistence
    MEMORY_STORE = "memory_store"
    SESSION_SUMMARY = "session_summary"
    MULTI_SESSION = "multi_session"
    # Coding-agent surfaces
    SHELL = "shell"
    FILESYSTEM = "filesystem"
    REPO = "repo"
    CI = "ci"
    # Sinks
    WRITE_SINK = "write_sink"
    EXTERNAL_EGRESS_SINK = "external_egress_sink"
    # Rendering / controls
    RENDERS_MARKDOWN = "renders_markdown"
    HITL_GUARD = "hitl_guard"
    # Always-true baseline (every chat agent)
    CHAT = "chat"
