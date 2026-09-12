from __future__ import annotations

from pathlib import Path

from nuguard.sbom.deps import DependencyScanner
from nuguard.sbom.java_dependencies import scan_java_dependencies


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_maven_dependencies_resolve_properties_management_and_groups(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pom.xml",
        """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId><artifactId>demo</artifactId><version>1.0</version>
  <properties><spring-ai.version>1.0.1</spring-ai.version></properties>
  <dependencyManagement><dependencies>
    <dependency><groupId>org.springframework.ai</groupId><artifactId>spring-ai-openai-spring-boot-starter</artifactId><version>${spring-ai.version}</version></dependency>
  </dependencies></dependencyManagement>
  <dependencies>
    <dependency><groupId>org.springframework.ai</groupId><artifactId>spring-ai-openai-spring-boot-starter</artifactId></dependency>
    <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.11.0</version><scope>test</scope></dependency>
    <dependency><groupId>dev.langchain4j</groupId><artifactId>langchain4j</artifactId><version>1.0.0-beta3</version><optional>true</optional></dependency>
  </dependencies>
</project>""",
    )

    records = scan_java_dependencies(tmp_path)
    by_name = {item["name"]: item for item in records}
    spring = by_name["org.springframework.ai:spring-ai-openai-spring-boot-starter"]
    assert spring["version_spec"] == "==1.0.1"
    assert spring["purl"].endswith("@1.0.1")
    assert by_name["org.junit.jupiter:junit-jupiter"]["group"] == "dev"
    assert by_name["dev.langchain4j:langchain4j"]["group"] == "optional:maven"


def test_gradle_groovy_kotlin_and_version_catalog_are_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "gradle.properties", "openaiVersion=0.31.0\n")
    _write(
        tmp_path,
        "build.gradle",
        """dependencies {
    implementation "com.openai:openai-java:$openaiVersion"
    testImplementation('org.junit.jupiter:junit-jupiter:5.11.0')
    runtimeOnly group: "org.postgresql", name: "postgresql", version: "42.7.4"
}
""",
    )
    _write(
        tmp_path,
        "service/build.gradle.kts",
        """dependencies {
    implementation("dev.langchain4j:langchain4j:1.0.0-beta3")
}
""",
    )
    _write(
        tmp_path,
        "gradle/libs.versions.toml",
        """[versions]
springAi = "1.0.1"
[libraries]
spring-ai-openai = { module = "org.springframework.ai:spring-ai-openai-spring-boot-starter", version.ref = "springAi" }
quarkus-langchain = "io.quarkiverse.langchain4j:quarkus-langchain4j-openai:1.1.3"
""",
    )

    deps = DependencyScanner().scan(tmp_path)
    by_name = {item.name: item for item in deps}
    assert by_name["com.openai:openai-java"].version_spec == "==0.31.0"
    assert by_name["org.junit.jupiter:junit-jupiter"].group == "dev"
    assert by_name["org.postgresql:postgresql"].purl.endswith("@42.7.4")
    assert "dev.langchain4j:langchain4j" in by_name
    assert (
        by_name["org.springframework.ai:spring-ai-openai-spring-boot-starter"].version_spec
        == "==1.0.1"
    )
    assert "io.quarkiverse.langchain4j:quarkus-langchain4j-openai" in by_name


def test_generated_build_directories_are_skipped(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "target/copied/pom.xml",
        "<project><dependencies><dependency><groupId>bad</groupId><artifactId>copied</artifactId><version>1</version></dependency></dependencies></project>",
    )
    _write(
        tmp_path,
        ".gradle/cache/build.gradle",
        'dependencies { implementation("bad:cache:1.0") }',
    )
    assert scan_java_dependencies(tmp_path) == []


def test_malformed_java_build_files_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project><dependencies>", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("dependencies { implementation(", encoding="utf-8")

    dependencies = scan_java_dependencies(tmp_path)

    assert dependencies == []
