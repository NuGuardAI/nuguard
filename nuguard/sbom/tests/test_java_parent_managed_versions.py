"""Spring Boot managed versions in Maven poms (NuGuardAI/nuguard#646).

``org.springframework.boot`` starters normally carry no version of their own: it comes from the
``spring-boot-starter-parent`` parent or an imported ``spring-boot-dependencies`` BOM. Without it
the SBOM purl has no version, and a vulnerability lookup matches every advisory ever published.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.java_dependencies import scan_java_dependencies

_NS = 'xmlns="http://maven.apache.org/POM/4.0.0"'


def _scan(tmp_path: Path, pom: str) -> dict[str, dict[str, str]]:
    (tmp_path / "pom.xml").write_text(pom, encoding="utf-8")
    return {item["name"]: item for item in scan_java_dependencies(tmp_path)}


def test_starters_inherit_the_spring_boot_parent_version(tmp_path: Path) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.4.2</version>
  </parent>
  <artifactId>demo</artifactId>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>
  </dependencies>
</project>""",
    )
    web = deps["org.springframework.boot:spring-boot-starter-web"]
    assert web["version_spec"] == "==3.4.2"
    assert web["purl"].endswith("@3.4.2")
    assert deps["org.springframework.boot:spring-boot-starter-test"]["purl"].endswith("@3.4.2")


def test_an_explicit_version_wins_over_the_parent(tmp_path: Path) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.4.2</version>
  </parent>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId><version>3.3.0</version></dependency>
  </dependencies>
</project>""",
    )
    assert deps["org.springframework.boot:spring-boot-starter-web"]["purl"].endswith("@3.3.0")


def test_parent_management_precedes_an_imported_bom(tmp_path: Path) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.4.2</version>
  </parent>
  <dependencyManagement><dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-dependencies</artifactId>
      <version>3.3.5</version>
      <type>pom</type>
      <scope>import</scope>
    </dependency>
  </dependencies></dependencyManagement>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
  </dependencies>
</project>""",
    )
    assert deps["org.springframework.boot:spring-boot-starter-web"]["purl"].endswith("@3.4.2")


@pytest.mark.parametrize(
    ("dependency_type", "scope"),
    [
        ("pom", "compile"),
        ("jar", "import"),
    ],
)
def test_non_import_entries_do_not_replace_the_parent(
    tmp_path: Path,
    dependency_type: str,
    scope: str,
) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.4.2</version>
  </parent>
  <dependencyManagement><dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-dependencies</artifactId>
      <version>3.3.5</version>
      <type>{dependency_type}</type>
      <scope>{scope}</scope>
    </dependency>
  </dependencies></dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
  </dependencies>
</project>""",
    )

    assert deps["org.springframework.boot:spring-boot-starter-web"]["purl"].endswith("@3.4.2")


def test_a_parent_version_given_as_a_property_is_resolved(tmp_path: Path) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>${{boot.version}}</version>
  </parent>
  <properties><boot.version>3.2.1</boot.version></properties>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
  </dependencies>
</project>""",
    )
    assert deps["org.springframework.boot:spring-boot-starter-web"]["purl"].endswith("@3.2.1")


def test_no_version_is_guessed_without_a_spring_boot_parent(tmp_path: Path) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
  <parent>
    <groupId>org.example</groupId>
    <artifactId>corporate-parent</artifactId>
    <version>7</version>
  </parent>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
  </dependencies>
</project>""",
    )
    assert "@" not in deps["org.springframework.boot:spring-boot-starter-web"]["purl"]


def test_only_spring_boot_group_dependencies_inherit_the_boot_version(tmp_path: Path) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.4.2</version>
  </parent>
  <dependencies>
    <dependency><groupId>org.springframework</groupId><artifactId>spring-web</artifactId></dependency>
    <dependency><groupId>dev.langchain4j</groupId><artifactId>langchain4j</artifactId></dependency>
  </dependencies>
</project>""",
    )
    assert "@" not in deps["org.springframework:spring-web"]["purl"]
    assert "@" not in deps["dev.langchain4j:langchain4j"]["purl"]


def _boot_dependency(
    artifact: str = "spring-boot-starter-web",
    version: str = "",
    extra: str = "",
) -> str:
    version_xml = f"<version>{version}</version>" if version else ""
    return (
        "<dependency><groupId>org.springframework.boot</groupId>"
        f"<artifactId>{artifact}</artifactId>{version_xml}{extra}</dependency>"
    )


