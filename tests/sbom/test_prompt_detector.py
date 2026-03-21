"""Test the PromptDetector."""

from __future__ import annotations

import pytest
from pathlib import Path


from nuguard.models.sbom import NodeType
try:
    from nuguard.sbom.extractor.prompt_detector import PromptDetector
except ImportError:
    pytest.skip("PromptDetector not yet ported to nuguard.sbom", allow_module_level=True)


@pytest.fixture
def detector() -> PromptDetector:
    return PromptDetector()


STATIC_PROMPT_SOURCE = """
system_prompt = "You are a helpful assistant. Answer questions about weather."
SYSTEM_MESSAGE = "Be concise and accurate."
"""

FSTRING_PROMPT_LOCAL_SOURCE = """
topic = "weather"
system_prompt = f"You are an expert on {topic}. Answer questions."
"""

FSTRING_PROMPT_PARAM_SOURCE = """
def build_prompt(city):
    system_prompt = f"You are an expert on {city} weather."
    return system_prompt
"""

FSTRING_HIGH_RISK_SOURCE = """
def get_system_prompt(user_input):
    system_prompt = f"Answer this user query: {user_input}"
    return system_prompt
"""

SYSTEM_MESSAGE_DICT_SOURCE = """
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello"}
]
"""

REQUEST_INJECTION_SOURCE = """
def handle_request(request):
    system_prompt = f"Process: {request.body} for user: {request.user}"
    return system_prompt
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_static_prompt_detected(detector: PromptDetector) -> None:
    """Static system_prompt variable is detected as a PROMPT node."""
    nodes = detector.detect(Path("app.py"), STATIC_PROMPT_SOURCE)
    prompt_nodes = [n for n in nodes if n.component_type == NodeType.PROMPT]
    assert len(prompt_nodes) >= 1
    names = {n.name for n in prompt_nodes}
    assert "system_prompt" in names


def test_system_message_variable(detector: PromptDetector) -> None:
    """SYSTEM_MESSAGE variable is detected."""
    nodes = detector.detect(Path("app.py"), STATIC_PROMPT_SOURCE)
    names = {n.name for n in nodes}
    assert "SYSTEM_MESSAGE" in names


def test_static_prompt_zero_risk(detector: PromptDetector) -> None:
    """Static string prompt has injection_risk_score=0.0."""
    nodes = detector.detect(Path("app.py"), STATIC_PROMPT_SOURCE)
    sp_node = next(n for n in nodes if n.name == "system_prompt")
    assert sp_node.metadata.extras["injection_risk_score"] == 0.0
    assert sp_node.metadata.extras["is_template"] is False


def test_fstring_local_var_risk(detector: PromptDetector) -> None:
    """f-string with local variable has injection_risk_score=0.3."""
    nodes = detector.detect(Path("app.py"), FSTRING_PROMPT_LOCAL_SOURCE)
    sp_node = next((n for n in nodes if n.name == "system_prompt"), None)
    assert sp_node is not None
    assert sp_node.metadata.extras["is_template"] is True
    assert sp_node.metadata.extras["injection_risk_score"] == pytest.approx(0.3, abs=0.05)


def test_fstring_high_risk_user_input(detector: PromptDetector) -> None:
    """f-string with user_input variable has injection_risk_score=1.0."""
    nodes = detector.detect(Path("app.py"), FSTRING_HIGH_RISK_SOURCE)
    sp_node = next((n for n in nodes if n.name == "system_prompt"), None)
    assert sp_node is not None
    assert sp_node.metadata.extras["injection_risk_score"] == pytest.approx(1.0, abs=0.05)


def test_system_message_dict_detected(detector: PromptDetector) -> None:
    """Dict with role='system' is detected as a PROMPT node."""
    nodes = detector.detect(Path("app.py"), SYSTEM_MESSAGE_DICT_SOURCE)
    prompt_nodes = [n for n in nodes if n.component_type == NodeType.PROMPT]
    assert len(prompt_nodes) >= 1


def test_content_truncated_to_500_chars(detector: PromptDetector) -> None:
    """Content is stored as first 500 chars."""
    long_content = "A" * 600
    source = f'system_prompt = "{long_content}"'
    nodes = detector.detect(Path("app.py"), source)
    sp_node = next((n for n in nodes if n.name == "system_prompt"), None)
    assert sp_node is not None
    content = sp_node.metadata.extras.get("content", "")
    assert len(content) <= 500


def test_request_interpolation_high_risk(detector: PromptDetector) -> None:
    """f-string with request.body yields high risk score."""
    nodes = detector.detect(Path("app.py"), REQUEST_INJECTION_SOURCE)
    sp_node = next((n for n in nodes if n.name == "system_prompt"), None)
    assert sp_node is not None
    assert sp_node.metadata.extras["injection_risk_score"] >= 0.7


def test_empty_source_returns_empty(detector: PromptDetector) -> None:
    nodes = detector.detect(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []


def test_invalid_syntax_returns_empty(detector: PromptDetector) -> None:
    nodes = detector.detect(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []


def test_node_ids_stable(detector: PromptDetector) -> None:
    nodes1 = detector.detect(Path("app.py"), STATIC_PROMPT_SOURCE)
    nodes2 = detector.detect(Path("app.py"), STATIC_PROMPT_SOURCE)
    assert {n.id for n in nodes1} == {n.id for n in nodes2}
