"""Route-group scope regressions for the ASP.NET Core Minimal API adapter."""

from __future__ import annotations

from nuguard.sbom.adapters.csharp.aspnet_core import (
    CSharpAspNetCoreAdapter,
    collect_route_group_bindings,
)
from nuguard.sbom.core.csharp_parser import parse_csharp
from nuguard.sbom.types import ComponentType


def _endpoint_auth(adapter: CSharpAspNetCoreAdapter, source: str, path: str) -> dict[str, bool]:
    return {
        detection.display_name: detection.metadata["auth_required"]
        for detection in adapter.extract(source, path, parse_csharp(source, path))
        if detection.component_type == ComponentType.API_ENDPOINT
    }


def test_later_parent_authorization_reaches_nested_group() -> None:
    source = """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
var api = app.MapGroup("/api");
var nested = api.MapGroup("/nested");
nested.MapGet("/x", () => Results.Ok());
api.RequireAuthorization();
"""

    assert _endpoint_auth(CSharpAspNetCoreAdapter(), source, "Program.cs") == {
        "GET /api/nested/x": True
    }


def test_standalone_chain_folds_every_call() -> None:
    source = """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
var api = app.MapGroup("/api");
api.RequireAuthorization().AllowAnonymous();
api.MapGet("/x", () => Results.Ok());
"""

    assert _endpoint_auth(CSharpAspNetCoreAdapter(), source, "Program.cs") == {"GET /api/x": False}


def test_bindings_keyed_by_declaring_type() -> None:
    program = """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
var orders = app.MapGroup("/orders");
var admin = app.MapGroup("/admin").RequireAuthorization();
OrdersEndpoints.Map(orders);
AdminEndpoints.Map(admin);
"""
    endpoints = """using Microsoft.AspNetCore.Builder;
public static class OrdersEndpoints
{
    public static void Map(IEndpointRouteBuilder app)
    {
        app.MapGet("/list", () => Results.Ok());
    }
}
public static class AdminEndpoints
{
    public static void Map(IEndpointRouteBuilder app)
    {
        app.MapGet("/users", () => Results.Ok());
    }
}
"""
    adapter = CSharpAspNetCoreAdapter()
    adapter.set_csharp_route_group_bindings(collect_route_group_bindings(program, "Program.cs"))

    assert _endpoint_auth(adapter, endpoints, "Endpoints.cs") == {
        "GET /orders/list": False,
        "GET /admin/users": True,
    }


def test_all_lambda_attributes_checked() -> None:
    source = """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
app.MapGet("/x", [ProducesResponseType(200)] [Authorize] () => Results.Ok());
app.MapGet("/y", [ProducesResponseType(200), AllowAnonymous] () => Results.Ok()).RequireAuthorization();
"""

    assert _endpoint_auth(CSharpAspNetCoreAdapter(), source, "Program.cs") == {
        "GET /x": True,
        "GET /y": False,
    }
