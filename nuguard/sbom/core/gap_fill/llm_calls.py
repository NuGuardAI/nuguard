"""Prompt construction, LLM invocation, and response parsing for gap-fill.

Three call shapes, corresponding to the three rounds in ``rounds.py``:

- :func:`call_broad_round` — Round 1, one call per category, same shape as
  the original single-shot gap-fill prompt plus an ``"ambiguous"`` flag.
- :func:`call_followup_round` — Round 2, one call per category batching all
  of that category's borderline candidates for a closer look.
- :func:`call_critique_round` — Round 3, one call per category presenting
  the combined survivor list back to the LLM as an adversarial reviewer,
  reusing ``verification.py``'s REJECT criteria almost verbatim.
"""

from __future__ import annotations

import json
from typing import Any

from ...models import Evidence, Node, NodeMetadata, SourceLocation
from ...types import ComponentType
from .categories import (
    _CATEGORY_DESCRIPTIONS,
    _DISCOVERY_CONFIDENCE_CAP,
    _MIN_ACCEPTED_CONFIDENCE,
    _MIN_ACCEPTED_CONFIDENCE_BY_CATEGORY,
    _TOOL_BLOCKLIST,
)
from .snippets import extract_context

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def build_existing_node_summary(doc_nodes: list[Node]) -> str:
    """Build a compact text summary of already-detected nodes for the prompt."""
    if not doc_nodes:
        return "(no nodes detected yet)"
    lines: list[str] = []
    for node in doc_nodes[:50]:
        lines.append(f"- [{node.component_type.value}] {node.name} (confidence={node.confidence:.2f})")
    if len(doc_nodes) > 50:
        lines.append(f"  ... and {len(doc_nodes) - 50} more")
    return "\n".join(lines)


def min_accepted_confidence(category: ComponentType) -> float:
    return _MIN_ACCEPTED_CONFIDENCE_BY_CATEGORY.get(category, _MIN_ACCEPTED_CONFIDENCE)


def _strip_and_extract_json(raw_text: str, *, array: bool) -> Any:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(ln for ln in lines if not ln.startswith("```"))
    open_ch, close_ch = ("[", "]") if array else ("{", "}")
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _category_extra_guidance(category: ComponentType) -> str:
    if category == ComponentType.FRAMEWORK:
        return (
            "\n\nFor MCP server instances (FastMCP / mcp.server.fastmcp):\n"
            "- Set \"name\" to the server display name passed to FastMCP() or 'mcp-server' if unknown.\n"
            '- In "detail", write a SHORT description: e.g. '
            '\'MCP server "my-server" exposing tools: <tool1>, <tool2>; '
            "transport: streamable-http; auth: BearerAuthProvider'.\n"
            '- Set canonical_name to the snake_case server name prefixed with "mcp:", '
            'e.g. "mcp:my-server".\n'
            'If it is a different framework (LangGraph, CrewAI, etc.) describe it in "detail" likewise.'
        )
    if category == ComponentType.PRIVILEGE:
        return (
            "\n\nFor PRIVILEGE nodes use one of these canonical_name values exactly:\n"
            '  "privilege:rbac"              — RBAC / permission checks / role assignment\n'
            '  "privilege:admin"             — sudo / superuser / admin escalation\n'
            '  "privilege:filesystem_write"  — file write, delete, or move operations\n'
            '  "privilege:db_write"          — database INSERT / UPDATE / DELETE / ORM write calls\n'
            '  "privilege:email_out"         — outbound email (smtplib, SendGrid, SES, etc.)\n'
            '  "privilege:social_media_out"  — posts to Twitter/X, Reddit, Discord, Telegram, Slack\n'
            '  "privilege:code_execution"    — subprocess, os.system, BashTool, E2BSandbox, shell=True\n'
            '  "privilege:network_out"       — outbound HTTP POST/PUT/PATCH, webhooks\n'
            'Set "name" to the human-readable privilege class (e.g. "Filesystem Write").\n'
            'In "detail" reference the specific function/class/pattern you found.'
        )
    if category == ComponentType.GUARDRAIL:
        return (
            "\n\nOnly report a GUARDRAIL if it is actually wired into the request/response path "
            "(called, not just imported/defined-but-unused). In \"detail\" cite the specific "
            "validator/filter/moderation call and what it protects against."
        )
    return ""


# ---------------------------------------------------------------------------
# Round 1 — broad candidate proposal
# ---------------------------------------------------------------------------

