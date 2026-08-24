"""Tests for DependencyScanner lifecycle script extraction methods."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuguard.sbom.deps import DependencyScanner, LifecycleScript

FIXTURES = Path(__file__).parent.parent / "fixtures" / "supply_chain"


def _scanner() -> DependencyScanner:
    return DependencyScanner()


# ---------------------------------------------------------------------------
# npm lifecycle scripts
# ---------------------------------------------------------------------------

class TestNpmLifecycleScripts:
    def test_npm_postinstall_parsed(self):
        scanner = _scanner()
        scripts = scanner.parse_lifecycle_scripts(FIXTURES / "package_postinstall_bun")
        assert scripts, "Expected at least one lifecycle script"
        postinstall = next((s for s in scripts if s.name == "postinstall"), None)
        assert postinstall is not None, "Expected a 'postinstall' script"
        assert "bun" in postinstall.body.lower()
        assert postinstall.ecosystem == "npm"
        assert postinstall.phase == "install-hook"

    def test_npm_postinstall_curl_bash(self):
        scanner = _scanner()
        scripts = scanner.parse_lifecycle_scripts(FIXTURES / "package_curl_bash")
        postinstall = next((s for s in scripts if s.name == "postinstall"), None)
        assert postinstall is not None
        assert "curl" in postinstall.body.lower()
        assert postinstall.phase == "install-hook"

    def test_npm_proc_environ_script(self):
        scanner = _scanner()
        scripts = scanner.parse_lifecycle_scripts(FIXTURES / "package_proc_environ")
        postinstall = next((s for s in scripts if s.name == "postinstall"), None)
        assert postinstall is not None
        assert "/proc/" in postinstall.body

    def test_npm_lifecycle_source_file_recorded(self):
        scanner = _scanner()
        scripts = scanner.parse_lifecycle_scripts(FIXTURES / "package_postinstall_bun")
        for script in scripts:
            assert script.source_file, "source_file should not be empty"
            assert "package.json" in script.source_file

    def test_clean_package_scripts_captured_but_benign(self):
        """All npm scripts are captured (not just install-hooks), but none of
        clean_package's scripts (jest/tsc/eslint) match a malicious pattern.
        """
        scanner = _scanner()
        scripts = scanner.parse_lifecycle_scripts(FIXTURES / "clean_package")
        assert {s.name for s in scripts} == {"test", "build", "lint"}
        assert all(s.phase == "other-script" or s.phase == "build-hook" for s in scripts)

    def test_lifecycle_script_model_is_frozen(self):
        script = LifecycleScript(
            name="postinstall",
            body="node payload.js",
            source_file="package.json",
            ecosystem="npm",
            phase="install-hook",
        )
        with pytest.raises(Exception):
            script.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Python build hooks
# ---------------------------------------------------------------------------

class TestPythonBuildHooks:
    def test_no_python_hooks_in_npm_only_dir(self):
        scanner = _scanner()
        scripts = scanner.parse_python_build_hooks(FIXTURES / "package_postinstall_bun")
        assert scripts == [], f"Expected no Python hooks for npm-only dir, got: {scripts}"

    def test_nonexistent_directory_returns_empty(self, tmp_path: Path):
        scanner = _scanner()
        scripts = scanner.parse_lifecycle_scripts(tmp_path / "nonexistent")
        assert scripts == []

    def test_parse_lifecycle_scripts_returns_list(self, tmp_path: Path):
        """parse_lifecycle_scripts returns list even for empty dirs."""
        scanner = _scanner()
        result = scanner.parse_lifecycle_scripts(tmp_path)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Lockfile parsing
# ---------------------------------------------------------------------------

class TestLockfileParsing:
    def test_parse_lockfiles_returns_list(self, tmp_path: Path):
        scanner = _scanner()
        result = scanner.parse_lockfiles(tmp_path)
        assert isinstance(result, list)

    def test_npm_lockfile_parsed(self, tmp_path: Path):
        lockfile = {
            "name": "test-pkg",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "node_modules/lodash": {
                    "version": "4.17.21",
                    "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",
                    "integrity": "sha512-v2kDEe57lecTulaDIuNTPy3Ry4gLGJ6Z1O3vE1krgXZNrsQ+LFTGHVxVjcXPs17LhbZfdQPBfo/J2+KkJT5A=="
                }
            }
        }
        (tmp_path / "package-lock.json").write_text(json.dumps(lockfile))
        scanner = _scanner()
        results = scanner.parse_lockfiles(tmp_path)
        assert len(results) >= 1
        lodash = next((r for r in results if r.get("name") == "lodash"), None)
        assert lodash is not None
        assert lodash.get("version") == "4.17.21"
        assert lodash.get("ecosystem") == "npm"
