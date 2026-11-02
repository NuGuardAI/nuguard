"""The C# datastore and prompt-constant adapters (#409, #411) must run in the
real pipeline, not only when their ``.extract()`` is called directly."""

from __future__ import annotations

import pytest

from nuguard.sbom.adapters.csharp import CSharpDatastoreAdapter, CSharpPromptAdapter
from nuguard.sbom.adapters.registry import default_framework_adapters
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.tests.conftest import CS_ONLY, nodes
from nuguard.sbom.types import ComponentType


def test_datastore_and_prompt_adapters_are_registered() -> None:
    registered = {type(a) for a in default_framework_adapters()}

    assert CSharpDatastoreAdapter in registered
    assert CSharpPromptAdapter in registered


def test_pipeline_emits_csharp_datastore_and_prompt_constant(tmp_path) -> None:
    (tmp_path / "SupportAgent.cs").write_text(
        "using Microsoft.EntityFrameworkCore;\n\n"
        "public class TicketDb : DbContext { }\n\n"
        "public static class SupportAgent\n{\n"
        '    public const string SystemPrompt = "You are a support assistant. '
        'Answer the question using only the ticket history.";\n'
        "}\n"
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)

    providers = {n.metadata.extras.get("provider") for n in nodes(doc, ComponentType.DATASTORE)}
    prompts = nodes(doc, ComponentType.PROMPT)
    assert "efcore" in providers, providers
    assert any(e.location.path == "SupportAgent.cs" for p in prompts for e in p.evidence), [
        p.name for p in prompts
    ]


@pytest.mark.parametrize(
    ("ctor", "conn", "kept"),
    [
        ("NpgsqlConnection", "Host=db;Username=app;Password=S3CRET;Database=tickets", "Host=db"),
        # ADO.NET splits on ';' only, so ',' stays inside the password value.
        ("SqlConnection", "Server=db;Password=a,Database=S3CRET", "Server=db"),
        # Quotes or braces let a value hold ';' or '=': no preview at all.
        ("SqlConnection", "Server=db;User ID=sa;Password='p;w,S3CRET';Database=t", None),
        ("SqlConnection", 'Server=db;Pwd="a,b;S3CRET";Initial Catalog=t', None),
        ("SqlConnection", "Server=db;Password={a;Database=S3CRET}", None),
        # Escapes decode to separators the parser can't see ('\\u003B' is ';').
        ("NpgsqlConnection", "Host=db\\u003BPassword=S3CRET", None),
        ("MongoClient", "mongodb://u:p\\u0040S3CRET@db/x", None),
        # '@' outside the parsed netloc ('/' inside a password): no preview.
        ("MongoClient", "mongodb://app:pa/ss@S3CRET@db:27017/x", None),
        ("MongoClient", "mongodb://u:123/S3CRET@db/x", None),
        ("MongoClient", "mongodb://app:p%40ss@S3CRET@db:27017/x", "mongodb://db:27017"),
        # Path and query (where secrets can hide) are never kept.
        (
            "MongoClient",
            "mongodb://db/x?authMechanismProperties=AWS_SESSION_TOKEN:S3CRET",
            "mongodb://db",
        ),
        ("ConnectionMultiplexer", "rediss://:S3CRET@cache:6380", "rediss://cache:6380"),
        # urlsplit takes "db;password=..." as a host; strict host grammar rejects it.
        ("MongoClient", "mongodb://db;password=S3CRET/x", None),
        ("MongoClient", "mongodb://h1,h2;pwd=S3CRET:27017/x", None),
        ("MongoClient", "mongodb://app:pw@h1,h2:27017/x", "mongodb://h1,h2:27017"),
        # Option strings get no preview (only redis:// URLs do); evidence still redacted.
        ("ConnectionMultiplexer", "cache:6379,password=S3CRET,ssl=true", None),
    ],
)
def test_connection_string_credentials_are_redacted(tmp_path, ctor, conn, kept) -> None:
    """Connection strings in source must not carry credentials into the SBOM."""
    usings = {
        "NpgsqlConnection": "Npgsql",
        "SqlConnection": "Microsoft.Data.SqlClient",
        "MongoClient": "MongoDB.Driver",
        "ConnectionMultiplexer": "StackExchange.Redis",
    }
    literal = conn.replace('"', '""')
    (tmp_path / "Db.cs").write_text(
        f"using {usings[ctor]};\n\npublic class Db\n{{\n"
        f'    {ctor} client = new {ctor}(@"{literal}");\n}}\n'
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)

    dumped = doc.model_dump_json()
    assert "s3cret" not in dumped.lower()
    previews = [
        n.metadata.extras.get("connection_string_preview")
        for n in nodes(doc, ComponentType.DATASTORE)
    ]
    if kept is not None:
        assert any(p and p.startswith(kept) for p in previews), previews


@pytest.mark.parametrize(
    "snippet",
    [
        'new NpgsqlConnection("Host=db;Password=S3CR',  # cut off by truncation
        'new SqlConnection(@"C:\\" + "Password=S3CRET")',  # verbatim: '\\' is no escape
        'new SqlConnection($"Password={pw}S3CRET")',
        'new SqlConnection("""Password=S3CRET""")',
    ],
)
def test_snippet_literals_are_redacted(snippet: str) -> None:
    from nuguard.sbom.adapters.csharp.datastores import _redact_string_literals

    redacted = _redact_string_literals(snippet)

    assert "s3cret" not in redacted.lower()
    assert redacted.endswith('"[REDACTED]"')


def test_escaped_separator_in_regular_literal_is_not_previewed(tmp_path) -> None:
    (tmp_path / "Db.cs").write_text(
        "using Npgsql;\n\npublic class Db\n{\n"
        '    NpgsqlConnection pg = new NpgsqlConnection("Host=db\\u003BPassword=S3CRET");\n}\n'
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)

    assert "s3cret" not in doc.model_dump_json().lower()


@pytest.mark.parametrize(
    "secret_literal",
    [
        "mongodb://user:S3CRET@db.example.com:27017/tickets?appName=openai:support"
        "&retryWrites=true&w=majority&readPreference=primary",
        # '/' inside the userinfo
        "https://u:pa/ss@S3CRET@api.example.com/v1 You are a helpful assistant. "
        "Answer the question and respond in JSON for the user: request.",
        "You are a helpful assistant. Answer the question. ApiKey=S3CRET; respond in JSON.",
        "You are a helpful assistant. Answer the question. client_secret=S3CRET summarize",
        "You are a helpful assistant. Answer the question. SharedAccessSignature=S3CRET ok",
    ],
)
def test_credential_literal_is_never_a_prompt(tmp_path, secret_literal: str) -> None:
    """A long connection string that happens to hold a prompt cue ("ai:") is not
    emitted as PROMPT content."""
    (tmp_path / "Cfg.cs").write_text(
        f'public static class Cfg\n{{\n    public const string Conn = "{secret_literal}";\n}}\n'
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)

    assert "s3cret" not in doc.model_dump_json().lower()
