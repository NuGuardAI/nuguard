"""End-to-end tests for the C# kernel-plugin index and auth adapter registration."""

from __future__ import annotations

from pathlib import Path

from nuguard.sbom.adapters.csharp import CSharpAuthAdapter
from nuguard.sbom.adapters.registry import default_framework_adapters
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.tests.conftest import CS_ONLY, nodes
from nuguard.sbom.types import ComponentType, RelationshipType

_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
</Project>
"""

_PLUGIN_PART_A = """using Microsoft.SemanticKernel;

namespace Bank;

public partial class BankPlugin
{
    [KernelFunction]
    public string GetBalance(string account) => "0";
}
"""

_PLUGIN_PART_B = """using Microsoft.SemanticKernel;

namespace Bank;

public partial class BankPlugin
{
    [KernelFunction]
    public string TransferFunds(string from, string to) => "ok";
}
"""

_AGENT = """using Microsoft.SemanticKernel;
using Microsoft.SemanticKernel.Agents;

namespace Bank;

public class AgentFactory
{
    public ChatCompletionAgent Build(Kernel kernel)
    {
        Kernel agentKernel = kernel.Clone();
        agentKernel.Plugins.AddFromType<BankPlugin>();
        ChatCompletionAgent agent = new ChatCompletionAgent
        {
            Name = "Teller",
            Instructions = "You are a bank teller.",
            Kernel = agentKernel,
        };
        return agent;
    }
}
"""

_AUTH_PROGRAM = """using Microsoft.AspNetCore.Authentication.JwtBearer;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddAuthentication()
    .AddJwtBearer(options =>
    {
        options.Authority = "https://login.example.com";
    });
var app = builder.Build();
app.UseAuthentication();
app.MapGet("/health", () => "ok");
app.Run();
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_partial_plugin_class_across_files_keeps_all_kernel_functions(tmp_path: Path) -> None:
    root = tmp_path / "bank"
    _write(
        root,
        {
            "Bank.csproj": _CSPROJ,
            "Plugins/BankPlugin.Balance.cs": _PLUGIN_PART_A,
            "Plugins/BankPlugin.Transfer.cs": _PLUGIN_PART_B,
            "AgentFactory.cs": _AGENT,
        },
    )
    doc = AiSbomExtractor().extract_from_path(root, CS_ONLY)

    agents = [n for n in nodes(doc, ComponentType.AGENT) if n.name.lower() == "teller"]
    assert agents, f"agents found: {[n.name for n in nodes(doc, ComponentType.AGENT)]}"
    agent_ids = {n.id for n in agents}
    by_id = {n.id: n for n in doc.nodes}
    called = {
        by_id[e.target].name.lower()
        for e in doc.edges
        if e.source in agent_ids
        and e.relationship_type == RelationshipType.CALLS
        and e.target in by_id
    }
    assert "getbalance" in called, called
    assert "transferfunds" in called, called


def test_csharp_auth_adapter_is_registered() -> None:
    assert any(isinstance(a, CSharpAuthAdapter) for a in default_framework_adapters())


def test_aspnet_jwt_bearer_project_emits_auth_node(tmp_path: Path) -> None:
    root = tmp_path / "api"
    _write(root, {"Api.csproj": _CSPROJ, "Program.cs": _AUTH_PROGRAM})
    doc = AiSbomExtractor().extract_from_path(root, CS_ONLY)

    auth = nodes(doc, ComponentType.AUTH)
    assert auth, f"node types: {sorted({str(n.component_type) for n in doc.nodes})}"
