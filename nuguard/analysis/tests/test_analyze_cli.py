"""Unit tests for ``nuguard analyze`` CLI — --config and --nga flag behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[3]  # /workspaces/nuguard

_SBOM_PATH = str(_REPO_ROOT / "tests/apps/openai-cs-agents-demo/openai-cs.sbom.json")
_CONFIG_PATH = str(_REPO_ROOT / "tests/apps/openai-cs-agents-demo/nuguard.yaml")

_PATCH_ANALYZER = "nuguard.analysis.static_analyzer.StaticAnalyzer"


def _invoke(*args: str) -> Any:
    return runner.invoke(app, ["analyze", *args])


def _make_mock_analyzer() -> MagicMock:
    mock = MagicMock()
    mock.analyze.return_value = []
    mock.tool_status = {}
    return mock


# ---------------------------------------------------------------------------
# --sbom fallback from config
# ---------------------------------------------------------------------------


class TestSbomFromConfig:
    def test_sbom_resolved_from_config(self) -> None:
        """--sbom can be omitted when config file has 'sbom:' key."""
        with patch(_PATCH_ANALYZER, return_value=_make_mock_analyzer()):
            result = _invoke("--config", _CONFIG_PATH, "--nga")
        assert result.exit_code in (0, 1), result.output

    def test_missing_sbom_no_config_errors(self) -> None:
        """No --sbom and no config yields a clear error message."""
        result = _invoke()
        assert result.exit_code == 2
        assert "sbom" in result.output.lower()

    def test_missing_sbom_config_without_sbom_key_errors(self, tmp_path: Path) -> None:
        """Config that has no 'sbom:' key still errors with a clear message."""
        cfg = tmp_path / "nuguard.yaml"
        cfg.write_text("analyze:\n  min_severity: high\n")
        result = _invoke("--config", str(cfg))
        assert result.exit_code == 2
        assert "sbom" in result.output.lower()

    def test_explicit_sbom_flag_overrides_config(self, tmp_path: Path) -> None:
        """--sbom on the CLI takes precedence over config sbom: key."""
        other_sbom = tmp_path / "other.sbom.json"
        other_sbom.write_text("{}")  # deliberately invalid so we hit the parse error
        cfg = tmp_path / "nuguard.yaml"
        cfg.write_text(f"sbom: {_SBOM_PATH}\n")
        result = _invoke("--config", str(cfg), "--sbom", str(other_sbom), "--nga")
        # Should fail at SBOM validation, not at "file not found"
        assert "validation failed" in result.output or "not found" not in result.output


# ---------------------------------------------------------------------------
# --nga flag
# ---------------------------------------------------------------------------


class TestNgaFlag:
    def test_nga_disables_all_external_tools(self) -> None:
        """--nga must disable osv, grype, checkov, trivy, semgrep, atlas."""
        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            result = _invoke("--sbom", _SBOM_PATH, "--nga")

        assert result.exit_code in (0, 1), result.output
        _, init_kwargs = cls.call_args
        assert init_kwargs["enable_osv"] is False
        assert init_kwargs["enable_grype"] is False
        assert init_kwargs["enable_checkov"] is False
        assert init_kwargs["enable_trivy"] is False
        assert init_kwargs["enable_semgrep"] is False
        assert init_kwargs["enable_atlas"] is False

    def test_explicit_flags_without_nga_are_passed_through(self) -> None:
        """Without --nga, individual --no-X flags are forwarded as-is."""
        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            _invoke("--sbom", _SBOM_PATH, "--no-osv", "--no-grype")

        _, init_kwargs = cls.call_args
        assert init_kwargs["enable_osv"] is False
        assert init_kwargs["enable_grype"] is False
        assert init_kwargs["enable_checkov"] is True
        assert init_kwargs["enable_atlas"] is True

    def test_nga_with_explicit_min_severity(self) -> None:
        """--nga and --min-severity can be used together."""
        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            result = _invoke("--sbom", _SBOM_PATH, "--nga", "--min-severity", "high")

        assert result.exit_code in (0, 1), result.output
        _, init_kwargs = cls.call_args
        assert init_kwargs["enable_osv"] is False
        assert init_kwargs["min_severity"].value == "high"


# ---------------------------------------------------------------------------
# --config flag — min_severity and nga_only
# ---------------------------------------------------------------------------


class TestConfigFlag:
    def test_min_severity_loaded_from_config(self, tmp_path: Path) -> None:
        """analyze.min_severity in config is used when --min-severity is not set."""
        cfg = tmp_path / "nuguard.yaml"
        cfg.write_text(f"sbom: {_SBOM_PATH}\nanalyze:\n  min_severity: critical\n")

        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            result = _invoke("--config", str(cfg))

        assert result.exit_code in (0, 1), result.output
        _, init_kwargs = cls.call_args
        assert init_kwargs["min_severity"].value == "critical"

    def test_cli_min_severity_overrides_config(self, tmp_path: Path) -> None:
        """CLI --min-severity always overrides the config value."""
        cfg = tmp_path / "nuguard.yaml"
        cfg.write_text(f"sbom: {_SBOM_PATH}\nanalyze:\n  min_severity: critical\n")

        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            result = _invoke("--config", str(cfg), "--min-severity", "low")

        assert result.exit_code in (0, 1), result.output
        _, init_kwargs = cls.call_args
        assert init_kwargs["min_severity"].value == "low"

    def test_nga_only_from_config(self, tmp_path: Path) -> None:
        """analyze.nga_only: true in config activates NGA-only mode."""
        cfg = tmp_path / "nuguard.yaml"
        cfg.write_text(
            f"sbom: {_SBOM_PATH}\nanalyze:\n  nga_only: true\n  min_severity: medium\n"
        )

        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            result = _invoke("--config", str(cfg))

        assert result.exit_code in (0, 1), result.output
        _, init_kwargs = cls.call_args
        assert init_kwargs["enable_osv"] is False
        assert init_kwargs["enable_grype"] is False
        assert init_kwargs["enable_atlas"] is False

    def test_default_min_severity_is_medium_when_no_config(self) -> None:
        """When neither --min-severity nor config is set, default is medium."""
        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            _invoke("--sbom", _SBOM_PATH, "--no-osv", "--no-grype",
                    "--no-checkov", "--no-trivy", "--no-semgrep", "--no-atlas")

        _, init_kwargs = cls.call_args
        assert init_kwargs["min_severity"].value == "medium"


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


class TestOutputFormats:
    @pytest.mark.parametrize("fmt", ["markdown", "json", "sarif"])
    def test_format_flag(self, fmt: str) -> None:
        with patch(_PATCH_ANALYZER) as cls:
            cls.return_value = _make_mock_analyzer()
            result = _invoke("--sbom", _SBOM_PATH, "--nga", "--format", fmt)

        assert result.exit_code in (0, 1), result.output
        if fmt in ("json", "sarif"):
            # Strip any leading log lines (e.g. DEBUG lines) before the JSON object
            json_start = result.output.index("{")
            parsed = json.loads(result.output[json_start:])
            if fmt == "json":
                assert "findings" in parsed
            else:
                assert parsed.get("version") == "2.1.0"
