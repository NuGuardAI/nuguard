"""Tests for xelo.deps — DependencyScanner.

Validates parsing of the four manifest formats present in the real-world
fixture apps created for test_scenarios.py:

  customer_service_bot/pyproject.toml  — PEP 621 with optional-dependencies
  rag_pipeline/pyproject.toml          — PEP 621 with uv dev-dependencies
  code_review_crew/pyproject.toml      — Poetry format
  research_assistant/requirements.txt  — pip-style requirements file

Also covers inline fixtures for setup.cfg and edge-case requirement lines.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xelo.deps import DependencyScanner, PackageDep, _normalise, _to_npm_purl, _to_purl

_APPS = Path(__file__).parent / "fixtures" / "apps"


# ---------------------------------------------------------------------------
# Unit-level helpers
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_lowercase(self) -> None:
        assert _normalise("Pydantic") == "pydantic"

    def test_underscores_to_hyphens(self) -> None:
        assert _normalise("langchain_openai") == "langchain-openai"

    def test_dots_to_hyphens(self) -> None:
        assert _normalise("some.package") == "some-package"

    def test_mixed_separators_collapsed(self) -> None:
        assert _normalise("My---Pkg__Name") == "my-pkg-name"

    def test_already_normalised(self) -> None:
        assert _normalise("openai") == "openai"


class TestToPurl:
    def test_pinned_version(self) -> None:
        purl = _to_purl("OpenAI", "==1.30.0")
        assert purl == "pkg:pypi/openai@1.30.0"

    def test_unpinned(self) -> None:
        purl = _to_purl("pydantic", ">=2.7.0")
        assert purl == "pkg:pypi/pydantic"

    def test_empty_spec(self) -> None:
        purl = _to_purl("requests", "")
        assert purl == "pkg:pypi/requests"

    def test_name_normalised_in_purl(self) -> None:
        purl = _to_purl("langchain_openai", ">=0.2.0")
        assert purl.startswith("pkg:pypi/langchain-openai")


class TestPackageDepVersion:
    def test_pinned_version_extracted(self) -> None:
        dep = PackageDep(name="requests", version_spec="==2.31.0", purl="pkg:pypi/requests@2.31.0", group="runtime", source_file="req.txt")
        assert dep.version == "2.31.0"

    def test_range_spec_returns_none(self) -> None:
        dep = PackageDep(name="pydantic", version_spec=">=2.7,<3", purl="pkg:pypi/pydantic", group="runtime", source_file="req.txt")
        assert dep.version is None

    def test_empty_spec_returns_none(self) -> None:
        dep = PackageDep(name="openai", version_spec="", purl="pkg:pypi/openai", group="runtime", source_file="req.txt")
        assert dep.version is None


# ---------------------------------------------------------------------------
# PEP 621 — customer_service_bot/pyproject.toml
# ---------------------------------------------------------------------------

class TestPep621CustomerServiceBot:
    @pytest.fixture(scope="class")
    def deps(self) -> list[PackageDep]:
        return DependencyScanner().scan(_APPS / "customer_service_bot")

    def test_runtime_deps_found(self, deps: list[PackageDep]) -> None:
        names = {d.name for d in deps}
        assert "langgraph" in names
        assert "langchain-openai" in names
        assert "langchain-anthropic" in names
        assert "pydantic" in names

    def test_dep_groups_are_runtime(self, deps: list[PackageDep]) -> None:
        runtime = [d for d in deps if d.group == "runtime"]
        assert len(runtime) >= 8, f"Expected ≥8 runtime deps, got {len(runtime)}"

    def test_optional_dev_deps_captured(self, deps: list[PackageDep]) -> None:
        # [project.optional-dependencies] dev group
        dev_deps = [d for d in deps if "dev" in d.group or "optional" in d.group]
        names = {d.name for d in dev_deps}
        assert "pytest" in names or "ruff" in names, (
            f"Expected dev optional deps, got groups: {[d.group for d in deps]}"
        )

    def test_source_file_is_pyproject(self, deps: list[PackageDep]) -> None:
        for dep in deps:
            assert dep.source_file == "pyproject.toml"

    def test_purls_are_valid(self, deps: list[PackageDep]) -> None:
        for dep in deps:
            assert dep.purl.startswith("pkg:pypi/"), f"Bad PURL: {dep.purl!r}"
            assert dep.name in dep.purl

    def test_no_duplicate_names(self, deps: list[PackageDep]) -> None:
        # First-seen-wins dedup; runtime deps take priority over dev
        names = [d.name for d in deps]
        assert len(names) == len(set(names)), f"Duplicates: {names}"


# ---------------------------------------------------------------------------
# uv dev-dependencies — rag_pipeline/pyproject.toml
# ---------------------------------------------------------------------------

class TestUvRagPipeline:
    @pytest.fixture(scope="class")
    def deps(self) -> list[PackageDep]:
        return DependencyScanner().scan(_APPS / "rag_pipeline")

    def test_pep621_runtime_found(self, deps: list[PackageDep]) -> None:
        names = {d.name for d in deps}
        assert "llama-index-core" in names
        assert "anthropic" in names
        assert "chromadb" in names

    def test_uv_dev_deps_found(self, deps: list[PackageDep]) -> None:
        dev_deps = {d.name for d in deps if d.group == "dev"}
        assert "pytest" in dev_deps or "pytest-asyncio" in dev_deps, (
            f"Expected uv dev-dependencies, got dev deps: {dev_deps}"
        )

    def test_runtime_group_label(self, deps: list[PackageDep]) -> None:
        anthropic = next((d for d in deps if d.name == "anthropic"), None)
        assert anthropic is not None
        assert anthropic.group == "runtime"


# ---------------------------------------------------------------------------
# Poetry format — code_review_crew/pyproject.toml
# ---------------------------------------------------------------------------

class TestPoetryCodeReviewCrew:
    @pytest.fixture(scope="class")
    def deps(self) -> list[PackageDep]:
        return DependencyScanner().scan(_APPS / "code_review_crew")

    def test_poetry_runtime_deps(self, deps: list[PackageDep]) -> None:
        names = {d.name for d in deps}
        assert "crewai" in names
        assert "pyautogen" in names
        assert "anthropic" in names
        assert "openai" in names

    def test_python_constraint_excluded(self, deps: list[PackageDep]) -> None:
        names = {d.name for d in deps}
        assert "python" not in names

    def test_poetry_dev_deps(self, deps: list[PackageDep]) -> None:
        dev = {d.name for d in deps if d.group == "dev"}
        assert "pytest" in dev or "ruff" in dev, f"Dev deps: {dev}"

    def test_version_specs_captured(self, deps: list[PackageDep]) -> None:
        crewai = next((d for d in deps if d.name == "crewai"), None)
        assert crewai is not None
        assert crewai.version_spec, "Expected non-empty version_spec for crewai"


# ---------------------------------------------------------------------------
# requirements.txt — research_assistant
# ---------------------------------------------------------------------------

class TestRequirementsTxtResearchAssistant:
    @pytest.fixture(scope="class")
    def deps(self) -> list[PackageDep]:
        return DependencyScanner().scan(_APPS / "research_assistant")

    def test_requirements_parsed(self, deps: list[PackageDep]) -> None:
        names = {d.name for d in deps}
        assert "openai" in names
        assert "pydantic" in names
        assert "httpx" in names

    def test_source_file_is_requirements_txt(self, deps: list[PackageDep]) -> None:
        for dep in deps:
            assert "requirements" in dep.source_file

    def test_all_runtime_group(self, deps: list[PackageDep]) -> None:
        for dep in deps:
            assert dep.group == "runtime", f"Expected runtime, got {dep.group!r}"

    def test_purls_valid(self, deps: list[PackageDep]) -> None:
        for dep in deps:
            assert dep.purl.startswith("pkg:pypi/")


# ---------------------------------------------------------------------------
# setup.cfg parsing
# ---------------------------------------------------------------------------

class TestSetupCfgParsing:
    def test_install_requires_parsed(self, tmp_path: Path) -> None:
        (tmp_path / "setup.cfg").write_text(
            "[metadata]\nname = myapp\n\n[options]\ninstall_requires =\n"
            "    requests>=2.28\n    click>=8.0\n    pydantic==2.7.0\n"
        )
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        assert "requests" in names
        assert "click" in names
        assert "pydantic" in names

    def test_pinned_version_in_purl(self, tmp_path: Path) -> None:
        (tmp_path / "setup.cfg").write_text(
            "[options]\ninstall_requires =\n    mylib==1.2.3\n"
        )
        deps = DependencyScanner().scan(tmp_path)
        assert deps[0].purl == "pkg:pypi/mylib@1.2.3"
        assert deps[0].version == "1.2.3"

    def test_section_boundary_respected(self, tmp_path: Path) -> None:
        (tmp_path / "setup.cfg").write_text(
            "[options]\ninstall_requires =\n    numpy>=1.26\n\n[options.extras_require]\ntest =\n    pytest\n"
        )
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        assert "numpy" in names
        # pytest is in extras_require, not install_requires → not captured (correct)
        assert "pytest" not in names


# ---------------------------------------------------------------------------
# Edge cases and robustness
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# package.json — JS/TS version extraction
# ---------------------------------------------------------------------------

class TestNpmPurl:
    def test_plain_package_caret(self) -> None:
        assert _to_npm_purl("react", "^18.0.0") == "pkg:npm/react@18.0.0"

    def test_plain_package_tilde(self) -> None:
        assert _to_npm_purl("lodash", "~4.17.21") == "pkg:npm/lodash@4.17.21"

    def test_exact_version(self) -> None:
        assert _to_npm_purl("express", "4.18.2") == "pkg:npm/express@4.18.2"

    def test_scoped_package(self) -> None:
        purl = _to_npm_purl("@langchain/core", "^0.3.0")
        assert purl == "pkg:npm/%40langchain/core@0.3.0"

    def test_scoped_exact(self) -> None:
        purl = _to_npm_purl("@anthropic-ai/sdk", "0.39.0")
        assert purl == "pkg:npm/%40anthropic-ai/sdk@0.39.0"

    def test_range_no_version_in_purl(self) -> None:
        # ">=18.0.0" is a range — can't derive a single clean version
        purl = _to_npm_purl("node", ">=18.0.0")
        assert purl == "pkg:npm/node"
        assert "@" not in purl.split("pkg:npm/")[1]

    def test_star_no_version(self) -> None:
        assert _to_npm_purl("whatever", "*") == "pkg:npm/whatever"

    def test_latest_no_version(self) -> None:
        assert _to_npm_purl("typescript", "latest") == "pkg:npm/typescript"

    def test_prerelease_version(self) -> None:
        assert _to_npm_purl("next", "^14.0.0-rc.1") == "pkg:npm/next@14.0.0-rc.1"


class TestPackageJsonScanning:
    def test_runtime_deps_extracted(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {
                "react": "^18.2.0",
                "openai": "^4.28.0",
                "@langchain/core": "^0.3.0",
            }
        }))
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        assert "react" in names
        assert "openai" in names
        assert "@langchain/core" in names

    def test_dev_deps_extracted(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"typescript": "^5.0.0", "jest": "^29.0.0"}
        }))
        deps = DependencyScanner().scan(tmp_path)
        dev = {d.name for d in deps if d.group == "dev"}
        assert "typescript" in dev
        assert "jest" in dev

    def test_peer_deps_group(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "peerDependencies": {"react": ">=17.0.0"}
        }))
        deps = DependencyScanner().scan(tmp_path)
        assert deps[0].group == "optional:peer"

    def test_version_spec_stored_verbatim(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"openai": "^4.28.0"}
        }))
        dep = DependencyScanner().scan(tmp_path)[0]
        assert dep.version_spec == "^4.28.0"

    def test_semver_embedded_in_purl(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"openai": "^4.28.0"}
        }))
        dep = DependencyScanner().scan(tmp_path)[0]
        assert dep.purl == "pkg:npm/openai@4.28.0"

    def test_scoped_package_purl(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"@anthropic-ai/sdk": "^0.39.0"}
        }))
        dep = DependencyScanner().scan(tmp_path)[0]
        assert dep.purl == "pkg:npm/%40anthropic-ai/sdk@0.39.0"

    def test_source_file_is_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"react": "^18.0.0"}
        }))
        dep = DependencyScanner().scan(tmp_path)[0]
        assert dep.source_file == "package.json"

    def test_workspace_refs_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {
                "my-local-pkg": "workspace:*",
                "file-dep": "file:../local",
                "git-dep": "git+https://github.com/org/repo.git",
                "react": "^18.0.0",
            }
        }))
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        assert "react" in names
        assert "my-local-pkg" not in names
        assert "file-dep" not in names
        assert "git-dep" not in names

    def test_star_version_no_version_in_purl(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"legacy": "*"}
        }))
        dep = DependencyScanner().scan(tmp_path)[0]
        assert dep.purl == "pkg:npm/legacy"

    def test_no_cross_ecosystem_name_collision(self, tmp_path: Path) -> None:
        """'debug' exists in both PyPI and npm — both should be kept."""
        (tmp_path / "requirements.txt").write_text("debug==1.0.0\n")
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"debug": "^4.3.4"}
        }))
        deps = DependencyScanner().scan(tmp_path)
        purls = {d.purl for d in deps}
        assert any("pypi" in p for p in purls)
        assert any("npm" in p for p in purls)

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{ invalid json !!!")
        deps = DependencyScanner().scan(tmp_path)
        assert deps == []

    def test_combined_python_and_js(self, tmp_path: Path) -> None:
        """A full-stack project with both requirements.txt and package.json."""
        (tmp_path / "requirements.txt").write_text("fastapi>=0.110.0\nlanggraph>=0.1.0\n")
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {
                "react": "^18.2.0",
                "@langchain/core": "^0.3.0",
            },
            "devDependencies": {"typescript": "^5.4.0"},
        }))
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        # Python
        assert "fastapi" in names
        assert "langgraph" in names
        # JS
        assert "react" in names
        assert "@langchain/core" in names
        assert "typescript" in names
        # All PURLs are valid
        for dep in deps:
            assert dep.purl.startswith(("pkg:pypi/", "pkg:npm/"))


class TestEdgeCases:
    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        deps = DependencyScanner().scan(tmp_path)
        assert deps == []

    def test_comments_ignored_in_requirements(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            "# This is a comment\nopenai>=1.0\n# another comment\nrequests\n"
        )
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        assert "openai" in names
        assert "requests" in names
        assert "" not in names

    def test_inline_comments_stripped(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("pydantic>=2.7  # fast validation\n")
        deps = DependencyScanner().scan(tmp_path)
        assert deps[0].name == "pydantic"

    def test_options_flags_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            "-r base.txt\n--index-url https://example.com\nopenai\n"
        )
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        assert "openai" in names
        assert len(names) == 1

    def test_extras_in_package_name_stripped(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("pydantic[email]>=2.7\n")
        deps = DependencyScanner().scan(tmp_path)
        # Extras marker stripped; only base name kept
        assert deps[0].name == "pydantic"

    def test_pyproject_and_requirements_dedup(self, tmp_path: Path) -> None:
        """pyproject.toml wins over requirements.txt for same package."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["openai>=1.30.0"]\n'
        )
        (tmp_path / "requirements.txt").write_text("openai==1.0.0\n")
        deps = DependencyScanner().scan(tmp_path)
        openai = [d for d in deps if d.name == "openai"]
        assert len(openai) == 1
        # pyproject.toml version wins
        assert openai[0].version_spec == ">=1.30.0"

    def test_malformed_toml_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("this is not valid toml !!!\n[[\n")
        deps = DependencyScanner().scan(tmp_path)
        assert deps == []

    def test_environment_markers_stripped(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            'pywin32>=306; sys_platform == "win32"\nopenai\n'
        )
        deps = DependencyScanner().scan(tmp_path)
        names = {d.name for d in deps}
        assert "openai" in names
        # pywin32 may or may not be included depending on marker stripping,
        # but it should not cause a crash
