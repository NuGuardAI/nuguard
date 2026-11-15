"""Route-group scope regressions for the ASP.NET Core Minimal API adapter."""

from __future__ import annotations

from pathlib import Path

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
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


def test_auth_attribute_after_first_in_section() -> None:
    source = """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
var api = app.MapGroup("/api").RequireAuthorization();
app.MapGet("/x", [EndpointName("x"), Authorize] () => Results.Ok());
api.MapGet("/y", [Tags("t"), AllowAnonymous] () => Results.Ok());
"""

    assert _endpoint_auth(CSharpAspNetCoreAdapter(), source, "Program.cs") == {
        "GET /x": True,
        "GET /api/y": False,
    }


def test_null_conditional_receiver_binding_keyed_by_declaring_type() -> None:
    program = """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
var orders = app.MapGroup("/orders");
var admin = app.MapGroup("/admin").RequireAuthorization();
Endpoints?.Map(admin);
OrdersEndpoints.Map(orders);
"""
    endpoints = """using Microsoft.AspNetCore.Builder;
public static class Endpoints
{
    public static void Map(IEndpointRouteBuilder app)
    {
        app.MapGet("/users", () => Results.Ok());
    }
}
"""
    bindings = collect_route_group_bindings(program, "Program.cs")
    assert "Endpoints.Map" in bindings
    assert "Map" not in bindings

    adapter = CSharpAspNetCoreAdapter()
    adapter.set_csharp_route_group_bindings(bindings)

    assert _endpoint_auth(adapter, endpoints, "Endpoints.cs") == {"GET /admin/users": True}


def test_cross_file_bindings_keep_qualified_and_drop_ambiguous_bare(tmp_path: Path) -> None:
    project = tmp_path / "api"
    project.mkdir()
    (project / "Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n', encoding="utf-8"
    )
    (project / "Program.cs").write_text(
        """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
AdminRegistration.Register(app);
OrdersRegistration.Register(app);
app.Run();
""",
        encoding="utf-8",
    )
    for name, prefix, auth in (
        ("Admin", "/admin", ".RequireAuthorization()"),
        ("Orders", "/orders", ""),
    ):
        (project / f"{name}Registration.cs").write_text(
            f"""using Microsoft.AspNetCore.Builder;
public static class {name}Registration
{{
    public static void Register(WebApplication app)
    {{
        var group = app.MapGroup("{prefix}"){auth};
        {name}Endpoints.Map(group);
        group.MapExtras();
    }}
}}
""",
            encoding="utf-8",
        )
        (project / f"{name}.cs").write_text(
            f"""using Microsoft.AspNetCore.Builder;
public static class {name}Endpoints
{{
    public static void Map(IEndpointRouteBuilder app)
    {{
        app.MapGet("/{name.lower()}-list", () => Results.Ok());
    }}
}}
public static class {name}Extensions
{{
    public static void MapExtras(this RouteGroupBuilder group)
    {{
        group.MapGet("/{name.lower()}-extra", () => Results.Ok());
    }}
}}
""",
            encoding="utf-8",
        )

    doc = AiSbomExtractor().extract_from_path(project, AiSbomConfig(enable_llm=False))
    endpoints = {
        node.metadata.extras["endpoint"]: node.metadata.extras["auth_required"]
        for node in doc.nodes
        if node.component_type == ComponentType.API_ENDPOINT
        and node.metadata.extras.get("framework") == "aspnet_core"
    }

    assert endpoints == {
        "/orders/orders-list": False,
        "/admin/admin-list": True,
        "/orders-extra": False,
        "/admin-extra": False,
    }
