from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters._test_paths import is_jvm_test_source_path
from nuguard.sbom.types import ComponentType

_JAVA = """package demo;
import dev.langchain4j.service.AiServices;
public class Assistant {
  Object build() { return AiServices.create(Object.class); }
}
"""


@pytest.mark.parametrize(
    "path",
    [
        "src/test/java/demo/AssistantIT.java",
        "module-a/src/test/java/demo/A.java",
        "app/src/testFixtures/java/demo/A.java",
        "app/src/integrationTest/java/demo/A.java",
        "app\\src\\test\\java\\demo\\A.java",
    ],
)
def test_jvm_test_source_paths_are_detected(path: str) -> None:
    assert is_jvm_test_source_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/main/java/demo/Assistant.java",
        "src/main/java/com/acme/test/Helper.java",
        "src/main/resources/application.yml",
        "test/java/demo/A.java",
        "src/test",
    ],
)
def test_production_paths_are_not_flagged(path: str) -> None:
    assert not is_jvm_test_source_path(path)


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _agent_paths(
    tmp_path: Path,
    include_test_sources: bool = False,
    *,
    scan_root: Path | None = None,
) -> set[str]:
    _write(tmp_path, "src/main/java/demo/Assistant.java", _JAVA)
    _write(
        tmp_path,
        "src/test/java/demo/AssistantIT.java",
        _JAVA.replace("Assistant", "AssistantIT"),
    )
    config = AiSbomConfig(enable_llm=False, include_test_sources=include_test_sources)
    extraction_root = scan_root if scan_root is not None else tmp_path
    document = AiSbomExtractor().extract_from_path(extraction_root, config)
    return {
        ev.location.path
        for node in document.nodes
        if node.component_type == ComponentType.AGENT
        for ev in node.evidence
    }


def test_test_sources_are_not_extracted_by_default(tmp_path: Path) -> None:
    paths = _agent_paths(tmp_path)

    assert any(p.endswith("src/main/java/demo/Assistant.java") for p in paths)
    assert not any("src/test" in p for p in paths)


def test_include_test_sources_opts_back_in(tmp_path: Path) -> None:
    paths = _agent_paths(tmp_path, include_test_sources=True)

    assert any(p.endswith("src/main/java/demo/Assistant.java") for p in paths)
    assert any(p.endswith("src/test/java/demo/AssistantIT.java") for p in paths)


def test_src_root_scan_excludes_test_sources_by_default(
    tmp_path: Path,
) -> None:
    paths = _agent_paths(
        tmp_path,
        scan_root=tmp_path / "src",
    )

    assert any(path.endswith("main/java/demo/Assistant.java") for path in paths)
    assert not any(path.endswith("test/java/demo/AssistantIT.java") for path in paths)


def test_src_root_scan_include_test_sources_opts_back_in(
    tmp_path: Path,
) -> None:
    paths = _agent_paths(
        tmp_path,
        include_test_sources=True,
        scan_root=tmp_path / "src",
    )

    assert any(path.endswith("main/java/demo/Assistant.java") for path in paths)
    assert any(path.endswith("test/java/demo/AssistantIT.java") for path in paths)
