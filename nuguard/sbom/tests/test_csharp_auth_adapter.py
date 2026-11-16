"""Tests for C# auth adapter."""

from __future__ import annotations

import re
from typing import Any

import pytest

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.csharp import CSharpAuthAdapter
from nuguard.sbom.core.csharp_parser import parse_csharp
from nuguard.sbom.types import ComponentType


def _extract(
    adapter: Any,
    source: str,
    path: str = "Auth.cs",
) -> list[ComponentDetection]:
    return adapter.extract(source, path, parse_csharp(source, path))


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [d for d in detections if d.component_type == component_type]


# ---------------------------------------------------------------------------
# JWT Bearer
# ---------------------------------------------------------------------------


def test_jwt_bearer_detects_addjwtbearer() -> None:
    source = """using Microsoft.AspNetCore.Authentication.JwtBearer;
services.AddAuthentication()
    .AddJwtBearer(options =>
    {
        options.Authority = "https://example.com";
    });
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    assert len(auth) >= 1
    providers = {d.metadata["provider"] for d in auth}
    assert "jwt_bearer" in providers


def test_jwt_bearer_detects_options_instantiation() -> None:
    source = """using Microsoft.AspNetCore.Authentication.JwtBearer;
var options = new JwtBearerOptions();
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    providers = {d.metadata["provider"] for d in auth}
    assert "jwt_bearer" in providers


# ---------------------------------------------------------------------------
# OAuth2 / OpenID Connect
# ---------------------------------------------------------------------------


def test_oauth_detects_addgoogle() -> None:
    source = """using Microsoft.AspNetCore.Authentication.Google;
services.AddAuthentication()
    .AddGoogle(options =>
    {
        options.ClientId = "id";
    });
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    providers = {d.metadata["provider"] for d in auth}
    assert "oauth_google" in providers


def test_oidc_detects_addopenidconnect() -> None:
    source = """using Microsoft.AspNetCore.Authentication.OpenIdConnect;
services.AddAuthentication()
    .AddOpenIdConnect(options =>
    {
        options.Authority = "https://login.example.com";
    });
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    providers = {d.metadata["provider"] for d in auth}
    assert "oidc" in providers


# ---------------------------------------------------------------------------
# Microsoft.Identity.Web
# ---------------------------------------------------------------------------


def test_identity_web_detects_import() -> None:
    source = """using Microsoft.Identity.Web;
services.AddAuthentication()
    .AddMicrosoftIdentityWebApp(Configuration);
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    providers = {d.metadata["provider"] for d in auth}
    assert "ms_identity_web" in providers


# ---------------------------------------------------------------------------
# [Authorize] / [AllowAnonymous] attributes
# ---------------------------------------------------------------------------


def test_authorize_attribute_detected() -> None:
    source = """using Microsoft.AspNetCore.Authorization;
[ApiController]
[Authorize]
[Route("api/[controller]")]
public class SecretController : ControllerBase
{
    [HttpGet]
    public string Get() => "secret";
}
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    attrs = [d for d in auth if d.metadata.get("auth_kind") == "attribute"]
    assert len(attrs) >= 1
    assert any(d.metadata["attribute"] == "Authorize" for d in attrs)


def test_allowanonymous_attribute_detected() -> None:
    source = """using Microsoft.AspNetCore.Authorization;
[ApiController]
[AllowAnonymous]
public class HealthController : ControllerBase
{
    [HttpGet]
    public string Get() => "ok";
}
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    attrs = [d for d in auth if d.metadata.get("auth_kind") == "attribute"]
    assert any(d.metadata["attribute"] == "AllowAnonymous" for d in attrs)


# ---------------------------------------------------------------------------
# Registration calls without namespace validation
# ---------------------------------------------------------------------------


def test_addauthentication_detected_even_without_explicit_import() -> None:
    """AddAuthentication/AddAuthorization are part of ASP.NET Core and don't
    require a specific auth namespace import."""
    source = """using Microsoft.AspNetCore.Builder;
