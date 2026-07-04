"""Phase 1 tests for the v2 technique knowledge base.

Covers schema validation, the loader, and cross-link integrity (builder keys +
scenario IDs), plus a snapshot of the bundled record IDs.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from nuguard.redteam.v2.knowledge import (
    KNOWLEDGE_BASE_VERSION,
    Detector,
    StateImpact,
    TechniqueRecord,
    load_technique_index,
    load_techniques,
    verify_builder_keys,
    verify_scenario_ids,
)

# Snapshot of bundled record IDs — update deliberately when the KB changes
# (and bump KNOWLEDGE_BASE_VERSION).
_EXPECTED_IDS = {
    "AIT-ADVERSARY-LOOP-001",
    "AIT-BOUNDARY-BLENDING-001",
    "AIT-COVERT-EGRESS-001",
    "AIT-CROSS-TENANT-001",
    "AIT-DIRECT-INJECTION-001",
    "AIT-ENCODING-CONFUSION-001",
    "AIT-EXCESSIVE-AGENCY-001",
    "AIT-FAUX-REASONING-001",
    "AIT-HUMAN-AGENT-TRUST-001",
    "AIT-INDIRECT-INJECTION-001",
    "AIT-INTER-AGENT-TRUST-001",
    "AIT-JUDGE-GAMING-001",
    "AIT-MANYSHOT-PRESSURE-001",
    "AIT-MCP-POISONING-001",
    "AIT-MEMORY-POISONING-001",
    "AIT-MULTIMODAL-INJECTION-001",
    "AIT-MULTITURN-ESCALATION-001",
    "AIT-OUTPUT-HANDLING-001",
    "AIT-POLICY-PATCH-001",
    "AIT-PRIVILEGE-ABUSE-001",
    "AIT-RAG-POISONING-001",
    "AIT-RESOURCE-EXHAUSTION-001",
    "AIT-SENSITIVE-EXFIL-001",
    "AIT-SESSION-FORGERY-001",
    "AIT-SUPPLY-CHAIN-001",
    "AIT-SYSPROMPT-EXTRACTION-001",
    "AIT-TOOL-ARG-INJECTION-001",
    "AIT-TRANSFER-TEMPLATE-001",
}


def _minimal_record_kwargs() -> dict:
    return {
        "id": "AIT-TEST-001",
        "name": "Test technique",
        "family": "test_family",
        "attack_intent": "Do a safe test.",
        "source_refs": ["OWASP LLM01:2025"],
        "surfaces": ["chat"],
        "safe_payload_strategy": ["use a benign canary"],
        "success_criteria": ["agent leaks the canary"],
        "detectors": ["canary_leak"],
        "execution": {"phase": 4},
    }


# ── schema ───────────────────────────────────────────────────────────────────
def test_minimal_record_validates() -> None:
    rec = TechniqueRecord.model_validate(_minimal_record_kwargs())
    assert rec.id == "AIT-TEST-001"
    assert rec.detectors == (Detector.CANARY_LEAK,)
    assert rec.execution.phase == 4
    assert rec.execution.state_impact is StateImpact.NONE
    assert rec.builder_key is None


@pytest.mark.parametrize("bad_id", ["test-001", "AIT-test-001", "AIT-FOO", "FOO-BAR-001"])
def test_invalid_id_rejected(bad_id: str) -> None:
    kwargs = _minimal_record_kwargs()
    kwargs["id"] = bad_id
    with pytest.raises(ValidationError):
        TechniqueRecord.model_validate(kwargs)


@pytest.mark.parametrize("field", ["source_refs", "success_criteria", "detectors", "surfaces"])
def test_empty_required_collection_rejected(field: str) -> None:
    kwargs = _minimal_record_kwargs()
    kwargs[field] = []
    with pytest.raises(ValidationError):
        TechniqueRecord.model_validate(kwargs)


def test_unknown_detector_rejected() -> None:
    kwargs = _minimal_record_kwargs()
    kwargs["detectors"] = ["not_a_real_detector"]
    with pytest.raises(ValidationError):
        TechniqueRecord.model_validate(kwargs)


def test_extra_field_forbidden() -> None:
    kwargs = _minimal_record_kwargs()
    kwargs["surprise"] = "nope"
    with pytest.raises(ValidationError):
        TechniqueRecord.model_validate(kwargs)


def test_phase_out_of_range_rejected() -> None:
    kwargs = _minimal_record_kwargs()
    kwargs["execution"] = {"phase": 11}
    with pytest.raises(ValidationError):
        TechniqueRecord.model_validate(kwargs)


# ── loader + cross-links ───────────────────────────────────────────────────────
def test_loader_returns_records() -> None:
    recs = load_techniques()
    assert len(recs) >= 20
    assert KNOWLEDGE_BASE_VERSION == "0.1.0"


def test_record_ids_snapshot() -> None:
    ids = {r.id for r in load_techniques()}
    assert ids == _EXPECTED_IDS


def test_records_sorted_and_unique() -> None:
    recs = load_techniques()
    ids = [r.id for r in recs]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_index_keyed_by_id() -> None:
    index = load_technique_index()
    assert index["AIT-INDIRECT-INJECTION-001"].family == "indirect_prompt_injection"


def test_all_builder_keys_resolve() -> None:
    assert verify_builder_keys() == []


def test_all_scenario_ids_resolve() -> None:
    assert verify_scenario_ids() == []


def test_every_record_is_well_formed() -> None:
    for rec in load_techniques():
        assert rec.detectors, f"{rec.id} has no detectors"
        assert rec.source_refs, f"{rec.id} has no source_refs"
        assert 0 <= rec.execution.phase <= 10, f"{rec.id} phase out of range"
        # Destructive/external impact must run behind a non-live safe-execution mode.
        if rec.execution.state_impact in (StateImpact.EXTERNAL_WRITE, StateImpact.DESTRUCTIVE):
            assert rec.safe_execution.value in {
                "dry_run_tool",
                "emulated_tool",
                "trap_endpoint",
                "sandbox",
                "synthetic_tenant",
            }, f"{rec.id} high-impact technique must use a safe execution mode"
