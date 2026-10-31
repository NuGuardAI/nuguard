"""PR #661: implicit profile versions and order-independent POM consolidation."""

from __future__ import annotations

from itertools import permutations
from pathlib import Path
from unittest.mock import patch

import pytest

from nuguard.analysis import osv_client
from nuguard.sbom import java_dependencies as maven
from nuguard.sbom.deps import DependencyScanner, PackageDep


def _dependency(name: str, version: str = "", extra: str = "") -> str:
    group, artifact = name.split(":", 1)
    version_xml = f"<version>{version}</version>" if version else ""
    return (
        f"<dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId>"
        f"{version_xml}{extra}</dependency>"
    )


def _management(dependency: str) -> str:
    return (
        "<dependencyManagement><dependencies>"
        + dependency
        + "</dependencies></dependencyManagement>"
    )


def _context(source: str) -> tuple[str, str, str, str]:
    if source == "local":
        return (
            "example:client",
            "1.2.3",
            "1.2.4",
            _management(_dependency("example:client", "1.2.3")),
        )
    name = "org.springframework.boot:spring-boot-starter-web"
    if source == "parent":
        context = (
            "<parent><groupId>org.springframework.boot</groupId>"
            "<artifactId>spring-boot-starter-parent</artifactId>"
            "<version>3.4.2</version></parent>"
        )
    else:
        context = _management(
            _dependency(
                "org.springframework.boot:spring-boot-dependencies",
                "3.4.2",
                "<type>pom</type><scope>import</scope>",
            )
        )
    return name, "3.4.2", "3.3.5", context


def _pom(context: str, declarations: list[str], direct: str = "") -> str:
    profiles = "".join(
        f"<profile><id>p{index}</id><dependencies>{dep}</dependencies></profile>"
        for index, dep in enumerate(declarations)
    )
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0">'
        "<modelVersion>4.0.0</modelVersion>"
        + context
        + "<groupId>demo</groupId><artifactId>app</artifactId><version>1</version>"
        + f"<dependencies>{direct}</dependencies><profiles>{profiles}</profiles></project>"
    )


def _scan(root: Path, content: str, name: str) -> dict[str, str]:
    (root / "pom.xml").write_text(content, encoding="utf-8")
    records = maven.scan_java_dependencies(root)
    matched = [record for record in records if record["name"] == name]
    assert len(matched) == 1
    return matched[0]


