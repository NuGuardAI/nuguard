"""Tests for nuguard.redteam.public_api._catalog_coverage_to_dict.

CoverageReport (nuguard/redteam/catalog/coverage.py) is a dataclass with two
Enum-typed list fields; dataclasses.asdict() leaves Enum members as-is rather
than converting them to plain strings, so this helper normalizes them
separately before the result can be embedded in the JSON-safe RedteamRunResult.
"""
from __future__ import annotations

import json

from nuguard.redteam.catalog.coverage import CoverageReport
from nuguard.redteam.catalog.taxonomy import Capability, ScenarioCategory
from nuguard.redteam.public_api import _catalog_coverage_to_dict


def test_catalog_coverage_to_dict_returns_none_for_none():
    assert _catalog_coverage_to_dict(None) is None


def test_catalog_coverage_to_dict_normalizes_enums_to_plain_strings():
    report = CoverageReport(
        profile="ci",
        total_generated=5,
        categories_covered=[ScenarioCategory.DATA_EXFILTRATION, ScenarioCategory.JAILBREAK],
        per_category_count={"Data Exfiltration": 3, "Jailbreak and Policy Bypass": 2},
        skipped=[("cat-1", "Data Exfiltration", "capped")],
        capabilities_detected=[Capability.DATASTORE_PII, Capability.WEB_FETCH],
    )

    d = _catalog_coverage_to_dict(report)

    assert d is not None
    assert d["categories_covered"] == ["Data Exfiltration", "Jailbreak and Policy Bypass"]
    assert d["capabilities_detected"] == ["datastore_pii", "web_fetch"]
    assert all(isinstance(c, str) for c in d["categories_covered"])
    assert all(isinstance(c, str) for c in d["capabilities_detected"])
    # Must be fully JSON-safe.
    json.dumps(d)


def test_catalog_coverage_to_dict_preserves_scalar_fields():
    report = CoverageReport(profile="standard", total_generated=10)
    d = _catalog_coverage_to_dict(report)
    assert d is not None
    assert d["profile"] == "standard"
    assert d["total_generated"] == 10
    assert d["categories_covered"] == []
    assert d["capabilities_detected"] == []
