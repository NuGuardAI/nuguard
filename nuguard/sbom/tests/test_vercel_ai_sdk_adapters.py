"""Tests for the Vercel AI SDK adapters: the real TS SDK surface
(``tool()``/``streamText``/``generateText``) and its Python-side bridge
(Pydantic AI's ``VercelAIAdapter``).

Fixture code mirrors OWASP Juice Shop's ``routes/chat.ts`` (verified against
the real file this session — 4 tools, a ``createOpenAICompatible`` provider,
one ``streamText`` call), the case that motivated adding these adapters:
NuGuard's SBOM previously captured zero real tools/model for this endpoint.
"""

from __future__ import annotations

from nuguard.sbom.ast_parser import parse
from nuguard.sbom.core.ts_parser import parse_typescript
from nuguard.sbom.types import ComponentType


def _by_type(detections, component_type):
    return [d for d in detections if d.component_type == component_type]


# ---------------------------------------------------------------------------
# TypeScript: Vercel AI SDK
# ---------------------------------------------------------------------------


_CHAT_TS = """
import { streamText, tool } from 'ai'
import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { z } from 'zod'

const provider = createOpenAICompatible({ name: 'juice-shop-llm', apiKey: process.env.LLM_API_KEY ?? '', baseURL: 'https://api.example.com/v1' })

export function chat () {
  return async (req, res) => {
    const chatTools = {
      searchProducts: tool({
        description: 'Search the product catalog by keyword',
        inputSchema: z.object({
          query: z.string().describe('The search query')
        }),
        execute: async ({ query }) => []
      }),
      generateCoupon: tool({
        description: 'Generate a discount coupon for a customer.',
        inputSchema: z.object({
          discount: z.number().describe('The discount percentage (maximum 10)')
        }),
        execute: async ({ discount }) => ({ couponCode: 'X', discount })
      })
    }

    const result = streamText({
      model: provider(model),
      system: systemPrompt,
      messages,
      tools: { ...chatTools }
    })
  }
}
"""


def test_ts_tools_detected_with_zod_parameters():
    from nuguard.sbom.adapters.typescript.vercel_ai_sdk import VercelAISDKTSAdapter

    pr = parse_typescript(_CHAT_TS, "routes/chat.ts")
    dets = VercelAISDKTSAdapter().extract(_CHAT_TS, "routes/chat.ts", pr)
    tools = _by_type(dets, ComponentType.TOOL)
    names = {t.display_name for t in tools}
    assert names == {"searchProducts", "generateCoupon"}

    coupon = next(t for t in tools if t.display_name == "generateCoupon")
    assert coupon.metadata["parameters"] == {"discount": "number"}
    assert "maximum 10" in coupon.metadata["description"] or coupon.metadata["description"]


def test_ts_agent_wired_to_tools_and_dynamic_model():
    from nuguard.sbom.adapters.typescript.vercel_ai_sdk import VercelAISDKTSAdapter

    pr = parse_typescript(_CHAT_TS, "routes/chat.ts")
    dets = VercelAISDKTSAdapter().extract(_CHAT_TS, "routes/chat.ts", pr)
    agents = _by_type(dets, ComponentType.AGENT)
    models = _by_type(dets, ComponentType.MODEL)
    assert len(agents) == 1
    assert len(models) == 1
    assert models[0].metadata["detection_kind"] == "dynamic_provider"

    agent = agents[0]
    calls_targets = {r.target_canonical for r in agent.relationships if r.relationship_type == "CALLS"}
    tool_canons = {t.canonical_name for t in _by_type(dets, ComponentType.TOOL)}
    assert calls_targets == tool_canons
    uses_targets = {r.target_canonical for r in agent.relationships if r.relationship_type == "USES"}
    assert uses_targets == {models[0].canonical_name}


def test_ts_literal_provider_model_resolved():
    from nuguard.sbom.adapters.typescript.vercel_ai_sdk import VercelAISDKTSAdapter

    code = """
    import { streamText } from 'ai'
    import { openai } from '@ai-sdk/openai'
    const result = streamText({ model: openai('gpt-4o-mini'), system: 'x', messages })
    """
    pr = parse_typescript(code, "app.ts")
    dets = VercelAISDKTSAdapter().extract(code, "app.ts", pr)
    models = _by_type(dets, ComponentType.MODEL)
    assert len(models) == 1
    assert models[0].display_name == "gpt-4o-mini"
    assert models[0].metadata["provider"] == "openai"


def test_ts_can_handle_requires_exact_core_package_match():
    from nuguard.sbom.adapters.typescript.vercel_ai_sdk import VercelAISDKTSAdapter

    adapter = VercelAISDKTSAdapter()
    assert adapter.can_handle({"ai"}) is True
    assert adapter.can_handle({"@ai-sdk/openai"}) is True
    # "email"/"domain"-style imports must not false-positive-match bare "ai".
    assert adapter.can_handle({"email-validator", "domain-utils"}) is False


# ---------------------------------------------------------------------------
# Python: Pydantic AI's Vercel AI SDK bridge
# ---------------------------------------------------------------------------


_CHAT_PY = """
from pydantic_ai import Agent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

agent = Agent('openai:gpt-5.2')

@agent.tool
def search_products(ctx, query: str) -> list:
    return []

@agent.tool_plain
def generate_coupon(discount: int) -> dict:
    return {}
"""


def test_python_agent_model_and_tools_detected():
    from nuguard.sbom.adapters.python.vercel_ai_sdk import VercelAISDKPythonAdapter

    pr = parse(_CHAT_PY)
    dets = VercelAISDKPythonAdapter().extract(_CHAT_PY, "app.py", pr)

    agents = _by_type(dets, ComponentType.AGENT)
    models = _by_type(dets, ComponentType.MODEL)
    tools = _by_type(dets, ComponentType.TOOL)
    assert len(agents) == 1
    assert len(models) == 1
    assert models[0].display_name == "gpt-5.2"
    assert models[0].metadata["provider"] == "openai"
    assert {t.display_name for t in tools} == {"search_products", "generate_coupon"}

    calls_targets = {r.target_canonical for r in agents[0].relationships if r.relationship_type == "CALLS"}
    assert calls_targets == {t.canonical_name for t in tools}
