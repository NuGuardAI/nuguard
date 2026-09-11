"""Regression tests for docs/sbom-accuracy-plan.md #1: a single file/adapter
pair producing far more detections than a threshold (a bulk data-catalog or
test-fixture file, e.g. a mocked models-listing API response) must collapse
to a few representative nodes instead of emitting one node per entry, and
the truncated entries must be excluded from downstream counts/findings via
the shared ``is_soft_rejected`` predicate.
"""
from __future__ import annotations

from nuguard.sbom.extractor.core import _collapse_bulk_catalog_files, _NodeAccumulator
from nuguard.sbom.models import ComponentType, Evidence, SourceLocation, is_soft_rejected


def _model_acc(name: str, *, file_path: str = "fixtures/models-api.json") -> _NodeAccumulator:
    return _NodeAccumulator(
        component_type=ComponentType.MODEL,
        canonical_name=name,
        display_name=name,
        adapter_name="model_generic",
        priority=110,
        confidence=0.7,
        metadata={},
        evidence=[
            Evidence(
                kind="regex",
                confidence=0.7,
                detail=f"model_generic: {name}",
                location=SourceLocation(path=file_path, line=1),
            )
        ],
    )


def test_file_above_threshold_collapses_representative_nodes():
    node_map = {
        (ComponentType.MODEL, f"model-{i}"): _model_acc(f"model-{i}")
        for i in range(20)
    }

    _collapse_bulk_catalog_files(node_map, threshold=15, keep=3)

    truncated = [acc for acc in node_map.values() if acc.metadata.get("bulk_catalog_truncated")]
    kept = [acc for acc in node_map.values() if not acc.metadata.get("bulk_catalog_truncated")]
    assert len(truncated) == 17
    assert len(kept) == 3
    assert all(is_soft_rejected(_fake_node(acc)) for acc in truncated)
    assert all(not is_soft_rejected(_fake_node(acc)) for acc in kept)
    assert all(acc.metadata.get("bulk_catalog_summary") for acc in kept)


def test_file_below_threshold_untouched():
    node_map = {
        (ComponentType.MODEL, f"model-{i}"): _model_acc(f"model-{i}")
        for i in range(5)
    }

    _collapse_bulk_catalog_files(node_map, threshold=15, keep=3)

    assert all(not acc.metadata.get("bulk_catalog_truncated") for acc in node_map.values())


def test_multi_file_evidence_node_not_collapsed():
    # A node corroborated across more than one file is real, not catalog
    # noise from a single fixture file — must never be truncated even if it
    # happens to land in an oversized group by coincidence of adapter name.
    acc = _model_acc("shared-model")
    acc.evidence.append(
        Evidence(
            kind="regex",
            confidence=0.7,
            detail="model_generic: shared-model",
            location=SourceLocation(path="other/file.py", line=5),
        )
    )
    node_map = {(ComponentType.MODEL, "shared-model"): acc}
    for i in range(20):
        node_map[(ComponentType.MODEL, f"catalog-{i}")] = _model_acc(f"catalog-{i}")

    _collapse_bulk_catalog_files(node_map, threshold=15, keep=3)

    assert not acc.metadata.get("bulk_catalog_truncated")


def _fake_node(acc: _NodeAccumulator):
    from nuguard.sbom.models import Node

    node = Node(name=acc.display_name, component_type=acc.component_type, confidence=acc.confidence)
    node.metadata.extras.update(acc.metadata)
    return node