services.AddAuthentication();
services.AddAuthorization();
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    # Should detect the middleware registration
    assert len(auth) >= 1


# ---------------------------------------------------------------------------
# No false positives
# ---------------------------------------------------------------------------


def test_unrelated_csharp_ignored() -> None:
    source = """using System;
public class Greeter
{
    public string Hello(string name) => $"Hello {name}";
}
"""
    detections = _extract(CSharpAuthAdapter(), source)
    assert _by_type(detections, ComponentType.AUTH) == []


def test_string_marker_not_detected() -> None:
    source = 'var marker = "JwtBearerOptions";'
    detections = _extract(CSharpAuthAdapter(), source)
    assert _by_type(detections, ComponentType.AUTH) == []


# ---------------------------------------------------------------------------
# Import-only fallback
# ---------------------------------------------------------------------------


def test_import_only_produces_lower_confidence_node() -> None:
    source = """using Microsoft.AspNetCore.Authentication.JwtBearer;
// No actual usage yet, just imported
"""
    detections = _extract(CSharpAuthAdapter(), source)
    auth = _by_type(detections, ComponentType.AUTH)
    assert len(auth) == 1
    assert auth[0].confidence < 0.85
    assert auth[0].metadata["auth_kind"] == "import"


def test_add_authentication_chained_jwt_bearer_is_one_jwt_node() -> None:
    source = """using Microsoft.AspNetCore.Authentication.JwtBearer;
builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer(options =>
    {
        options.MapInboundClaims = false;
    });
"""
    auth = _by_type(_extract(CSharpAuthAdapter(), source), ComponentType.AUTH)

    assert [d.canonical_name for d in auth] == ["auth:jwt"]
    assert auth[0].metadata["auth_type"] == "jwt"
    assert auth[0].display_name == "JWT Bearer Auth"


def test_snippet_redacts_string_literals() -> None:
    source = """using Microsoft.AspNetCore.Authentication;
services.AddAuthentication("Bearer")
    .AddOAuth("GitHub", o => {
        o.ClientSecret = "S3CRETclient"; // old: hunter2
        o.ClientId = @"verbatimSecret";
        o.Scope.Add($"tok{x}en");
        o.Backchannel = \"\"\"rawSecret\"\"\";
    });
var h = new JwtSecurityTokenHandler("Server=db;Password=pw123");
"""
    auth = _by_type(_extract(CSharpAuthAdapter(), source), ComponentType.AUTH)
    snippets = " ".join(d.snippet for d in auth)

    for secret in ("S3CRETclient", "hunter2", "verbatimSecret", "tok", "rawSecret", "pw123"):
        assert secret not in snippets
    assert '"<redacted>"' in snippets
    assert 'AddAuthentication("Bearer")' in snippets


def test_snippet_secret_absent_from_extracted_sbom(tmp_path) -> None:
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.tests.conftest import CS_ONLY

    (tmp_path / "Program.cs").write_text(
        "using Microsoft.AspNetCore.Authentication;\n"
        "using Microsoft.IdentityModel.Tokens;\n"
        "builder.Services.AddAuthentication()\n"
        '    .AddOAuth("GitHub", o => { o.ClientSecret = "S3CRETclient"; });\n'
        "builder.Services.AddAuthentication().AddJwtBearer(o => {\n"
        "    o.TokenValidationParameters = new TokenValidationParameters {\n"
        '        IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(@"K3YsigningSecret")),\n'
        "    };\n"
        "});\n"
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)
    serialized = doc.model_dump_json()

    assert any(n.name == "auth:jwt" or "jwt" in n.name.lower() for n in doc.nodes)
    assert "S3CRETclient" not in serialized
    assert "K3YsigningSecret" not in serialized