_BROAD_SYSTEM_PROMPT = """\
You are an AI component detection assistant.
You will be given:
1. A summary of AI components already detected in a codebase.
2. Relevant source file snippets.
3. A target component category to look for.

Your job is to identify ONLY components of the target category that are NOT
already in the existing summary.

Return a JSON array of objects. Each object must have:
  "name"            — display name (string)
  "canonical_name"  — lowercase slug form, e.g. "gpt-4o-mini" or "redis"
  "confidence"      — float 0.0-1.0 (be conservative; cap at 0.75 for uncertain finds)
  "detail"          — one-sentence justification referencing the file and a code snippet
  "evidence_files"  — list of relative file paths supporting this detection
  "ambiguous"       — true if you are not fully sure this is a real, wired-in component
                       (e.g. it might be unused, a false-positive name collision, or test-only)

Return an empty array [] if you find nothing new.
Return ONLY the JSON array — no prose, no markdown, no code fences.
"""


async def call_broad_round(
    category: ComponentType,
    existing_summary: str,
    snippets: str,
    client: Any,
) -> list[dict[str, Any]]:
    """Round 1: one focused LLM call, returns parsed+validated candidate dicts."""
    category_desc = _CATEGORY_DESCRIPTIONS.get(category, category.value)
    extra_guidance = _category_extra_guidance(category)
    user_prompt = (
        f"## Already-detected components\n{existing_summary}\n\n"
        f"## Target category: {category.value}\n"
        f"Description: {category_desc}{extra_guidance}\n\n"
        f"## Source code snippets\n{snippets}\n\n"
        f"Find any {category.value} components NOT listed above and return JSON."
    )
    raw_text = await client.complete(prompt=user_prompt, system=_BROAD_SYSTEM_PROMPT)
    parsed = _strip_and_extract_json(raw_text, array=True)
    if not isinstance(parsed, list):
        return []

    floor = min_accepted_confidence(category)
    valid: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if conf < floor:
            continue
        valid.append(item)
    return valid


# ---------------------------------------------------------------------------
# Round 2 — targeted follow-up on borderline candidates
# ---------------------------------------------------------------------------

_FOLLOWUP_SYSTEM_PROMPT = """\
You are re-examining borderline AI-component detections with more code context.
For each candidate, decide whether it is really a wired-in production component
of the stated category, using the wider code excerpt provided.

Return a JSON array with one object per candidate, in the same order given, each with:
  "name"               — echo the candidate's name
  "confirmed"          — true or false
  "refined_confidence" — float 0.0-1.0
  "refined_detail"     — updated one-sentence justification (or rejection reason)

Return ONLY the JSON array — no prose, no markdown, no code fences.
"""


async def call_followup_round(
    category: ComponentType,
    borderline: list[dict[str, Any]],
    file_contents: dict[str, str],
    client: Any,
) -> list[dict[str, Any]]:
    """Round 2: confirm/reject/refine borderline Round-1 candidates."""
    if not borderline:
        return []

    blocks: list[str] = []
    for item in borderline:
        evidence_files = item.get("evidence_files") or []
        path = evidence_files[0] if evidence_files else ""
        content = file_contents.get(path, "")
        context = extract_context(content, 1, context_lines=40) if content else content[:2000]
        blocks.append(
            f"### Candidate: {item.get('name')}\n"
            f"Original detail: {item.get('detail', '')}\n"
            f"File: {path}\n```\n{context}\n```"
        )

    user_prompt = (
        f"## Target category: {category.value}\n\n" + "\n\n".join(blocks)
    )
    raw_text = await client.complete(prompt=user_prompt, system=_FOLLOWUP_SYSTEM_PROMPT)
    parsed = _strip_and_extract_json(raw_text, array=True)
    if not isinstance(parsed, list):
        return []

    by_name = {str(item.get("name", "")).lower(): item for item in borderline}
    confirmed: list[dict[str, Any]] = []
    for result in parsed:
        if not isinstance(result, dict) or not result.get("confirmed"):
            continue
        original = by_name.get(str(result.get("name", "")).lower())
        if not original:
            continue
        merged = dict(original)
        if "refined_confidence" in result:
            merged["confidence"] = result["refined_confidence"]
        if result.get("refined_detail"):
            merged["detail"] = result["refined_detail"]
        confirmed.append(merged)
    return confirmed


# ---------------------------------------------------------------------------
# Round 3 — self-critique / adversarial review
# ---------------------------------------------------------------------------

