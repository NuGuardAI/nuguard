"""Engine selection for the SAST scanner: Opengrep first, Semgrep as fallback."""

from __future__ import annotations

import pytest

from nuguard.analysis.plugins import semgrep_scanner
from nuguard.analysis.plugins.semgrep_scanner import _engine_path, _semgrep_command


def _fake_which(available: dict[str, str]):
    def _which(name: str) -> str | None:
        return available.get(name)

    return _which


def test_engine_prefers_opengrep_when_both_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        semgrep_scanner.shutil,
        "which",
        _fake_which({"opengrep": "/bin/opengrep", "semgrep": "/bin/semgrep"}),
    )
    assert _engine_path() == ("/bin/opengrep", "opengrep")


def test_engine_falls_back_to_semgrep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(semgrep_scanner.shutil, "which", _fake_which({"semgrep": "/bin/semgrep"}))
    assert _engine_path() == ("/bin/semgrep", "semgrep")


def test_engine_none_when_neither_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(semgrep_scanner.shutil, "which", _fake_which({}))
    assert _engine_path() == (None, None)


def test_metrics_flag_not_passed_to_opengrep() -> None:
    cmd = _semgrep_command("/bin/opengrep", "/src", ["rules.yaml"], exclude_tests=False)
    assert "--metrics" not in cmd
    assert cmd[:2] == ["/bin/opengrep", "scan"]
    assert cmd[-1] == "/src"


def test_metrics_flag_passed_to_semgrep() -> None:
    cmd = _semgrep_command("/usr/bin/semgrep", "/src", ["rules.yaml"], exclude_tests=False)
    idx = cmd.index("--metrics")
    assert cmd[idx + 1] == "off"
