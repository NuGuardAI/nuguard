"""Tests for the Guardrails AI adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.models.sbom import NodeType
from nuguard.sbom.extractor.framework_adapters.guardrails_ai import GuardrailsAIAdapter


@pytest.fixture
def adapter() -> GuardrailsAIAdapter:
    return GuardrailsAIAdapter()


GUARD_FROM_RAIL_SOURCE = """
import guardrails as gd
from guardrails import Guard

guard = Guard.from_rail("my_rail.rail")
"""

GUARD_FROM_PYDANTIC_SOURCE = """
from guardrails import Guard
from pydantic import BaseModel

class MyOutput(BaseModel):
    name: str
    age: int

guard = Guard.from_pydantic(MyOutput)
"""

GUARD_WITH_VALIDATORS_SOURCE = """
from guardrails import Guard
from guardrails.validators import TwoWords, ValidLength

guard = Guard(validators=[TwoWords(on_fail="reask"), ValidLength(min=1, max=100)])
"""

VALIDATOR_CLASS_SOURCE = """
from guardrails import validator
from guardrails.validators import Validator

@validator(name="my_validator")
class MyValidator(Validator):
    def validate(self, value, metadata):
        return value
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_guard_from_rail_detected(adapter: GuardrailsAIAdapter) -> None:
    """Guard.from_rail → GUARDRAIL node."""
    nodes, _ = adapter.extract(Path("app.py"), GUARD_FROM_RAIL_SOURCE)
    guardrail_nodes = [n for n in nodes if n.component_type == NodeType.GUARDRAIL]
    assert len(guardrail_nodes) >= 1


def test_guard_from_pydantic_detected(adapter: GuardrailsAIAdapter) -> None:
    """Guard.from_pydantic → GUARDRAIL node."""
    nodes, _ = adapter.extract(Path("app.py"), GUARD_FROM_PYDANTIC_SOURCE)
    guardrail_nodes = [n for n in nodes if n.component_type == NodeType.GUARDRAIL]
    assert len(guardrail_nodes) >= 1


def test_guard_with_validators_detected(adapter: GuardrailsAIAdapter) -> None:
    """Guard(validators=[...]) → GUARDRAIL node with validators metadata."""
    nodes, _ = adapter.extract(Path("app.py"), GUARD_WITH_VALIDATORS_SOURCE)
    guardrail_nodes = [n for n in nodes if n.component_type == NodeType.GUARDRAIL]
    assert len(guardrail_nodes) >= 1


def test_validator_class_detected(adapter: GuardrailsAIAdapter) -> None:
    """@validator decorated class → GUARDRAIL node."""
    nodes, _ = adapter.extract(Path("app.py"), VALIDATOR_CLASS_SOURCE)
    guardrail_nodes = [n for n in nodes if n.component_type == NodeType.GUARDRAIL]
    assert len(guardrail_nodes) >= 1
    assert any(n.name == "MyValidator" for n in guardrail_nodes)


def test_node_ids_are_stable(adapter: GuardrailsAIAdapter) -> None:
    nodes1, _ = adapter.extract(Path("app.py"), GUARD_FROM_RAIL_SOURCE)
    nodes2, _ = adapter.extract(Path("app.py"), GUARD_FROM_RAIL_SOURCE)
    ids1 = {n.id for n in nodes1}
    ids2 = {n.id for n in nodes2}
    assert ids1 == ids2


def test_empty_source_returns_empty(adapter: GuardrailsAIAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: GuardrailsAIAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []


def test_confidence_in_range(adapter: GuardrailsAIAdapter) -> None:
    nodes, _ = adapter.extract(Path("app.py"), GUARD_WITH_VALIDATORS_SOURCE)
    for node in nodes:
        assert 0.0 <= node.confidence <= 1.0
