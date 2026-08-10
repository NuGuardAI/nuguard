"""Regression tests for sub-folder package manifest discovery.

Issue #226: "package discovery" — when ``requirements.txt`` or
``package.json`` lives inside a few sub-folders, the scanner was reported
to miss them. These tests pin the deep-nesting behaviour so any future
walker change that breaks it (e.g. introducing a max depth) will fail
loudly rather than silently drop packages.
"""

from __future__ import annotations

import json
from pathlib import Path

from nuguard.sbom.deps import DependencyScanner


def _touch(parent: Path, rel: str, content: str) -> Path:
    path = parent / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_requirements_txt_in_arbitrary_subfolder(tmp_path: Path) -> None:
    _touch(tmp_path, "services/auth/requirements.txt", "pyjwt>=2.0\n")
    deps = DependencyScanner().scan(tmp_path)
    purls = {d.purl for d in deps}
    assert "pkg:pypi/pyjwt" in purls


def test_package_json_in_arbitrary_subfolder(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "apps/web/package.json",
        json.dumps({"dependencies": {"react": "^18.0.0"}}),
    )
    deps = DependencyScanner().scan(tmp_path)
    purls = {d.purl for d in deps}
    assert "pkg:npm/react@18.0.0" in purls


def test_pyproject_toml_in_arbitrary_subfolder(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "packages/core/pyproject.toml",
        '[project]\nname = "core"\nversion = "0.1.0"\n'
        'dependencies = ["pydantic>=2.7"]\n',
    )
    deps = DependencyScanner().scan(tmp_path)
    purls = {d.purl for d in deps}
    assert "pkg:pypi/pydantic" in purls


def test_deep_nested_requirements(tmp_path: Path) -> None:
    """Manifests nested four+ directories deep must still be discovered."""
    _touch(tmp_path, "a/b/c/d/requirements.txt", "flask>=2.0\n")
    deps = DependencyScanner().scan(tmp_path)
    purls = {d.purl for d in deps}
    assert "pkg:pypi/flask" in purls


def test_multiple_subfolder_manifests_all_discovered(tmp_path: Path) -> None:
    """All sub-folder manifests are found in one scan, not just the first."""
    _touch(tmp_path, "frontend/package.json",
           json.dumps({"dependencies": {"react": "^18.0.0"}}))
    _touch(tmp_path, "backend/api/requirements.txt", "fastapi>=0.100\n")
    _touch(tmp_path, "workers/queue/package.json",
           json.dumps({"dependencies": {"bull": "^4.0.0"}}))

    deps = DependencyScanner().scan(tmp_path)
    purls = {d.purl for d in deps}
    assert "pkg:npm/react@18.0.0" in purls
    assert "pkg:pypi/fastapi" in purls
    assert "pkg:npm/bull@4.0.0" in purls


def test_subfolder_manifest_filtered_in_venv(tmp_path: Path) -> None:
    """Manifests inside venv / node_modules are still filtered out."""
    _touch(tmp_path, "real/requirements.txt", "flask>=2.0\n")
    _touch(tmp_path, "real/.venv/requirements.txt", "django>=4.2\n")
    _touch(tmp_path, "web/package.json",
           json.dumps({"dependencies": {"react": "^18.0.0"}}))
    _touch(tmp_path, "web/node_modules/lodash/package.json",
           json.dumps({"dependencies": {"lodash": "^4.0.0"}}))

    deps = DependencyScanner().scan(tmp_path)
    purls = {d.purl for d in deps}
    assert "pkg:pypi/flask" in purls
    assert "pkg:pypi/django" not in purls
    assert "pkg:npm/react@18.0.0" in purls
    assert "pkg:npm/lodash@4.0.0" not in purls
