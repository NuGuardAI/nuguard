"""CLI tests for ``nuguard policy`` sub-commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_POLICY = """\
# Cognitive Policy: Test Agent
version: 1.0

## Allowed Topics
- Customer support queries
- Order status and account inquiries

## Restricted Actions
- Do not execute financial transactions without explicit user confirmation
- Do not share data across tenant boundaries

## HITL Triggers
- Any action with financial impact > $500
- Password reset or account deletion requests

## Data Classification
- PII fields: name, email, phone
- Internal fields: user_id, tenant_id
"""

_FIXTURE_APP = (
    Path(__file__).parent.parent.parent
    / "nuguard"
    / "sbom"
    / "tests"
    / "fixtures"
    / "apps"
    / "customer_service_bot"
)


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    p = tmp_path / "policy.md"
    p.write_text(_MINIMAL_POLICY, encoding="utf-8")
    return p


@pytest.fixture
def sbom_file(tmp_path: Path) -> Path:
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.extractor.config import AiSbomConfig
    from nuguard.sbom.extractor.serializer import AiSbomSerializer

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    doc = AiSbomExtractor().extract_from_path(_FIXTURE_APP, config)
    f = tmp_path / "app.sbom.json"
    f.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# nuguard policy validate
# ---------------------------------------------------------------------------


def test_validate_clean_policy(policy_file: Path) -> None:
    result = runner.invoke(app, ["policy", "validate", "--file", str(policy_file)])
    assert result.exit_code == 0, result.output
    assert "no policy issues" in result.output.lower()


def test_validate_empty_policy(tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("# Cognitive Policy: Empty\n", encoding="utf-8")
    result = runner.invoke(app, ["policy", "validate", "--file", str(empty)])
    # POLICY-005: all sections empty → error → exit 2
    assert result.exit_code != 0


def test_validate_policy_with_warnings(tmp_path: Path) -> None:
    # Policy has HITL triggers but no restricted_actions → POLICY-002 warning
    policy = tmp_path / "warn.md"
    policy.write_text(
        "# Cognitive Policy: Warn\n\n"
        "## Allowed Topics\n- Support\n\n"
        "## HITL Triggers\n- Financial action > $500\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["policy", "validate", "--file", str(policy)])
    # warnings only → exit 1
    assert result.exit_code == 1


def test_validate_missing_file_exits(tmp_path: Path) -> None:
    result = runner.invoke(app, ["policy", "validate", "--file", str(tmp_path / "nope.md")])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# nuguard policy compile
# ---------------------------------------------------------------------------


def test_compile_writes_origin_and_evidence(policy_file: Path, tmp_path: Path) -> None:
    dest = tmp_path / "compiled.json"
    result = runner.invoke(
        app,
        ["policy", "compile", "--policy", str(policy_file), "--output", str(dest)],
    )
    assert result.exit_code == 0, result.output
    assert dest.exists()

    controls = json.loads(dest.read_text())
    assert controls
    doc_controls = [c for c in controls if c["origin"] == "policy_document"]
    assert doc_controls
    for c in doc_controls:
        assert c["evidence"]
        assert all(ev["path"] == policy_file.name for ev in c["evidence"])


def test_compile_with_sbom_adds_component_evidence(
    policy_file: Path, sbom_file: Path, tmp_path: Path
) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text(f"sbom: {sbom_file}\n", encoding="utf-8")
    dest = tmp_path / "compiled.json"
    result = runner.invoke(
        app,
        [
            "policy",
            "compile",
            "--policy",
            str(policy_file),
            "--config",
            str(cfg),
            "--output",
            str(dest),
        ],
    )
    assert result.exit_code == 0, result.output
    controls = json.loads(dest.read_text())
    assert controls
    # Not asserting a specific component match (fixture-dependent) — just that
    # compilation with an SBOM present still succeeds and produces valid
    # origin/evidence-shaped controls.
    for c in controls:
        assert c["origin"] in ("policy_document", "nuguard_best_practice")
        assert isinstance(c["evidence"], list)


# ---------------------------------------------------------------------------
# nuguard policy check
# ---------------------------------------------------------------------------


def test_check_requires_sbom_or_policy(tmp_path: Path) -> None:
    result = runner.invoke(app, ["policy", "check"])
    assert result.exit_code != 0


def test_check_policy_against_sbom(policy_file: Path, sbom_file: Path) -> None:
    result = runner.invoke(
        app,
        ["policy", "check", "--policy", str(policy_file), "--sbom", str(sbom_file)],
    )
    # Exit 0 (no gaps), 1 (medium gaps), or 2 (high/critical gaps) — not an internal error
    assert result.exit_code in (0, 1, 2), result.output


def test_check_uses_policy_flag_not_file_flag(policy_file: Path, sbom_file: Path) -> None:
    """--policy is the correct flag; --file should not be accepted for check."""
    result_policy = runner.invoke(
        app,
        ["policy", "check", "--policy", str(policy_file), "--sbom", str(sbom_file)],
    )
    assert result_policy.exit_code in (0, 1, 2), result_policy.output

    # --file is no longer a valid option for policy check
    result_file = runner.invoke(
        app,
        ["policy", "check", "--file", str(policy_file), "--sbom", str(sbom_file)],
    )
    assert result_file.exit_code != 0


def test_check_sbom_only_no_error(sbom_file: Path) -> None:
    """Passing only --sbom without --policy is allowed (no gap check, no framework)."""
    result = runner.invoke(app, ["policy", "check", "--sbom", str(sbom_file)])
    assert result.exit_code in (0, 1, 2), result.output


def test_check_json_output(policy_file: Path, sbom_file: Path) -> None:
    result = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--policy",
            str(policy_file),
            "--sbom",
            str(sbom_file),
            "--format",
            "json",
        ],
    )
    assert result.exit_code in (0, 1, 2), result.output
    # Output may contain log lines before the JSON object; find the opening '{'
    output = result.output
    import re as _re
    m = _re.search(r"\{", output)
    assert m is not None, f"No JSON object found in output: {output!r}"
    parsed = json.loads(output[m.start():])
    # Supports both legacy bare-array format and current {"_meta":…,"findings":[…]} envelope
    if isinstance(parsed, list):
        findings = parsed
    else:
        findings = parsed.get("findings", [])
    assert isinstance(findings, list)


def test_check_findings_invariant_with_verbose_toggle(policy_file: Path, sbom_file: Path) -> None:
    import re as _re

    base = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--policy",
            str(policy_file),
            "--sbom",
            str(sbom_file),
            "--format",
            "json",
            "--no-verbose",
        ],
    )
    verbose = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--policy",
            str(policy_file),
            "--sbom",
            str(sbom_file),
            "--format",
            "json",
            "--verbose",
        ],
    )
    assert base.exit_code in (0, 1, 2), base.output
    assert verbose.exit_code in (0, 1, 2), verbose.output

    base_json_start = _re.search(r"\{", base.output)
    verbose_json_start = _re.search(r"\{", verbose.output)
    assert base_json_start is not None
    assert verbose_json_start is not None

    base_payload = json.loads(base.output[base_json_start.start():])
    verbose_payload = json.loads(verbose.output[verbose_json_start.start():])

    assert base_payload.get("findings", []) == verbose_payload.get("findings", [])


def test_check_json_verbose_adds_diagnostics(policy_file: Path, sbom_file: Path) -> None:
    import re as _re

    verbose = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--policy",
            str(policy_file),
            "--sbom",
            str(sbom_file),
            "--format",
            "json",
            "--verbose",
        ],
    )
    non_verbose = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--policy",
            str(policy_file),
            "--sbom",
            str(sbom_file),
            "--format",
            "json",
            "--no-verbose",
        ],
    )
    assert verbose.exit_code in (0, 1, 2), verbose.output
    assert non_verbose.exit_code in (0, 1, 2), non_verbose.output

    v_start = _re.search(r"\{", verbose.output)
    n_start = _re.search(r"\{", non_verbose.output)
    assert v_start is not None
    assert n_start is not None
    v_payload = json.loads(verbose.output[v_start.start():])
    n_payload = json.loads(non_verbose.output[n_start.start():])

    assert v_payload.get("_meta", {}).get("verbose") is True
    assert "diagnostics" in v_payload
    assert n_payload.get("_meta", {}).get("verbose") is False
    assert "diagnostics" not in n_payload


def test_check_multiple_formats_require_output(policy_file: Path, sbom_file: Path) -> None:
    result = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--policy",
            str(policy_file),
            "--sbom",
            str(sbom_file),
            "--format",
            "json",
            "--format",
            "markdown",
        ],
    )
    assert result.exit_code == 3
    assert "--output is required" in result.output


def test_check_multiple_formats_write_multiple_files(
    policy_file: Path, sbom_file: Path, tmp_path: Path
) -> None:
    out_base = tmp_path / "policy-report"
    result = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--policy",
            str(policy_file),
            "--sbom",
            str(sbom_file),
            "--format",
            "json",
            "--format",
            "markdown",
            "--output",
            str(out_base),
        ],
    )
    assert result.exit_code in (0, 1, 2), result.output
    assert (tmp_path / "policy-report.json").exists()
    assert (tmp_path / "policy-report.md").exists()


def test_check_reads_paths_from_nuguard_yaml(
    policy_file: Path, sbom_file: Path, tmp_path: Path
) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text(
        f"sbom: {sbom_file}\npolicy: {policy_file}\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["policy", "check", "--config", str(cfg)])
    assert result.exit_code in (0, 1, 2), result.output


def test_check_compliance_framework(sbom_file: Path) -> None:
    result = runner.invoke(
        app,
        [
            "policy",
            "check",
            "--sbom",
            str(sbom_file),
            "--framework",
            "owasp-llm-top10",
        ],
    )
    assert result.exit_code in (0, 1, 2), result.output


def test_check_missing_sbom_exits(policy_file: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["policy", "check", "--policy", str(policy_file), "--sbom", str(tmp_path / "nope.json")],
    )
    assert result.exit_code != 0


def test_check_missing_policy_exits(sbom_file: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["policy", "check", "--policy", str(tmp_path / "nope.md"), "--sbom", str(sbom_file)],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# nuguard policy show
# ---------------------------------------------------------------------------


def test_show_unknown_policy_exits() -> None:
    """The stub show command reports "not found" until a registry exists (issue #162)."""
    result = runner.invoke(app, ["policy", "show", "--policy-id", "nonexistent-id"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "not found" in (result.stderr or "").lower()


def test_show_command_is_hidden_from_help() -> None:
    """``policy show`` is a deprecated stub and must be hidden from --help output."""
    result = runner.invoke(app, ["policy", "--help"])
    assert result.exit_code == 0
    assert "show" not in result.output
