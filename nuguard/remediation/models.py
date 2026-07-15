"""Common remediation output format shared by behavior, redteam, and analysis.

``RemediationArtefact`` is the single structured representation produced by
:class:`~nuguard.remediation.synthesizer.RemediationSynthesizer` for every
finding, regardless of which module (behavior, redteam, analysis) generated
that finding.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RemediationArtefactType(str, Enum):
    """The type of concrete remediation artefact produced by RemediationSynthesizer."""

    SYSTEM_PROMPT_PATCH = "system_prompt_patch"
    INPUT_GUARDRAIL = "input_guardrail"
    OUTPUT_GUARDRAIL = "output_guardrail"
    ARCHITECTURAL_CHANGE = "architectural_change"


class RemediationArtefact(BaseModel):
    """A concrete, SBOM-node-specific remediation action.

    Produced by RemediationSynthesizer as the final pass after all findings
    are collected. Each artefact targets a specific SBOM node and provides
    actionable instructions (patch text, guardrail spec, or architectural change).
    """

    finding_ids: list[str] = Field(default_factory=list)
    """Finding IDs this artefact addresses."""

    component: str
    """Affected SBOM node name."""

    component_type: str
    """Node type: AGENT | TOOL | GUARDRAIL | system."""

    artefact_type: RemediationArtefactType
    priority: str  # critical | high | medium | low

    # System prompt patch fields
    patch_location: str | None = None
    """Source file location of the prompt, e.g. 'webapp/prompts/system.py:12'."""
    patch_section: str | None = None
    """Section heading to add/replace in the system prompt."""
    patch_text: str | None = None
    """Exact text to insert into the system prompt."""

    # Guardrail fields
    guardrail_name: str | None = None
    guardrail_type: str | None = None
    """input_classifier | output_redactor | regex | topic_classifier |
    auth_check | allowlist | confirmation_required | rate_limiter"""
    guardrail_trigger: str | None = None
    """Condition that activates the guardrail (regex, topic label, auth check, etc.)."""
    guardrail_action: str | None = None
    """BLOCK | REDACT | ROUTE | ESCALATE | HOLD"""
    guardrail_message: str | None = None
    """User-facing message shown when the guardrail fires."""

    # Privilege-specific fields (BA-005, BA-003, BA-006)
    privilege_scope: str | None = None
    """PrivilegeScope value: db_write | admin | filesystem_write | code_execution | ..."""
    privilege_node: str | None = None
    """Name of the PRIVILEGE SBOM node involved."""
    requires_auth: bool = False
    """True when an AUTH node must be added to protect this component."""
    requires_hitl: bool = False
    """True when HITL approval is mandated before the privileged action."""
    edge_to_remove: tuple[str, str] | None = None
    """(source_node, target_node) CALLS edge to remove if access is unnecessary."""

    # Architectural change fields
    change_description: str | None = None
    change_detail: str | None = None

    rationale: str
    """Human-readable explanation of why this remediation is needed."""
