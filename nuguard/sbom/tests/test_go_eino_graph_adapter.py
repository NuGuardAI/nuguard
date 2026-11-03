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
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.tests.conftest import GO_ONLY, nodes
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


def test_adk_chat_model_agent_links_model_prompt_tools_and_runner() -> None:
    source = """package agent

import (
    "github.com/cloudwego/eino-ext/components/model/openai"
    "github.com/cloudwego/eino/adk"
    "github.com/cloudwego/eino/components/tool/utils"
    "github.com/cloudwego/eino/compose"
)

func build(ctx context.Context) {
    cm, _ := openai.NewChatModel(ctx, &openai.ChatModelConfig{Model: "gpt-4o-mini"})
    lookup := utils.NewTool(&schema.ToolInfo{Name: "lookup_order", Desc: "Look up"}, fn)
    agent, _ := adk.NewChatModelAgent(ctx, &adk.ChatModelAgentConfig{
        Name:        "support",
        Description: "Answer support questions",
        Instruction: systemPrompt,
        Model:       cm,
        ToolsConfig: adk.ToolsConfig{ToolsNodeConfig: compose.ToolsNodeConfig{
            Tools: []tool.BaseTool{lookup},
        }},
    })
    runner := adk.NewRunner(ctx, adk.RunnerConfig{Agent: agent})
    _ = runner
}
"""

    detections = _extract(source)
    (framework,) = _by_type(detections, ComponentType.FRAMEWORK)
    (agent,) = _by_type(detections, ComponentType.AGENT)
    (tool,) = _by_type(detections, ComponentType.TOOL)
    targets = {
        (relationship.relationship_type, relationship.target_canonical)
        for relationship in agent.relationships
        if relationship.source_canonical == agent.canonical_name
    }

    assert agent.display_name == "support"
    assert agent.line == _line_of(source, "adk.NewChatModelAgent")
    assert agent.metadata["orchestration_kind"] == "adk_chat_model_agent"
    assert agent.metadata["description"] == "Answer support questions"
    assert agent.metadata["model_name"] == "gpt-4o-mini"
    assert agent.metadata["provider"] == "openai"
    assert agent.metadata["instruction_variable"] == "systemPrompt"
    assert agent.metadata["runner"]["line"] == _line_of(source, "adk.NewRunner")
    assert ("USES", "systemprompt") in targets
    assert ("USES", "gpt_4o_mini") in targets
    assert ("CALLS", tool.canonical_name) in targets
    assert _has_relationship(agent, source=framework, relationship_type="USES")


def test_adk_agent_with_unresolvable_fields_emits_no_guessed_edges() -> None:
    source = """package agent

import "github.com/cloudwego/eino/adk"

func New(ctx context.Context, deps Deps, tools []tool.BaseTool) {
    agentInstance, _ := adk.NewChatModelAgent(ctx, &adk.ChatModelAgentConfig{
        Name:        "support-copilot",
        Instruction: "Answer briefly.",
        Model:       deps.Model,
        ToolsConfig: adk.ToolsConfig{ToolsNodeConfig: compose.ToolsNodeConfig{
            Tools: tools,
        }},
    })
    _ = agentInstance
}
"""

    (agent,) = _by_type(_extract(source), ComponentType.AGENT)

    assert agent.display_name == "support-copilot"
    assert agent.metadata["system_prompt"] == "Answer briefly."
    assert "model_name" not in agent.metadata
    assert all(
        relationship.source_canonical != agent.canonical_name
        for relationship in agent.relationships
    )


_ADK_SAME_FILE_PROMPT = """package agent

import (
    "context"

    "github.com/cloudwego/eino-ext/components/model/openai"
    "github.com/cloudwego/eino/adk"
)

const supportPrompt = "You are a careful support agent."

func Build(ctx context.Context) {
    cm, _ := openai.NewChatModel(ctx, &openai.ChatModelConfig{Model: "support_prod"})
    agent, _ := adk.NewChatModelAgent(ctx, &adk.ChatModelAgentConfig{
        Name:        "support",
        Instruction: supportPrompt,
        Model:       cm,
    })
    _ = agent
}
"""


def test_adk_same_file_prompt_const_still_emits_prompt_hint() -> None:
    (agent,) = _by_type(_extract(_ADK_SAME_FILE_PROMPT), ComponentType.AGENT)
    targets = {
        (relationship.relationship_type, relationship.target_type, relationship.target_canonical)
        for relationship in agent.relationships
        if relationship.source_canonical == agent.canonical_name
    }

    assert agent.metadata["system_prompt"] == "You are a careful support agent."
    assert agent.metadata["instruction_variable"] == "supportPrompt"
    assert ("USES", ComponentType.PROMPT, "supportprompt") in targets


def test_adk_agent_emits_model_node_and_preview() -> None:
    detections = _extract(_ADK_SAME_FILE_PROMPT)
    (agent,) = _by_type(detections, ComponentType.AGENT)
    (model,) = _by_type(detections, ComponentType.MODEL)

    assert model.canonical_name == "support_prod"
    assert model.display_name == "support_prod"
    assert model.metadata["provider"] == "openai"
    assert any(
        relationship.source_canonical == agent.canonical_name
        and relationship.target_canonical == model.canonical_name
        and relationship.relationship_type == "USES"
        for relationship in agent.relationships
    )


def test_adk_literal_instruction_sets_system_prompt_preview() -> None:
    long_instruction = "x" * 600
    source = _ADK_SAME_FILE_PROMPT.replace(
        "Instruction: supportPrompt,",
        f'Instruction: "{long_instruction}",',
    )
    (agent,) = _by_type(_extract(source), ComponentType.AGENT)

    assert agent.metadata["system_prompt"] == long_instruction
    assert agent.metadata["system_prompt_preview"] == long_instruction[:500]
    assert "instruction_variable" not in agent.metadata


def test_adk_agent_extractor_sets_excerpt_and_model_edge(tmp_path: Path) -> None:
    (tmp_path / "agent.go").write_text(
        _ADK_SAME_FILE_PROMPT.replace(
            "Instruction: supportPrompt,",
            'Instruction: "Answer support questions politely.",',
        ),
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(tmp_path, GO_ONLY)
    by_id = {node.id: node for node in doc.nodes}
    (agent,) = nodes(doc, ComponentType.AGENT)
    (model,) = [node for node in nodes(doc, ComponentType.MODEL) if node.name == "support_prod"]

    assert agent.metadata.system_prompt_excerpt == "Answer support questions politely."
    assert any(
        by_id[edge.source] is agent
        and by_id[edge.target] is model
        and edge.relationship_type == "USES"
        for edge in doc.edges
    )
