"""Unit tests for the GuardrailHeuristicAdapter.

Each test class targets one detection surface:

  TestCanHandle          — adapter activates on the right import prefixes
  TestAdjacentStatements  — sanitize call immediately before an outbound call
  TestNestedArgument      — sanitize call nested as an outbound call's argument
  TestProtectsEdge        — PROTECTS edge + TOOL node for locally-defined targets
  TestConfidence          — heuristic confidence/marking stays low and labeled
  TestNegatives           — no false positives on unrelated code
"""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.sbom.adapters.python.guardrail_heuristic import GuardrailHeuristicAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.types import ComponentType

_ADAPTER = GuardrailHeuristicAdapter()


def _extract(code: str, file_path: str = "handler.py") -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, file_path, pr)


def _by_type(detections: list[Any], ctype: ComponentType) -> list[Any]:
    return [d for d in detections if d.component_type == ctype]


class TestCanHandle:
    @pytest.mark.parametrize("imports", [{"requests"}, {"httpx"}, {"aiohttp"}, {"urllib3"}])
    def test_activates_on_known_imports(self, imports: set[str]) -> None:
        assert _ADAPTER.can_handle(imports) is True

    def test_does_not_activate_on_unrelated_imports(self) -> None:
        assert _ADAPTER.can_handle({"flask", "pydantic"}) is False


class TestAdjacentStatements:
    def test_sanitize_then_post_flagged(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    clean = sanitize_output(raw)\n"
            "    requests.post(url, json=clean)\n"
        )
        guardrails = _by_type(_extract(code), ComponentType.GUARDRAIL)
        assert len(guardrails) == 1
        assert guardrails[0].display_name == "sanitize_output"

    def test_redact_and_scrub_prefixes_flagged(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    a = redact_pii(raw)\n"
            "    requests.post(url, json=a)\n"
            "    b = scrub_secrets(raw)\n"
            "    requests.get(url, params=b)\n"
        )
        guardrails = _by_type(_extract(code), ComponentType.GUARDRAIL)
        assert {g.display_name for g in guardrails} == {"redact_pii", "scrub_secrets"}

    def test_filter_for_external_suffix_flagged(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    clean = filter_fields_for_external(raw)\n"
            "    requests.post(url, json=clean)\n"
        )
        guardrails = _by_type(_extract(code), ComponentType.GUARDRAIL)
        assert guardrails[0].display_name == "filter_fields_for_external"

    def test_non_adjacent_statements_not_flagged(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    clean = sanitize_output(raw)\n"
            "    log.info('sanitized')\n"
            "    requests.post(url, json=clean)\n"
        )
        assert _by_type(_extract(code), ComponentType.GUARDRAIL) == []

    def test_outbound_call_before_sanitize_not_flagged(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    requests.post(url, json=raw)\n"
            "    clean = sanitize_output(raw)\n"
        )
        assert _by_type(_extract(code), ComponentType.GUARDRAIL) == []


class TestNestedArgument:
    def test_sanitize_call_nested_in_outbound_call(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    requests.post(url, json=sanitize_output(raw))\n"
        )
        guardrails = _by_type(_extract(code), ComponentType.GUARDRAIL)
        assert len(guardrails) == 1
        assert guardrails[0].display_name == "sanitize_output"


class TestProtectsEdge:
    def test_locally_defined_outbound_target_emits_tool_and_protects_edge(self) -> None:
        code = (
            "import requests\n\n"
            "def push_to_partner_api(payload):\n"
            "    requests.post('https://partner.example/api', json=payload)\n\n"
            "def handler(raw):\n"
            "    clean = sanitize_output(raw)\n"
            "    push_to_partner_api(clean)\n"
        )
        detections = _extract(code)
        guardrails = _by_type(detections, ComponentType.GUARDRAIL)
        tools = _by_type(detections, ComponentType.TOOL)
        assert len(guardrails) == 1
        assert len(tools) == 1
        assert tools[0].display_name == "Push To Partner Api"
        assert len(guardrails[0].relationships) == 1
        hint = guardrails[0].relationships[0]
        assert hint.relationship_type == "PROTECTS"
        assert hint.source_canonical == guardrails[0].canonical_name
        assert hint.target_canonical == tools[0].canonical_name

    def test_third_party_http_call_no_speculative_tool_node(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    clean = sanitize_output(raw)\n"
            "    requests.post(url, json=clean)\n"
        )
        detections = _extract(code)
        assert _by_type(detections, ComponentType.TOOL) == []
        guardrails = _by_type(detections, ComponentType.GUARDRAIL)
        assert guardrails[0].relationships == []


class TestConfidence:
    def test_confidence_below_framework_native_range(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    clean = sanitize_output(raw)\n"
            "    requests.post(url, json=clean)\n"
        )
        guardrails = _by_type(_extract(code), ComponentType.GUARDRAIL)
        assert 0.5 <= guardrails[0].confidence <= 0.6

    def test_marked_as_heuristic_in_metadata(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    clean = sanitize_output(raw)\n"
            "    requests.post(url, json=clean)\n"
        )
        guardrails = _by_type(_extract(code), ComponentType.GUARDRAIL)
        assert guardrails[0].metadata["detection_kind"] == "heuristic"


class TestNegatives:
    def test_unrelated_function_names_not_flagged(self) -> None:
        code = (
            "import requests\n\n"
            "def send(raw):\n"
            "    clean = transform_output(raw)\n"
            "    requests.post(url, json=clean)\n"
        )
        assert _by_type(_extract(code), ComponentType.GUARDRAIL) == []

    def test_sanitize_call_with_no_outbound_call_not_flagged(self) -> None:
        code = (
            "import requests\n\n"
            "def process(raw):\n"
            "    clean = sanitize_output(raw)\n"
            "    return clean\n"
        )
        assert _by_type(_extract(code), ComponentType.GUARDRAIL) == []

    def test_empty_file(self) -> None:
        assert _extract("") == []
