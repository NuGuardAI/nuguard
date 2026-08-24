"""Regression tests for docs/sbom-regression.md item 3: a PROMPT node with
already-captured content must not be soft-rejected because verification
only samples evidence_list[0]'s file (often an import-only reference when
evidence spans multiple files)."""

from __future__ import annotations

import pytest

from nuguard.sbom.core.verification import verify_uncertain_nodes
from nuguard.sbom.models import ComponentType, Evidence, Node, NodeMetadata, SourceLocation


def _prompt_node(*, content: str | None, confidence: float = 0.75) -> Node:
    return Node(
        name="Messages",
        component_type=ComponentType.PROMPT,
        confidence=confidence,
        metadata=NodeMetadata(extras={"content": content} if content else {}),
    )


def _evidence(path: str) -> Evidence:
    return Evidence(
        kind="ast",
        confidence=0.85,
        detail="ast_prompt_detector: ast",
        location=SourceLocation(path=path, line=10),
    )


async def _unexpected_llm_call(system_prompt: str, user_prompt: str) -> tuple[str, int]:
    raise AssertionError("LLM should not be called for a PROMPT node with captured content")


@pytest.mark.asyncio
async def test_prompt_node_with_captured_content_skips_llm_verification() -> None:
    node = _prompt_node(content="You are a medical document extraction assistant...")
    # evidence_list[0] is an import-only reference file; the real definition
    # is in a different file — this is exactly the shape that caused the
    # regression when verification only looked at evidence_list[0].
    evidence_list = [_evidence("chat.py"), _evidence("pdf_forms.py")]

    results, stats = await verify_uncertain_nodes(
        nodes=[node],
        evidence_map={node.id: evidence_list},
        llm_call_fn=_unexpected_llm_call,
        file_contents={"chat.py": "from prompt_detector import ast_prompt_detector"},
    )

    assert results == []
    assert stats.skipped_count == 1
    assert stats.verified_count == 0
    assert stats.rejected_count == 0
    # Node itself is untouched — no soft-rejection metadata applied.
    assert node.confidence == 0.75


@pytest.mark.asyncio
async def test_prompt_node_without_captured_content_still_verified() -> None:
    node = _prompt_node(content=None)
    evidence_list = [_evidence("chat.py")]
    called = False

    async def _llm_call(system_prompt: str, user_prompt: str) -> tuple[str, int]:
        nonlocal called
        called = True
        return '{"verified": true, "confidence": 0.9, "reason": "ok"}', 100

    results, stats = await verify_uncertain_nodes(
        nodes=[node],
        evidence_map={node.id: evidence_list},
        llm_call_fn=_llm_call,
        file_contents={"chat.py": "prompt = {'role': 'system', 'content': 'hi'}"},
    )

    assert called is True
    assert len(results) == 1
    assert stats.verified_count == 1
    assert stats.skipped_count == 0


@pytest.mark.asyncio
async def test_non_prompt_node_still_verified() -> None:
    node = Node(name="db_client", component_type=ComponentType.DATASTORE, confidence=0.7)
    evidence_list = [_evidence("db.py")]
    called = False

    async def _llm_call(system_prompt: str, user_prompt: str) -> tuple[str, int]:
        nonlocal called
        called = True
        return '{"verified": true, "confidence": 0.9, "reason": "ok"}', 100

    results, stats = await verify_uncertain_nodes(
        nodes=[node],
        evidence_map={node.id: evidence_list},
        llm_call_fn=_llm_call,
        file_contents={"db.py": "client = redis.Redis()"},
    )

    assert called is True
    assert len(results) == 1
    assert stats.skipped_count == 0
