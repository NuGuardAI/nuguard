"""Tests for the LLM clients adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.models.sbom import NodeType
from nuguard.sbom.adapters.python.llm_clients import LLMClientsAdapter


@pytest.fixture
def adapter() -> LLMClientsAdapter:
    return LLMClientsAdapter()


OPENAI_SOURCE = """
import openai
from openai import OpenAI

client = OpenAI(api_key="sk-...")
response = client.chat.completions.create(model="gpt-4o", messages=[])
"""

ANTHROPIC_SOURCE = """
import anthropic

client = anthropic.Anthropic()
message = client.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=1024)
"""

GROQ_SOURCE = """
from groq import Groq

client = Groq(api_key="gsk_...")
chat_completion = client.chat.completions.create(model="llama-3.1-8b-instant")
"""

BASE_URL_PROXY_SOURCE = """
from openai import OpenAI

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key="gsk_..."
)
"""

BOTO3_BEDROCK_SOURCE = """
import boto3

bedrock_runtime = boto3.client("bedrock-runtime", region_name="us-east-1")
bedrock_agent = boto3.client("bedrock-agent-runtime")
"""

HUGGINGFACE_SOURCE = """
from huggingface_hub import InferenceClient

client = InferenceClient(model="meta-llama/Meta-Llama-3-8B", provider="fireworks-ai")
"""

EMPTY_SOURCE = ""


def test_openai_client_detected(adapter: LLMClientsAdapter) -> None:
    """OpenAI() client → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), OPENAI_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1


def test_openai_model_from_api_call(adapter: LLMClientsAdapter) -> None:
    """client.chat.completions.create(model=...) → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), OPENAI_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert any(n.name == "gpt-4o" for n in model_nodes)


def test_anthropic_client_detected(adapter: LLMClientsAdapter) -> None:
    """anthropic.Anthropic() → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), ANTHROPIC_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1


def test_anthropic_model_from_api_call(adapter: LLMClientsAdapter) -> None:
    """client.messages.create(model=...) → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), ANTHROPIC_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert any(n.name == "claude-3-5-sonnet-20241022" for n in model_nodes)


def test_groq_client_detected(adapter: LLMClientsAdapter) -> None:
    """Groq() client → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), GROQ_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1


def test_base_url_proxy_detected(adapter: LLMClientsAdapter) -> None:
    """OpenAI(base_url=groq.com/...) → MODEL with groq provider."""
    nodes, _ = adapter.extract(Path("app.py"), BASE_URL_PROXY_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1
    # Provider should be groq
    groq_nodes = [n for n in model_nodes if n.metadata.framework == "groq"]
    assert len(groq_nodes) >= 1


def test_boto3_bedrock_runtime(adapter: LLMClientsAdapter) -> None:
    """boto3.client('bedrock-runtime') → MODEL node (amazon)."""
    nodes, _ = adapter.extract(Path("app.py"), BOTO3_BEDROCK_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1


def test_huggingface_inference_client(adapter: LLMClientsAdapter) -> None:
    """InferenceClient(model=...) → MODEL node."""
    nodes, _ = adapter.extract(Path("app.py"), HUGGINGFACE_SOURCE)
    model_nodes = [n for n in nodes if n.component_type == NodeType.MODEL]
    assert len(model_nodes) >= 1
    assert any("Meta-Llama" in n.name or "InferenceClient" in n.name or n.metadata.model_name == "meta-llama/Meta-Llama-3-8B" for n in model_nodes)


def test_empty_source_returns_empty(adapter: LLMClientsAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_node_ids_are_stable(adapter: LLMClientsAdapter) -> None:
    nodes1, _ = adapter.extract(Path("app.py"), OPENAI_SOURCE)
    nodes2, _ = adapter.extract(Path("app.py"), OPENAI_SOURCE)
    ids1 = {n.id for n in nodes1}
    ids2 = {n.id for n in nodes2}
    assert ids1 == ids2
