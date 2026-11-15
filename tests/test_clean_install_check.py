"""Tests for scripts/check_clean_install.py, the no-extras import smoke check."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "check_clean_install.py"
_FIXTURE_PKG = "clean_install_fixture_pkg"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_clean_install", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_clean_install = _load_script()


@pytest.fixture
def clean_sys_modules() -> Iterator[None]:
    """Drop the fixture package from sys.modules so it cannot leak into other tests."""
    yield
    for name in list(sys.modules):
        if name.startswith(_FIXTURE_PKG):
            del sys.modules[name]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_expected_optional_failure_accepts_extras_and_rejects_core_gaps() -> None:
    mcp_missing = ModuleNotFoundError("No module named 'mcp'", name="mcp")
    submodule_missing = ModuleNotFoundError("No module named 'mcp.server'", name="mcp.server")
    core_missing = ModuleNotFoundError("No module named 'cryptography'", name="cryptography")
    nuguard_missing = ModuleNotFoundError("No module named 'nuguard.gone'", name="nuguard.gone")

    assert check_clean_install.is_expected_optional_failure(mcp_missing)
    assert check_clean_install.is_expected_optional_failure(submodule_missing)
    assert not check_clean_install.is_expected_optional_failure(core_missing)
    assert not check_clean_install.is_expected_optional_failure(nuguard_missing)


def test_discover_modules_skips_test_packages(tmp_path: Path) -> None:
    package_root = tmp_path / "nuguard"
    _write(package_root / "__init__.py", "")
    _write(package_root / "cli" / "main.py", "")
    _write(package_root / "cli" / "__init__.py", "")
    _write(package_root / "sbom" / "tests" / "test_thing.py", "")
    _write(package_root / "sbom" / "tests" / "fixtures" / "app" / "agents.py", "")

    assert check_clean_install.discover_modules(package_root) == [
        "nuguard",
        "nuguard.cli",
        "nuguard.cli.main",
    ]


def test_check_modules_reports_unexpected_failures_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_sys_modules: None
) -> None:
    missing_extra = "clean_install_missing_extra"
    monkeypatch.setitem(check_clean_install.OPTIONAL_MODULE_EXTRAS, missing_extra, "fake")
    _write(tmp_path / _FIXTURE_PKG / "__init__.py", "")
    _write(tmp_path / _FIXTURE_PKG / "ok.py", "VALUE = 1\n")
    _write(tmp_path / _FIXTURE_PKG / "optional.py", f"import {missing_extra}\n")
    _write(tmp_path / _FIXTURE_PKG / "core_gap.py", "import cryptography_not_installed_xyz\n")
    _write(tmp_path / _FIXTURE_PKG / "broken.py", "raise RuntimeError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    failures = check_clean_install.check_modules(
        [
            f"{_FIXTURE_PKG}.ok",
            f"{_FIXTURE_PKG}.optional",
            f"{_FIXTURE_PKG}.core_gap",
            f"{_FIXTURE_PKG}.broken",
        ]
    )

    assert [failure.module for failure in failures] == [
        f"{_FIXTURE_PKG}.core_gap",
        f"{_FIXTURE_PKG}.broken",
    ]
    assert "cryptography_not_installed_xyz" in failures[0].error
    assert "boom" in failures[1].error


def test_all_nuguard_modules_import_in_dev_environment() -> None:
    """Every nuguard module imports when the dev environment provides core dependencies."""
    package_root = _ROOT / "nuguard"
    module_names = check_clean_install.discover_modules(package_root)

    assert "nuguard.cli.main" in module_names
    assert check_clean_install.check_modules(module_names) == []
