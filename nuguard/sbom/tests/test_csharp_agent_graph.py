"""End-to-end tests for the C# Semantic Kernel agent graph shape."""

from __future__ import annotations

from pathlib import Path

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

_PLUGIN = """using Microsoft.SemanticKernel;

namespace Bank;

public class BankPlugin
{
    [KernelFunction]
    public string GetBalance(string account) => "0";
}
"""

_FACTORY = """using Microsoft.SemanticKernel;
using Microsoft.SemanticKernel.Agents;

namespace Bank;

public class AgentFactory
{
    public AgentGroupChat BuildAgentGroupChat(Kernel kernel)
    {
        Kernel agentKernel = kernel.Clone();
        agentKernel.Plugins.AddFromType<BankPlugin>();
        ChatCompletionAgent teller = new ChatCompletionAgent
        {
            Name = "Teller",
            Instructions = "You are a bank teller.",
            Kernel = agentKernel,
        };
        return new AgentGroupChat(teller);
    }
}
"""

_SK_SERVICE = """namespace Bank;

public class SkService
{
    public async Task<string> GetResponse(string prompt)
    {
        AgentFactory factory = new AgentFactory();
        var chat = factory.BuildAgentGroupChat(null);
        return "ok";
    }

    public string Summarize(string prompt) => prompt;
}
"""

_CHAT_SERVICE = """namespace Bank;

public class ChatService
{
    private readonly SkService _skService;

    public ChatService(SkService skService)
    {
        _skService = skService;
    }

    public Task<string> GetChatCompletionAsync(string prompt) => _skService.GetResponse(prompt);

    public string Rename(string name) => _skService.Summarize(name);

    public string ListSessions() => "[]";
}
"""

_ENDPOINTS = """using Microsoft.AspNetCore.Mvc;

namespace Bank;

public class ChatEndpoints
{
    private readonly ChatService _chatService;

    public ChatEndpoints(ChatService chatService)
    {
        _chatService = chatService;
    }

    public void Map(IEndpointRouteBuilder app)
    {
        app.MapPost("/chat/completion", async (string prompt) =>
            await _chatService.GetChatCompletionAsync(prompt));

        app.MapPost("/chat/rename", async (string name) =>
            await _chatService.Rename(name));

        app.MapGet("/chat/sessions", async () =>
            await _chatService.ListSessions());

        app.MapPost("/chat/injected", async (ChatService svc, string prompt) =>
            await svc.GetChatCompletionAsync(prompt));
    }
}
"""

_CONTROLLER = """using Microsoft.AspNetCore.Mvc;

namespace Bank;

[ApiController]
[Route("api/chat")]
public class ChatController : ControllerBase
{
    private readonly IChatService _chat;

    public ChatController(IChatService chat) { _chat = chat; }

    [HttpPost("ask")]
    public async Task<string> Ask([FromBody] string prompt)
    {
        return await _chat.GetChatCompletionAsync(prompt);
    }
}

public interface IChatService
{
    Task<string> GetChatCompletionAsync(string prompt);
}
"""

_PROGRAM = """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.Run();
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _scan(tmp_path: Path, extra: dict[str, str] | None = None):
    root = tmp_path / "bank"
    files = {
        "Bank.csproj": _CSPROJ,
        "Program.cs": _PROGRAM,
        "BankPlugin.cs": _PLUGIN,
        "AgentFactory.cs": _FACTORY,
        "SkService.cs": _SK_SERVICE,
        "ChatService.cs": _CHAT_SERVICE,
        "ChatEndpoints.cs": _ENDPOINTS,
    }
    files.update(extra or {})
    _write(root, files)
    return AiSbomExtractor().extract_from_path(root, CS_ONLY)


def _edges(doc, source_type, rel, target_type):
    by_id = {n.id: n for n in doc.nodes}
    return [
        (by_id[e.source], by_id[e.target])
        for e in doc.edges
        if e.relationship_type == rel
        and by_id[e.source].component_type == source_type
        and by_id[e.target].component_type == target_type
    ]


def test_framework_does_not_call_agent_owned_tools(tmp_path: Path) -> None:
    doc = _scan(tmp_path)

    assert _edges(doc, ComponentType.AGENT, RelationshipType.CALLS, ComponentType.TOOL)
    leaked = [
        (fw.name, tool.name)
        for fw, tool in _edges(
            doc, ComponentType.FRAMEWORK, RelationshipType.CALLS, ComponentType.TOOL
        )
        if (fw.metadata.framework or "") == "semantic_kernel"
    ]
    assert leaked == []
    hosted = _edges(doc, ComponentType.FRAMEWORK, RelationshipType.USES, ComponentType.TOOL)
    assert any(fw.metadata.framework == "semantic_kernel" for fw, _ in hosted)


def test_endpoint_reaches_agent_through_service_chain(tmp_path: Path) -> None:
    doc = _scan(tmp_path)

    reached = {
        (ep.metadata.extras.get("endpoint"), agent.name)
        for ep, agent in _edges(
            doc, ComponentType.API_ENDPOINT, RelationshipType.CALLS, ComponentType.AGENT
        )
    }
    assert ("/chat/completion", "Teller") in reached
    assert ("/chat/injected", "Teller") in reached
    assert {path for path, _ in reached} == {"/chat/completion", "/chat/injected"}


def test_endpoint_agent_hint_is_not_left_on_node_metadata(tmp_path: Path) -> None:
    doc = _scan(tmp_path)

    for node in nodes(doc, ComponentType.API_ENDPOINT):
        assert "agent_host_files" not in node.metadata.extras


def test_controller_action_reaches_agent_through_interface(tmp_path: Path) -> None:
    doc = _scan(
        tmp_path,
        {
            "ChatEndpoints.cs": "namespace Bank;\n",
            "ChatController.cs": _CONTROLLER,
            "ChatService.cs": _CHAT_SERVICE.replace(
                "public class ChatService", "public class ChatService : IChatService"
            ),
        },
    )

    reached = {
        (ep.metadata.extras.get("endpoint"), agent.name)
        for ep, agent in _edges(
            doc, ComponentType.API_ENDPOINT, RelationshipType.CALLS, ComponentType.AGENT
        )
    }
    assert reached == {("/api/chat/ask", "Teller")}


def test_unresolvable_handler_adds_no_agent_edge(tmp_path: Path) -> None:
    doc = _scan(
        tmp_path,
        {
            "ChatEndpoints.cs": _ENDPOINTS.replace("ChatService _chatService", "object _chatService"),
            "ChatService.cs": "namespace Bank;\npublic class Unrelated { }\n",
        },
    )

    assert not _edges(doc, ComponentType.API_ENDPOINT, RelationshipType.CALLS, ComponentType.AGENT)
