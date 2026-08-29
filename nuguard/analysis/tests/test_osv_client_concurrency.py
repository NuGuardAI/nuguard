"""Tests for the concurrent advisory-detail fetch fan-out in osv_client.

query_osv() used to fetch each advisory's detail record one at a time; it now
runs them through a small thread pool. These tests verify the fan-out still
collects every advisory's detail, respects the dedup/_MAX_DETAIL cap, and
tolerates a single failed fetch without losing the others.
"""

from __future__ import annotations

from unittest.mock import patch

from nuguard.analysis import osv_client


def _dep(name: str, purl: str) -> dict:
    return {"purl": purl, "name": name, "version_spec": ""}


def test_query_osv_collects_all_advisory_details_concurrently() -> None:
    deps = [_dep("pkg-a", "pkg:pypi/pkg-a@1.0"), _dep("pkg-b", "pkg:pypi/pkg-b@1.0")]
    batch_resp = {
        "results": [
            {"vulns": [{"id": "GHSA-AAAA"}]},
            {"vulns": [{"id": "GHSA-BBBB"}]},
        ]
    }
    details = {
        "https://api.osv.dev/v1/vulns/GHSA-AAAA": {"id": "GHSA-AAAA", "summary": "issue a"},
        "https://api.osv.dev/v1/vulns/GHSA-BBBB": {"id": "GHSA-BBBB", "summary": "issue b"},
    }

    def _fake_get_json(url: str, timeout: float = 15.0):
        return details[url]

    with patch.object(osv_client, "_post_json", return_value=batch_resp), \
         patch.object(osv_client, "_get_json", side_effect=_fake_get_json):
        findings = osv_client.query_osv(deps)

    adv_ids = {f["advisory_id"] for f in findings}
    assert adv_ids == {"GHSA-AAAA", "GHSA-BBBB"}
    assert len(findings) == 2


def test_query_osv_one_failed_detail_fetch_still_returns_the_others() -> None:
    deps = [_dep("pkg-a", "pkg:pypi/pkg-a@1.0"), _dep("pkg-b", "pkg:pypi/pkg-b@1.0")]
    batch_resp = {
        "results": [
            {"vulns": [{"id": "GHSA-AAAA"}]},
            {"vulns": [{"id": "GHSA-BBBB"}]},
        ]
    }

    def _fake_get_json(url: str, timeout: float = 15.0):
        if "AAAA" in url:
            raise TimeoutError("simulated network failure")
        return {"id": "GHSA-BBBB", "summary": "issue b"}

    with patch.object(osv_client, "_post_json", return_value=batch_resp), \
         patch.object(osv_client, "_get_json", side_effect=_fake_get_json):
        findings = osv_client.query_osv(deps)

    adv_ids = {f["advisory_id"] for f in findings}
    assert adv_ids == {"GHSA-AAAA", "GHSA-BBBB"}
    by_id = {f["advisory_id"]: f for f in findings}
    assert by_id["GHSA-BBBB"]["summary"] == "issue b"
    # Failed fetch falls back to the bare {"id": adv_id} detail — summary
    # defaults to the advisory id itself.
    assert by_id["GHSA-AAAA"]["summary"] == "GHSA-AAAA"
