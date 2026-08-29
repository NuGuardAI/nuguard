"""CLI tests for ``nuguard init``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from nuguard.cli.main import app

runner = CliRunner()


def test_init_creates_starter_files(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    cognitive_policy = tmp_path / "cognitive-policy.md"
    canary = tmp_path / "canary.example.json"
    config = tmp_path / "nuguard.yaml"

    assert cognitive_policy.exists()
    assert canary.exists()
    assert config.exists()

    assert cognitive_policy.read_text(encoding="utf-8") == (
        "# Cognitive Policy\n\n"
        "## Allowed Topics\n\n"
        "## Restricted Topics\n\n"
        "## Restricted Actions\n\n"
        "## HITL Triggers\n\n"
        "## Data Classification\n\n"
        "## Rate Limits\n"
    )


def test_init_does_not_overwrite_without_force(tmp_path: Path) -> None:
    cognitive_policy = tmp_path / "cognitive-policy.md"
    cognitive_policy.parent.mkdir(parents=True, exist_ok=True)
    cognitive_policy.write_text("existing\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert cognitive_policy.read_text(encoding="utf-8") == "existing\n"
    assert "skipped" in result.output


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    cognitive_policy = tmp_path / "cognitive-policy.md"
    cognitive_policy.parent.mkdir(parents=True, exist_ok=True)
    cognitive_policy.write_text("existing\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--dir", str(tmp_path), "--force"])

    assert result.exit_code == 0, result.output
    assert "## Allowed Topics" in cognitive_policy.read_text(encoding="utf-8")


def test_init_llm_with_config_uses_configs_own_model(tmp_path: Path, monkeypatch) -> None:
    """--llm --config <file> must source the drafting LLM from that file's own
    llm.model/llm.api_key, not fall back to generic env-var credential
    discovery — which would otherwise pick up the *target app's* own LLM key
    (e.g. a GEMINI_API_KEY meant for the app under test, not for nuguard),
    silently burning that app's quota. See tests/apps/ChapterApps.
    """
    other_yaml = tmp_path / "other-config" / "nuguard.yaml"
    other_yaml.parent.mkdir(parents=True, exist_ok=True)
    other_yaml.write_text(
        "llm:\n"
        "  model: azure/DeepSeek-V4-Pro\n"
        "  api_key: a-distinct-test-key\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class _FakeLLMClient:
        def __init__(self, model=None, api_key=None, api_base=None, **_kwargs):
            captured["model"] = model
            captured["api_key"] = api_key
            captured["api_base"] = api_base
            self.api_key = api_key

    monkeypatch.setattr("nuguard.common.llm_client.LLMClient", _FakeLLMClient)

    result = runner.invoke(
        app, ["init", "--dir", str(tmp_path), "--llm", "--config", str(other_yaml)]
    )

    assert result.exit_code == 0, result.output
    assert captured["model"] == "azure/DeepSeek-V4-Pro"
    assert captured["api_key"] == "a-distinct-test-key"
