"""Tests for C# AI and web framework adapters."""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.sbom.adapters.base import (
    ComponentDetection,
)
from nuguard.sbom.adapters.csharp import (
    CSharpAspNetCoreAdapter,
    CSharpLLMClientsAdapter,
    CSharpMLNetAdapter,
    CSharpSemanticKernelAdapter,
)
from nuguard.sbom.adapters.csharp._source import (
    find_calls,
)
from nuguard.sbom.core.csharp_parser import (
    parse_csharp,
)
from nuguard.sbom.types import ComponentType


def _extract(
    adapter: Any,
    source: str,
    path: str = "App.cs",
) -> list[ComponentDetection]:
    return adapter.extract(
        source,
        path,
        parse_csharp(source, path),
    )


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [detection for detection in detections if detection.component_type == component_type]


def test_call_parser_extracts_generic_and_named_arguments() -> None:
    source = (
        "var plugin = "
        "kernel.Plugins."
        "AddFromType<WeatherPlugin>("
        '"Weather", '
        "serviceProvider: services);\n"
    )

    call = find_calls(
        source,
        {"AddFromType"},
    )[0]

    assert call.receiver == "kernel.Plugins"
    assert call.generic_arguments == ("WeatherPlugin",)
    assert call.positional_arguments == ('"Weather"',)
    assert call.named_arguments == {"serviceProvider": "services"}
    assert call.assigned_to == "plugin"


def test_call_parser_ignores_comments_and_string_contents() -> None:
    source = """// kernel.Plugins.AddFromType<FakePlugin>("Fake");
var text = "client.GetChatClient(\\\"not-a-model\\\")";
var actual = client.GetChatClient("gpt-4o");
"""

    calls = find_calls(
        source,
        {
            "AddFromType",
            "GetChatClient",
        },
    )

    assert len(calls) == 1
    assert calls[0].name == "GetChatClient"


def test_llm_clients_detect_azure_and_openai_models() -> None:
    source = """using Azure.AI.OpenAI;
using OpenAI.Chat;
var azure = new AzureOpenAIClient(
    new Uri(endpoint), credential);
var azureChat = azure.GetChatClient("gpt-4o");
var direct = new ChatClient(
    model: "gpt-4.1-mini",
    apiKey: key);
"""

    detections = _extract(
        CSharpLLMClientsAdapter(),
        source,
    )
    frameworks = {
        detection.metadata["provider"]
        for detection in _by_type(
            detections,
            ComponentType.FRAMEWORK,
        )
    }
    models = {
        detection.display_name: detection
        for detection in _by_type(
            detections,
            ComponentType.MODEL,
        )
    }

    assert frameworks == {
        "azure",
        "openai",
    }
    assert {
        "gpt-4o",
        "gpt-4.1-mini",
    } <= set(models)
    assert models["gpt-4o"].metadata["framework"] == "azure_openai"
    assert models["gpt-4o"].relationships[0].relationship_type == "USES"


def test_llm_clients_detect_anthropic_model_constants() -> None:
    source = """using Anthropic;
var client = new AnthropicClient(apiKey);
var request = new MessageParameters
{
    Model = Model.ClaudeSonnet4_20250514,
};
"""

    detections = _extract(
        CSharpLLMClientsAdapter(),
        source,
        "Anthropic.cs",
    )
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )

    assert len(models) == 1
    assert models[0].display_name == "Model.ClaudeSonnet4_20250514"
    assert models[0].metadata["provider"] == "anthropic"


def test_generic_chat_client_without_supported_namespace_is_ignored() -> None:
    source = """using Contoso.Chat;
var client = new ChatClient("internal-model");
"""

    assert (
        _extract(
            CSharpLLMClientsAdapter(),
            source,
        )
        == []
    )


def test_semantic_kernel_detects_services_plugins_planners_and_prompts() -> None:
    source = """using Microsoft.SemanticKernel;
using Microsoft.SemanticKernel.Planning.Handlebars;
var builder = Kernel.CreateBuilder();
builder.AddAzureOpenAIChatCompletion(
    deploymentName: "gpt-4o",
    endpoint: endpoint,
    apiKey: key);
var kernel = builder.Build();
kernel.Plugins.AddFromType<WeatherPlugin>(
    "Weather");
var planner = new HandlebarsPlanner();
public class WeatherPlugin
{
    [KernelFunction]
    public string GetWeather() => "sunny";
}
var systemPrompt = "You are a weather assistant.";
"""

    detections = _extract(
        CSharpSemanticKernelAdapter(),
        source,
        "Kernel.cs",
    )

    assert _by_type(
        detections,
        ComponentType.FRAMEWORK,
    )
    assert any(
        detection.display_name == "gpt-4o"
        for detection in _by_type(
            detections,
            ComponentType.MODEL,
        )
    )
    assert {
        "Weather",
        "GetWeather",
    } <= {
        detection.display_name
        for detection in _by_type(
            detections,
            ComponentType.TOOL,
        )
    }
    assert _by_type(
        detections,
        ComponentType.AGENT,
    )
    assert _by_type(
        detections,
        ComponentType.PROMPT,
    )


def test_semantic_kernel_accepts_kernel_function_attribute_suffix() -> None:
    source = """using Microsoft.SemanticKernel;
public class SearchPlugin
{
    [KernelFunctionAttribute]
    public string Search(string query) => query;
}
"""

    detections = _extract(
        CSharpSemanticKernelAdapter(),
        source,
    )

    assert any(
        detection.display_name == "Search"
        for detection in _by_type(
            detections,
            ComponentType.TOOL,
        )
    )