@pytest.mark.parametrize("source", ["parent", "local", "bom"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("property_version", [False, True])
def test_explicit_and_implicit_profile_conflicts_are_unversioned(
    tmp_path: Path,
    source: str,
    reverse: bool,
    property_version: bool,
) -> None:
    name, _, alternate, context = _context(source)
    context += f"<properties><alternate>{alternate}</alternate></properties>"
    explicit = "${alternate}" if property_version else alternate
    declarations = [_dependency(name, explicit), _dependency(name)]
    if reverse:
        declarations.reverse()
    record = _scan(tmp_path, _pom(context, declarations), name)
    assert record["version_spec"] == ""
    assert record["purl"] == "pkg:maven/" + name.replace(":", "/")
    assert record["source_file"] == "pom.xml"


@pytest.mark.parametrize("source", ["parent", "local", "bom"])
@pytest.mark.parametrize("reverse", [False, True])
def test_agreeing_explicit_and_implicit_profile_versions_remain_queryable(
    tmp_path: Path,
    source: str,
    reverse: bool,
) -> None:
    name, version, _, context = _context(source)
    declarations = [_dependency(name, version), _dependency(name)]
    if reverse:
        declarations.reverse()
    record = _scan(tmp_path, _pom(context, declarations), name)
    assert record["version_spec"] == f"=={version}"
    assert record["purl"].endswith("@" + version)


@pytest.mark.parametrize("reverse", [False, True])
def test_unresolved_implicit_competitor_does_not_preserve_a_literal_pin(
    tmp_path: Path,
    reverse: bool,
) -> None:
    name = "example:client"
    declarations = [_dependency(name, "1.2.4"), _dependency(name)]
    if reverse:
        declarations.reverse()
    record = _scan(tmp_path, _pom("", declarations), name)
    assert record["purl"] == "pkg:maven/example/client"
    assert record["version_spec"] == ""


@pytest.mark.parametrize("source", ["parent", "local", "bom"])
def test_root_literal_and_profile_implicit_conflict_is_consolidated(
    tmp_path: Path, source: str
) -> None:
    name, _, alternate, context = _context(source)
    pom = _pom(context, [_dependency(name)], direct=_dependency(name, alternate))
    record = _scan(tmp_path, pom, name)
    assert record["version_spec"] == ""
    assert "@" not in record["purl"]


def test_identical_literal_profile_declarations_do_not_lose_the_version(tmp_path: Path) -> None:
    name = "example:client"
    record = _scan(tmp_path, _pom("", [_dependency(name, "1.2.3")] * 2), name)
    assert record["purl"] == "pkg:maven/example/client@1.2.3"
    assert record["version_spec"] == "==1.2.3"


@pytest.mark.parametrize(
    "versions,expected",
    [
        (("1.2.3", "1.2.4", "1.2.3"), ""),
        (("1.2.3", "", "1.2.3"), ""),
        (("1.2.3", "${unknown}", "1.2.3"), ""),
        (("1.2.3", "[1.2,2)", "1.2.3"), ""),
        (("1.2.3", "1.2.3", "1.2.3"), "==1.2.3"),
    ],
)
def test_record_consolidation_is_independently_conflict_aware(
    versions: tuple[str, str, str],
    expected: str,
) -> None:
    records = [
        maven._record("example", "client", version, "runtime", "pom.xml") for version in versions
    ]
    assert all(record is not None for record in records)
    concrete_records = [record for record in records if record is not None]
    original = [dict(record) for record in concrete_records]
    for order in permutations(concrete_records):
        collapsed = maven._collapse_maven_declarations(list(order))
        assert len(collapsed) == 1
        assert collapsed[0]["version_spec"] == expected
        assert collapsed[0]["purl"] == (
            "pkg:maven/example/client@1.2.3" if expected else "pkg:maven/example/client"
        )
        dep = PackageDep(**collapsed[0])
        assert PackageDep.model_validate(dep.model_dump(mode="json")) == dep
    assert concrete_records == original


def test_profile_record_reduction_does_not_merge_independent_manifests() -> None:
    records = [
        maven._record("example", "client", "1.2.3", "runtime", "module-a/pom.xml"),
        maven._record("example", "client", "1.2.4", "runtime", "module-b/pom.xml"),
    ]
    concrete_records = [record for record in records if record is not None]
    assert maven._collapse_maven_declarations(concrete_records) == concrete_records


def test_conflicting_record_scope_is_deterministic() -> None:
    records = [
        maven._record("example", "client", "1.2.3", "dev", "pom.xml"),
        maven._record("example", "client", "1.2.4", "runtime", "pom.xml"),
    ]
    concrete_records = [record for record in records if record is not None]
    expected = maven._collapse_maven_declarations(concrete_records)
    assert expected[0]["group"] == "runtime"
    assert expected[0]["version_spec"] == ""
    assert maven._collapse_maven_declarations(list(reversed(concrete_records))) == expected


@pytest.mark.parametrize("source", ["parent", "local", "bom"])
@pytest.mark.parametrize("reverse", [False, True])
def test_osv_skips_implicit_profile_conflicts_but_queries_unaffected_dependencies(
    tmp_path: Path,
    source: str,
    reverse: bool,
) -> None:
    name, _, alternate, context = _context(source)
    declarations = [_dependency(name, alternate), _dependency(name)]
    if reverse:
        declarations.reverse()
    content = _pom(context, declarations, direct=_dependency("example:unaffected", "9.8.7"))
    (tmp_path / "pom.xml").write_text(content, encoding="utf-8")
    deps = [dep.model_dump(mode="json") for dep in DependencyScanner().scan(tmp_path)]
    assert len(deps) == 2
    with (
        patch.object(osv_client, "_post_json", return_value={"results": []}) as post,
        patch.object(osv_client, "_get_json") as get,
    ):
        assert osv_client.query_osv(deps) == []
    post.assert_called_once()
    assert post.call_args.args[1] == {
        "queries": [{"package": {"purl": "pkg:maven/example/unaffected@9.8.7"}}],
    }
    get.assert_not_called()
