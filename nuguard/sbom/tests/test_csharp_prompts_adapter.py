"""Tests for C# prompt-constant extractor adapter."""

from __future__ import annotations

from typing import Any

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.csharp import CSharpPromptAdapter
from nuguard.sbom.core.csharp_parser import parse_csharp
from nuguard.sbom.types import ComponentType


def _extract(
    adapter: Any,
    source: str,
    path: str = "Prompts.cs",
) -> list[ComponentDetection]:
    return adapter.extract(source, path, parse_csharp(source, path))


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [d for d in detections if d.component_type == component_type]


# ---------------------------------------------------------------------------
# Const string prompts
# ---------------------------------------------------------------------------


def test_const_string_with_prompt_keywords_detected() -> None:
    source = '''public static class Prompts
{
    const string SystemPrompt =
        "You are a helpful assistant. Answer the question based on the context provided. "
        + "Respond in JSON format with the following fields: answer, confidence.";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) >= 1
    assert prompts[0].metadata["assigned_to"] == "SystemPrompt"
    assert prompts[0].confidence >= 0.80


def test_const_string_prompt_name_derived_from_field() -> None:
    source = '''class ChatService
{
    const string RAG_PROMPT =
        "You are a helpful assistant. Given the following context, answer the question "
        + "based on the data provided. If you don't know, say you don't know.";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) >= 1
    assert "Rag" in prompts[0].display_name or "RAG" in prompts[0].display_name


# ---------------------------------------------------------------------------
# Static readonly prompts
# ---------------------------------------------------------------------------


def test_static_readonly_prompt_detected() -> None:
    source = '''class Agent
{
    private static readonly string SystemMessage =
        "You are an expert data analyst. "
        + "Your task is to summarize the provided data and return json output.";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) >= 1
    assert prompts[0].metadata["role"] == "system"


# ---------------------------------------------------------------------------
# Interpolated prompts with template variables
# ---------------------------------------------------------------------------


def test_interpolated_prompt_with_template_vars() -> None:
    source = '''class Agent
{
    const string Prompt =
        "You are a helpful assistant. Answer the question: {question} given the context: {context}.";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) >= 1
    assert prompts[0].metadata["is_template"] is True
    assert "question" in prompts[0].metadata["template_variables"]
    assert "context" in prompts[0].metadata["template_variables"]


# ---------------------------------------------------------------------------
# Prompt-context variable names (strong signal)
# ---------------------------------------------------------------------------


def test_prompt_field_name_triggers_detection() -> None:
    source = '''class Config
{
    const string system_prompt = "You are a helpful assistant that summarizes text.";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) >= 1


# ---------------------------------------------------------------------------
# Role detection
# ---------------------------------------------------------------------------


def test_role_detected_from_markers() -> None:
    source = '''class Agent
{
    const string SysPrompt =
        "system: You are a security expert. user: {input}";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) >= 1
    assert prompts[0].metadata["role"] == "system"


# ---------------------------------------------------------------------------
# Injection risk scoring
# ---------------------------------------------------------------------------


def test_injection_risk_scored_for_user_input_vars() -> None:
    source = '''class Agent
{
    const string Prompt =
        "You are helpful. Answer the question: {userInput} based on context.";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) >= 1
    assert prompts[0].metadata["injection_risk_score"] > 0.0


# ---------------------------------------------------------------------------
# No false positives
# ---------------------------------------------------------------------------


def test_short_string_not_detected() -> None:
    source = '''class Config
{
    const string Version = "1.0.0";
    const string Name = "MyApp";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    assert _by_type(detections, ComponentType.PROMPT) == []


def test_non_prompt_const_string_ignored() -> None:
    source = '''class Config
{
    const string ConnectionString = "Server=localhost;Database=test";
    const string ApiKey = "sk-1234567890abcdef";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    assert _by_type(detections, ComponentType.PROMPT) == []


def test_string_in_method_body_not_const_ignored() -> None:
    source = '''class Service
{
    void Log()
    {
        logger.Info("You are being logged");
    }
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    assert _by_type(detections, ComponentType.PROMPT) == []


def test_empty_or_short_string_in_const_not_detected() -> None:
    source = '''class Config
{
    const string Empty = "";
    const string Short = "hi";
}
'''
    detections = _extract(CSharpPromptAdapter(), source)
    assert _by_type(detections, ComponentType.PROMPT) == []


# ---------------------------------------------------------------------------
# Credential redaction and performance
# ---------------------------------------------------------------------------


def test_long_letters_only_literal_is_linear_time() -> None:
    import time

    source = 'class C\n{\n    const string Blob = "' + ("abcdef" * 17_000) + '";\n}\n'
    start = time.perf_counter()
    _extract(CSharpPromptAdapter(), source)
    assert time.perf_counter() - start < 2.0


def test_url_password_with_apostrophe_redacted() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. Connect to mongodb://user:pa'S3CRET@db.example.com now.";
}
'''
    prompts = _by_type(_extract(CSharpPromptAdapter(), source), ComponentType.PROMPT)
    assert prompts
    for p in prompts:
        assert "S3CRET" not in p.metadata["content"]
        assert "S3CRET" not in (p.snippet or "")


def test_url_token_as_username_redacted() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. Clone https://ghp_S3CRETtoken@github.com/org/repo first.";
}
'''
    prompts = _by_type(_extract(CSharpPromptAdapter(), source), ComponentType.PROMPT)
    assert len(prompts) == 1
    assert "S3CRET" not in prompts[0].metadata["content"]
    assert "github.com/org/repo" in prompts[0].metadata["content"]


def test_kv_secret_with_spaces_fully_redacted() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. Use Password=my S3CRET pass phrase; Database=bank for lookups.";
}
'''
    prompts = _by_type(_extract(CSharpPromptAdapter(), source), ComponentType.PROMPT)
    assert len(prompts) == 1
    content = prompts[0].metadata["content"]
    assert "S3CRET" not in content
    assert "pass phrase" not in content
    assert "Database=bank" in content


def test_url_userinfo_missed_by_redaction_drops_literal() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. Backup target is 1mongodb://admin:S3CRET@db.example.com now.";
}
'''
    for p in _extract(CSharpPromptAdapter(), source):
        assert "S3CRET" not in str(p.metadata)
        assert "S3CRET" not in (p.snippet or "")


def test_handle_url_prompt_kept() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. Refer users to https://x.com/@handle for updates.";
}
'''
    prompts = _by_type(_extract(CSharpPromptAdapter(), source), ComponentType.PROMPT)
    assert len(prompts) == 1
    assert "https://x.com/@handle" in prompts[0].metadata["content"]


def test_api_key_value_redacted_prompt_kept() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. Call the tool with api_key=abc123 and summarize.";
}
'''
    prompts = _by_type(_extract(CSharpPromptAdapter(), source), ComponentType.PROMPT)
    assert len(prompts) == 1
    content = prompts[0].metadata["content"]
    assert "abc123" not in content
    assert "api_key=<redacted>" in content
