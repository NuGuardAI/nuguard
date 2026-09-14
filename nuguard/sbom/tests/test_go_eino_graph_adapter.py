"""Tests for Eino component-graph extraction."""

from __future__ import annotations

from pathlib import Path

from nuguard.sbom.adapters.base import (
    ComponentDetection,
)
from nuguard.sbom.adapters.go import EinoAdapter
from nuguard.sbom.core.go_parser import (
    parse_go,
)
from nuguard.sbom.types import ComponentType

_FIXTURES = Path(__file__).parent / "fixtures" / "apps"
_ADAPTER = EinoAdapter()


def _extract(
    source: str,
    file_path: str = "main.go",
) -> list[ComponentDetection]:
    return _ADAPTER.extract(
        source,
        file_path,
        parse_go(
            source,
            file_path,
        ),
    )


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def _line_of(
    source: str,
    fragment: str,
) -> int:
    return next(
        number
        for number, line in enumerate(
            source.splitlines(),
            start=1,
        )
        if fragment in line
    )


def _has_relationship(
    component: ComponentDetection,
    *,
    source: ComponentDetection,
    relationship_type: str,
) -> bool:
    return any(
        relationship.source_canonical == source.canonical_name
        and relationship.target_canonical == component.canonical_name
        and relationship.relationship_type == relationship_type
        for relationship in component.relationships
    )


def test_fixture_emits_alias_safe_graph_components_and_relationships() -> None:
    fixture = _FIXTURES / "go_eino_graph" / "main.go"
    source = fixture.read_text(encoding="utf-8")
    detections = _extract(
        source,
        str(fixture),
    )

    frameworks = _by_type(
        detections,
        ComponentType.FRAMEWORK,
    )
    agents = _by_type(
        detections,
        ComponentType.AGENT,
    )
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )
    prompts = _by_type(
        detections,
        ComponentType.PROMPT,
    )
    tools = _by_type(
        detections,
        ComponentType.TOOL,
    )

    assert len(frameworks) == 1
    assert len(agents) == 1
    assert len(models) == 1
    assert len(prompts) == 1
    assert {tool.display_name for tool in tools} == {
        "lookup_weather",
        "tools",
    }

    agent = agents[0]
    assert agent.display_name == "graph"
    assert agent.metadata["orchestration_kind"] == "graph"
    assert agent.metadata["module"] == "github.com/cloudwego/eino/compose"

    model = models[0]
    assert model.display_name == "gpt-4o"
    assert model.canonical_name == "gpt_4o"
    assert model.metadata["provider"] == "openai"
    assert model.metadata["node_key"] == "generate"
    assert model.metadata["referenced_variable"] == "chatModel"
    assert model.line == _line_of(
        source,
        'graph.AddChatModelNode("generate"',
    )
    assert _has_relationship(
        model,
        source=agent,
        relationship_type="USES",
    )

    prompt = prompts[0]
    assert prompt.display_name == "prompt"
    assert prompt.metadata["node_key"] == "prompt"
    assert prompt.metadata["referenced_variable"] == "chatTemplate"
    assert prompt.metadata["template_variables"] == [
        "role",
        "question",
    ]
    assert "You are a {role}." in prompt.metadata["content"]
    assert prompt.line == _line_of(
        source,
        'graph.AddChatTemplateNode("prompt"',
    )
    assert _has_relationship(
        prompt,
        source=agent,
        relationship_type="USES",
    )

    standalone_tool = next(tool for tool in tools if tool.display_name == "lookup_weather")
    assert standalone_tool.metadata["description"] == "Look up current weather"
    assert standalone_tool.metadata["registered"] is False

    tools_node = next(tool for tool in tools if tool.display_name == "tools")
    assert tools_node.metadata["node_key"] == "tools"
    assert tools_node.metadata["referenced_variable"] == "toolsNode"
    assert tools_node.metadata["member_variables"] == ["weatherTool"]
    assert tools_node.metadata["member_tools"] == [standalone_tool.canonical_name]
    assert tools_node.line == _line_of(
        source,
        'graph.AddToolsNode("tools"',
    )
    assert _has_relationship(
        tools_node,
        source=agent,
        relationship_type="CALLS",
    )


