"""PR #661: status selectors, inherited management and indexed POM lookups."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

from nuguard.analysis import osv_client
from nuguard.sbom import java_dependencies as maven
from nuguard.sbom.deps import DependencyScanner, PackageDep

_BOOT = "org.springframework.boot:spring-boot-starter-web"
_BOM = "org.springframework.boot:spring-boot-dependencies"


def _dep(name: str = _BOOT, version: str = "", extra: str = "") -> str:
    group, artifact = name.split(":", 1)
    pin = f"<version>{version}</version>" if version else ""
    return (
        f"<dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId>"
        f"{pin}{extra}</dependency>"
    )


def _management(entries: str) -> str:
    return f"<dependencyManagement><dependencies>{entries}</dependencies></dependencyManagement>"


def _import(version: str = "3.3.5", name: str = _BOM) -> str:
    return _dep(name, version, "<type>pom</type><scope>import</scope>")


def _parent(version: str = "3.4.2", artifact: str = "spring-boot-starter-parent") -> str:
    return (
        "<parent><groupId>org.springframework.boot</groupId>"
        f"<artifactId>{artifact}</artifactId><version>{version}</version></parent>"
    )


def _profile(body: str, name: str = "test") -> str:
    return (
        f"<profile><id>{name}</id><activation><activeByDefault>true</activeByDefault>"
        f"</activation>{body}</profile>"
    )


def _pom(
    dependencies: str = "",
    *,
    parent: str = "",
    managed: str = "",
    profiles: str = "",
    properties: str = "",
) -> str:
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        f"{parent}<groupId>demo</groupId><artifactId>app</artifactId><version>1</version>"
        f"{properties}{_management(managed)}<dependencies>{dependencies}</dependencies>"
        f"<profiles>{profiles}</profiles></project>"
    )


def _scan(root: Path, pom: str) -> dict[str, dict[str, str]]:
    (root / "pom.xml").write_text(pom, encoding="utf-8")
    return {record["name"]: record for record in maven.scan_java_dependencies(root)}


def _osv_queries(dependencies: list[PackageDep]) -> list[dict[str, dict[str, str]]]:
    with (
        patch.object(osv_client, "_post_json", return_value={"results": []}) as post,
        patch.object(osv_client, "_get_json") as get,
    ):
        assert osv_client.query_osv([dep.model_dump(mode="json") for dep in dependencies]) == []
    get.assert_not_called()
    return [query for call in post.call_args_list for query in call.args[1]["queries"]]


@pytest.mark.parametrize(
    "selector",
    [
        "latest.milestone",
        "latest.company-approved",
        "latest.teamReady",
        "latest.release",
        "latest.integration",
    ],
)
@pytest.mark.parametrize(
    "route",
    [
        "groovy",
        "kotlin",
        "groovy_map",
        "kotlin_map",
        "property",
        "catalog",
        "catalog_ref",
    ],
)
def test_all_gradle_status_selectors_are_unresolved_and_not_queried(
    tmp_path: Path,
    selector: str,
    route: str,
) -> None:
    if route.startswith("catalog"):
        catalog = tmp_path / "gradle/libs.versions.toml"
        catalog.parent.mkdir()
        if route == "catalog_ref":
            content = (
                f'[versions]\nselected = "{selector}"\n[libraries]\n'
                'dynamic = { module = "example:client", version.ref = "selected" }\n'
            )
        else:
            content = f'[libraries]\ndynamic = "example:client:{selector}"\n'
        catalog.write_text(content + 'healthy = "example:healthy:9.8.7"\n', encoding="utf-8")
    else:
        expression = f'implementation "example:client:{selector}"'
        filename = "build.gradle"
        if route == "kotlin":
            filename = "build.gradle.kts"
            expression = f'implementation("example:client:{selector}")'
        elif route == "groovy_map":
            expression = f'implementation group: "example", name: "client", version: "{selector}"'
        elif route == "kotlin_map":
            filename = "build.gradle.kts"
            expression = (
                f'implementation(group = "example", name = "client", version = "{selector}")'
            )
        elif route == "property":
            (tmp_path / "gradle.properties").write_text(f"selected={selector}\n", encoding="utf-8")
            expression = 'implementation "example:client:$selected"'
        (tmp_path / filename).write_text(
            "dependencies {\n" + expression + '\nimplementation("example:healthy:9.8.7")\n}\n',
            encoding="utf-8",
        )
    records = DependencyScanner().scan(tmp_path)
    dynamic = next(dep for dep in records if dep.name == "example:client")
    assert len(records) == 2
    assert dynamic.version_spec == selector and dynamic.version is None
    assert dynamic.purl == "pkg:maven/example/client"
    assert _osv_queries(records) == [{"package": {"purl": "pkg:maven/example/healthy@9.8.7"}}]


@pytest.mark.parametrize(
    "literal",
    [
        "1.2.3",
        "1.0+build.7",
        "1.0-RELEASE",
        "latest-release",
        "1.2.3.latest.milestone",
        "milestone",
    ],
)
def test_literal_versions_are_not_confused_with_status_selectors(literal: str) -> None:
    assert maven._concrete_version(literal) == literal
    assert maven._version_spec(literal) == "==" + literal
    assert maven._purl("example", "client", literal).endswith("@" + literal)


@pytest.mark.parametrize(
    "parent_artifact", ["spring-boot-starter-parent", "spring-boot-dependencies"]
)
@pytest.mark.parametrize("property_version", [False, True])
def test_parent_management_survives_both_root_and_profile_bom_imports(
    tmp_path: Path,
    parent_artifact: str,
    property_version: bool,
) -> None:
    version = "${boot.version}" if property_version else "3.4.2"
    props = "<properties><boot.version>3.4.2</boot.version></properties>"
    for profile_import in (False, True):
        records = _scan(
            tmp_path,
            _pom(
                _dep(),
                parent=_parent(version, parent_artifact),
                properties=props,
                managed="" if profile_import else _import(),
                profiles=_profile(_management(_import())) if profile_import else "",
            ),
        )
        assert (
            records[_BOOT]["purl"]
            == "pkg:maven/org.springframework.boot/spring-boot-starter-web@3.4.2"
        )
        assert records[_BOOT]["version_spec"] == "==3.4.2"
        assert _osv_queries(DependencyScanner().scan(tmp_path)) == [
            {"package": {"purl": records[_BOOT]["purl"]}},
        ]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kind", ["managed", "direct"])
def test_parent_exemption_does_not_hide_real_profile_overrides(
    tmp_path: Path,
    kind: str,
    reverse: bool,
) -> None:
    override = (
        _management(_dep(version="3.3.5"))
        if kind == "managed"
        else f"<dependencies>{_dep(version='3.3.5')}</dependencies>"
    )
    profiles = [
        _profile(_management(_import()), "import"),
        _profile(override, "override"),
    ]
    if reverse:
        profiles.reverse()
    record = _scan(tmp_path, _pom(_dep(), parent=_parent(), profiles="".join(profiles)))[_BOOT]
    assert record["purl"] == "pkg:maven/org.springframework.boot/spring-boot-starter-web"
    assert record["version_spec"] == ""
    assert _osv_queries(DependencyScanner().scan(tmp_path)) == []


def test_direct_literal_still_wins_over_profile_management_and_imports(tmp_path: Path) -> None:
    record = _scan(
        tmp_path,
        _pom(
            _dep(version="3.2.9"),
            parent=_parent(),
            profiles=_profile(_management(_import() + _dep(version="3.3.5"))),
        ),
    )[_BOOT]
    assert record["purl"].endswith("@3.2.9")


@pytest.mark.parametrize("parent_kind", ["none", "unrecognized", "symbolic", "dynamic"])
def test_profile_imports_do_not_invent_concrete_parent_management(
    tmp_path: Path,
    parent_kind: str,
) -> None:
    parents = {
        "none": "",
        "unrecognized": _parent(artifact="not-a-boot-parent"),
        "symbolic": _parent("${unknown}"),
        "dynamic": _parent("LATEST"),
    }
    record = _scan(
        tmp_path,
        _pom(
            _dep(),
            parent=parents[parent_kind],
            managed=_import("3.4.2"),
            profiles=_profile(_management(_import("3.3.5"))),
        ),
    )[_BOOT]
    assert "@" not in record["purl"]
    assert not record["version_spec"].startswith("==")


def test_profile_controlled_parent_property_remains_unresolved(tmp_path: Path) -> None:
    record = _scan(
        tmp_path,
        _pom(
            _dep(),
            parent=_parent("${boot.version}"),
            properties="<properties><boot.version>3.4.2</boot.version></properties>",
            profiles=_profile(
                "<properties><boot.version>3.3.5</boot.version></properties>"
                + _management(_import()),
            ),
        ),
    )[_BOOT]
    assert "@" not in record["purl"]
    assert "${boot.version}" in record["version_spec"]


def test_ambiguous_profile_import_metadata_is_not_exempted_as_a_known_import(
    tmp_path: Path,
) -> None:
    candidate = _dep(version="3.3.5", extra="<type>${kind}</type><scope>${scope}</scope>")
    record = _scan(
        tmp_path,
        _pom(
            _dep(),
            parent=_parent(),
            profiles=_profile(_management(candidate)),
        ),
    )[_BOOT]
    assert "@" not in record["purl"]


@pytest.mark.parametrize("field", ["groupId", "artifactId", "type", "classifier"])
def test_indexed_unresolved_management_still_marks_possible_overrides(
    tmp_path: Path,
    field: str,
) -> None:
    entry = _dep(version="3.3.5", extra="<type>jar</type><classifier></classifier>")
    if field == "groupId":
        entry = entry.replace(
            "<groupId>org.springframework.boot</groupId>", "<groupId>${unknown}</groupId>"
        )
    elif field == "artifactId":
        entry = entry.replace(
            "<artifactId>spring-boot-starter-web</artifactId>",
            "<artifactId>${unknown}</artifactId>",
        )
    elif field == "type":
        entry = entry.replace("<type>jar</type>", "<type>${unknown}</type>")
    else:
        entry = entry.replace("<classifier></classifier>", "<classifier>${unknown}</classifier>")
    record = _scan(tmp_path, _pom(_dep(), parent=_parent(), managed=entry))[_BOOT]
    assert "@" not in record["purl"]


def test_unrelated_unresolved_management_does_not_disable_parent_version(tmp_path: Path) -> None:
    record = _scan(
        tmp_path,
        _pom(
            _dep(),
            parent=_parent(),
            managed=_dep("example:other", "1.2.3", "<type>${unknown}</type>"),
        ),
    )[_BOOT]
    assert record["purl"].endswith("@3.4.2")


@pytest.mark.parametrize("extra", ["<type>test-jar</type>", "<classifier>sources</classifier>"])
def test_parent_shortcut_is_not_extended_to_artifact_variants(tmp_path: Path, extra: str) -> None:
    record = _scan(
        tmp_path,
        _pom(
            _dep(extra=extra),
            parent=_parent(),
            profiles=_profile(_management(_import())),
        ),
    )[_BOOT]
    assert "@" not in record["purl"] and "?" in record["purl"]


@pytest.mark.parametrize("with_imports", [False, True])
def test_literal_management_identity_work_scales_linearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_imports: bool,
) -> None:
    identity = maven._maven_identity
    elements = maven._maven_dependency_elements
    counts: list[int] = []
    for size in (32, 64, 128):
        calls = {"identity": 0, "elements": 0}

        def count_identity(dep: ET.Element, props: dict[str, str]) -> tuple[str, str, str, str]:
            calls["identity"] += 1
            return identity(dep, props)

        def count_elements(project: ET.Element) -> tuple[list[ET.Element], list[ET.Element]]:
            calls["elements"] += 1
            return elements(project)

        names = [f"org.springframework.boot:spring-boot-component{i}" for i in range(size)]
        profiles = (
            "".join(
                _profile(_management(_import(version)), name)
                for name, version in (("dev", "3.3.5"), ("prod", "3.2.9"))
            )
            if with_imports
            else ""
        )
        pom = _pom(
            "".join(_dep(name) for name in names),
            managed="".join(_dep(name, "3.4.2") for name in names),
            profiles=profiles,
        )
        with monkeypatch.context() as scoped:
            scoped.setattr(maven, "_maven_identity", count_identity)
            scoped.setattr(maven, "_maven_dependency_elements", count_elements)
            records = _scan(tmp_path, pom)
        assert len(records) == size
        assert all(record["version_spec"] == "==3.4.2" for record in records.values())
        # Root/profile management lists are collected once each, not per dependency.
        assert calls["elements"] == (3 if with_imports else 1)
        assert calls["identity"] <= 10 * size + 12
        counts.append(calls["identity"])
    assert counts[1] <= 2 * counts[0] + 12
    assert counts[2] <= 2 * counts[1] + 12