def test_jwt_chain_with_aspnet_authentication_import_only() -> None:
    source = """using Microsoft.AspNetCore.Authentication;
builder.Services.AddAuthentication("Bearer").AddJwtBearer();
"""
    auth = _by_type(_extract(CSharpAuthAdapter(), source), ComponentType.AUTH)

    assert [d.canonical_name for d in auth] == ["auth:jwt"]


def test_jwt_chain_with_web_sdk_implicit_usings(tmp_path) -> None:
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.tests.conftest import CS_ONLY

    (tmp_path / "Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
        "<ImplicitUsings>enable</ImplicitUsings></PropertyGroup></Project>"
    )
    (tmp_path / "Program.cs").write_text(
        "var builder = WebApplication.CreateBuilder(args);\n"
        'builder.Services.AddAuthentication("Bearer").AddJwtBearer();\n'
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)
    auth = [n for n in doc.nodes if n.component_type == ComponentType.AUTH]

    assert any(n.metadata.extras.get("auth_kind") == "jwt" for n in auth), auth


def test_msal_import_alone_is_not_inbound_auth() -> None:
    source = """using Microsoft.Identity.Client;
public class CosmosDbService { }
"""

    assert _by_type(_extract(CSharpAuthAdapter(), source), ComponentType.AUTH) == []


def test_generic_auth_regex_ignores_namespace_segments() -> None:
    from nuguard.sbom.adapters.registry import default_registry

    generic = next(a for a in default_registry() if a.name == "auth_generic")

    assert (
        generic.detect("resource r 'Microsoft.Authorization/roleAssignments@2022-04-01' = {")
        is None
    )
    assert generic.detect("using Microsoft.AspNetCore.Authentication.JwtBearer;") is None
    assert generic.detect("// requires authentication for every route") is not None


_LEAK_SHAPES = {
    "raw_four_quotes": 'AddAuthentication(""""prefix"""S3CRET"""")',
    "string_in_hole": 'AddAuthentication($"Bearer {Encode("S3CRET")}")',
    "quote_char_in_hole": 'AddAuthentication($"{\'"\'}S3CRET")',
    "char_literals": "AddAuthentication(new string(new[] { 'S','3','C','R','E','T' }))",
}


@pytest.mark.parametrize("call", _LEAK_SHAPES.values(), ids=_LEAK_SHAPES.keys())
def test_snippet_tokenizer_desync_does_not_leak(tmp_path, call: str) -> None:
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.tests.conftest import CS_ONLY

    project = tmp_path / "src" / "Api"
    project.mkdir(parents=True)
    (project / "Program.cs").write_text(
        f"using Microsoft.AspNetCore.Authentication;\nbuilder.Services.{call};\n"
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)

    assert any(n.component_type == ComponentType.AUTH for n in doc.nodes)
    assert "S3CRET" not in re.sub(r"[',\s]", "", doc.model_dump_json())


def test_snippet_keeps_safe_scheme_in_extracted_sbom(tmp_path) -> None:
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.tests.conftest import CS_ONLY

    project = tmp_path / "src" / "Api"
    project.mkdir(parents=True)
    (project / "Program.cs").write_text(
        "using Microsoft.AspNetCore.Authentication;\n"
        'builder.Services.AddAuthentication("Bearer");\n'
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, CS_ONLY)

    assert 'AddAuthentication(\\"Bearer\\")' in doc.model_dump_json()


def test_add_authentication_kept_when_chained_scheme_not_allowed() -> None:
    source = "builder.Services.AddAuthentication().AddJwtBearer();\n"
    auth = _by_type(_extract(CSharpAuthAdapter(), source), ComponentType.AUTH)

    assert [d.canonical_name for d in auth] == ["auth_aspnet_auth"]


def test_add_authentication_kept_before_identity_web_api() -> None:
    source = "builder.Services.AddAuthentication().AddMicrosoftIdentityWebApi(config);\n"
    auth = _by_type(_extract(CSharpAuthAdapter(), source), ComponentType.AUTH)

    assert [d.canonical_name for d in auth] == ["auth_aspnet_auth"]
