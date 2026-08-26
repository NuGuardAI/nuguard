"""Unit tests for LLMClientTSAdapter regressions from docs/sbom-fix2.md #4.

Covers:
  TestOpenRouterBaseUrlWithoutModelLiteral — a baseURL that resolves to a
    known gateway/provider (e.g. OpenRouter) but has no model literal must
    emit a MODEL node, not a FRAMEWORK node.
  TestBareIdentifierModelArgRejected — a shorthand call-site `{ model }`
    where `model` is an unresolved local variable must not produce a
    MODEL node from the stray identifier text.
"""

from __future__ import annotations

from typing import Any

from nuguard.sbom.adapters.typescript.llm_clients import LLMClientTSAdapter
from nuguard.sbom.core.ts_parser import parse_typescript
from nuguard.sbom.types import ComponentType

_ADAPTER = LLMClientTSAdapter()


def _extract(code: str, file_path: str = "ai.service.ts") -> list[Any]:
    pr = parse_typescript(code, file_path)
    return _ADAPTER.extract(code, file_path, pr)


class TestOpenRouterBaseUrlWithoutModelLiteral:
    def test_openrouter_base_url_emits_model_not_framework(self) -> None:
        code = (
            "import OpenAI from 'openai';\n"
            "const client = new OpenAI({ baseURL: 'https://openrouter.ai/api/v1' });\n"
        )
        detections = _extract(code)

        model_nodes = [d for d in detections if d.component_type == ComponentType.MODEL]
        framework_nodes = [d for d in detections if d.component_type == ComponentType.FRAMEWORK]

        assert len(model_nodes) == 1, detections
        assert model_nodes[0].metadata["provider"] == "openrouter"
        assert not framework_nodes, framework_nodes

    def test_groq_base_url_without_model_still_emits_model(self) -> None:
        code = (
            "import OpenAI from 'openai';\n"
            "const client = new OpenAI({ baseURL: 'https://api.groq.com/openai/v1' });\n"
        )
        detections = _extract(code)

        model_nodes = [d for d in detections if d.component_type == ComponentType.MODEL]
        framework_nodes = [d for d in detections if d.component_type == ComponentType.FRAMEWORK]

        assert len(model_nodes) == 1, detections
        assert model_nodes[0].metadata["provider"] == "groq"
        assert not framework_nodes, framework_nodes


class TestBareIdentifierModelArgRejected:
    def test_unresolved_local_variable_model_not_emitted(self) -> None:
        code = (
            "import OpenAI from 'openai';\n"
            "class AiService {\n"
            "  async chat(options) {\n"
            "    const model = options.model || this.getModel('text');\n"
            "    return this.client.chat.completions.create({ model });\n"
            "  }\n"
            "}\n"
        )
        detections = _extract(code)

        model_nodes = [d for d in detections if d.component_type == ComponentType.MODEL]
        assert not any(d.display_name == "model" for d in model_nodes), model_nodes

    def test_real_literal_model_still_emitted(self) -> None:
        code = (
            "import OpenAI from 'openai';\n"
            "class AiService {\n"
            "  async chat() {\n"
            "    return this.client.chat.completions.create({ model: 'gpt-4o-mini' });\n"
            "  }\n"
            "}\n"
        )
        detections = _extract(code)

        model_nodes = [d for d in detections if d.component_type == ComponentType.MODEL]
        assert any(d.display_name == "gpt-4o-mini" for d in model_nodes), model_nodes

    def test_resolved_variable_model_still_emitted(self) -> None:
        code = (
            "import OpenAI from 'openai';\n"
            "class AiService {\n"
            "  async chat() {\n"
            "    const model = 'gpt-4o';\n"
            "    return this.client.chat.completions.create({ model });\n"
            "  }\n"
            "}\n"
        )
        detections = _extract(code)

        model_nodes = [d for d in detections if d.component_type == ComponentType.MODEL]
        assert any(d.display_name == "gpt-4o" for d in model_nodes), model_nodes
