"""Tests for version-aware OSV querying in the analysis osv_client.

An unversioned querybatch returns advisories for every release of a package,
so a dependency constrained to ``>=1.3.3`` was reported for CVEs fixed before
1.3.3. These tests pin the version each query is sent at.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from nuguard.analysis import osv_client


@pytest.mark.parametrize(
    ("version_spec", "expected"),
    [
        ("==1.2.3", "1.2.3"),
        (">=1.3.3", "1.3.3"),
        (">=1.0,<2.0", "1.0"),
        ("<2,>=1.5", "1.5"),
        ("~=2.1", "2.1"),
        ("^15.2.4", "15.2.4"),
        ("~0.9", "0.9"),
        ("<2", ""),
        ("!=1.0", ""),
        ("", ""),
    ],
)
def test_query_version_is_lowest_admitted_version(version_spec: str, expected: str) -> None:
    dep = {"purl": "pkg:pypi/x", "version_spec": version_spec}
    assert osv_client._query_version(dep) == expected


def test_resolved_version_wins_over_specifier() -> None:
    dep = {"purl": "pkg:pypi/x", "version": "2.0.1", "version_spec": ">=1.0"}
    assert osv_client._query_version(dep) == "2.0.1"


def test_purl_version_wins_over_specifier() -> None:
    dep = {"purl": "pkg:pypi/x@3.1", "version_spec": ">=1.0"}
    assert osv_client._query_version(dep) == "3.1"


def test_query_purl_appends_version_before_qualifiers() -> None:
    dep = {"purl": "pkg:pypi/x?repository_url=h", "version_spec": ">=1.3.3"}
    assert osv_client._query_purl(dep) == "pkg:pypi/x@1.3.3?repository_url=h"


def test_query_purl_keeps_existing_purl_version() -> None:
    dep = {"purl": "pkg:pypi/x@9.9", "version_spec": ">=1.3.3"}
    assert osv_client._query_purl(dep) == "pkg:pypi/x@9.9"


def test_query_purl_unchanged_without_any_version() -> None:
    dep = {"purl": "pkg:pypi/x", "version_spec": "<2"}
    assert osv_client._query_purl(dep) == "pkg:pypi/x"


def test_query_osv_sends_versioned_purl_in_batch() -> None:
    deps = [{"purl": "pkg:pypi/langchain-core", "name": "langchain-core", "version_spec": ">=1.3.3"}]
    captured: dict[str, Any] = {}

    def _fake_post(url: str, body: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
        captured["body"] = body
        return {"results": [{"vulns": []}]}

    with patch.object(osv_client, "_post_json", side_effect=_fake_post):
        findings = osv_client.query_osv(deps)

    assert findings == []
    assert captured["body"] == {
        "queries": [{"package": {"purl": "pkg:pypi/langchain-core@1.3.3"}}]
    }