def _managed_dependencies(dependencies: str) -> str:
    return (
        f"<dependencyManagement><dependencies>{dependencies}</dependencies></dependencyManagement>"
    )


def _boot_bom(version: str = "3.3.5", kind: str = "pom", scope: str = "import") -> str:
    return _boot_dependency(
        "spring-boot-dependencies", version, f"<type>{kind}</type><scope>{scope}</scope>"
    )


def _maven_profile(body: str, activation: str = "<activeByDefault>true</activeByDefault>") -> str:
    return (
        "<profiles><profile><id>review</id>"
        f"<activation>{activation}</activation>{body}</profile></profiles>"
    )


def _boot_pom(
    *,
    parent: str = "3.4.2",
    management: str = "",
    profiles: str = "",
    properties: str = "",
    version: str = "",
    extra_dependencies: str = "",
) -> str:
    parent_xml = (
        "<parent><groupId>org.springframework.boot</groupId>"
        f"<artifactId>spring-boot-starter-parent</artifactId><version>{parent}</version></parent>"
        if parent
        else ""
    )
    return (
        f"<project {_NS}><modelVersion>4.0.0</modelVersion>{parent_xml}"
        "<groupId>demo</groupId><artifactId>demo</artifactId><version>1.0</version>"
        f"{properties}{_managed_dependencies(management)}{profiles}<dependencies>"
        f"{_boot_dependency(version=version)}{extra_dependencies}</dependencies></project>"
    )


def _web_dependency(tmp_path: Path, **kwargs: str) -> dict[str, str]:
    return _scan(tmp_path, _boot_pom(**kwargs))["org.springframework.boot:spring-boot-starter-web"]


@pytest.mark.parametrize(
    ("kind", "scope", "expected"),
    [
        ("pom", "import", "3.3.5"),
        ("${bom.type}", "${bom.scope}", "3.3.5"),
        ("pom", "compile", ""),
        ("jar", "import", ""),
    ],
)
def test_import_only_fallback_requires_a_real_bom(
    tmp_path: Path,
    kind: str,
    scope: str,
    expected: str,
) -> None:
    dep = _web_dependency(
        tmp_path,
        parent="",
        management=_boot_bom(kind=kind, scope=scope),
        properties="<properties><bom.type>pom</bom.type><bom.scope>import</bom.scope></properties>",
    )
    base = "pkg:maven/org.springframework.boot/spring-boot-starter-web"
    assert dep["purl"] == base + (f"@{expected}" if expected else "")
    assert dep["version_spec"] == (f"=={expected}" if expected else "")


@pytest.mark.parametrize(
    ("direct", "managed", "expected"),
    [
        ("", "3.2.9", "3.2.9"),
        ("3.1.9", "3.2.9", "3.1.9"),
        ("3.1.9", "", "3.1.9"),
    ],
)
def test_local_and_explicit_versions_keep_precedence(
    tmp_path: Path,
    direct: str,
    managed: str,
    expected: str,
) -> None:
    management = _boot_bom()
    if managed:
        management += _boot_dependency(version=managed)
    dep = _web_dependency(tmp_path, management=management, version=direct)
    assert dep["version_spec"] == f"=={expected}"
    assert dep["purl"].endswith(f"@{expected}")


@pytest.mark.parametrize(
    "activation",
    [
        "<activeByDefault>true</activeByDefault>",
        "<property><name>ci</name></property>",
        "",
    ],
)
@pytest.mark.parametrize("section", ["managed", "direct"])
def test_profile_dependent_versions_remain_unresolved(
    tmp_path: Path,
    activation: str,
    section: str,
) -> None:
    dependency = _boot_dependency(version="3.3.5")
    body = (
        _managed_dependencies(dependency)
        if section == "managed"
        else f"<dependencies>{dependency}</dependencies>"
    )
    dep = _web_dependency(tmp_path, profiles=_maven_profile(body, activation))
    assert dep["purl"] == "pkg:maven/org.springframework.boot/spring-boot-starter-web"
    assert dep["version_spec"] == ""


