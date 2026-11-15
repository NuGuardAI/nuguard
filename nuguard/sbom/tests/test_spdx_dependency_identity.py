"""Dependency identity must survive SPDX export, including Maven variants."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import pytest

from nuguard.sbom.deps import PackageDep
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.toolbox.plugins.spdx_exporter import SpdxExporter, _to_spdx3

_CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _dep(
    purl: str,
    version: str = "==1.2.3",
    *,
    name: str = "example:client",
    group: str = "runtime",
    source_file: str = "pom.xml",
) -> PackageDep:
    return PackageDep(
        name=name,
        purl=purl,
        version_spec=version,
        group=group,
        source_file=source_file,
    )


def _export(deps: list[PackageDep], *, plugin: bool = False) -> dict[str, Any]:
    document = AiSbomDocument(target="fixture", deps=deps, generated_at=_CREATED)
    if plugin:
        result = SpdxExporter().run(document.model_dump(mode="json"), {})
        assert result.status == "ok"
        assert isinstance(result.details, dict)
        return result.details
    return _to_spdx3(document)


def _packages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in payload["@graph"] if item["type"] == "software_Package"]


def _id_map(deps: list[PackageDep]) -> dict[str, str]:
    return {
        package["externalIdentifier"][0]["identifier"]: package["spdxId"]
        for package in _packages(_export(deps))
    }


@pytest.mark.parametrize("plugin", [False, True])
def test_maven_classifier_and_version_remain_separate_in_export(plugin: bool) -> None:
    dependencies = [
        _dep("pkg:maven/example/client@1.2.3"),
        _dep("pkg:maven/example/client@9.9.9?classifier=tests", "==9.9.9"),
    ]
    payload = json.loads(json.dumps(_export(dependencies, plugin=plugin)))
    packages = _packages(payload)
    by_id = {package["spdxId"]: package for package in packages}
    assert len(packages) == len(by_id) == 2
    assert {package["name"] for package in packages} == {"example:client"}
    assert {
        (package["externalIdentifier"][0]["identifier"], package["software_packageVersion"])
        for package in packages
    } == {(dep.purl, dep.version_spec) for dep in dependencies}
    sbom = next(item for item in payload["@graph"] if item["type"] == "software_Sbom")
    assert len(sbom["element"]) == 2
    assert set(sbom["element"]) == set(by_id)
    assert set(sbom["rootElement"]) <= set(by_id)
    graph_ids = [item["spdxId"] for item in payload["@graph"] if "spdxId" in item]
    assert len(graph_ids) == len(set(graph_ids))
    for package in packages:
        assert re.fullmatch(r"SPDXRef-Dep-[A-Za-z0-9.\-]+", package["spdxId"])
        assert package["creationInfo"] == "_:creationinfo"
        assert package["suppliedBy"] == "SPDXRef-Org-NuGuardAI"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("pkg:maven/example/client@1.2.3", "pkg:maven/example/client@9.9.9"),
        ("pkg:maven/example/client@1.2.3", "pkg:maven/example/client@1.2.3?classifier=tests"),
        ("pkg:maven/example/client@1.2.3?type=jar", "pkg:maven/example/client@1.2.3?type=war"),
        (
            "pkg:maven/example/client?classifier=tests",
            "pkg:maven/example/client?classifier=sources",
        ),
        (
            "pkg:maven/example/client@1.2.3?classifier=a_b",
            "pkg:maven/example/client@1.2.3?classifier=a-b",
        ),
        ("pkg:pypi/client@1.2.3", "pkg:npm/client@1.2.3"),
        ("pkg:maven/example/client@1.2.3#first", "pkg:maven/example/client@1.2.3#second"),
    ],
)
def test_full_purl_distinguishes_packages_with_the_same_display_name(left: str, right: str) -> None:
    packages = _packages(_export([_dep(left), _dep(right)]))
    assert len({package["spdxId"] for package in packages}) == 2
    assert {package["externalIdentifier"][0]["identifier"] for package in packages} == {left, right}


def test_sanitized_names_cannot_collapse_different_purls() -> None:
    packages = _packages(
        _export(
            [
                _dep("pkg:maven/example/client@1.2.3", name="example:client"),
                _dep("pkg:pypi/example-client@1.2.3", name="example-client"),
            ]
        )
    )
    assert len({package["spdxId"] for package in packages}) == 2


def test_dependency_ids_are_stable_across_order_and_unrelated_insertions() -> None:
    dependencies = [
        _dep("pkg:maven/example/client@1.2.3"),
        _dep("pkg:maven/example/client@9.9.9?classifier=tests", "==9.9.9"),
    ]
    expected = _id_map(dependencies)
    assert len(set(expected.values())) == 2
    assert _id_map(dependencies) == expected
    assert _id_map(list(reversed(dependencies))) == expected
    expanded = _id_map([_dep("pkg:pypi/unrelated@1.0", name="unrelated"), *dependencies])
    assert {purl: expanded[purl] for purl in expected} == expected


def test_source_location_scope_and_creation_time_do_not_change_package_identity() -> None:
    dependency = _dep("pkg:maven/example/client@1.2.3?classifier=tests")
    moved = dependency.model_copy(update={"source_file": "module/pom.xml", "group": "test"})
    assert _id_map([dependency]) == _id_map([moved])
    document = AiSbomDocument(
        target="another-project",
        deps=[dependency],
        generated_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    exported = _packages(_to_spdx3(document))[0]
    assert exported["spdxId"] == _id_map([dependency])[dependency.purl]


@pytest.mark.parametrize(
    "dependencies",
    [
        [_dep("", "==1.2.3"), _dep("", "==9.9.9")],
        [_dep("", name="example:client"), _dep("", name="example-client")],
    ],
)
def test_empty_purls_have_a_deterministic_name_and_specifier_fallback(
    dependencies: list[PackageDep],
) -> None:
    first = _packages(_export(dependencies))
    second = _packages(_export(list(reversed(dependencies))))
    assert len({item["spdxId"] for item in first}) == len(dependencies)
    assert {item["spdxId"] for item in first} == {item["spdxId"] for item in second}
    assert all(item["externalIdentifier"][0]["identifier"] == "" for item in first)


def test_pinned_purl_identity_does_not_depend_on_redundant_specifier_spelling() -> None:
    first = _dep("pkg:maven/example/client@1.2.3", "==1.2.3")
    second = first.model_copy(update={"version_spec": "1.2.3"})
    assert _id_map([first]) == _id_map([second])


def test_dependency_model_is_not_mutated_by_export() -> None:
    dependency = _dep("pkg:maven/example/client@1.2.3?classifier=tests&type=jar")
    before = dependency.model_dump(mode="json")
    _export([dependency], plugin=True)
    assert dependency.model_dump(mode="json") == before
