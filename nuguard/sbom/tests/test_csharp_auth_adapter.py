"""Tests for C# auth adapter."""

from __future__ import annotations

from typing import Any

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