def test_chain_append_methods_emit_model_prompt_and_tool_components() -> None:
    source = """package main

import (
    "github.com/cloudwego/eino/compose"
    "github.com/cloudwego/eino/components/prompt"
    "github.com/cloudwego/eino/schema"
)

func main() {
    chatModel := buildModel()
    chatTemplate := prompt.FromMessages(
        schema.FString,
        schema.UserMessage("Question: {input}"),
    )
    toolsNode := buildToolsNode()
    chain := compose.NewChain[string, string]()
    chain.AppendChatModel(chatModel)
    chain.AppendChatTemplate(chatTemplate)
    chain.AppendToolsNode(toolsNode)
}
"""

    detections = _extract(source)
    agent = _by_type(
        detections,
        ComponentType.AGENT,
    )[0]
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )
    prompts = _by_type(
        detections,
        ComponentType.PROMPT,
    )
    tools = _by_type(
        detections,
        ComponentType.TOOL,
    )

    assert agent.metadata["orchestration_kind"] == "chain"
    assert [item.display_name for item in models] == ["chatModel"]
    assert [item.display_name for item in prompts] == ["chatTemplate"]
    assert [item.display_name for item in tools] == ["toolsNode"]

    assert _has_relationship(
        models[0],
        source=agent,
        relationship_type="USES",
    )
    assert _has_relationship(
        prompts[0],
        source=agent,
        relationship_type="USES",
    )
    assert _has_relationship(
        tools[0],
        source=agent,
        relationship_type="CALLS",
    )


def test_agentic_graph_and_chain_methods_are_supported() -> None:
    source = """package main

import "github.com/cloudwego/eino/compose"

func main() {
    graph := compose.NewGraph[string, string]()
    graph.AddAgenticModelNode("agentic-model", agenticModel)
    graph.AddAgenticChatTemplateNode("agentic-prompt", agenticPrompt)
    graph.AddAgenticToolsNode("agentic-tools", agenticTools)

    chain := compose.NewChain[string, string]()
    chain.AppendAgenticModel(chainModel)
    chain.AppendAgenticChatTemplate(chainPrompt)
    chain.AppendAgenticToolsNode(chainTools)
}
"""

    detections = _extract(source)
    agents = {
        agent.display_name: agent
        for agent in _by_type(
            detections,
            ComponentType.AGENT,
        )
    }
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )
    prompts = _by_type(
        detections,
        ComponentType.PROMPT,
    )
    tools = _by_type(
        detections,
        ComponentType.TOOL,
    )

    assert set(agents) == {
        "graph",
        "chain",
    }
    assert {item.display_name for item in models} == {
        "agentic-model",
        "chainModel",
    }
    assert {item.display_name for item in prompts} == {
        "agentic-prompt",
        "chainPrompt",
    }
    assert {item.display_name for item in tools} == {
        "agentic-tools",
        "chainTools",
    }

    graph_model = next(item for item in models if item.display_name == "agentic-model")
    chain_model = next(item for item in models if item.display_name == "chainModel")

    assert _has_relationship(
        graph_model,
        source=agents["graph"],
        relationship_type="USES",
    )
    assert _has_relationship(
        chain_model,
        source=agents["chain"],
        relationship_type="USES",
    )


def test_unrelated_receivers_do_not_register_components() -> None:
    source = """package main

import "github.com/cloudwego/eino/compose"

func main() {
    graph := compose.NewGraph[string, string]()
    registry.AddChatModelNode("model", model)
    registry.AddChatTemplateNode("prompt", prompt)
    registry.AddToolsNode("tools", toolsNode)
    other.AppendChatModel(model)
    _ = graph
}
"""

    detections = _extract(source)

    assert (
        len(
            _by_type(
                detections,
                ComponentType.AGENT,
            )
        )
        == 1
    )
    assert (
        _by_type(
            detections,
            ComponentType.MODEL,
        )
        == []
    )
    assert (
        _by_type(
            detections,
            ComponentType.PROMPT,
        )
        == []
    )
    assert (
        _by_type(
            detections,
            ComponentType.TOOL,
        )
        == []
    )


def test_graph_static_node_keys_allow_safe_inline_component_fallbacks() -> None:
    source = """package main

import "github.com/cloudwego/eino/compose"

func main() {
    graph := compose.NewGraph[string, string]()
    graph.AddChatModelNode("generate", buildModel())
    graph.AddChatTemplateNode("prompt", buildPrompt())
    graph.AddToolsNode("tools", buildToolsNode())
}
"""

    detections = _extract(source)

    assert [
        item.display_name
        for item in _by_type(
            detections,
            ComponentType.MODEL,
        )
    ] == ["generate"]
    assert [
        item.display_name
        for item in _by_type(
            detections,
            ComponentType.PROMPT,
        )
    ] == ["prompt"]
    assert [
        item.display_name
        for item in _by_type(
            detections,
            ComponentType.TOOL,
        )
    ] == ["tools"]


