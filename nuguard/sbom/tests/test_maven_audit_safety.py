"""PR #661 audit: bounded expansion, Maven variants and unresolved selectors."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from itertools import permutations
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest

from nuguard.analysis import osv_client
from nuguard.sbom import java_dependencies as maven
from nuguard.sbom.deps import DependencyScanner, PackageDep


def _dependency(
    version: str = "",
    *,
    name: str = "example:client",
    kind: str = "",
    classifier: str = "",
) -> str:
    group, artifact = name.split(":", 1)
    return (
        f"<dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId>"
        + (f"<version>{version}</version>" if version else "")
        + (f"<type>{kind}</type>" if kind else "")
        + (f"<classifier>{classifier}</classifier>" if classifier else "")
        + "</dependency>"
    )


def _management(entries: str) -> str:
    return f"<dependencyManagement><dependencies>{entries}</dependencies></dependencyManagement>"


def _profiles(*bodies: str) -> str:
    return (
        "<profiles>"
        + "".join(
            f"<profile><id>p{index}</id>{body}</profile>" for index, body in enumerate(bodies)
        )
        + "</profiles>"
    )


def _pom(declarations: str = "", context: str = "") -> str:
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<groupId>demo</groupId><artifactId>app</artifactId><version>1</version>"
        + context
        + f"<dependencies>{declarations}</dependencies></project>"
    )


def _scan(root: Path, declarations: str = "", context: str = "") -> list[PackageDep]:
    (root / "pom.xml").write_text(_pom(declarations, context), encoding="utf-8")
    dependencies = DependencyScanner().scan(root)
    # The existing public boundary stays JSON-safe with unchanged fields.
    for dependency in dependencies:
        assert PackageDep.model_validate(dependency.model_dump(mode="json")) == dependency
    return dependencies


def _osv_queries(dependencies: list[PackageDep]) -> list[dict[str, Any]]:
    with (
        patch.object(osv_client, "_post_json", return_value={"results": []}) as post,
        patch.object(osv_client, "_get_json") as get,
    ):
        assert osv_client.query_osv([dep.model_dump(mode="json") for dep in dependencies]) == []
    get.assert_not_called()
    if not post.called:
        return []
    post.assert_called_once()
    queries: list[dict[str, Any]] = post.call_args.args[1]["queries"]
    return queries


# A failed implementation must not exhaust the pytest worker's memory.
_LIMITED_SCAN = r"""
import importlib.util, json, resource, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("scanner_under_test", sys.argv[1])
assert spec and spec.loader
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)
resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
try:
    records = scanner.scan_java_dependencies(Path(sys.argv[2]))
    print(json.dumps({"status": "returned", "records": records}))
except MemoryError:
    print(json.dumps({"status": "memory_exhausted"}))
