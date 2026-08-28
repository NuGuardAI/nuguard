"""Tests for the Go Google Checks/AI-Safety guardrails adapter."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import GoGuardrailsAdapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def _extract(source: str, file_path: str = "main.go") -> list[ComponentDetection]:
    return GoGuardrailsAdapter().extract(source, file_path, parse_go(source, file_path))


_CHECKS_SRC = """
package main

import (
	"context"

	checks "google.golang.org/api/checks/v1alpha"
)

func run(ctx context.Context) {
	checksService, err := checks.NewService(ctx)
	if err != nil {
		return
	}
	result, err := checksService.Aisafety.ClassifyContent(nil).Do()
	_ = result
	_ = err
}
"""


def test_checks_new_service_emits_framework_node() -> None:
    detections = _extract(_CHECKS_SRC)
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)
    assert len(frameworks) == 1


def test_classify_content_emits_guardrail_node() -> None:
    detections = _extract(_CHECKS_SRC)
    guardrails = _by_type(detections, ComponentType.GUARDRAIL)
    assert len(guardrails) == 1
    assert guardrails[0].metadata["detection_kind"] == "framework_native"
    assert guardrails[0].line > 0


def test_classify_content_via_arbitrary_receiver_name_still_matches() -> None:
    src = """
package main

import checks "google.golang.org/api/checks/v1alpha"

func run() {
	svc, _ := checks.NewService(nil)
	aisafety := svc.Aisafety
	res, _ := aisafety.ClassifyContent(nil).Do()
	_ = res
}
"""
    detections = _extract(src)
    guardrails = _by_type(detections, ComponentType.GUARDRAIL)
    assert len(guardrails) == 1


def test_guardrails_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no checks api here")
}
"""
    assert _extract(src) == []