def test_chain_inline_dynamic_components_are_not_fabricated() -> None:
    source = """package main

import "github.com/cloudwego/eino/compose"

func main() {
    chain := compose.NewChain[string, string]()
    chain.AppendChatModel(buildModel())
    chain.AppendChatTemplate(buildPrompt())
    chain.AppendToolsNode(buildToolsNode())
}
"""

    detections = _extract(source)

    assert (
        len(
            _by_type(
                detections,
                ComponentType.AGENT,
            )
        )
        == 1
    )
    assert (
        _by_type(
            detections,
            ComponentType.MODEL,
        )
        == []
    )
    assert (
        _by_type(
            detections,
            ComponentType.PROMPT,
        )
        == []
    )
    assert (
        _by_type(
            detections,
            ComponentType.TOOL,
        )
        == []
    )


def test_registered_standalone_tool_is_not_duplicated() -> None:
    source = """package main

import (
    cmp "github.com/cloudwego/eino/compose"
    util "github.com/cloudwego/eino/components/tool/utils"
    "github.com/cloudwego/eino/schema"
)

func main() {
    info := &schema.ToolInfo{
        Name: "weather",
        Desc: "Weather lookup",
    }
    weatherTool := util.NewTool[Input, Output](
        info,
        lookupWeather,
    )
    graph := cmp.NewGraph[string, string]()
    graph.AddToolsNode("tools", weatherTool)
}
"""

    detections = _extract(source)
    agent = _by_type(
        detections,
        ComponentType.AGENT,
    )[0]
    tools = _by_type(
        detections,
        ComponentType.TOOL,
    )

    assert len(tools) == 1
    assert tools[0].display_name == "weather"
    assert tools[0].metadata["registered"] is True
    assert tools[0].metadata["eino_registrations"][0]["node_key"] == "tools"
    assert _has_relationship(
        tools[0],
        source=agent,
        relationship_type="CALLS",
    )


def test_repeated_model_registration_deduplicates_component_and_relationship() -> None:
    source = """package main

import (
    "context"

    "github.com/cloudwego/eino-ext/components/model/openai"
    "github.com/cloudwego/eino/compose"
)

func main() {
    chatModel, _ := openai.NewChatModel(
        context.Background(),
        &openai.ChatModelConfig{
            Model: "gpt-4o",
        },
    )
    graph := compose.NewGraph[string, string]()
    graph.AddChatModelNode("primary", chatModel)
    graph.AddChatModelNode("fallback", chatModel)
}
"""

    detections = _extract(source)
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )

    assert len(models) == 1
    assert models[0].display_name == "gpt-4o"
    assert len(models[0].metadata["eino_registrations"]) == 2
    assert len(models[0].relationships) == 1


def test_same_variable_name_in_another_function_is_not_treated_as_eino_graph() -> None:
    source = """package main

import "github.com/cloudwego/eino/compose"

func buildEino() {
    graph := compose.NewGraph[string, string]()
    _ = graph
}

func buildOther() {
    graph := other.NewGraph()
    graph.AddChatModelNode("wrong", model)
}
"""

    detections = _extract(source)

    assert (
        len(
            _by_type(
                detections,
                ComponentType.AGENT,
            )
        )
        == 1
    )
    assert (
        _by_type(
            detections,
            ComponentType.MODEL,
        )
        == []
    )


def test_lookalike_fixture_emits_no_detections() -> None:
    fixture = _FIXTURES / "go_eino_graph_negative" / "main.go"
    source = fixture.read_text(encoding="utf-8")

    assert (
        _extract(
            source,
            str(fixture),
        )
        == []
    )


def test_extract_parses_content_when_parse_result_is_not_go_result() -> None:
    source = """package main

import cmp "github.com/cloudwego/eino/compose"

func main() {
    graph := cmp.NewGraph[string, string]()
    _ = graph
}
"""

    detections = _ADAPTER.extract(
        source,
        "main.go",
        None,
    )

    assert [
        item.display_name
        for item in _by_type(
            detections,
            ComponentType.AGENT,
        )
    ] == ["graph"]
