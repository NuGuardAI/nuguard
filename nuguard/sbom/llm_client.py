"""
LLM enrichment helpers for AI SBOM.

Standalone async functions that accept the shared
:class:`nuguard.common.llm_client.LLMClient` and perform SBOM-specific
enrichment: structured JSON calls, node descriptions, and descriptive names.

Only imported when ``AiSbomConfig.enable_llm=True``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient as _CommonLLMClient

_log = get_logger(__name__)


async def complete_structured(
    llm_client: "_CommonLLMClient",
    system: str,
    user: str,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the LLM and return a parsed JSON dict.

    SBOM-specific scaffolding: strips markdown fences and extracts the first
    JSON object from the response. Uses the shared LLMClient for the actual
    LLM call.

    Parameters
    ----------
    llm_client:
        Shared ``nuguard.common.llm_client.LLMClient`` instance.
    system:
        System prompt.
    user:
        User prompt. Should instruct the model to respond with JSON.
    response_schema:
        Informational JSON Schema (not enforced — for documentation only).

    Returns
    -------
    dict
        Parsed JSON response. Returns ``{}`` on parse failure.
    """
    raw = await llm_client.complete(prompt=user, system=system)
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    try:
        return dict(json.loads(raw))
    except json.JSONDecodeError as exc:
        _log.warning("complete_structured: JSON parse failed: %s", exc)
        return {}


_DESCRIPTION_BATCH_SIZE = 25
_DESCRIPTION_BATCH_CONCURRENCY = 5


async def enrich_node_descriptions(
    nodes: list,  # list[Node] — avoid circular import
    llm_client: "_CommonLLMClient",
    *,
    concurrency: int | None = None,
) -> None:
    """Use LLM to write descriptions for AGENT/TOOL nodes missing or short ones.

    Batches nodes into chunked structured calls (instead of one call per node)
    to minimise round trips, running chunks concurrently. Mutates nodes
    in-place. Skips nodes where description is already >30 chars.
    Called from AiSbomExtractor._llm_enrich() after other enrichment steps.

    Args:
        nodes: AGENT/TOOL nodes to enrich.
        llm_client: LLM client used for description generation.
        concurrency: Max in-flight batch calls.  When ``None``, falls back to
            :data:`_DESCRIPTION_BATCH_CONCURRENCY`.
    """
    targets = []
    for node in nodes:
        component_type = getattr(node, "component_type", None)
        ct_str = str(getattr(component_type, "value", component_type)) if component_type is not None else ""
        if ct_str not in ("AGENT", "TOOL"):
            continue

        meta = getattr(node, "metadata", None)
        if meta is None:
            continue

        description = getattr(meta, "description", None) or ""
        if len(description) >= 30:
            continue  # already has a sufficient description

        targets.append((node, ct_str, meta))

    if not targets:
        return

    _SYSTEM = (
        "You are an AI security analyst writing concise descriptions for AI system components. "
        "Given a list of components, return a JSON object mapping each component's id to a "
        "single sentence (15-40 words) describing what it does."
    )

    async def _run_batch(batch: list[tuple[Any, str, Any]]) -> None:
        items = []
        for node, ct_str, meta in batch:
            name = getattr(node, "name", "") or ""
            framework = getattr(meta, "framework", None) or "unknown"
            excerpt = (getattr(meta, "system_prompt_excerpt", None) or "")[:300]
            parameters = getattr(meta, "parameters", None) or []
            param_names = ", ".join(p.name for p in parameters if p.name)
            items.append(
                {
                    "id": str(node.id),
                    "name": name,
                    "type": ct_str,
                    "framework": framework,
                    "system_prompt_excerpt": excerpt,
                    "parameters": param_names,
                }
            )

        user_prompt = (
            f"Components (JSON list):\n{json.dumps(items, ensure_ascii=False)}\n\n"
            "Return a JSON object: {\"<id>\": \"<description sentence>\", ...} for every "
            "component."
        )
        response_schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": {"type": "string"},
        }

        try:
            result = await complete_structured(
                llm_client,
                system=_SYSTEM,
                user=user_prompt,
                response_schema=response_schema,
            )
        except Exception as exc:
            _log.warning("enrich_node_descriptions: batch call failed: %s", exc)
            return

        if not isinstance(result, dict):
            return
        id_map = {str(node.id): (node, meta) for node, _ct_str, meta in batch}
        for node_id_str, text in result.items():
            if not isinstance(text, str):
                continue
            text = text.strip()
            if not text or node_id_str not in id_map:
                continue
            node, meta = id_map[node_id_str]
            meta.description = text[:200]
            _log.debug(
                "enrich_node_descriptions: set description for %s",
                getattr(node, "name", "") or "",
            )

    batches = [
        targets[i : i + _DESCRIPTION_BATCH_SIZE]
        for i in range(0, len(targets), _DESCRIPTION_BATCH_SIZE)
    ]
    _effective_concurrency = concurrency if concurrency is not None else _DESCRIPTION_BATCH_CONCURRENCY
    semaphore = asyncio.Semaphore(_effective_concurrency)

    async def _run_batch_limited(batch: list[tuple[Any, str, Any]]) -> None:
        async with semaphore:
            await _run_batch(batch)

    await asyncio.gather(*(_run_batch_limited(batch) for batch in batches))


async def enrich_descriptive_names(
    nodes: list,  # list[Node] — avoid circular import
    llm_client: "_CommonLLMClient",
) -> None:
    """Use LLM to generate a human-readable descriptive_name for every node.

    Batches all nodes into a single structured call to minimise token usage.
    Mutates nodes in-place. Skips nodes that already have descriptive_name set.
    Called from AiSbomExtractor._llm_enrich() as the final enrichment step.
    """
    targets = [
        n for n in nodes
        if getattr(getattr(n, "metadata", None), "descriptive_name", None) is None
    ]
    if not targets:
        return

    _SYSTEM = (
        "You are an AI security analyst generating short, human-readable names for AI system "
        "components. Given a list of components, return a JSON object mapping each component's "
        "id to a descriptive name (3–6 words, title case) that reflects its purpose and type, "
        "e.g. 'User Authentication API', 'PostgreSQL Credentials Store', 'LangGraph Chat Agent'."
    )

    items = []
    for node in targets:
        meta = getattr(node, "metadata", None)
        ct = str(getattr(getattr(node, "component_type", None), "value", "")) or "UNKNOWN"
        name = getattr(node, "name", "") or ""
        desc = (getattr(meta, "description", None) or "")[:100]
        items.append({"id": str(node.id), "type": ct, "name": name, "desc": desc})

    user_prompt = (
        f"Components (JSON list):\n{json.dumps(items, ensure_ascii=False)}\n\n"
        "Return a JSON object: {\"<id>\": \"<Descriptive Name>\", ...} for every component."
    )

    response_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }

    try:
        result = await complete_structured(
            llm_client,
            system=_SYSTEM,
            user=user_prompt,
            response_schema=response_schema,
        )
        if not isinstance(result, dict):
            return
        id_map = {str(n.id): n for n in targets}
        for node_id_str, label in result.items():
            if isinstance(label, str) and label.strip() and node_id_str in id_map:
                id_map[node_id_str].metadata.descriptive_name = label.strip()[:120]
        _log.debug("enrich_descriptive_names: assigned names to %d nodes", len(result))
    except Exception as exc:
        _log.warning("enrich_descriptive_names: batch call failed: %s", exc)