def test_semantic_kernel_ignores_unrelated_build_calls() -> None:
    source = """using Microsoft.SemanticKernel;
var result = unrelated.Build();
"""

    detections = _extract(
        CSharpSemanticKernelAdapter(),
        source,
    )
    frameworks = _by_type(
        detections,
        ComponentType.FRAMEWORK,
    )

    assert [detection.canonical_name for detection in frameworks] == ["framework:semantic_kernel"]


def test_mlnet_detects_context_pipeline_trainer_and_fit() -> None:
    source = """using Microsoft.ML;
var ml = new MLContext(seed: 1);
var pipeline =
    ml.Transforms.Conversion.MapValueToKey("Label")
    .Append(
        ml.Transforms.Text.FeaturizeText(
            "Features", "Text"))
    .Append(
        ml.MulticlassClassification.Trainers
        .SdcaMaximumEntropy());
var model = pipeline.Fit(data);
"""

    detections = _extract(
        CSharpMLNetAdapter(),
        source,
        "Train.cs",
    )
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )

    assert _by_type(
        detections,
        ComponentType.FRAMEWORK,
    )
    assert any(detection.metadata.get("trainer") == "SdcaMaximumEntropy" for detection in models)
    assert any(detection.metadata.get("trained_model") is True for detection in models)


def test_mlnet_does_not_treat_unrelated_append_as_pipeline() -> None:
    source = """using Microsoft.ML;
items.Append(value);
"""

    detections = _extract(
        CSharpMLNetAdapter(),
        source,
    )

    assert not any(detection.metadata.get("pipeline") is True for detection in detections)


def test_aspnet_controller_emits_ai_endpoint_metadata() -> None:
    source = """using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using OpenAI.Chat;
public record ChatRequest(string Message);
public record ChatResponse(string Answer);
[ApiController]
[Route("api/[controller]")]
public class ChatController : ControllerBase
{
    [HttpPost("complete")]
    [Authorize]
    public async Task<ActionResult<ChatResponse>> Complete(
        ChatRequest request)
    {
        var answer = await client.CompleteChatAsync(
            request.Message);
        return new ChatResponse(answer);
    }
}
"""

    detections = _extract(
        CSharpAspNetCoreAdapter(),
        source,
        "ChatController.cs",
    )
    endpoints = _by_type(
        detections,
        ComponentType.API_ENDPOINT,
    )

    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint.metadata["method"] == "POST"
    assert endpoint.metadata["endpoint"] == "/api/Chat/complete"
    assert endpoint.metadata["auth_required"] is True
    assert endpoint.metadata["chat_payload_key"] == "Message"
    assert endpoint.metadata["response_text_key"] == "Answer"


def test_aspnet_controller_expands_action_route_token() -> None:
    source = """using Microsoft.AspNetCore.Mvc;
[ApiController]
[Route("api/[controller]/[action]")]
public class ChatController : ControllerBase
{
    [HttpPost]
    public string Complete(ChatRequest request) =>
        client.CompleteChat(request.Message);
}
public record ChatRequest(string Message);
"""

    detections = _extract(
        CSharpAspNetCoreAdapter(),
        source,
    )
    endpoint = _by_type(
        detections,
        ComponentType.API_ENDPOINT,
    )[0]

    assert endpoint.metadata["endpoint"] == "/api/Chat/Complete"


def test_aspnet_allow_anonymous_overrides_controller_authorization() -> None:
    source = """using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
[ApiController]
[Authorize]
[Route("api/chat")]
public class ChatController : ControllerBase
{
    [HttpPost]
    [AllowAnonymous]
    public string Complete(ChatRequest request) =>
        client.CompleteChat(request.Message);
}
public record ChatRequest(string Message);
"""

    detections = _extract(
        CSharpAspNetCoreAdapter(),
        source,
    )
    endpoint = _by_type(
        detections,
        ComponentType.API_ENDPOINT,
    )[0]

    assert endpoint.metadata["auth_required"] is False


def test_aspnet_minimal_api_emits_route_schema_and_auth() -> None:
    source = """using Microsoft.AspNetCore.Builder;
using OpenAI.Chat;
public record ChatRequest(string Prompt);
var app = WebApplication.CreateBuilder(args).Build();
app.MapPost(
    "/chat",
    async (
        ChatRequest request,
        ChatClient client) =>
    {
        return await client.CompleteChatAsync(
            request.Prompt);
    }).RequireAuthorization();
"""

    detections = _extract(
        CSharpAspNetCoreAdapter(),
        source,
        "Program.cs",
    )
    endpoints = _by_type(
        detections,
        ComponentType.API_ENDPOINT,
    )

    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint.metadata["endpoint"] == "/chat"
    assert endpoint.metadata["request_body_schema"] == {"Prompt": "string"}
    assert endpoint.metadata["chat_payload_key"] == "Prompt"
    assert endpoint.metadata["auth_required"] is True


def test_non_ai_aspnet_route_is_not_emitted() -> None:
    source = """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
app.MapGet("/health", () => Results.Ok());
"""

    detections = _extract(
        CSharpAspNetCoreAdapter(),
        source,
        "Program.cs",
    )

    assert (
        _by_type(
            detections,
            ComponentType.API_ENDPOINT,
        )
        == []
    )


@pytest.mark.parametrize(
    "adapter",
    [
        CSharpLLMClientsAdapter(),
        CSharpSemanticKernelAdapter(),
        CSharpMLNetAdapter(),
        CSharpAspNetCoreAdapter(),
    ],
)
def test_adapters_ignore_unrelated_csharp(
    adapter: Any,
) -> None:
    source = """using System;
public class Greeter
{
    public string Hello(string name) =>
        $"Hello {name}";
}
"""

    assert (
        _extract(
            adapter,
            source,
        )
        == []
    )
