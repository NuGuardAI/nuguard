"""Tests for the extended GUARDRAIL-detection adapters: cloud-native guardrails
(AWS Bedrock, Azure Content Safety, GCP Model Armor/Vertex safety settings),
Claude Agent SDK hooks, LangGraph human-in-the-loop, LangChain moderation
chains, and third-party AI-security vendors (Palo Alto, Protect AI, Presidio,
llm-guard, Rebuff, NeMo Guardrails, Lakera).
"""

from __future__ import annotations

from nuguard.sbom.ast_parser import parse
from nuguard.sbom.core.ts_parser import parse_typescript
from nuguard.sbom.types import ComponentType


def _guardrails(detections):
    return [d for d in detections if d.component_type == ComponentType.GUARDRAIL]


# ---------------------------------------------------------------------------
# Python: cloud-native guardrails
# ---------------------------------------------------------------------------


def test_aws_bedrock_apply_guardrail_detected() -> None:
    from nuguard.sbom.adapters.python.aws_bedrock_guardrails import AWSBedrockGuardrailsAdapter

    code = (
        "import boto3\n"
        'br = boto3.client("bedrock-runtime")\n'
        'br.apply_guardrail(guardrailIdentifier="abc", guardrailVersion="1")\n'
    )
    dets = AWSBedrockGuardrailsAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    assert len(guardrails) == 1
    assert guardrails[0].metadata["guardrail_type"] == "bedrock_guardrails"


def test_aws_bedrock_converse_guardrail_config_detected() -> None:
    from nuguard.sbom.adapters.python.aws_bedrock_guardrails import AWSBedrockGuardrailsAdapter

    code = (
        "import boto3\n"
        'br = boto3.client("bedrock-runtime")\n'
        "br.converse(modelId=m, guardrailConfig={\"guardrailIdentifier\": \"abc\"})\n"
    )
    dets = AWSBedrockGuardrailsAdapter().extract(code, "app.py", parse(code))
    assert len(_guardrails(dets)) == 1


def test_azure_content_safety_sdk_and_prompt_shields_detected() -> None:
    from nuguard.sbom.adapters.python.azure_content_safety import AzureContentSafetyAdapter

    code = (
        "from azure.ai.contentsafety import ContentSafetyClient\n"
        "client = ContentSafetyClient(endpoint, cred)\n"
        "result = client.analyze_text(request)\n"
        'requests.post("https://x.cognitiveservices.azure.com/contentsafety/text:shieldPrompt", json={})\n'
    )
    dets = AzureContentSafetyAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    types_seen = {g.metadata["guardrail_type"] for g in guardrails}
    assert types_seen == {"content_safety", "prompt_shields"}


def test_gcp_model_armor_client_detected() -> None:
    from nuguard.sbom.adapters.python.gcp_model_armor import GCPModelArmorAdapter

    code = (
        "from google.cloud import modelarmor_v1\n"
        "client = modelarmor_v1.ModelArmorClient()\n"
        "resp = client.sanitize_user_prompt(request=request)\n"
    )
    dets = GCPModelArmorAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    assert len(guardrails) == 2
    assert all(g.metadata["guardrail_type"] == "model_armor" for g in guardrails)


def test_gcp_vertex_safety_settings_detected() -> None:
    from nuguard.sbom.adapters.python.gcp_model_armor import GCPModelArmorAdapter

    code = (
        "from google.generativeai.types import SafetySetting\n"
        's = SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_LOW_AND_ABOVE")\n'
        'resp = model.generate_content(prompt, safety_settings=[s])\n'
    )
    dets = GCPModelArmorAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    assert len(guardrails) == 2
    assert all(g.metadata["guardrail_type"] == "vertex_safety_settings" for g in guardrails)


# ---------------------------------------------------------------------------
# Python: Claude Agent SDK hooks (explicit PROTECTS hint)
# ---------------------------------------------------------------------------


def test_claude_agent_sdk_hooks_emit_explicit_protects_hint() -> None:
    from nuguard.sbom.adapters.python.claude_agent_sdk import ClaudeAgentSDKAdapter

    code = (
        "import claude_agent_sdk\n"
        "from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient\n"
        'options = ClaudeAgentOptions(model="claude-opus", hooks={"PreToolUse": [my_hook]})\n'
        "client = ClaudeSDKClient(options=options)\n"
    )
    dets = ClaudeAgentSDKAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    assert len(guardrails) == 1
    assert guardrails[0].metadata["guardrail_type"] == "hooks"

    agent_dets = [d for d in dets if d.component_type == ComponentType.AGENT]
    assert len(agent_dets) == 1
    protects = [
        r
        for r in agent_dets[0].relationships
        if r.relationship_type == "PROTECTS"
    ]
    assert len(protects) == 1
    assert protects[0].source_canonical == guardrails[0].canonical_name
    assert protects[0].target_canonical == agent_dets[0].canonical_name
    assert protects[0].source_type == ComponentType.GUARDRAIL
    assert protects[0].target_type == ComponentType.AGENT


def test_claude_agent_sdk_can_use_tool_emits_explicit_protects_hint() -> None:
    from nuguard.sbom.adapters.python.claude_agent_sdk import ClaudeAgentSDKAdapter

    code = (
        "import claude_agent_sdk\n"
        "from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient\n"
        'options = ClaudeAgentOptions(model="claude-opus", can_use_tool=my_callback)\n'
        "client = ClaudeSDKClient(options=options)\n"
    )
    dets = ClaudeAgentSDKAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    assert len(guardrails) == 1
    assert guardrails[0].metadata["guardrail_type"] == "can_use_tool"


