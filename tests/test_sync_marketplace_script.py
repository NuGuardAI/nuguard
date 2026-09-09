"""Tests for scripts/sync-marketplace.js transactional version synchronization."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "sync-marketplace.js"
_VERSION_FILES = [
    "pyproject.toml",
    "uv.lock",
    "nuguard/__init__.py",
    "npm/package.json",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "plugin/.claude-plugin/plugin.json",
    "plugin/.claude-plugin/marketplace.json",
    "marketplace.json",
    "gemini-extension.json",
    "openclaw.plugin.json",
    "smithery.yaml",
]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_version_fixture(root: Path, pyproject_version: str = "1.2.3") -> None:
    _write(root / "scripts" / "sync-marketplace.js", _SCRIPT.read_text(encoding="utf-8"))
    _write(
        root / "pyproject.toml",
        "\n".join(
            [
                "[project]",
                'name = "nuguard"',
                f'version = "{pyproject_version}"',
                "",
            ]
        ),
    )
    _write(
        root / "uv.lock",
        "\n".join(
            [
                "version = 1",
                "revision = 3",
                'requires-python = ">=3.12"',
                "",
                "[[package]]",
                'name = "nuguard"',
                'version = "0.0.1"',
                'source = { editable = "." }',
                "",
            ]
        ),
    )
    _write(root / "nuguard" / "__init__.py", '__version__ = "0.0.1"\n')

    plugin_doc = {"name": "nuguard-plugin", "version": "0.0.1"}
    market_doc = {
        "metadata": {"version": "0.0.1"},
        "plugins": [{"name": "nuguard-plugin", "version": "0.0.1"}],
    }
    _write(root / "npm" / "package.json", json.dumps(plugin_doc, indent=2) + "\n")
    _write(root / ".claude-plugin" / "plugin.json", json.dumps(plugin_doc, indent=2) + "\n")
    _write(
        root / ".claude-plugin" / "marketplace.json", json.dumps(market_doc, indent=2) + "\n"
    )
    _write(
        root / "plugin" / ".claude-plugin" / "plugin.json",
        json.dumps(plugin_doc, indent=2) + "\n",
    )
    _write(
        root / "plugin" / ".claude-plugin" / "marketplace.json",
        json.dumps(market_doc, indent=2) + "\n",
    )
    _write(root / "marketplace.json", json.dumps(market_doc, indent=2) + "\n")
    _write(root / "gemini-extension.json", json.dumps(plugin_doc, indent=2) + "\n")
    _write(root / "openclaw.plugin.json", json.dumps(plugin_doc, indent=2) + "\n")
    _write(root / "smithery.yaml", 'name: nuguard\nversion: "0.0.1"\n')


def _run_sync_script(
    root: Path,
    *args: str,
    lifecycle_event: str | None = None,
) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run sync-marketplace.js tests")
    env = os.environ.copy()
    if lifecycle_event is not None:
        env["npm_lifecycle_event"] = lifecycle_event
    return subprocess.run(
        [node, "scripts/sync-marketplace.js", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_sync_marketplace_synchronizes_all_projected_versions(tmp_path: Path) -> None:
    """sync-marketplace updates all version projections from pyproject.toml in one pass."""
    _make_version_fixture(tmp_path, pyproject_version="2.4.6")

    result = _run_sync_script(tmp_path)

    assert result.returncode == 0, result.stderr
    init_text = (tmp_path / "nuguard" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "2.4.6"' in init_text

    for rel_path in [
        "npm/package.json",
        ".claude-plugin/plugin.json",
        "plugin/.claude-plugin/plugin.json",
        "gemini-extension.json",
        "openclaw.plugin.json",
    ]:
        payload = json.loads((tmp_path / rel_path).read_text(encoding="utf-8"))
        assert payload["version"] == "2.4.6", rel_path

    for rel_path in [
        ".claude-plugin/marketplace.json",
        "plugin/.claude-plugin/marketplace.json",
        "marketplace.json",
    ]:
        payload = json.loads((tmp_path / rel_path).read_text(encoding="utf-8"))
        assert payload["metadata"]["version"] == "2.4.6", rel_path
        assert payload["plugins"][0]["version"] == "2.4.6", rel_path

    smithery = (tmp_path / "smithery.yaml").read_text(encoding="utf-8")
    assert 'version: "2.4.6"' in smithery

    before_second_run = {
        rel_path: (tmp_path / rel_path).read_bytes() for rel_path in _VERSION_FILES
    }
    second_result = _run_sync_script(tmp_path)
    assert second_result.returncode == 0, second_result.stderr
    after_second_run = {
        rel_path: (tmp_path / rel_path).read_bytes() for rel_path in _VERSION_FILES
    }
    assert after_second_run == before_second_run


def test_sync_marketplace_malformed_input_leaves_all_files_unchanged(tmp_path: Path) -> None:
    """Malformed marketplace metadata aborts sync and preserves all tracked version files."""
    _make_version_fixture(tmp_path, pyproject_version="3.0.0")

    broken_marketplace = {
        "metadata": [],
        "plugins": [{"name": "nuguard-plugin", "version": "0.0.1"}],
    }
    _write(
        tmp_path / "marketplace.json",
        json.dumps(broken_marketplace, indent=2) + "\n",
    )

    before = {
        rel_path: (tmp_path / rel_path).read_bytes()
        for rel_path in _VERSION_FILES
    }

    result = _run_sync_script(tmp_path)

    assert result.returncode != 0
    assert "ERROR:" in result.stderr
    assert "All version files were restored." in result.stderr

    after = {
        rel_path: (tmp_path / rel_path).read_bytes()
        for rel_path in _VERSION_FILES
    }
    assert after == before


def test_version_bump_repairs_stale_lock_for_same_version(tmp_path: Path) -> None:
    """version:bump must reconcile uv.lock even when pyproject already has the target."""
    if shutil.which("uv") is None:
        pytest.skip("uv is required to test version:bump")
    _make_version_fixture(tmp_path, pyproject_version="2.4.6")

    result = _run_sync_script(
        tmp_path,
        "2.4.6",
        lifecycle_event="version:bump",
    )

    assert result.returncode == 0, result.stderr
    lock_text = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "nuguard"\nversion = "2.4.6"' in lock_text
