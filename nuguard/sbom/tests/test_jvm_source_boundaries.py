"""PR #668: source-set boundaries, subtree scans and discovery controls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters._test_paths import is_jvm_test_source_path
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.public_api import SbomGenerateRequest, generate_sbom
from nuguard.sbom.types import ComponentType

JAVA = """package demo;
import dev.langchain4j.service.AiServices;
public class Assistant {
    Object build() { return AiServices.create(Object.class); }
}
"""
TEST_DIRS = ("test", "testFixtures", "integrationTest")


def write(root: Path, rel: str, content: str = JAVA) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def discovered(root: Path, include: bool = False, **kw: Any) -> set[Path]:
    config = AiSbomConfig(enable_llm=False, include_test_sources=include, **kw)
    return {p for p, _ in AiSbomExtractor._iter_files(root, config)}


def agent_paths(doc: AiSbomDocument) -> set[str]:
    return {
        e.location.path
        for n in doc.nodes
        if n.component_type == ComponentType.AGENT
        for e in n.evidence
        if e.location
    }


@pytest.mark.parametrize("directory", TEST_DIRS)
def test_production_package_with_src_test_components_is_not_excluded(
    tmp_path: Path,
    directory: str,
) -> None:
    file = write(tmp_path, f"src/main/java/com/acme/src/{directory}/Assistant.java")
    assert file in discovered(tmp_path, include=True)
    assert file in discovered(tmp_path)


@pytest.mark.parametrize("directory", TEST_DIRS)
@pytest.mark.parametrize("tail", ["", "/java", "/java/demo"])
def test_narrower_test_scan_root_does_not_bypass_default_exclusion(
    tmp_path: Path,
    directory: str,
    tail: str,
) -> None:
    file = write(tmp_path, f"src/{directory}/java/demo/Assistant.java")
    # Project and src scans already correctly exclude exactly this file.
    assert file not in discovered(tmp_path)
    assert file not in discovered(tmp_path / "src")
    root = tmp_path / f"src/{directory}{tail}"
    assert file in discovered(root, include=True)
    assert file not in discovered(root)


def test_production_agent_is_retained_end_to_end(tmp_path: Path) -> None:
    rel = "src/main/java/com/acme/src/test/Assistant.java"
    write(tmp_path, rel)
    expected = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(enable_llm=False, include_test_sources=True),
    )
    assert rel in agent_paths(expected)
    actual = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))
    assert rel in agent_paths(actual)


def test_test_only_agent_does_not_reappear_for_a_narrow_root(tmp_path: Path) -> None:
    rel = "src/test/java/demo/Assistant.java"
    write(tmp_path, rel)
    config = AiSbomConfig(enable_llm=False)
    whole = AiSbomExtractor().extract_from_path(tmp_path, config)
    assert not agent_paths(whole)
    narrow = AiSbomExtractor().extract_from_path(tmp_path / "src/test", config)
    assert not agent_paths(narrow)


@pytest.mark.parametrize("directory", TEST_DIRS)
@pytest.mark.parametrize(
    "file",
    [
        "java/demo/Assistant.java",
        "resources/application.yml",
        "resources/application.yaml",
        "resources/application.properties",
    ],
)
@pytest.mark.parametrize("root_path", ["", "src"])
def test_standard_roots_exclude_and_opt_back_in(
    tmp_path: Path,
    directory: str,
    file: str,
    root_path: str,
) -> None:
    excluded = write(tmp_path, f"src/{directory}/{file}")
    main = write(tmp_path, "src/main/java/demo/Live.java")
    extensions = AiSbomConfig().include_extensions | {".properties"}
    root = tmp_path / root_path
    assert discovered(root, include_extensions=extensions) == {main}
    assert discovered(root, include=True, include_extensions=extensions) == {main, excluded}


@pytest.mark.parametrize("ext", ["py", "ts", "go", "cs"])
def test_non_java_test_source_scope_is_unchanged(tmp_path: Path, ext: str) -> None:
    file = write(tmp_path, f"src/test/example.{ext}", "ordinary file")
    assert file in discovered(tmp_path)


def test_configuration_scope_is_limited_to_application_files(tmp_path: Path) -> None:
    included = write(tmp_path, "src/test/resources/settings.yml", "other: true")
    excluded = write(tmp_path, "src/test/resources/application-prod.yml", "other: true")
    assert discovered(tmp_path) == {included}
    assert discovered(tmp_path, include=True) == {included, excluded}


def test_unrelated_absolute_ancestors_do_not_suppress_a_production_project(tmp_path: Path) -> None:
    root = tmp_path / "outer/src/test/unrelated-project"
    file = write(root, "src/main/java/demo/Assistant.java")
    flat = write(root, "Flat.java")
    assert discovered(root) == {file, flat}


def test_opt_in_does_not_override_explicit_exclusions(tmp_path: Path) -> None:
    write(tmp_path, "src/test/java/demo/Assistant.java")
    assert discovered(tmp_path, include=True, exclude_patterns=["*Assistant.java"]) == set()


def test_opt_in_does_not_override_gitignore(tmp_path: Path) -> None:
    write(tmp_path, "src/test/java/demo/Assistant.java")
    write(tmp_path, ".gitignore", "src/test/\n")
    assert discovered(tmp_path, include=True) == set()


def test_excluded_files_do_not_consume_the_max_files_budget(tmp_path: Path) -> None:
    write(tmp_path, "a/src/test/java/demo/Assistant.java")
    live = write(tmp_path, "z/src/main/java/demo/Live.java")
    assert discovered(tmp_path, max_files=1) == {live}


@pytest.mark.parametrize("include", [False, True])
async def test_json_public_request_preserves_opt_in(tmp_path: Path, include: bool) -> None:
    rel = "src/test/java/demo/Assistant.java"
    write(tmp_path, rel)
    request = SbomGenerateRequest.model_validate_json(
        json.dumps(
            {
                "source_path": str(tmp_path),
                "config": {"enable_llm": False, "include_test_sources": include},
            }
        )
    )
    assert request.config.include_test_sources is include
    assert SbomGenerateRequest.model_validate_json(request.model_dump_json()) == request
    result = await generate_sbom(request)
    assert (rel in agent_paths(result.sbom)) is include


def test_public_schema_field_matches_implementation() -> None:
    root = Path(__file__).resolve().parents[3]
    recorded = json.loads((root / "tests/contracts/public_api.schema.json").read_text())
    expected = recorded["sbom.SbomGenerateRequest"]["$defs"]["AiSbomConfig"]["properties"][
        "include_test_sources"
    ]
    actual = SbomGenerateRequest.model_json_schema()["$defs"]["AiSbomConfig"]["properties"][
        "include_test_sources"
    ]
    assert actual == expected


@pytest.mark.parametrize("include", [False, True])
def test_excluded_source_does_not_enter_llm_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include: bool,
) -> None:
    write(tmp_path, "src/main/java/demo/Live.java", JAVA.replace("Assistant", "Live"))
    test_file = "src/test/java/demo/Assistant.java"
    yaml_file = "src/test/resources/application.yml"
    write(tmp_path, test_file)
    write(tmp_path, yaml_file, "ordinary: harmless\n")
    caches: list[dict[str, str]] = []

    async def capture(
        self: AiSbomExtractor,
        doc: AiSbomDocument,
        file_contents: dict[str, str],
        config: AiSbomConfig,
    ) -> AiSbomDocument:
        caches.append(dict(file_contents))
        return doc

    monkeypatch.setattr(AiSbomExtractor, "_llm_enrich", capture)
    AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(enable_llm=True, include_test_sources=include),
    )
    assert len(caches) == 1
    assert "src/main/java/demo/Live.java" in caches[0]
    assert (test_file in caches[0]) is include
    assert (yaml_file in caches[0]) is include


@pytest.mark.parametrize("directory", TEST_DIRS)
@pytest.mark.parametrize(
    "scan_suffix",
    ["", "/java", "/java/com/acme", "/java/com/acme/src", "/java/com/acme/src/{directory}"],
)
def test_production_context_survives_narrow_package_roots(
    tmp_path: Path,
    directory: str,
    scan_suffix: str,
) -> None:
    package = f"src/main/java/com/acme/src/{directory}"
    file = write(tmp_path, f"{package}/Assistant.java")
    resources = write(
        tmp_path, f"src/main/resources/com/acme/src/{directory}/application.yml", "ordinary: true\n"
    )
    root = tmp_path / ("src/main" + scan_suffix.format(directory=directory))
    assert file in discovered(root)
    if scan_suffix == "":
        assert resources in discovered(root)
    assert not is_jvm_test_source_path(f"{package}/Assistant.java")
    assert not is_jvm_test_source_path(f"{package}/Assistant.java".replace("/", "\\"))


@pytest.mark.parametrize("directory", TEST_DIRS)
@pytest.mark.parametrize("suffix", ["properties", "yml", "yaml"])
def test_resource_root_retains_test_context_and_opt_in(
    tmp_path: Path,
    directory: str,
    suffix: str,
) -> None:
    root = tmp_path / f"src/{directory}/resources"
    file = write(root, f"application-prod.{suffix}", "ordinary: true\n")
    extensions = AiSbomConfig().include_extensions | {".properties"}
    assert discovered(root, include_extensions=extensions) == set()
    assert discovered(root, include=True, include_extensions=extensions) == {file}


@pytest.mark.parametrize(
    "marker",
    [
        ".git-file",
        ".git-dir",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    ],
)
def test_context_lookup_stops_at_an_independent_project(
    tmp_path: Path,
    marker: str,
) -> None:
    # Even an ancestor with a plausible Java-source layout cannot own a new project.
    root = tmp_path / "outer/src/test/java/unrelated-project"
    flat = write(root, "Flat.java")
    production = write(root, "src/main/java/demo/Assistant.java")
    test = write(root, "src/test/java/demo/TestAssistant.java")
    if marker == ".git-dir":
        (root / ".git").mkdir()
    else:
        write(root, ".git" if marker == ".git-file" else marker, "fixture\n")
    assert discovered(root) == {flat, production}
    assert discovered(root, include=True) == {flat, production, test}
    assert discovered(root / "src/main/java") == {production}
    assert discovered(root / "src/test/java") == set()


@pytest.mark.parametrize("source_set", ["main", "test", "testFixtures", "integrationTest"])
def test_relative_dot_scan_root_keeps_only_classification_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_set: str,
) -> None:
    root = tmp_path / f"src/{source_set}/java"
    write(root, "demo/Assistant.java")
    monkeypatch.chdir(root)
    assert discovered(Path("."), include=True) == {Path("demo/Assistant.java")}
    assert discovered(Path(".")) == (
        {Path("demo/Assistant.java")} if source_set == "main" else set()
    )


def test_narrow_opt_in_keeps_evidence_paths_relative_to_selected_root(tmp_path: Path) -> None:
    root = tmp_path / "src/test/java"
    write(root, "demo/Assistant.java")
    document = AiSbomExtractor().extract_from_path(
        root,
        AiSbomConfig(enable_llm=False, include_test_sources=True),
    )
    assert agent_paths(document) == {"demo/Assistant.java"}


def test_narrow_opt_in_keeps_ignore_patterns_relative_to_selected_root(tmp_path: Path) -> None:
    root = tmp_path / "src/test/java"
    write(root, "demo/Assistant.java")
    assert discovered(root, include=True, exclude_patterns=["demo/*"]) == set()
    write(root, ".gitignore", "demo/\n")
    assert discovered(root, include=True) == set()


@pytest.mark.parametrize("language_dir", ["java", "resources", "kotlin", "scala", "groovy"])
def test_conventional_content_directory_can_recover_source_set(
    tmp_path: Path,
    language_dir: str,
) -> None:
    root = tmp_path / f"src/test/{language_dir}/demo"
    file = write(root, "Assistant.java")
    assert discovered(root) == set()
    assert discovered(root, include=True) == {file}


def test_test_fixture_subproject_remains_test_evidence_when_scanning_parent(tmp_path: Path) -> None:
    file = write(tmp_path, "src/test/resources/fixture/src/main/java/demo/Assistant.java")
    assert discovered(tmp_path) == set()
    assert discovered(tmp_path, include=True) == {file}


def test_multimodule_production_and_test_boundaries(tmp_path: Path) -> None:
    production = write(tmp_path, "modules/customer/src/main/java/com/src/test/Assistant.java")
    write(tmp_path, "modules/customer/src/test/java/demo/AssistantTest.java")
    write(tmp_path, "modules/other/src/testFixtures/java/demo/Fixture.java")
    assert discovered(tmp_path) == {production}


def test_root_with_custom_source_set_does_not_inherit_test_ancestor(tmp_path: Path) -> None:
    # Nonstandard roots require explicit patterns rather than arbitrary ancestor matching.
    root = tmp_path / "outer/src/test/custom-project"
    file = write(root, "Assistant.java")
    assert discovered(root) == {file}
