"""Tests for the shared ``is_soft_rejected`` predicate (issue #511).

``llm_soft_rejected`` is set once by SBOM verification
(``nuguard/sbom/core/verification.py``) and must be honored consistently by
every downstream consumer (analysis, policy, behavior, redteam, node_counts
summaries). This module tests the shared helper itself; each consumer has
its own regression test confirming it delegates to this helper.
"""
from __future__ import annotations

from nuguard.sbom.models import Node, is_soft_rejected
from nuguard.sbom.types import ComponentType


def _node(soft_rejected: bool) -> Node:
    node = Node(name="n", component_type=ComponentType.MODEL, confidence=0.9)
    if soft_rejected:
        node.metadata.extras["llm_soft_rejected"] = True
    return node


def test_node_without_flag_is_not_soft_rejected():
    assert is_soft_rejected(_node(False)) is False


def test_node_with_flag_is_soft_rejected():
    assert is_soft_rejected(_node(True)) is True


def test_accepts_raw_dict_node():
    raw = {"metadata": {"extras": {"llm_soft_rejected": True}}}
    assert is_soft_rejected(raw) is True


def test_raw_dict_without_extras_is_not_soft_rejected():
    assert is_soft_rejected({}) is False
    assert is_soft_rejected({"metadata": {}}) is False
    assert is_soft_rejected({"metadata": {"extras": {}}}) is False