# Reject criteria mirrored from verification.py's _SYSTEM_PROMPT — general
# "is this really production code" checks, not verification-specific.
_CRITIQUE_SYSTEM_PROMPT = """\
You are an adversarial reviewer double-checking AI-component discovery results
before they are added to an AI Bill of Materials. Be strict and conservative.

### REJECT a candidate if:
1. It is in a test file or test function (test_*, *_test.py, conftest.py)
2. It is a mock, stub, or fixture for testing
3. It is only mentioned in a comment, docstring, or documentation
4. It is an abstract base class without concrete instantiation
5. It is example code in a README or docs folder
6. It is an import statement without actual usage
7. It is a false positive (e.g. name collision with an unrelated meaning)

### CONFIRM only if:
1. It is a concrete instantiation/call that will run in production
2. It is defined in application code (not tests)
3. It matches the claimed category

{category_extra}

Return a JSON array with one object per candidate, in the same order given, each with:
  "name"       — echo the candidate's name
  "confirmed"  — true or false
  "reason"     — brief explanation

Return ONLY the JSON array — no prose, no markdown, no code fences.
"""

_PRIVILEGE_CRITIQUE_EXTRA = (
    "For PRIVILEGE candidates specifically: also verify the canonical_name sub-type "
    "(rbac/admin/filesystem_write/db_write/email_out/social_media_out/code_execution/"
    "network_out) is the CORRECT one for the cited code — reject if the sub-type "
    "looks misclassified, even if the underlying finding is real."
)


async def call_critique_round(
    category: ComponentType,
    survivors: list[dict[str, Any]],
    file_contents: dict[str, str],
    client: Any,
) -> list[dict[str, Any]]:
    """Round 3: adversarial re-check of the combined Round 1+2 survivor list."""
    if not survivors:
        return []

    extra = _PRIVILEGE_CRITIQUE_EXTRA if category == ComponentType.PRIVILEGE else ""
    system_prompt = _CRITIQUE_SYSTEM_PROMPT.format(category_extra=extra)

    blocks: list[str] = []
    for item in survivors:
        evidence_files = item.get("evidence_files") or []
        path = evidence_files[0] if evidence_files else ""
        content = file_contents.get(path, "")
        context = extract_context(content, 1, context_lines=40) if content else content[:2000]
        blocks.append(
            f"### Candidate: {item.get('name')} (canonical: {item.get('canonical_name', '')})\n"
            f"Detail: {item.get('detail', '')}\n"
            f"File: {path}\n```\n{context}\n```"
        )

    user_prompt = f"## Target category: {category.value}\n\n" + "\n\n".join(blocks)
    raw_text = await client.complete(prompt=user_prompt, system=system_prompt)
    parsed = _strip_and_extract_json(raw_text, array=True)
    if not isinstance(parsed, list):
        # Fail closed for PRIVILEGE/GUARDRAIL (higher-risk, opt-in categories):
        # an unparseable critique response means "not confirmed," not "confirmed."
        return []

    by_name = {str(item.get("name", "")).lower(): item for item in survivors}
    confirmed: list[dict[str, Any]] = []
    for result in parsed:
        if not isinstance(result, dict) or not result.get("confirmed"):
            continue
        original = by_name.get(str(result.get("name", "")).lower())
        if original:
            confirmed.append(original)
    return confirmed


# ---------------------------------------------------------------------------
# Node conversion
# ---------------------------------------------------------------------------


def is_tool_blocklisted(item: dict[str, Any]) -> bool:
    candidate_name = str(item.get("canonical_name") or item.get("name") or "").lower()
    return candidate_name in _TOOL_BLOCKLIST or any(
        blocked in candidate_name for blocked in _TOOL_BLOCKLIST
    )


def result_to_node(item: dict[str, Any], category: ComponentType) -> Node | None:
    """Convert a raw LLM discovery dict to a ``Node``."""
    try:
        name = str(item["name"]).strip()
        canonical = str(item.get("canonical_name") or name).lower().strip()
        confidence = min(
            _DISCOVERY_CONFIDENCE_CAP,
            float(item.get("confidence", 0.5)),
        )
        detail = str(item.get("detail") or f"llm_discovery: {name}")[:200]
        evidence_files: list[str] = [str(f) for f in (item.get("evidence_files") or [])]

        primary_file = evidence_files[0] if evidence_files else "unknown"
        evidence = Evidence(
            kind="llm_discovery",
            confidence=confidence,
            detail=detail,
            location=SourceLocation(path=primary_file, line=None),
        )

        node = Node(
            name=name,
            component_type=category,
            confidence=confidence,
            metadata=NodeMetadata(),
            evidence=[evidence],
        )
        node.metadata.extras["canonical_name"] = canonical
        node.metadata.extras["adapter"] = "gap_fill"
        node.metadata.extras["evidence_files"] = evidence_files
        node.metadata.extras["source_tier"] = "llm"
        if detail and detail != f"llm_discovery: {name}":
            node.metadata.extras["description"] = detail
        if category == ComponentType.FRAMEWORK and "framework" not in node.metadata.extras:
            node.metadata.framework = canonical

        return node
    except (KeyError, ValueError, TypeError):
        return None
