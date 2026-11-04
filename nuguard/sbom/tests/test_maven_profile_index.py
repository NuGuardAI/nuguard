"""PR #661: profile matching must not repeat interpolation per dependency pair."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nuguard.analysis import osv_client
from nuguard.sbom import java_dependencies as maven
from nuguard.sbom.deps import DependencyScanner


def _dep(
    artifact: str = "client",
    version: str = "1.2.3",
    *,
    group: str = "example",
    kind: str = "",
    classifier: str = "",
    scope: str = "",
) -> str:
    fields = {
        "groupId": group,
        "artifactId": artifact,
        "version": version,
        "type": kind,
        "classifier": classifier,
        "scope": scope,
    }
    return (
        "<dependency>"
        + "".join(f"<{key}>{value}</{key}>" for key, value in fields.items() if value)
        + "</dependency>"
    )


def _management(entries: str) -> str:
    return f"<dependencyManagement><dependencies>{entries}</dependencies></dependencyManagement>"


def _profile(entries: str = "", managed: str = "", name: str = "test") -> str:
    return (
        f"<profile><id>{name}</id><dependencies>{entries}</dependencies>"
        f"{_management(managed)}</profile>"
    )


def _pom(
    profiles: str,
    *,
    direct: str = "",
    managed: str = "",
    properties: str = "",
    parent: str = "",
) -> str:
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        f"{parent}<groupId>demo</groupId><artifactId>app</artifactId><version>1</version>"
        f"<properties>{properties}</properties>{_management(managed)}"
        f"<dependencies>{direct}</dependencies><profiles>{profiles}</profiles></project>"
    )


def _scan(root: Path, content: str) -> list[dict[str, str]]:
    (root / "pom.xml").write_text(content, encoding="utf-8")
    return maven.scan_java_dependencies(root)


@pytest.mark.parametrize("layout", ["profile", "root_and_profile", "same_identity", "managed"])
@pytest.mark.parametrize("property_coordinates", [False, True])
def test_concrete_profile_lookup_work_scales_linearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    layout: str,
    property_coordinates: bool,
) -> None:
    original_identity = maven._maven_identity
    original_resolve = maven._resolve
    counts: list[dict[str, int]] = []
    for size in (32, 64, 128):
        count = {"identity": 0, "resolve": 0}

        def identity(element: ET.Element, properties: dict[str, str]) -> tuple[str, str, str, str]:
            count["identity"] += 1
            return original_identity(element, properties)

        def resolve(value: str, properties: dict[str, str]) -> str:
            count["resolve"] += 1
            return original_resolve(value, properties)

        group = "${group}" if property_coordinates else "example"
        names = ["client" if layout == "same_identity" else f"client{i}" for i in range(size)]
        entries = "".join(_dep(name, group=group) for name in names)
        direct = entries if layout == "root_and_profile" else ""
        managed = ""
        profiles = _profile(entries)
        if layout == "managed":
            direct = "".join(_dep(name, "", group=group) for name in names)
            managed = entries
            profiles = _profile(
                managed="".join(_dep(f"other{i}", group=group) for i in range(size))
            )
        with monkeypatch.context() as scoped:
            scoped.setattr(maven, "_maven_identity", identity)
            scoped.setattr(maven, "_resolve", resolve)
            result = _scan(
                tmp_path,
                _pom(
                    profiles,
                    direct=direct,
                    managed=managed,
                    properties="<group>example</group>",
                ),
            )
        assert len(result) == (1 if layout == "same_identity" else size)
        assert all(item["version_spec"] == "==1.2.3" for item in result)
        # Count operations, not wall-clock timings. Include duplicate identities
        # as well as distinct artifacts: a per-identity list scan is not sufficient.
        assert count["identity"] <= 4 * size + 8, count
        assert count["resolve"] <= 24 * size + 20, count
        counts.append(count)
    for previous, current in zip(counts, counts[1:]):
        for name in ("identity", "resolve"):
            assert current[name] <= 2 * previous[name] + 20


@pytest.mark.parametrize("size", [256, 512])
@pytest.mark.parametrize("all_fields", [False, True])
@pytest.mark.parametrize("profile_count", [1, 8])
def test_property_resolved_profile_coordinates_keep_every_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    all_fields: bool,
    profile_count: int,
) -> None:
    budgets: list[Any] = []
    original = maven._maven_properties

    def capture(project: ET.Element) -> dict[str, str]:
        properties = original(project)
        assert isinstance(properties, maven._PropertyValues)
        budgets.append(properties.budget)
        return properties

    monkeypatch.setattr(maven, "_maven_properties", capture)
    # Use the shipped budget, not a larger allowance that hides quadratic work.
    assert maven._MAX_PROPERTY_REFERENCES == 50_000
    assert maven._MAX_PROPERTY_WORK == 1_000_000
    profiles = "".join(
        _profile(
            "".join(
                _dep(
                    f"client{i}",
                    "${version}" if all_fields else "1.2.3",
                    group="${group}",
                    kind="${kind}" if all_fields else "",
                    scope="${scope}" if all_fields else "",
                )
                for i in range(part, size, profile_count)
            ),
            name=f"p{part}",
        )
        for part in range(profile_count)
    )
    records = _scan(
        tmp_path,
        _pom(
            profiles,
            properties=(
                "<group>example</group><version>${major}.2.3</version><major>1</major>"
                "<kind>jar</kind><scope>runtime</scope>"
            ),
        ),
    )
    assert {item["purl"] for item in records} == {
        f"pkg:maven/example/client{i}@1.2.3" for i in range(size)
    }
    assert all(item["version_spec"] == "==1.2.3" for item in records)
    assert len(budgets) == 1
    assert budgets[0].references > maven._MAX_PROPERTY_REFERENCES // 2
    assert budgets[0].characters > maven._MAX_PROPERTY_WORK // 2


def test_all_256_profile_versions_reach_the_osv_request(tmp_path: Path) -> None:
    entries = "".join(_dep(f"client{i}", group="${group}") for i in range(256))
    _scan(tmp_path, _pom(_profile(entries), properties="<group>example</group>"))
    dependencies = DependencyScanner().scan(tmp_path)
    with (
        patch.object(osv_client, "_post_json", return_value={"results": []}) as post,
        patch.object(osv_client, "_get_json") as get,
    ):
        assert osv_client.query_osv([item.model_dump(mode="json") for item in dependencies]) == []
    get.assert_not_called()
    sent = [
        query["package"]["purl"]
        for call in post.call_args_list
        for query in call.args[1]["queries"]
    ]
    assert len(sent) == 256
    assert set(sent) == {f"pkg:maven/example/client{i}@1.2.3" for i in range(256)}


@pytest.mark.parametrize("field", ["group", "artifact", "kind", "classifier"])
@pytest.mark.parametrize("is_managed", [False, True])
@pytest.mark.parametrize("matching", [False, True])
def test_symbolic_profile_candidates_remain_conservative_and_scoped(
    tmp_path: Path,
    field: str,
    is_managed: bool,
    matching: bool,
) -> None:
    kwargs = {"artifact": "client", "group": "example", "kind": "jar", "classifier": ""}
    kwargs[field] = "${unknown}"
    if not matching:
        kwargs["group" if field == "artifact" else "artifact"] = "unrelated"
    candidate = _dep(**kwargs)
    profile = _profile(managed=candidate) if is_managed else _profile(candidate)
    records = _scan(tmp_path, _pom(profile, direct=_dep(version=""), managed=_dep()))
    current = next(
        record
        for record in records
        if record["name"] == "example:client" and "?" not in record["purl"]
    )
    assert current["purl"] == "pkg:maven/example/client" + ("" if matching else "@1.2.3")


@pytest.mark.parametrize("source", ["local", "parent", "bom"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
def test_cached_implicit_versions_keep_precedence_and_order_independence(
    tmp_path: Path,
    source: str,
    reverse: bool,
    conflict: bool,
) -> None:
    group = "example" if source == "local" else "org.springframework.boot"
    artifact = "client" if source == "local" else "spring-boot-starter-web"
    managed = _dep(artifact, group=group) if source == "local" else ""
    parent = ""
    if source == "parent":
        parent = (
            "<parent><groupId>org.springframework.boot</groupId>"
            "<artifactId>spring-boot-starter-parent</artifactId><version>1.2.3</version></parent>"
        )
    if source == "bom":
        managed = _dep("spring-boot-dependencies", group=group, kind="pom", scope="import")
    entries = [
        _dep(artifact, "", group=group),
        _dep(artifact, "2.0.0" if conflict else "1.2.3", group=group),
    ]
    if reverse:
        entries.reverse()
    # Unrelated profile entries must not consume another declaration's budget.
    distractors = "".join(_dep(f"other{i}", group="${group}") for i in range(256))
    profiles = _profile(distractors) + "".join(
        _profile(entry, name=f"p{i}") for i, entry in enumerate(entries)
    )
    records = _scan(
        tmp_path,
        _pom(
            profiles,
            managed=managed,
            parent=parent,
            properties="<group>other</group>",
        ),
    )
    record = next(record for record in records if record["name"] == f"{group}:{artifact}")
    assert record["version_spec"] == ("" if conflict else "==1.2.3")
    assert record["purl"] == f"pkg:maven/{group}/{artifact}" + ("" if conflict else "@1.2.3")


def test_uncertainty_matching_does_not_interpolate_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = maven._profile_version_uncertain

    def no_resolution(*args: Any, **kwargs: Any) -> bool:
        def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("profile matching must only read pre-resolved facts")

        with monkeypatch.context() as scoped:
            scoped.setattr(maven, "_resolve", forbidden)
            scoped.setattr(maven, "_maven_identity", forbidden)
            scoped.setattr(maven, "_maven_declared_version", forbidden)
            return original(*args, **kwargs)

    monkeypatch.setattr(maven, "_profile_version_uncertain", no_resolution)
    profiles = _profile(_dep(), managed=_dep(group="${unknown}")) + _profile(
        managed=_dep("bom", group="${unknown}", kind="pom", scope="import")
    )
    records = _scan(
        tmp_path,
        _pom(
            profiles,
            direct=_dep("client", "", group="${group}"),
            managed=_dep(),
            properties="<group>example</group>",
        ),
    )
    assert any(record["name"] == "example:client" for record in records)


def test_resolved_facts_are_not_reused_between_manifests(tmp_path: Path) -> None:
    for index, version in enumerate(("1.2.3", "2.3.4")):
        root = tmp_path / str(index)
        root.mkdir()
        records = _scan(
            root,
            _pom(
                _profile(_dep(version="${version}", group="${group}")),
                properties=f"<version>{version}</version><group>example</group>",
            ),
        )
        assert [item["purl"] for item in records] == [f"pkg:maven/example/client@{version}"]


def test_actual_budget_exhaustion_remains_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(maven, "_MAX_PROPERTY_REFERENCES", 1)
    records = _scan(
        tmp_path,
        _pom(
            _profile(_dep("first", group="${group}") + _dep("second", group="${group}")),
            properties="<group>example</group>",
        ),
    )
    assert any(item["purl"] == "pkg:maven/example/first@1.2.3" for item in records)
    unresolved = next(item for item in records if item["name"].endswith(":second"))
    assert "@" not in unresolved["purl"]
    assert unresolved["version_spec"] == ""
