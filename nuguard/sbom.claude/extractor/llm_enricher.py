"""LLM enricher — uses LLMClient to add higher-fidelity metadata to SBOM nodes.

When ``config.enable_llm=True`` and ``LITELLM_API_KEY`` is set, this module
enriches nodes post-extraction with LLM-verified descriptions and risk scores.
"""

from __future__ import annotations

from nuguard.common.llm_client import LLMClient
from nuguard.common.logging import get_logger
from nuguard.models.sbom import AiSbomDocument, DataClassification, NodeType

_log = get_logger(__name__)

_CANNED_PREFIX = "[NUGUARD_CANNED_RESPONSE]"

# Approximate tokens per character for budget estimation
_CHARS_PER_TOKEN = 4


class LLMEnricher:
    """Enrich SBOM nodes with LLM-verified metadata, respecting a token budget.

    Args:
        llm_client: Shared :class:`~nuguard.common.llm_client.LLMClient`.
        budget_tokens: Approximate token budget for all enrichment calls combined.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        budget_tokens: int = 50_000,
    ) -> None:
        self._llm = llm_client
        self._budget_tokens = budget_tokens
        self._used_tokens = 0

    async def enrich(self, doc: AiSbomDocument) -> AiSbomDocument:
        """Add LLM-verified fields to nodes, respecting the token budget.

        Enriches:
        - AGENT nodes: use_case description → ``doc.summary.use_case``
        - PROMPT nodes: verify ``injection_risk_score``
        - DATASTORE nodes: confirm ``data_classification``

        Sets ``extras.llm_verified=True`` and ``extras.llm_confidence=float``
        on enriched nodes.  Stops when the token budget is exhausted.
        """
        if self._llm.api_key is None:
            _log.debug("LLMEnricher: no API key configured, skipping enrichment")
            return doc

        for node in doc.nodes:
            if self._budget_exhausted():
                _log.debug("LLMEnricher: token budget exhausted, stopping")
                break

            if node.component_type == NodeType.AGENT:
                await self._enrich_agent(node, doc)

            elif node.component_type == NodeType.PROMPT:
                await self._enrich_prompt(node)

            elif node.component_type == NodeType.DATASTORE:
                await self._enrich_datastore(node)

        return doc

    # ------------------------------------------------------------------
    # Per-type enrichment
    # ------------------------------------------------------------------

    async def _enrich_agent(self, node: object, doc: AiSbomDocument) -> None:
        from nuguard.models.sbom import Node as SbomNode
        if not isinstance(node, SbomNode):
            return
        prompt = (
            f"You are an AI security analyst. Describe the security use-case for an "
            f"AI agent named '{node.name}' (framework: {node.metadata.framework or 'unknown'}). "
            f"Reply in one sentence, max 30 words."
        )
        response = await self._call_llm(prompt)
        if response and not response.startswith(_CANNED_PREFIX):
            if not doc.summary.use_case:
                doc.summary.use_case = response.strip()
            node.metadata.extras["llm_verified"] = True
            node.metadata.extras["llm_confidence"] = 0.8

    async def _enrich_prompt(self, node: object) -> None:
        from nuguard.models.sbom import Node as SbomNode
        if not isinstance(node, SbomNode):
            return
        content = node.metadata.extras.get("content", "")
        if not content:
            return
        prompt = (
            f"You are an AI security analyst. Rate the prompt injection risk of this system prompt "
            f"on a scale 0.0 to 1.0. Reply with only a float number.\n\nPrompt: {content[:200]}"
        )
        response = await self._call_llm(prompt)
        if response and not response.startswith(_CANNED_PREFIX):
            try:
                score = float(response.strip())
                score = max(0.0, min(1.0, score))
                node.metadata.extras["injection_risk_score"] = score
                node.metadata.extras["llm_verified"] = True
                node.metadata.extras["llm_confidence"] = 0.75
            except ValueError:
                pass

    async def _enrich_datastore(self, node: object) -> None:
        from nuguard.models.sbom import Node as SbomNode
        if not isinstance(node, SbomNode):
            return
        prompt = (
            f"You are an AI security analyst. For a datastore named '{node.name}', "
            f"what is the most likely data classification? Choose one: PII, PHI, INTERNAL, PUBLIC. "
            f"Reply with only the classification word."
        )
        response = await self._call_llm(prompt)
        if response and not response.startswith(_CANNED_PREFIX):
            raw = response.strip().upper()
            try:
                dc = DataClassification(raw)
                if dc not in node.metadata.data_classification:
                    node.metadata.data_classification.append(dc)
                node.metadata.extras["llm_verified"] = True
                node.metadata.extras["llm_confidence"] = 0.6
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _call_llm(self, prompt: str) -> str:
        estimated_tokens = len(prompt) // _CHARS_PER_TOKEN + 100  # response overhead
        if self._used_tokens + estimated_tokens > self._budget_tokens:
            return ""
        try:
            response = await self._llm.complete(
                prompt,
                system="You are a concise AI security analyst. Reply briefly.",
            )
            self._used_tokens += len(prompt) // _CHARS_PER_TOKEN + len(response) // _CHARS_PER_TOKEN
            return response
        except Exception as exc:
            _log.debug("LLMEnricher call failed: %s", exc)
            return ""

    def _budget_exhausted(self) -> bool:
        return self._used_tokens >= self._budget_tokens