# ---------------------------------------------------------------------------
# Python: LangGraph human-in-the-loop, LangChain moderation
# ---------------------------------------------------------------------------


def test_langgraph_interrupt_detected() -> None:
    from nuguard.sbom.adapters.python.langgraph import LangGraphAdapter

    code = (
        "from langgraph.graph import StateGraph\n"
        "from langgraph.types import interrupt\n"
        "def review_node(state):\n"
        '    decision = interrupt("please review")\n'
        "    return state\n"
    )
    dets = LangGraphAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    assert any(g.metadata["guardrail_type"] == "human_in_the_loop" for g in guardrails)


def test_langchain_moderation_chain_detected() -> None:
    from nuguard.sbom.adapters.python.langchain_moderation import LangChainModerationAdapter

    code = "from langchain.chains import OpenAIModerationChain\nchain = OpenAIModerationChain()\n"
    dets = LangChainModerationAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    assert len(guardrails) == 1
    assert guardrails[0].metadata["moderation_class"] == "OpenAIModerationChain"


# ---------------------------------------------------------------------------
# Python: third-party AI-security vendors
# ---------------------------------------------------------------------------


def test_ai_security_vendors_all_detected() -> None:
    from nuguard.sbom.adapters.python.ai_security_vendors import AISecurityVendorsAdapter

    code = (
        "import aisecurity\n"
        'aisecurity.init(api_key="x")\n'
        "from guardian_client import GuardianAPIClient\n"
        'g = GuardianAPIClient(base_url="x")\n'
        "from presidio_analyzer import AnalyzerEngine\n"
        "a = AnalyzerEngine()\n"
        "import llm_guard\n"
        "from llm_guard import scan_prompt\n"
        "r = scan_prompt(scanners, prompt)\n"
        "from rebuff import Rebuff\n"
        'rb = Rebuff(api_token="x")\n'
        "from nemoguardrails import LLMRails, RailsConfig\n"
        "rails = LLMRails(config)\n"
        "import requests\n"
        'requests.post("https://api.lakera.ai/v1/prompt_injection", json={})\n'
    )
    dets = AISecurityVendorsAdapter().extract(code, "app.py", parse(code))
    guardrails = _guardrails(dets)
    types_seen = {g.metadata["guardrail_type"] for g in guardrails}
    assert types_seen == {
        "prisma_airs",
        "protect_ai_guardian",
        "presidio",
        "llm_guard",
        "rebuff",
        "nemo_guardrails",
        "lakera_guard",
    }
    lakera = next(g for g in guardrails if g.metadata["guardrail_type"] == "lakera_guard")
    assert lakera.metadata["detection_kind"] == "heuristic"
    assert lakera.confidence < 0.7


# ---------------------------------------------------------------------------
# TypeScript twins
# ---------------------------------------------------------------------------


def test_ts_aws_bedrock_apply_guardrail_detected() -> None:
    from nuguard.sbom.adapters.typescript.aws_bedrock_guardrails import (
        AWSBedrockGuardrailsTSAdapter,
    )

    code = (
        'import { BedrockRuntimeClient } from "@aws-sdk/client-bedrock-runtime";\n'
        "const client = new BedrockRuntimeClient({});\n"
        'const resp = await client.applyGuardrail({ guardrailIdentifier: "abc" });\n'
    )
    pr = parse_typescript(code, "app.ts")
    dets = AWSBedrockGuardrailsTSAdapter().extract(code, "app.ts", pr)
    assert len(_guardrails(dets)) == 1


def test_ts_claude_agent_sdk_hooks_emit_explicit_protects_hint() -> None:
    from nuguard.sbom.adapters.typescript.claude_agent_sdk import ClaudeAgentSDKTSAdapter

    code = (
        'import { query } from "@anthropic-ai/claude-code";\n'
        "const result = query({ prompt: \"hi\", options: { model: \"claude-opus\", "
        "hooks: { PreToolUse: [myHook] } } });\n"
    )
    pr = parse_typescript(code, "app.ts")
    dets = ClaudeAgentSDKTSAdapter().extract(code, "app.ts", pr)
    guardrails = _guardrails(dets)
    assert len(guardrails) == 1
    agent_dets = [d for d in dets if d.component_type == ComponentType.AGENT]
    assert len(agent_dets) == 1
    protects = [r for r in agent_dets[0].relationships if r.relationship_type == "PROTECTS"]
    assert len(protects) == 1
    assert protects[0].source_canonical == guardrails[0].canonical_name
    assert protects[0].target_canonical == agent_dets[0].canonical_name


def test_ts_langgraph_interrupt_detected() -> None:
    from nuguard.sbom.adapters.typescript.langgraph import LangGraphTSAdapter

    code = (
        'import { interrupt } from "@langchain/langgraph";\n'
        "function reviewNode(state) {\n"
        '  const decision = interrupt("please review");\n'
        "  return state;\n"
        "}\n"
    )
    pr = parse_typescript(code, "app.ts")
    dets = LangGraphTSAdapter().extract(code, "app.ts", pr)
    guardrails = _guardrails(dets)
    assert any(g.metadata["guardrail_type"] == "human_in_the_loop" for g in guardrails)
