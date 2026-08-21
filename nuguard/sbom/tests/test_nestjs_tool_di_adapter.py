"""Unit tests for NestJSToolDIAdapter.

Covers:
  TestPositiveDetection    — co-injection + action-verb match fires
  TestNegativeNoActionVerb — AI-client sibling alone doesn't fire
  TestNegativeNoLlmSibling — action-verb type alone (no AI sibling) doesn't fire
  TestInfraDenylist        — infra-suffix overrides an action-verb substring match
  TestKnownSdkClient       — a raw SDK client class satisfies Signal 1 too
  TestConsolidation        — same injected type from two files -> one detection each,
                              same canonical name
  TestNoFalseSubstringMatch — regression: "ResearchGateway" must not match "search"
"""

from __future__ import annotations

from typing import Any

from nuguard.sbom.adapters.typescript.nestjs_tool_di import NestJSToolDIAdapter
from nuguard.sbom.core.ts_parser import parse_typescript
from nuguard.sbom.types import ComponentType

_ADAPTER = NestJSToolDIAdapter()


def _extract(code: str, file_path: str = "orchestrator.service.ts") -> list[Any]:
    pr = parse_typescript(code, file_path)
    return _ADAPTER.extract(code, file_path, pr)


def _tool_names(detections: list[Any]) -> set[str]:
    return {d.display_name for d in detections if d.component_type == ComponentType.TOOL}


class TestPositiveDetection:
    def test_web_search_service_detected_alongside_ai_service(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ResearchService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly webSearchService: WebSearchService,\n"
            "  ) {}\n"
            "}\n"
        )
        dets = _extract(code)
        assert "Web Search" in _tool_names(dets)

    def test_knowledge_base_service_detected(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ChatService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly knowledgeBaseService: KnowledgeBaseService,\n"
            "  ) {}\n"
            "}\n"
        )
        dets = _extract(code)
        assert "Knowledge Base" in _tool_names(dets)

    def test_two_qualifying_siblings_both_detected_with_distinct_lines(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ResearchService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly knowledgeBaseService: KnowledgeBaseService,\n"
            "    private readonly webSearchService: WebSearchService,\n"
            "  ) {}\n"
            "}\n"
        )
        dets = [d for d in _extract(code) if d.component_type == ComponentType.TOOL]
        assert {d.display_name for d in dets} == {"Knowledge Base", "Web Search"}
        # Regression: each must carry its own distinct source line, not the
        # constructor's opening line for both — otherwise the extractor's
        # cross-adapter _dedup_by_location pass (same component_type, file,
        # line) treats them as duplicate detections of one source token and
        # drops one at random.
        lines = {d.line for d in dets}
        assert len(lines) == 2, f"expected distinct lines per tool, got: {lines}"


class TestNegativeNoActionVerb:
    def test_ai_sibling_with_unrelated_service_not_flagged(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ChatService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly userService: UserService,\n"
            "  ) {}\n"
            "}\n"
        )
        assert _tool_names(_extract(code)) == set()

    def test_ai_sibling_with_config_service_not_flagged(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ChatService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly configService: ConfigService,\n"
            "  ) {}\n"
            "}\n"
        )
        assert _tool_names(_extract(code)) == set()


class TestNegativeNoLlmSibling:
    def test_action_verb_type_without_ai_sibling_not_flagged(self) -> None:
        code = (
            "@Injectable()\n"
            "export class SearchModule {\n"
            "  constructor(\n"
            "    private readonly dbService: DatabaseService,\n"
            "    private readonly webSearchService: WebSearchService,\n"
            "  ) {}\n"
            "}\n"
        )
        assert _tool_names(_extract(code)) == set()


class TestInfraDenylist:
    def test_search_cache_not_flagged_despite_search_substring(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ChatService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly searchCache: SearchCache,\n"
            "  ) {}\n"
            "}\n"
        )
        assert _tool_names(_extract(code)) == set()


class TestKnownSdkClient:
    def test_raw_openai_client_satisfies_llm_signal(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ChatService {\n"
            "  constructor(\n"
            "    private readonly openai: OpenAI,\n"
            "    private readonly webSearchService: WebSearchService,\n"
            "  ) {}\n"
            "}\n"
        )
        assert "Web Search" in _tool_names(_extract(code))


class TestConsolidation:
    def test_same_injected_type_from_two_files_shares_canonical_name(self) -> None:
        code_a = (
            "@Injectable()\n"
            "export class ResearchService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly knowledgeBaseService: KnowledgeBaseService,\n"
            "  ) {}\n"
            "}\n"
        )
        code_b = (
            "@Injectable()\n"
            "export class ChatService {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly knowledgeBaseService: KnowledgeBaseService,\n"
            "  ) {}\n"
            "}\n"
        )
        dets_a = [d for d in _extract(code_a, "research.service.ts") if d.component_type == ComponentType.TOOL]
        dets_b = [d for d in _extract(code_b, "chat.service.ts") if d.component_type == ComponentType.TOOL]
        assert len(dets_a) == 1 and len(dets_b) == 1
        assert dets_a[0].canonical_name == dets_b[0].canonical_name


class TestNoFalseSubstringMatch:
    def test_research_gateway_not_flagged_despite_containing_search_substring(self) -> None:
        code = (
            "@Injectable()\n"
            "export class ResearchGateway {\n"
            "  constructor(\n"
            "    private readonly aiService: AiService,\n"
            "    private readonly researchGateway: ResearchGateway,\n"
            "  ) {}\n"
            "}\n"
        )
        assert _tool_names(_extract(code)) == set()