@pytest.mark.parametrize("source", ["direct", "managed", "parent", "bom"])
def test_nested_profile_property_overrides_remain_symbolic(
    tmp_path: Path,
    source: str,
) -> None:
    kwargs = {
        "properties": (
            "<properties><selected>3.4.2</selected><middle>${selected}</middle>"
            "<boot.version>${middle}</boot.version></properties>"
        ),
        "profiles": _maven_profile("<properties><selected>3.3.5</selected></properties>"),
    }
    if source == "direct":
        kwargs["version"] = "${boot.version}"
    elif source == "managed":
        kwargs["management"] = _boot_dependency(version="${boot.version}")
    elif source == "parent":
        kwargs.update(parent="${boot.version}", management=_boot_bom())
    else:
        kwargs.update(parent="", management=_boot_bom("${boot.version}"))
    dep = _web_dependency(tmp_path, **kwargs)
    assert "@" not in dep["purl"]
    assert "${selected}" in dep["version_spec"]


@pytest.mark.parametrize("direct", ["", "3.1.9"])
def test_profile_management_does_not_override_a_literal_direct_version(
    tmp_path: Path,
    direct: str,
) -> None:
    dep = _web_dependency(
        tmp_path,
        version=direct,
        management=_boot_dependency(version="3.2.9"),
        profiles=_maven_profile(_managed_dependencies(_boot_dependency(version="3.3.5"))),
    )
    assert dep["version_spec"] == ("==3.1.9" if direct else "")
    assert ("@" in dep["purl"]) == bool(direct)


def test_profile_direct_override_also_invalidates_a_literal_version(tmp_path: Path) -> None:
    dep = _web_dependency(
        tmp_path,
        version="3.1.9",
        profiles=_maven_profile(
            f"<dependencies>{_boot_dependency(version='3.3.5')}</dependencies>"
        ),
    )
    assert "@" not in dep["purl"]


def test_unrelated_profile_changes_leave_the_parent_fallback_intact(tmp_path: Path) -> None:
    dep = _web_dependency(
        tmp_path,
        profiles=_maven_profile(
            "<properties><unrelated>value</unrelated></properties>"
            + _managed_dependencies(_boot_dependency("spring-boot-starter-test", "3.3.5"))
        ),
    )
    assert dep["purl"].endswith("@3.4.2")


def test_profile_bom_does_not_silently_pin_an_import_only_version(tmp_path: Path) -> None:
    dep = _web_dependency(
        tmp_path,
        parent="",
        management=_boot_bom("3.4.2"),
        profiles=_maven_profile(_managed_dependencies(_boot_bom())),
    )
    assert "@" not in dep["purl"]


@pytest.mark.parametrize("field", ["groupId", "artifactId"])
@pytest.mark.parametrize("target", ["parent", "managed"])
def test_profile_controlled_coordinates_do_not_bypass_precedence(
    tmp_path: Path,
    field: str,
    target: str,
) -> None:
    original = (
        "org.springframework.boot"
        if field == "groupId"
        else "spring-boot-starter-parent"
        if target == "parent"
        else "spring-boot-starter-web"
    )
    profiles = _maven_profile(f"<properties><selected>{original}</selected></properties>")
    old = f"<{field}>{original}</{field}>"
    new = f"<{field}>${{selected}}</{field}>"
    if target == "parent":
        pom = _boot_pom(management=_boot_bom(), profiles=profiles).replace(old, new, 1)
    else:
        management = _boot_dependency(version="3.2.9").replace(old, new, 1)
        pom = _boot_pom(management=management, profiles=profiles)
    dep = _scan(tmp_path, pom)["org.springframework.boot:spring-boot-starter-web"]
    assert "@" not in dep["purl"]


def test_profile_uncertainty_reaches_the_osv_filter(tmp_path: Path) -> None:
    from unittest.mock import patch

    from nuguard.analysis import osv_client

    deps = _scan(
        tmp_path,
        _boot_pom(
            profiles=_maven_profile(_managed_dependencies(_boot_dependency(version="3.3.5"))),
            extra_dependencies=_boot_dependency("spring-boot-starter-test"),
        ),
    )
    with (
        patch.object(osv_client, "_post_json", return_value={"results": []}) as post,
        patch.object(osv_client, "_get_json") as get,
    ):
        assert osv_client.query_osv(list(deps.values())) == []
    post.assert_called_once()
    assert post.call_args.args[1]["queries"] == [
        {
            "package": {
                "purl": "pkg:maven/org.springframework.boot/spring-boot-starter-test@3.4.2"
            },
        }
    ]
    get.assert_not_called()
