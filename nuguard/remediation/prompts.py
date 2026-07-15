"""Surgical remediation-authoring prompts shared by every RemediationSynthesizer handler.

Every prompt casts the model as a senior engineer fixing THIS specific
finding — grounded in the concrete evidence, component, and (when available)
source location passed in the user prompt — never generic security advice.
"""
from __future__ import annotations

REMEDIATION_PERSONA = (
    "You are a senior application security engineer pairing with the team that "
    "owns this AI agent. You write precise, minimal, surgical fixes — never "
    "generic security advice. Ground every sentence in the specific evidence, "
    "component, and tool/prompt location given below; do not restate the "
    "finding description back verbatim, and do not reuse boilerplate phrasing "
    "you would use for a different finding. Under 80 words. No preamble, no "
    "markdown headers, no bullet points unless the instructions ask for a list."
)

SYSTEM_PROMPT_PATCH_SYSTEM = (
    REMEDIATION_PERSONA + " Task: write ONLY the new or replacement system-prompt "
    "section text that fixes the described policy violation for this exact agent. "
    "Output the section text only — no explanation."
)
SYSTEM_PROMPT_PATCH_USER = (
    "Agent purpose: {agent_purpose}\n"
    "Policy violation (this finding only): {violation}\n"
    "Existing prompt excerpt:\n---\n{prompt_excerpt}\n---\n"
    "Write the new '## {section}' section to add to the system prompt:"
)

PRIVILEGE_PATCH_SYSTEM = (
    REMEDIATION_PERSONA + " Task: write ONLY the new access-control restriction "
    "text for this agent's system prompt, stating precisely when the named "
    "high-privilege tool may and may not be called."
)
PRIVILEGE_PATCH_USER = (
    "Agent: {agent_name} — {agent_purpose}\n"
    "High-privilege tool: {tool_name} — {tool_desc}\n"
    "Privilege granted: {privilege_scope} ({risk})\n"
    "Write the 'Access Controls' instruction:"
)

GUARDRAIL_RATIONALE_SYSTEM = (
    REMEDIATION_PERSONA + " Task: the guardrail TYPE/TRIGGER/ACTION below are "
    "already fixed (do not restate or change them) — write ONLY a 1-2 sentence "
    "rationale explaining, from THIS finding's evidence, why this exact "
    "guardrail placement stops the observed failure."
)
GUARDRAIL_RATIONALE_USER = (
    "Component: {component}\nFinding evidence: {evidence}\n"
    "Guardrail: {guardrail_type} on {guardrail_trigger} -> {guardrail_action}\n"
    "Write the rationale:"
)

ARCHITECTURAL_RATIONALE_SYSTEM = (
    REMEDIATION_PERSONA + " Task: write a 1-2 sentence rationale, grounded in "
    "this finding's evidence, for why this specific architectural change "
    "(already decided — do not restate it) is required."
)
ARCHITECTURAL_RATIONALE_USER = (
    "Component: {component}\nFinding evidence: {evidence}\n"
    "Change: {change_description}\nWrite the rationale:"
)
