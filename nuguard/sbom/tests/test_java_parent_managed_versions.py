"""Spring Boot managed versions in Maven poms (NuGuardAI/nuguard#646).

``org.springframework.boot`` starters normally carry no version of their own: it comes from the
``spring-boot-starter-parent`` parent or an imported ``spring-boot-dependencies`` BOM. Without it
the SBOM purl has no version, and a vulnerability lookup matches every advisory ever published.
"""

from __future__ import annotations

from pathlib import Path

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


def test_an_imported_spring_boot_bom_provides_the_version(tmp_path: Path) -> None:
    deps = _scan(
        tmp_path,
        f"""<project {_NS}>
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
    assert deps["org.springframework.boot:spring-boot-starter-web"]["purl"].endswith("@3.3.5")


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
