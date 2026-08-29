"""Tests for Go package-level prompt-constant extraction."""

from __future__ import annotations

from nuguard.sbom.adapters.go import extract_go_prompt_constants
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType

_LONG_PROMPT = (
    "You are Mosaic's health assistant. You help a patient understand THEIR "
    "OWN health record and make sense of it in plain, calm, reassuring "
    "language. Never diagnose or prescribe medication."
)
assert len(_LONG_PROMPT) >= 80


def _extract(source: str, file_path: str = "chat.go"):
    result = parse_go(source, file_path)
    return extract_go_prompt_constants(result, file_path)


def test_package_level_const_prompt_is_detected() -> None:
    src = f"""
package main

const systemPrompt = `{_LONG_PROMPT}`

func main() {{
	_ = systemPrompt
}}
"""
    detections = _extract(src)
    assert len(detections) == 1
    node = detections[0]
    assert node.component_type == ComponentType.PROMPT
    assert node.display_name == "systemPrompt"
    assert node.metadata["content"] == _LONG_PROMPT
    assert node.metadata["role"] == "system"
    assert node.evidence_kind == "ast_constant"
    assert node.line > 0


def test_camel_case_suffix_variants_are_matched() -> None:
    src = f"""
package main

const supplementSysPrompt = `{_LONG_PROMPT}`

func main() {{
	_ = supplementSysPrompt
}}
"""
    detections = _extract(src)
    assert len(detections) == 1
    assert detections[0].display_name == "supplementSysPrompt"
    # "system" doesn't appear in the variable name itself
    assert detections[0].metadata["role"] == "unspecified"


def test_function_local_string_is_not_a_package_level_prompt() -> None:
    src = f"""
package main

func run() {{
	localPrompt := `{_LONG_PROMPT}`
	_ = localPrompt
}}
"""
    assert _extract(src) == []


def test_short_string_below_min_length_is_skipped() -> None:
    src = """
package main

const shortPrompt = "too short"

func main() {
	_ = shortPrompt
}
"""
    assert _extract(src) == []


def test_non_prompt_named_constant_is_skipped() -> None:
    src = f"""
package main

const banner = `{_LONG_PROMPT}`

func main() {{
	_ = banner
}}
"""
    assert _extract(src) == []


def test_eval_prompt_is_skipped() -> None:
    src = f"""
package main

const testEvalPrompt = `{_LONG_PROMPT}`

func main() {{
	_ = testEvalPrompt
}}
"""
    assert _extract(src) == []


def test_template_variables_are_captured() -> None:
    src = """
package main

const greetingPrompt = `Hello {{.Name}}, welcome to your {{.PlanName}} health plan. This message should be long enough to pass the minimum length threshold for prompt detection in this test file.`

func main() {
	_ = greetingPrompt
}
"""
    detections = _extract(src)
    assert len(detections) == 1
    assert detections[0].metadata["is_template"] is True
    assert set(detections[0].metadata["template_variables"]) >= {"Name", "PlanName"}
