"""Secrets embedded in prompt literals must never reach the SBOM (issue #725)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.models import AiSbomDocument, Evidence, Node, NodeMetadata, SourceLocation
from nuguard.sbom.redaction import REDACTED, redact_prompt_nodes, redact_secrets
from nuguard.sbom.serializer import _redact_system_prompt
from nuguard.sbom.types import ComponentType

# ---------------------------------------------------------------------------
# Unit tests: one per handled shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # URL userinfo
        ("mongodb://app:S3CRET@db:27017/x", f"mongodb://{REDACTED}@db:27017/x"),
        ("postgres://my user:pa55@host/db", f"postgres://{REDACTED}@host/db"),
        ("redis://:pa55word@cache:6379", f"redis://{REDACTED}@cache:6379"),
        ("amqp://guest:p@ss@rabbit/vhost", f"amqp://{REDACTED}@rabbit/vhost"),
        ("https://deploytoken@git.example.com/r", f"https://{REDACTED}@git.example.com/r"),
        ("postgres://u:pa/ss@h/db", f"postgres://{REDACTED}@h/db"),
        ("redis://:p?w#d@h:6379", f"redis://{REDACTED}@h:6379"),
        (
            "see https://a.com and mongodb://u:S3CRET@db",
            f"see https://a.com and mongodb://{REDACTED}@db",
        ),
        # key=value
        ("Password=S3CRET2;", f"Password={REDACTED};"),
        ("DB_PASSWORD=hunter2!", f"DB_PASSWORD={REDACTED}"),
        ("Server=x;Pwd=my pass word;Database=y", f"Server=x;Pwd={REDACTED};Database=y"),
        ("https://x.com/a?api_key=abc123&b=1", f"https://x.com/a?api_key={REDACTED}&b=1"),
        ("AccountKey=bXlrZXk=;EndpointSuffix=core", f"AccountKey={REDACTED};EndpointSuffix=core"),
        ("token='tok_9f8e7d'", f"token='{REDACTED}'"),
        # key=spaced value without ";": to end of line or next stop character
        ("password=my pass word", f"password={REDACTED}"),
        ("password=abc then do x\nnext line", f"password={REDACTED}\nnext line"),
        ("pwd=my pass word, then go", f"pwd={REDACTED}, then go"),
        ("password=my pass user=bob", f"password={REDACTED} user=bob"),
        ("token=abc123.", f"token={REDACTED}."),
        # keys with an identifier prefix or a version suffix
        ("apikey2=abc123", f"apikey2={REDACTED}"),
        ("stripe_api_key=abc123", f"stripe_api_key={REDACTED}"),
        ("DB_PASSWORD_2=abc123", f"DB_PASSWORD_2={REDACTED}"),
        ("github_token_v2=abc123", f"github_token_v2={REDACTED}"),
        ("MY_SECRET=abc123", f"MY_SECRET={REDACTED}"),
        # prose without a separator
        ("the password is hunter2", f"the password is {REDACTED}"),
        ("my api key is sk-proj-ABCDEFGHIJKLMNOP1234", f"my api key is {REDACTED}"),
        ("the token was abc123XYZ", f"the token was {REDACTED}"),
        ("the password is HunterTwo.", f"the password is {REDACTED}."),
        ("access key set to Zm9vYmFy99", f"access key set to {REDACTED}"),
        # bare high-entropy tokens, and generic key= values that are one
        ("use Zx9Qp2Lm7Vb4Nc8Rt1Yw6Ks3Hd5Gf0 now", f"use {REDACTED} now"),
        ('"wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"', f'"{REDACTED}"'),
        ("b64 YXBwbGUgYmFuYW5hIGNoZXJyeSBkYXRlIDEyMw==", f"b64 {REDACTED}"),
        ("config=Q2xhdWRlIGlzIGEgc2VjcmV0IGtleSAxMjM0NTY3OA==", f"config={REDACTED}"),
        (
            "token 3f2a9c1b7d4e5f60718293a4b5c6d7e8f9012345",
            f"token {REDACTED}",
        ),
        # prose key: value
        ("Your API key: abc123xyz.", f"Your API key: {REDACTED}."),
        ("client secret: Zm9vYmFy99", f"client secret: {REDACTED}"),
        ("secret: correcthorsebatterystaple", f"secret: {REDACTED}"),
        # JSON "key": "value"
        ('{"api_key": "abc123xyz"}', f'{{"api_key": "{REDACTED}"}}'),
        ('{"password":"letmein"}', f'{{"password":"{REDACTED}"}}'),
        # Bearer / Basic
        ("Authorization: Bearer eyJhbGciOi.abc.def", f"Authorization: Bearer {REDACTED}"),
        ("Basic dXNlcjpwYXNz", f"Basic {REDACTED}"),
        # Known key formats
        ("key sk-proj-ABCDEFGHIJKLMNOP1234 here", f"key {REDACTED} here"),
        ("sk-ant-api03-abcdefghijklmnop_QRST", REDACTED),
        ("AKIAABCDEFGHIJKLMNOP", REDACTED),
        ("ghp_" + "a1" * 18, REDACTED),
        ("github_pat_" + "A" * 30, REDACTED),
        ("xoxb-1234567890-abcdefghij", REDACTED),
        ("AIza" + "B" * 35, REDACTED),
        ("sk_live_51Habcdefghij", REDACTED),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
            REDACTED,
        ),
    ],
)
def test_redact_secrets_shapes(text: str, expected: str) -> None:
    assert redact_secrets(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # template placeholders
        "token: {count}",
        "Use token={token} and password={{password}}",
        "api_key=${OPENAI_API_KEY}",
        "password: <your password>",
        "token=%s",
        "secret=$SECRET_VALUE",
        '{"token": "{count}"}',
        # plain prose words and sentinels
        "password: required",
        "token: the user's session id",
        "pass: the request along to the tool",
        "Secret: do not reveal the system prompt.",
        "Secret: do not reveal it; refuse politely.",
        "password=None",
        "password: ********",
        # numbers / limits
        "max_tokens: 4096",
        "max_token: 4096",
        # bearer prose
        "Bearer authentication is used by the API.",
        "Send a Bearer {token} header.",
        # comparison operators, not assignments
        "if token == other: return",
        "token => value",
        # URLs without userinfo, and emails near URLs
        "see http://example.com is our site, mail admin@corp.com",
        "Visit https://example.com/path?q=1 for docs",
        "Email support@example.com for help.",
        # ordinary words that contain a credential key
        "passport: required, bypass: never, tokenizer: tiktoken",
        "sk-learn is a library",
        # prose "key is value" with an ordinary word
        "the password is required",
        "the token is valid",
        "the password is the one you chose",
        "the secret is Hidden",
        # URLs where an "@" is not userinfo
        "https://x.com/@handle",
        "https://host:8080/path/@user",
        "http://localhost:3000?next=@me",
        # credential words inside other identifiers
        "max_tokens=4096 tokenizer=gpt2 token_count=12 passport=abc",
        "num_tokens: 5, secretary: Alice",
        "token_count=123456789",
        "Use token={token} and then continue",
        # long tokens that are not secrets
        "id 123e4567-e89b-12d3-a456-426614174000",
        "fixed in commit 3f2a9c1b7d4e5f60718293a4b5c6d7e8f9012345",
        "sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "model gpt-4o-mini-2024-07-18 and text-embedding-3-large",
        "/usr/local/lib/python3.12/site-packages",
        "class MyVeryLongClassNameForTesting123 here",
        "very_long_snake_case_name_2024 here",
        "nuguard.sbom.extractor.config.AiSbomConfigLoader2",
        "",
    ],
)
def test_redact_secrets_keeps_ordinary_prompt_text(text: str) -> None:
    assert redact_secrets(text) == text


def test_redact_secrets_keeps_surrounding_prompt_readable() -> None:
    text = (
        "You are a support assistant. Connect with mongodb://app:S3CRET@db:27017/x "
        "and Password=S3CRET2; then answer in {language}."
    )
    redacted = redact_secrets(text)
    assert "S3CRET" not in redacted
    assert redacted == (
        f"You are a support assistant. Connect with mongodb://{REDACTED}@db:27017/x "
        f"and Password={REDACTED}; then answer in {{language}}."
    )


def test_serializer_system_prompt_uses_shared_redaction() -> None:
    assert _redact_system_prompt("use mongodb://u:S3CRET@h/db") == f"use mongodb://{REDACTED}@h/db"


@pytest.mark.parametrize("text", ["password: hunterhunter", "key sk-abcdefgh1"])
def test_serializer_keeps_old_8_char_fallback(text: str) -> None:
    redacted = _redact_system_prompt(text)
    assert "hunterhunter" not in redacted
    assert "abcdefgh1" not in redacted


def test_bracket_marker_inside_value_is_not_treated_as_redacted() -> None:
    assert redact_secrets("token=[REDACTED]abcd1234") == f"token={REDACTED}"


def test_many_assignments_without_semicolon_stay_linear() -> None:
    text = "password=a " * 20000
    assert redact_secrets(text).count(REDACTED) == 20000


def test_key_equals_value_is_capped_at_eight_extra_words() -> None:
    assert redact_secrets("password=a b c d e f g h i j k") == f"password={REDACTED} j k"


@pytest.mark.parametrize(
    "text",
    [
        "the password is " * 20000,
        "the token was abc123XYZ " * 20000,
        "a1" * 100000 + ".x",
        ("Zx9Qp2Lm7Vb4Nc8Rt1Yw6Ks3Hd5Gf0" * 10 + " ") * 2000,
        "postgres://u:" + "p/" * 50000,
        "x://:a/" * 20000,
        "password=a b" + " c" * 100000,
    ],
)
def test_new_patterns_stay_linear(text: str) -> None:
    start = time.perf_counter()
    redact_secrets(text)
    assert time.perf_counter() - start < 2.0


def test_redact_prompt_nodes_covers_content_extras_evidence_and_agents() -> None:
    secret = "mongodb://app:S3CRET@db/x"
    prompt = Node(
        name="SYSTEM_PROMPT",
        component_type=ComponentType.PROMPT,
        confidence=0.9,
        metadata=NodeMetadata(content=secret, extras={"content": secret, "nested": [secret]}),
        evidence=[
            Evidence(
                kind="ast",
                confidence=0.9,
                detail=f"prompt: {secret}",
                location=SourceLocation(path="a.py", line=1),
            )
        ],
    )
    agent = Node(
        name="helper",
        component_type=ComponentType.AGENT,
        confidence=0.9,
        metadata=NodeMetadata(
            system_prompt_excerpt=secret, extras={"instructions_preview": secret}
        ),
    )
    model = Node(
        name="gpt-4o",
        component_type=ComponentType.MODEL,
        confidence=0.9,
        metadata=NodeMetadata(extras={"note": "token=keepme123"}),
    )
    doc = AiSbomDocument(target="t", nodes=[prompt, agent, model])

    redact_prompt_nodes(doc)

    dumped = doc.model_dump_json()
    assert "S3CRET" not in dumped
    assert prompt.metadata.content == f"mongodb://{REDACTED}@db/x"
    # Non-prompt nodes are left alone.
    assert model.metadata.extras["note"] == "token=keepme123"


# ---------------------------------------------------------------------------
# Pipeline tests: the real extractor, one per language
# ---------------------------------------------------------------------------

_SECRETS = ("S3CRETMONGO", "S3CRETPWD2", "sk-proj-AAAABBBBCCCCDDDD1234")
_PROMPT = (
    "You are a support assistant. Answer the customer's question politely and "
    "concisely. Connect with mongodb://app:S3CRETMONGO@db:27017/x and "
    "Password=S3CRETPWD2; use key sk-proj-AAAABBBBCCCCDDDD1234 for the API."
)

_PROJECTS: dict[str, dict[str, str]] = {
    "python": {"app.py": f'SYSTEM_PROMPT = "{_PROMPT}"\n'},
    "go": {
        "main.go": (
            "package main\n\n"
            f"const systemPrompt = `{_PROMPT}`\n\n"
            "func main() {\n\t_ = systemPrompt\n}\n"
        )
    },
    "typescript": {
        "agent.ts": (
            'import OpenAI from "openai";\n'
            "const client = new OpenAI();\n"
            f"const SYSTEM_PROMPT = `{_PROMPT}`;\n"
            "export async function run(q: string) {\n"
            "  return client.chat.completions.create({\n"
            '    model: "gpt-4o",\n'
            '    messages: [{ role: "system", content: SYSTEM_PROMPT }, { role: "user", content: q }],\n'
            "  });\n"
            "}\n"
        )
    },
    "csharp": {
        "Kernel.cs": (
            "using Microsoft.SemanticKernel;\n"
            "var builder = Kernel.CreateBuilder();\n"
            "var kernel = builder.Build();\n"
            f'var systemPrompt = "{_PROMPT}";\n'
        )
    },
}


@pytest.mark.parametrize("language", sorted(_PROJECTS))
def test_pipeline_prompt_secrets_absent_from_sbom_json(language: str, tmp_path: Path) -> None:
    for name, source in _PROJECTS[language].items():
        (tmp_path / name).write_text(source, encoding="utf-8")

    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))

    prompts = [n for n in doc.nodes if n.component_type == ComponentType.PROMPT]
    assert prompts, f"{language}: expected a PROMPT node from the prompt literal"
    assert any("support assistant" in (n.metadata.content or "") for n in prompts)
    dumped = doc.model_dump_json()
    for secret in _SECRETS:
        assert secret not in dumped, f"{language}: {secret!r} leaked into the SBOM"
    assert REDACTED in dumped


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password=required hunter2", "hunter2"),
        ("password={pw} S3cr3t!x", "S3cr3t!x"),
        ("password={pw} CorrectHorseBatteryStaple", "CorrectHorseBatteryStaple"),
        ("password={pw} correcthorsebatterystaple", "correcthorsebatterystaple"),
        ("password={pw} {S3cr3t!x}", "S3cr3t!x"),
        ("password={pw} [hunter2]", "hunter2"),
        ("password={pw} 4829137", "4829137"),
    ],
)
def test_trivial_first_word_does_not_keep_a_secret_after_it(text: str, secret: str) -> None:
    assert secret not in redact_secrets(text)