"""


@pytest.mark.skipif(
    sys.platform != "linux" or importlib.util.find_spec("resource") is None,
    reason="requires Linux address-space and CPU limits",
)
@pytest.mark.parametrize(
    "properties",
    [
        "<v>${v}${v}</v>",
        "<v>${w}${w}</v><w>${v}${v}</w>",
        "".join(f"<v{i}>${{v{i + 1}}}${{v{i + 1}}}</v{i}>" for i in range(22))
        + "<v22>1</v22><v>${v0}</v>",
    ],
    ids=["self-cycle", "mutual-cycle", "acyclic-expansion"],
)
def test_expanding_properties_are_bounded_in_an_isolated_process(
    tmp_path: Path,
    properties: str,
) -> None:
    (tmp_path / "pom.xml").write_text(
        _pom(
            _dependency("${v}") + _dependency("2.3.4", name="example:healthy"),
            "<properties>" + properties + "</properties>",
        ),
        encoding="utf-8",
    )
    other = tmp_path / "other"
    other.mkdir()
    (other / "pom.xml").write_text(
        _pom(_dependency("3.4.5", name="example:other")),
        encoding="utf-8",
    )
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            _LIMITED_SCAN,
            str(Path(maven.__file__).resolve()),
            str(tmp_path),
        ],
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    observed = json.loads(process.stdout)
    assert observed["status"] == "returned"
    by_name = {record["name"]: record for record in observed["records"]}
    assert by_name["example:client"]["purl"] == "pkg:maven/example/client"
    assert len(by_name["example:client"]["version_spec"]) <= 128
    assert by_name["example:healthy"]["purl"].endswith("@2.3.4")
    assert by_name["example:other"]["purl"].endswith("@3.4.5")


@pytest.mark.parametrize("reverse", [False, True])
def test_valid_nested_properties_do_not_depend_on_xml_order(tmp_path: Path, reverse: bool) -> None:
    properties = ["<major>1</major>", "<minor>2</minor>", "<v>${major}.${minor}.3</v>"]
    if reverse:
        properties.reverse()
    result = _scan(
        tmp_path, _dependency("${v}"), "<properties>" + "".join(properties) + "</properties>"
    )
    assert result[0].purl == "pkg:maven/example/client@1.2.3"


@pytest.mark.parametrize("limit", ["_MAX_PROPERTY_WORK", "_MAX_PROPERTY_REFERENCES"])
def test_work_budget_is_shared_by_one_manifest_and_reset_for_another(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    monkeypatch.setattr(maven, limit, 24 if limit == "_MAX_PROPERTY_WORK" else 2)
    properties = maven._PropertyValues({"v": "1.2.3"})
    results = [maven._resolve("${v}", properties) for _ in range(8)]
    assert results[0] == "1.2.3"
    assert results[-1] == "${v}"
    assert maven._resolve("2.0.0", properties) == "2.0.0"
    assert maven._resolve("${v}", maven._PropertyValues({"v": "1.2.3"})) == "1.2.3"
    # Real manifest loading also creates a separate budget for each file.
    for index in range(2):
        module = tmp_path / str(index)
        module.mkdir()
        (module / "pom.xml").write_text(
            _pom(
                _dependency("${v}", name=f"example:module{index}"),
                "<properties><v>1.2.3</v></properties>",
            ),
            encoding="utf-8",
        )
    records = maven.scan_java_dependencies(tmp_path)
    assert len(records) == 2
    # Low budgets may prevent subsequent profile checks from reconfirming a pin;
    # either way the second manifest has exactly the same independent allowance.
    assert records[0]["version_spec"] == records[1]["version_spec"]


@pytest.mark.parametrize("limit", ["_MAX_PROPERTY_VALUE_CHARS", "_MAX_PROPERTY_DEPTH"])
def test_per_value_limits_leave_the_original_expression_unresolved(
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    monkeypatch.setattr(maven, limit, 8 if limit == "_MAX_PROPERTY_VALUE_CHARS" else 2)
    properties = {"v": "${a}${a}", "a": "${b}${b}", "b": "${c}", "c": "1.2.3"}
    assert maven._resolve("${v}", properties) == "${v}"
    assert maven._resolve("1.2.3", properties) == "1.2.3"


def test_oversized_literal_cannot_become_a_false_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maven, "_MAX_PROPERTY_VALUE_CHARS", 16)
    value = maven._resolve("1" * 17, {})
    assert maven._concrete_version(value) is None


@pytest.mark.parametrize("route", ["explicit", "managed", "parent", "bom"])
def test_unresolved_version_never_falls_through_to_an_unrelated_release(
    tmp_path: Path,
    route: str,
) -> None:
    name = "org.springframework.boot:spring-boot-starter-web"
    props = "<properties><v>${v}</v></properties>"
    parent = (
        "<parent><groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-parent</artifactId><version>3.4.2</version></parent>"
    )
    bom = _dependency("3.3.5", name="org.springframework.boot:spring-boot-dependencies", kind="pom")
    bom = bom.replace("</dependency>", "<scope>import</scope></dependency>")
    declaration = _dependency(name=name)
    if route == "explicit":
        declaration = _dependency("${v}", name=name)
        context = parent + _management(_dependency("3.2.9", name=name))
    elif route == "managed":
        context = parent + _management(_dependency("${v}", name=name))
    elif route == "parent":
        context = parent.replace("3.4.2", "${v}") + _management(bom)
    else:
        context = _management(bom.replace("3.3.5", "${v}"))
    result = _scan(tmp_path, declaration, props + context)
    assert result[0].purl == "pkg:maven/" + name.replace(":", "/")
    assert _osv_queries(result) == []


@pytest.mark.parametrize("kind,classifier", [("", "tests"), ("war", ""), ("test-jar", "")])
@pytest.mark.parametrize("reverse", [False, True])
def test_variant_management_is_not_used_for_the_default_jar(
    tmp_path: Path,
    kind: str,
    classifier: str,
    reverse: bool,
) -> None:
    entries = [_dependency("1.2.3"), _dependency("9.9.9", kind=kind, classifier=classifier)]
    if reverse:
        entries.reverse()
    result = _scan(tmp_path, _dependency(), _management("".join(entries)))
    assert len(result) == 1 and result[0].purl == "pkg:maven/example/client@1.2.3"
    assert _osv_queries(result) == [{"package": {"purl": result[0].purl}}]


@pytest.mark.parametrize(
    "kind,classifier,qualifier",
    [
        ("", "tests", "classifier=tests"),
        ("war", "", "type=war"),
        ("test-jar", "", "type=test-jar"),
        ("jar", "tests", "classifier=tests"),
        ("zip", "linux-x64", "classifier=linux-x64&type=zip"),
    ],
)
@pytest.mark.parametrize("managed", [False, True])
def test_variants_survive_both_scanner_deduplication_stages(
    tmp_path: Path,
    kind: str,
    classifier: str,
    qualifier: str,
    managed: bool,
) -> None:
    entries = [_dependency("1.2.3"), _dependency("9.9.9", kind=kind, classifier=classifier)]
    expected = {"pkg:maven/example/client@1.2.3", "pkg:maven/example/client@9.9.9?" + qualifier}
    for order in permutations(entries):
        if managed:
            result = _scan(
                tmp_path,
                _dependency() + _dependency(kind=kind, classifier=classifier),
                _management("".join(order)),
            )
        else:
            result = _scan(tmp_path, "".join(order))
        assert len(result) == 2
        assert {dep.purl for dep in result} == expected
        assert {query["package"]["purl"] for query in _osv_queries(result)} == expected


@pytest.mark.parametrize("section", ["direct", "managed"])
@pytest.mark.parametrize("kind,classifier", [("", "tests"), ("war", "")])
def test_other_profile_variant_does_not_invalidate_the_default_jar(
    tmp_path: Path,
    section: str,
    kind: str,
    classifier: str,
) -> None:
    variant = _dependency("9.9.9", kind=kind, classifier=classifier)
    body = (
        f"<dependencies>{variant}</dependencies>" if section == "direct" else _management(variant)
    )
    context = _management(_dependency("1.2.3")) + _profiles(body)
    result = _scan(tmp_path, _dependency(), context)
    default = next(dep for dep in result if "?" not in dep.purl)
    assert default.purl == "pkg:maven/example/client@1.2.3"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kind,classifier", [("", "tests"), ("war", "")])
def test_same_variant_conflicts_preserve_qualifiers_and_other_variants(
    tmp_path: Path,
    reverse: bool,
    kind: str,
    classifier: str,
) -> None:
    entries = [
        _dependency("1.2.3", kind=kind, classifier=classifier),
        _dependency("1.2.4", kind=kind, classifier=classifier),
    ]
    if reverse:
        entries.reverse()
    result = _scan(
        tmp_path,
        _dependency("3.4.5"),
        _profiles(*(f"<dependencies>{entry}</dependencies>" for entry in entries)),
    )
    assert len(result) == 2
    variant = next(dep for dep in result if "?" in dep.purl)
    assert variant.version_spec == "" and "@" not in variant.purl
    assert parse_qs(variant.purl.partition("?")[2]) == (
        {"classifier": [classifier]} if classifier else {"type": [kind]}
    )
    assert _osv_queries(result) == [{"package": {"purl": "pkg:maven/example/client@3.4.5"}}]


def test_same_variant_explicit_and_implicit_agreement_keeps_the_pin(tmp_path: Path) -> None:
    declaration = _dependency(kind="war", classifier="dist")
    explicit = _dependency("1.2.3", kind="war", classifier="dist")
    result = _scan(
        tmp_path,
        "",
        _management(explicit)
        + _profiles(
            f"<dependencies>{declaration}</dependencies>",
            f"<dependencies>{explicit}</dependencies>",
        ),
    )
    assert len(result) == 1
    assert result[0].purl == "pkg:maven/example/client@1.2.3?classifier=dist&type=war"


def test_property_resolved_variant_identity_and_default_jar_are_preserved(tmp_path: Path) -> None:
    context = "<properties><kind>war</kind><variant>dist</variant></properties>" + _management(
        _dependency("2.0", kind="${kind}", classifier="${variant}")
    )
    result = _scan(
        tmp_path,
        _dependency("1.0", kind="jar")
        + _dependency("1.0")
        + _dependency(kind="war", classifier="dist"),
        context,
    )
    assert {dep.purl for dep in result} == {
        "pkg:maven/example/client@1.0",
        "pkg:maven/example/client@2.0?classifier=dist&type=war",
    }


def test_profile_controlled_variant_is_not_treated_as_a_default_jar(tmp_path: Path) -> None:
    context = "<properties><kind>jar</kind></properties>" + _profiles(
        "<properties><kind>war</kind></properties>"
    )
    result = _scan(tmp_path, _dependency("1.2.3", kind="${kind}"), context)
    assert len(result) == 1 and result[0].version_spec == ""
    assert parse_qs(result[0].purl.partition("?")[2]) == {"type": ["${kind}"]}
    assert _osv_queries(result) == []


@pytest.mark.parametrize("kind,classifier", [("test-jar", ""), ("jar", "tests"), ("war", "")])
def test_boot_fallback_does_not_guess_variant_versions(
    tmp_path: Path, kind: str, classifier: str
) -> None:
    name = "org.springframework.boot:spring-boot-starter-web"
    parent = (
        "<parent><groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-parent</artifactId><version>3.4.2</version></parent>"
    )
    result = _scan(tmp_path, _dependency(name=name, kind=kind, classifier=classifier), parent)
    assert result[0].version_spec == "" and "@" not in result[0].purl


def test_purl_qualifiers_are_encoded_and_case_sensitive(tmp_path: Path) -> None:
    result = _scan(
        tmp_path,
        _dependency("1.0", classifier="Linux")
        + _dependency("2.0", classifier="linux")
        + _dependency("3.0", kind="zip", classifier="test&amp;bundle@v")
        + _dependency("4.0", name="Example:client"),
    )
    assert {dep.purl for dep in result} == {
        "pkg:maven/example/client@1.0?classifier=Linux",
        "pkg:maven/example/client@2.0?classifier=linux",
        "pkg:maven/example/client@3.0?classifier=test%26bundle%40v&type=zip",
        "pkg:maven/Example/client@4.0",
    }


@pytest.mark.parametrize("selector", ["LATEST", "RELEASE"])
@pytest.mark.parametrize("route", ["direct", "property", "managed", "parent", "bom"])
def test_dynamic_maven_versions_never_reach_osv(
    tmp_path: Path,
    selector: str,
    route: str,
) -> None:
    name = "org.springframework.boot:spring-boot-starter-web"
    context = ""
    declaration = _dependency(name=name)
    if route == "direct":
        declaration = _dependency(selector, name=name)
    elif route == "property":
        declaration = _dependency("${v}", name=name)
        context = f"<properties><v>{selector}</v></properties>"
    elif route == "managed":
        context = _management(_dependency(selector, name=name))
    elif route == "parent":
        context = (
            "<parent><groupId>org.springframework.boot</groupId>"
            "<artifactId>spring-boot-starter-parent</artifactId>"
            f"<version>{selector}</version></parent>"
        )
    else:
        bom = _dependency(
            selector, name="org.springframework.boot:spring-boot-dependencies", kind="pom"
        )
        context = _management(bom.replace("</dependency>", "<scope>import</scope></dependency>"))
    result = _scan(tmp_path, declaration + _dependency("9.8.7", name="example:healthy"), context)
    dynamic = next(dep for dep in result if dep.name == name)
    assert dynamic.version_spec == selector
    assert dynamic.version is None and "@" not in dynamic.purl
    assert _osv_queries(result) == [{"package": {"purl": "pkg:maven/example/healthy@9.8.7"}}]


@pytest.mark.parametrize("selector", ["+", "1.+", "1.0+", "latest.release", "latest.integration"])
def test_gradle_dynamic_selectors_remain_unversioned(tmp_path: Path, selector: str) -> None:
    (tmp_path / "build.gradle").write_text(
        'dependencies {\n    implementation "example:client:' + selector + '"\n}\n',
        encoding="utf-8",
    )
    result = DependencyScanner().scan(tmp_path)
    assert len(result) == 1 and result[0].version_spec == selector
    assert result[0].purl == "pkg:maven/example/client"
    assert _osv_queries(result) == []


@pytest.mark.parametrize("version", ["1.2.3", "1.0-RELEASE", "1.0+build.7", "1.0-SNAPSHOT"])
def test_literal_version_control_is_still_queryable(tmp_path: Path, version: str) -> None:
    result = _scan(tmp_path, _dependency(version))
    assert result[0].version_spec == "==" + version
    assert result[0].purl == "pkg:maven/example/client@" + version
    assert _osv_queries(result) == [{"package": {"purl": result[0].purl}}]
