"""Regression tests for docs/sbom-regression.md item 1: a generic-regex
API_ENDPOINT node (raw, unprefixed path guess) must fold into the matching
framework-adapter node once the real, prefix-resolved path is known,
instead of surviving as a duplicate low-confidence node."""

from __future__ import annotations

from nuguard.sbom.extractor.core import _NodeAccumulator, _dedup_generic_endpoints
from nuguard.sbom.models import ComponentType, Evidence, SourceLocation


def _generic_acc(method: str, path: str, evidence_path: str) -> _NodeAccumulator:
    return _NodeAccumulator(
        component_type=ComponentType.API_ENDPOINT,
        canonical_name=f"endpoint:{method}:{path}",
        display_name=path,
        adapter_name="api_endpoint_generic",
        priority=50,
        confidence=0.55,
        metadata={"endpoint": path, "method": method, "_generic_endpoint_fallback": True},
        evidence=[
            Evidence(
                kind="regex",
                confidence=0.55,
                detail=f"api_endpoint_generic: {path}",
                location=SourceLocation(path=evidence_path, line=12),
            )
        ],
    )


def _real_acc(method: str, path: str, evidence_path: str, *, confidence: float = 0.864) -> _NodeAccumulator:
    return _NodeAccumulator(
        component_type=ComponentType.API_ENDPOINT,
        canonical_name=f"endpoint:{method}:{path}",
        display_name=path,
        adapter_name="fastapi",
        priority=10,
        confidence=confidence,
        metadata={"endpoint": path, "method": method},
        evidence=[
            Evidence(
                kind="ast",
                confidence=confidence,
                detail=f"fastapi: {path}",
                location=SourceLocation(path=evidence_path, line=20),
            )
        ],
    )


def test_generic_endpoint_folds_into_prefixed_real_endpoint() -> None:
    generic = _generic_acc("POST", "/re-embed", "server/api/rag.py")
    real = _real_acc("POST", "/api/rag/re-embed", "server/api/rag.py")
    node_map = {
        (ComponentType.API_ENDPOINT, "generic"): generic,
        (ComponentType.API_ENDPOINT, "real"): real,
    }

    _dedup_generic_endpoints(node_map)

    assert (ComponentType.API_ENDPOINT, "generic") not in node_map
    assert (ComponentType.API_ENDPOINT, "real") in node_map
    # Generic node's evidence is preserved by folding into the surviving node.
    assert len(node_map[(ComponentType.API_ENDPOINT, "real")].evidence) == 2


def test_unique_generic_endpoint_survives_when_no_real_counterpart() -> None:
    generic = _generic_acc("GET", "/status", "server/misc.py")
    real = _real_acc("POST", "/api/rag/re-embed", "server/api/rag.py")
    node_map = {
        (ComponentType.API_ENDPOINT, "generic"): generic,
        (ComponentType.API_ENDPOINT, "real"): real,
    }

    _dedup_generic_endpoints(node_map)

    assert (ComponentType.API_ENDPOINT, "generic") in node_map
    assert (ComponentType.API_ENDPOINT, "real") in node_map


def test_ambiguous_suffix_match_is_left_alone() -> None:
    generic = _generic_acc("GET", "/list", "server/misc.py")
    real_x = _real_acc("GET", "/api/x/list", "server/x.py")
    real_y = _real_acc("GET", "/api/y/list", "server/y.py")
    node_map = {
        (ComponentType.API_ENDPOINT, "generic"): generic,
        (ComponentType.API_ENDPOINT, "real_x"): real_x,
        (ComponentType.API_ENDPOINT, "real_y"): real_y,
    }

    _dedup_generic_endpoints(node_map)

    # Ambiguous — nothing dropped, nothing merged into the wrong bucket.
    assert len(node_map) == 3
    assert len(node_map[(ComponentType.API_ENDPOINT, "real_x")].evidence) == 1
    assert len(node_map[(ComponentType.API_ENDPOINT, "real_y")].evidence) == 1


def test_method_mismatch_prevents_merge() -> None:
    generic = _generic_acc("DELETE", "/re-embed", "server/api/rag.py")
    real = _real_acc("POST", "/api/rag/re-embed", "server/api/rag.py")
    node_map = {
        (ComponentType.API_ENDPOINT, "generic"): generic,
        (ComponentType.API_ENDPOINT, "real"): real,
    }

    _dedup_generic_endpoints(node_map)

    assert (ComponentType.API_ENDPOINT, "generic") in node_map
