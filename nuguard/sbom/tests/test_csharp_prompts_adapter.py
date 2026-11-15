"""Tests for C# prompt-constant extractor adapter."""

from __future__ import annotations

from typing import Any

import pytest

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


@pytest.mark.parametrize(
    "secret",
    [
        'Password=pa\\"S3CRET pass;',
        "Password='pa'S3CRET;",
        'token=\\"pa\\"S3CRET&x=1',
        'Password=\\"S3;CRET\\"',
        "Password='S3&CRET'",
        'Password=\\"S3 pass; CRET',
    ],
)
def test_kv_secret_with_stray_quotes_fully_redacted(secret: str) -> None:
    source = (
        "class Agent\n{\n    const string SystemPrompt =\n"
        f'        "You are a helpful assistant. Connect with {secret} Database=bank for lookups.";\n}}\n'
    )
    for p in _extract(CSharpPromptAdapter(), source):
        assert "CRET" not in str(p.metadata)
        assert "CRET" not in (p.snippet or "")


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


def test_url_userinfo_with_space_in_user_redacted() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. Connect to mongodb://user name:pw@host/db now.";
}
'''
    for p in _extract(CSharpPromptAdapter(), source):
        assert "pw" not in str(p.metadata)
        assert "pw" not in (p.snippet or "")


def test_url_before_spaced_userinfo_url_kept() -> None:
    source = '''class Agent
{
    const string SystemPrompt =
        "You are a helpful assistant. See https://a.com and mongodb://u:S3CRET@db now.";
}
'''
    prompts = _by_type(_extract(CSharpPromptAdapter(), source), ComponentType.PROMPT)
    assert len(prompts) == 1
    content = prompts[0].metadata["content"]
    assert "https://a.com" in content
    assert "S3CRET" not in content
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


def test_long_literal_with_scheme_is_linear_time() -> None:
    import time

    source = 'class C\n{\n    const string Blob = "' + ("abcdef" * 17_000) + ' see x://y";\n}\n'
    start = time.perf_counter()
    _extract(CSharpPromptAdapter(), source)
    assert time.perf_counter() - start < 2.0


@pytest.mark.parametrize(
    "literal",
    [
        '@"You are a helpful assistant. Connect to mongodb://user:S3""CRET@db.example.com now."',
        '"You are a helpful assistant. Connect to mongodb://user:S3\\"CRET@db.example.com now."',
        '"""\n        You are a helpful assistant. Connect to mongodb://user:S3"CRET@db.example.com now.\n        """',
    ],
)
def test_url_password_with_double_quote_redacted(literal: str) -> None:
    source = f"class Agent\n{{\n    const string SystemPrompt =\n        {literal};\n}}\n"
    detections = _extract(CSharpPromptAdapter(), source)
    for p in detections:
        assert "CRET" not in str(p.metadata)
        assert "CRET" not in (p.snippet or "")
    prompts = _by_type(detections, ComponentType.PROMPT)
    assert len(prompts) == 1
    assert "mongodb://<redacted>@db.example.com" in prompts[0].metadata["content"]


@pytest.mark.parametrize(
    "url",
    ["See https://a.com and 1mongodb://u:S3CRET@db", '1mongodb://u:S3\\"CRET@db'],
)
def test_url_userinfo_missed_by_redaction_variants_drop_literal(url: str) -> None:
    source = (
        "class Agent\n{\n    const string SystemPrompt =\n"
        f'        "You are a helpful assistant. {url} now.";\n}}\n'
    )
    for p in _extract(CSharpPromptAdapter(), source):
        assert "CRET" not in str(p.metadata)
        assert "CRET" not in (p.snippet or "")


@pytest.mark.parametrize(
    "key",
    ["pwd", "passwd", "pass", "DB_PASSWORD", "secret_key", "private_key"],
)
def test_kv_secret_key_variants_redacted(key: str) -> None:
    source = (
        "class Agent\n{\n    const string SystemPrompt =\n"
        f'        "You are a helpful assistant. Use server=db;{key}=S3CRET;database=x for lookups.";\n}}\n'
    )
    prompts = _by_type(_extract(CSharpPromptAdapter(), source), ComponentType.PROMPT)
    assert len(prompts) == 1
    content = prompts[0].metadata["content"]
    assert "S3CRET" not in content
    assert f"{key}=<redacted>" in content


_SPACED_PASSWORD_URLS = [
    "mongodb://user:pa' S3CRET@db/",
    "mongodb://user:pa S3CRET@db/",
    "mongodb://user:pa  S3CRET@db/",
    "mongodb://user:a b c S3CRET@db/",
    "mongodb://us er:pa S3CRET@db/",
    "mongodb://user:pa' S3CRET@db:27017/x?retryWrites=true",
]


@pytest.mark.parametrize("url", _SPACED_PASSWORD_URLS)
def test_url_password_with_space_redacted(url: str) -> None:
    from nuguard.sbom.adapters.csharp.prompts import (
        _has_unredacted_url_userinfo,
        _redact_credentials,
    )

    assert "S3CRET" not in _redact_credentials(url)
    assert "://<redacted>@db" in _redact_credentials(url)
    # Safety net must flag the raw shape on its own.
    assert _has_unredacted_url_userinfo(url)
    assert not _has_unredacted_url_userinfo(_redact_credentials(url))


@pytest.mark.parametrize(
    "text",
    [
        "see https://example.com/docs for details",
        "contact a@b.com",
        "see http://host:8080 or mail a@b.com",
        "see https://example.com/docs for details, mail a@b.com",
        "mongodb://<redacted>@db:27017/x and mail a@b.com",
    ],
)
def test_ordinary_prose_not_overredacted(text: str) -> None:
    from nuguard.sbom.adapters.csharp.prompts import (
        _has_unredacted_url_userinfo,
        _redact_credentials,
    )

    assert _redact_credentials(text) == text
    assert not _has_unredacted_url_userinfo(text)


@pytest.mark.parametrize("url", _SPACED_PASSWORD_URLS)
def test_url_password_with_space_never_emitted(url: str) -> None:
    source = (
        "class Agent\n{\n    const string SystemPrompt =\n"
        f'        "You are a helpful assistant. Connect with {url} now and answer.";\n}}\n'
    )
    for p in _extract(CSharpPromptAdapter(), source):
        assert "S3CRET" not in str(p.metadata)
        assert "S3CRET" not in (p.snippet or "")


def test_spaced_password_regex_is_linear() -> None:
    import time

    from nuguard.sbom.adapters.csharp.prompts import (
        _has_unredacted_url_userinfo,
        _redact_credentials,
    )

    # Long runs of spaces/tabs once let the password words split the run in
    # many ways (polynomial backtracking); each case must stay fast.
    cases = [
        "mongodb://u:" + "a " * 100_000,
        "mongodb://u:a" + " " * 4_000 + "x",
        "mongodb://u:" + " \t" * 4_000,
        "mongodb://u:a" + (" " * 50 + "b") * 80,
    ]
    for text in cases:
        start = time.perf_counter()
        _redact_credentials(text)
        _has_unredacted_url_userinfo(text)
        assert time.perf_counter() - start < 2.0


def test_url_password_trailing_blank_before_at_redacted() -> None:
    from nuguard.sbom.adapters.csharp.prompts import (
        _has_unredacted_url_userinfo,
        _redact_credentials,
    )

    for raw in (
        "mongodb://u:pass @host/",
        "mongodb://u:pass\t@host/",
        "mongodb://u:pa S3CRET @db/",
        "mongodb://u:pa S3CRET \t @db/",
    ):
        assert _has_unredacted_url_userinfo(raw), raw
        redacted = _redact_credentials(raw)
        assert "pass" not in redacted and "S3CRET" not in redacted, redacted
        assert not _has_unredacted_url_userinfo(redacted), redacted
