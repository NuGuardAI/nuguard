"""Tests for langchaingo ``tools.Tool`` interface-satisfaction detection."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import LangChainGoAdapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def _extract(source: str, file_path: str = "calculator.go") -> list[ComponentDetection]:
    return LangChainGoAdapter().extract(source, file_path, parse_go(source, file_path))


_CALCULATOR_SRC = """
package main

import (
	"context"

	"github.com/tmc/langchaingo/tools"
)

type Calculator struct{}

func (c Calculator) Name() string {
	return "calculator"
}

func (c Calculator) Description() string {
	return "does math"
}

func (c Calculator) Call(ctx context.Context, input string) (string, error) {
	return "", nil
}

var _ tools.Tool = Calculator{}
"""


def test_type_implementing_tool_interface_emits_tool_node() -> None:
    detections = _extract(_CALCULATOR_SRC)
    tools_found = _by_type(detections, ComponentType.TOOL)
    assert len(tools_found) == 1
    assert tools_found[0].display_name == "Calculator"
    assert tools_found[0].metadata["detection_kind"] == "interface_satisfaction"
    assert tools_found[0].metadata["name_source"] == "receiver_type"
    assert tools_found[0].relationships


def test_type_missing_call_method_is_not_flagged() -> None:
    src = """
package main

import "github.com/tmc/langchaingo/tools"

type Incomplete struct{}

func (c Incomplete) Name() string {
	return "incomplete"
}

func (c Incomplete) Description() string {
	return "missing Call method"
}

var _ tools.Tool
"""
    assert _by_type(_extract(src), ComponentType.TOOL) == []


def test_type_with_wrong_call_signature_is_not_flagged() -> None:
    src = """
package main

import "github.com/tmc/langchaingo/tools"

type WrongShape struct{}

func (c WrongShape) Name() string {
	return "wrong"
}

func (c WrongShape) Description() string {
	return "wrong call shape"
}

func (c WrongShape) Call(input string) string {
	return ""
}

var _ tools.Tool
"""
    assert _by_type(_extract(src), ComponentType.TOOL) == []


def test_multiple_types_each_satisfying_interface_emit_separate_nodes() -> None:
    src = """
package main

import (
	"context"

	"github.com/tmc/langchaingo/tools"
)

type Calculator struct{}

func (c Calculator) Name() string { return "calculator" }
func (c Calculator) Description() string { return "does math" }
func (c Calculator) Call(ctx context.Context, input string) (string, error) { return "", nil }

type WebSearch struct{}

func (w WebSearch) Name() string { return "web_search" }
func (w WebSearch) Description() string { return "searches the web" }
func (w WebSearch) Call(ctx context.Context, input string) (string, error) { return "", nil }

var _ tools.Tool = Calculator{}
var _ tools.Tool = WebSearch{}
"""
    detections = _extract(src)
    names = {t.display_name for t in _by_type(detections, ComponentType.TOOL)}
    assert names == {"Calculator", "WebSearch"}


def test_no_op_without_matching_import() -> None:
    src = """
package main

type Calculator struct{}

func (c Calculator) Name() string { return "calculator" }
func (c Calculator) Description() string { return "does math" }
func (c Calculator) Call(input string) (string, error) { return "", nil }
"""
    assert _extract(src) == []
