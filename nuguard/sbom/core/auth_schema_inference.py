"""Targeted LLM fallback for login-token-key / chat-endpoint field enrichment.

Unlike gap-fill (whole-category *existence* discovery, ``gap_fill/``) this
pass enriches fields on an already-known node — modeled on
``verification.py``'s ``build_verification_prompt``/``extract_context``
pattern, not ``gap_fill/llm_calls.py``'s broad-round discovery.

Fallback-only: only ever fires when static DTO extraction
(``nuguard/sbom/adapters/**``, ``nuguard/sbom/enricher.py::_enrich_login_token_key``)
left ``NodeMetadata.login_token_response_key`` unset, and never overwrites a
value static extraction already produced. Every value it writes is stamped
with provenance in ``extras`` so downstream consumers (and the still-active
runtime fallback in ``nuguard/common/auth.py::_find_token_recursive``) can
tell which mechanism produced it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nuguard.common.logging import get_logger

from .frontend_evidence import find_frontend_call_sites
from .gap_fill.budget import GapFillBudget
from .gap_fill.snippets import extract_context

_log = get_logger(__name__)

# Async callable (system_prompt, user_prompt) -> (response_text, tokens_used),
# matching LLMClient.complete's shape used elsewhere in this package.
LLMCallFn = Callable[[str, str], Awaitable[tuple[str, int]]]

_AUTH_SYSTEM_PROMPT = (
    "You are analyzing a login endpoint's backend handler code, and "
    "optionally the frontend code that calls it, to determine the exact key "
    "path where the authentication token lives in the JSON response body. "
    "Respond with a JSON object only: "
    '{"login_token_response_key": "<dotted key path, e.g. \'access_token\' '
    "or 'tokens.accessToken', or null if you cannot determine it>\", "
    '"confidence": <0.0-1.0>, "reasoning": "<one short sentence>"}. '
    "Never include an actual token value in your response — only field names."
)

_AUTH_USER_PROMPT_TEMPLATE = """Login endpoint: {method} {endpoint}

Backend handler code:
```
{backend_context}
```
{frontend_section}
Determine the key path to the auth token in the login response body."""

_FRONTEND_SECTION_TEMPLATE = """
Frontend code calling this endpoint ({file_path}):
```
{frontend_context}
```
"""


@dataclass
class AuthSchemaInferenceStats:
    attempted: int = 0
    resolved: int = 0
    budget: GapFillBudget = field(default_factory=GapFillBudget)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "resolved": self.resolved,
            **self.budget.to_dict(),
        }


def _parse_json_object(response: str) -> dict[str, Any] | None:
    raw = response.strip()
    if raw.startswith("```"):
        raw = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("```"))
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def build_auth_token_key_prompt(
    endpoint: str,
    method: str,
    backend_file_content: str | None,
    backend_line: int,
    frontend_matches: "list[Any]",
    file_contents: dict[str, str],
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for one login-endpoint inference call."""
    backend_context = (
        extract_context(backend_file_content, backend_line)
        if backend_file_content
        else "(backend source not available)"
    )

    frontend_section = ""
    if frontend_matches:
        match = frontend_matches[0]
        fe_content = file_contents.get(match.file_path, "")
        fe_context = extract_context(fe_content, match.line) if fe_content else ""
        if fe_context:
            frontend_section = _FRONTEND_SECTION_TEMPLATE.format(
                file_path=match.file_path, frontend_context=fe_context
            )

    user_prompt = _AUTH_USER_PROMPT_TEMPLATE.format(
        method=method,
        endpoint=endpoint,
        backend_context=backend_context,
        frontend_section=frontend_section,
    )
    return _AUTH_SYSTEM_PROMPT, user_prompt


async def infer_login_token_key(
    doc: "Any",
    file_contents: dict[str, str],
    llm_call_fn: LLMCallFn,
    budget: GapFillBudget | None = None,
    enabled: bool = True,
) -> AuthSchemaInferenceStats:
    """Fill in ``login_token_response_key`` for the login endpoint node, via LLM.

    No-op (fallback-only) when static extraction already resolved it, when
    disabled, when no login-like endpoint is found, or when the budget can't
    afford one more call. Mutates *doc* in place.
    """
    stats = AuthSchemaInferenceStats(budget=budget or GapFillBudget(max_calls=2, max_cost_usd=0.5))
    if not enabled:
        return stats

    from nuguard.common.target_client_builder import (  # noqa: PLC0415
        _discover_login_endpoint,
    )

    from ..models import ComponentType  # noqa: PLC0415

    result = _discover_login_endpoint(doc)
    if result is None:
        return stats
    endpoint_path, _user_field, _pass_field, existing_token_key = result
    if existing_token_key:
        # Static extraction (or a precomputed field) already resolved it —
        # fallback-only, never overwrite.
        return stats

    login_node = None
    for node in doc.nodes:
        if node.component_type != ComponentType.API_ENDPOINT:
            continue
        if (node.metadata.endpoint or "").strip() == endpoint_path:
            login_node = node
            break
    if login_node is None:
        return stats

    if not stats.budget.can_afford(1):
        _log.info("auth-schema-inference: budget exhausted, skipping login token-key inference")
        return stats

    evidence = login_node.evidence[0] if login_node.evidence else None
    backend_file = evidence.location.path if evidence and evidence.location else None
    backend_line = (evidence.location.line or 0) if evidence and evidence.location else 0
    backend_content = file_contents.get(backend_file, "") if backend_file else ""

    exclude_paths = {backend_file} if backend_file else set()
    frontend_matches = find_frontend_call_sites(file_contents, endpoint_path, exclude_paths)

    system_prompt, user_prompt = build_auth_token_key_prompt(
        endpoint_path,
        login_node.metadata.method or "POST",
        backend_content,
        backend_line,
        frontend_matches,
        file_contents,
    )

    stats.attempted += 1
    try:
        response_text, _tokens = await llm_call_fn(system_prompt, user_prompt)
        stats.budget.record(1)
    except Exception as exc:
        _log.warning("auth-schema-inference: LLM call failed (non-fatal): %s", exc)
        return stats

    parsed = _parse_json_object(response_text)
    if not parsed:
        return stats
    token_key = parsed.get("login_token_response_key")
    if not token_key or not isinstance(token_key, str):
        return stats

    login_node.metadata.login_token_response_key = token_key
    login_node.metadata.extras["login_token_response_key_source"] = "llm_frontend_inferred"
    confidence = parsed.get("confidence")
    if isinstance(confidence, (int, float)):
        login_node.metadata.extras["login_token_response_key_confidence"] = float(confidence)
    if frontend_matches:
        login_node.metadata.extras["login_token_response_key_evidence"] = [
            m.file_path for m in frontend_matches[:3]
        ]
    stats.resolved += 1
    return stats
