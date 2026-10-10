"""Dependencies without a version must not be sent to OSV (NuGuardAI/nuguard#646).

A purl without ``@version`` makes OSV return every advisory ever published for the package name,
which showed up as a false CRITICAL (Spring4Shell) on a Spring Boot 3.4 application.
"""

from __future__ import annotations

from unittest.mock import patch

from nuguard.analysis import osv_client


def _dep(name: str, purl: str) -> dict:
    return {"purl": purl, "name": name, "version_spec": ""}


def test_purl_has_version() -> None:
    assert osv_client._purl_has_version(
        "pkg:maven/org.springframework.boot/spring-boot-starter-web@3.4.2"
    )
    assert osv_client._purl_has_version("pkg:pypi/requests@2.31.0?extra=1")
    assert osv_client._purl_has_version("pkg:npm/%40scope/name@1.0.0")
    assert osv_client._purl_has_version("pkg:npm/@scope/name@1.0.0")
    assert not osv_client._purl_has_version(
        "pkg:maven/org.springframework.boot/spring-boot-starter-web"
    )
    assert not osv_client._purl_has_version("pkg:npm/@scope/name")
    assert not osv_client._purl_has_version("pkg:pypi/requests?extra=1")


def test_unversioned_dependency_is_not_queried_and_versioned_one_is() -> None:
    deps = [
        _dep("a", "pkg:maven/g/a"),
        _dep("b", "pkg:maven/g/b@1.2.3"),
    ]
    sent: list[dict] = []

    def _fake_post(url: str, payload: dict, timeout: float = 15.0):
        sent.append(payload)
        return {"results": [{"vulns": [{"id": "GHSA-BBBB"}]}]}

    with (
        patch.object(osv_client, "_post_json", side_effect=_fake_post),
        patch.object(
            osv_client, "_get_json", return_value={"id": "GHSA-BBBB", "summary": "issue b"}
        ),
    ):
        findings = osv_client.query_osv(deps)

    assert len(sent) == 1
    assert [q["package"]["purl"] for q in sent[0]["queries"]] == ["pkg:maven/g/b@1.2.3"]
    assert {f["advisory_id"] for f in findings} == {"GHSA-BBBB"}


def test_only_unversioned_dependencies_means_no_request_at_all() -> None:
    with patch.object(osv_client, "_post_json") as post:
        findings = osv_client.query_osv([_dep("a", "pkg:maven/g/a"), _dep("b", "pkg:pypi/b")])
    assert findings == []
    post.assert_not_called()
