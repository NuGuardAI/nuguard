# Tests for the LangChainGo framework adapter.

from __future__ import annotations

from pathlib import Path

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import LangChainGoAdapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType

_FIXTURES = Path(__file__).parent / "fixtures" / "apps"
_ADAPTER = LangChainGoAdapter()


def _extract(
    source: str,
    file_path: str = "main.go",
) -> list[ComponentDetection]:
    return _ADAPTER.extract(
        source,
        file_path,
        parse_go(source, file_path),
    )


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def test_can_handle_langchaingo_root_and_subpackages() -> None:
    assert _ADAPTER.can_handle({"github.com/tmc/langchaingo"}) is True
    assert _ADAPTER.can_handle({"github.com/tmc/langchaingo/agents"}) is True
    assert _ADAPTER.can_handle({"github.com/tmc/langchaingo/llms/openai"}) is True


def test_can_handle_rejects_lookalikes_and_case_changes() -> None:
    assert _ADAPTER.can_handle({"github.com/tmc/langchaingo-extra/agents"}) is False
    assert _ADAPTER.can_handle({"evilgithub.com/tmc/langchaingo/agents"}) is False
    assert _ADAPTER.can_handle({"github.com/TMC/langchaingo/agents"}) is False


def test_fixture_emits_framework_agent_model_and_tools() -> None:
    fixture = _FIXTURES / "go_langchaingo" / "main.go"
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
    tools = _by_type(
        detections,
        ComponentType.TOOL,
    )

    assert len(frameworks) == 1
    assert len(agents) == 1
    assert len(models) == 1
    assert {tool.display_name for tool in tools} == {
        "Calculator",
        "WeatherTool",
        "WebSearch",
    }

    framework = frameworks[0]
    assert framework.canonical_name == "framework:langchaingo"
    assert framework.display_name == "LangChainGo"
    assert framework.metadata["framework"] == "langchaingo"
    assert framework.metadata["language"] == "golang"
    assert framework.metadata["module"] == "github.com/tmc/langchaingo"
    assert framework.evidence_kind == "ast_import"

    model = models[0]
    assert model.canonical_name == "gpt_4o_mini"
    assert model.display_name == "gpt-4o-mini"
    assert model.metadata["provider"] == "openai"
    assert model.metadata["model_name"] == "gpt-4o-mini"
    assert model.metadata["assigned_to"] == "llm"
    assert model.confidence == 0.94
    assert model.evidence_kind == "ast_instantiation"
    assert len(model.relationships) == 1
    assert model.relationships[0].source_canonical == framework.canonical_name
    assert model.relationships[0].target_canonical == model.canonical_name
    assert model.relationships[0].relationship_type == "USES"

    agent = agents[0]
    assert agent.canonical_name == "langchaingo_agent_agent"
    assert agent.display_name == "agent"
    assert agent.metadata["agent_type"] == "OneShotAgent"
    assert agent.metadata["assigned_to"] == "agent"
    assert agent.confidence == 0.94
    assert agent.evidence_kind == "ast_instantiation"

    assert len(tools) == 3
    assert all(tool.evidence_kind == "ast_instantiation" for tool in tools)
    assert all(tool.relationships for tool in tools)
    assert all(
        tool.relationships[0].source_canonical == framework.canonical_name
        and tool.relationships[0].target_canonical == tool.canonical_name
        and tool.relationships[0].relationship_type == "CALLS"
        for tool in tools
    )

    weather_tool = next(tool for tool in tools if tool.display_name == "WeatherTool")
    assert weather_tool.line == 16
    assert weather_tool.snippet == "WeatherTool{}"


def test_aliases_and_conversational_agent_are_supported() -> None:
    source = """package main

import (
    lcagents "github.com/tmc/langchaingo/agents"
    lcopenai "github.com/tmc/langchaingo/llms/openai"
    lctools "github.com/tmc/langchaingo/tools"
)

func main() {
    llm, _ := lcopenai.New(lcopenai.WithModel("gpt-4.1"))
    toolset := []lctools.Tool{lctools.Calculator{}}
    assistant := lcagents.NewConversationalAgent(llm, toolset)
    _ = assistant
}
"""

    detections = _extract(source)
    agents = _by_type(
        detections,
        ComponentType.AGENT,
    )
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )
    tools = _by_type(
        detections,
        ComponentType.TOOL,
    )

    assert [agent.display_name for agent in agents] == ["assistant"]
    assert agents[0].metadata["agent_type"] == "ConversationalAgent"
    assert [model.display_name for model in models] == ["gpt-4.1"]
    assert [tool.display_name for tool in tools] == ["Calculator"]


def test_dot_import_and_provider_fallback_are_supported() -> None:
    source = """package main

import (
    . "github.com/tmc/langchaingo/agents"
    "github.com/tmc/langchaingo/llms/ollama"
)

func main() {
    llm, _ := ollama.New()
    agent := NewOneShotAgent(llm, nil)
    _ = agent
}
"""

    detections = _extract(source)
    agents = _by_type(
        detections,
        ComponentType.AGENT,
    )
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )

    assert [agent.display_name for agent in agents] == ["agent"]
    assert [model.display_name for model in models] == ["ollama"]
    assert models[0].metadata["provider"] == "ollama"
    assert "model_name" not in models[0].metadata
    assert models[0].confidence == 0.86


def test_custom_struct_is_only_a_tool_inside_typed_tool_collection() -> None:
    source = """package main

import "github.com/tmc/langchaingo/tools"

type Helper struct{}
type WeatherTool struct{}

func main() {
    helper := Helper{}
    toolset := []tools.Tool{WeatherTool{}}
    _, _ = helper, toolset
}
"""

    detections = _extract(source)
    tools = _by_type(
        detections,
        ComponentType.TOOL,
    )

    assert [tool.display_name for tool in tools] == ["WeatherTool"]
    assert tools[0].confidence == 0.84
    assert tools[0].metadata["module"] == "github.com/tmc/langchaingo/tools"


def test_commented_and_string_tool_collections_are_ignored() -> None:
    source = """package main

import "github.com/tmc/langchaingo/tools"

var example = `[]tools.Tool{StringTool{}}`

func main() {
    // ignored := []tools.Tool{CommentTool{}}
    _ = example
}
"""

    detections = _extract(source)

    assert (
        len(
            _by_type(
                detections,
                ComponentType.FRAMEWORK,
            )
        )
        == 1
    )
    assert (
        _by_type(
            detections,
            ComponentType.TOOL,
        )
        == []
    )


def test_dot_import_does_not_create_unrelated_components() -> None:
    source = """package main

import (
    . "github.com/tmc/langchaingo/llms/openai"
    . "github.com/tmc/langchaingo/tools"
)

type Config struct{}

func main() {
    config := Config{}
    helper := NewHelper()
    _, _ = config, helper
}
"""

    detections = _extract(source)

    assert (
        len(
            _by_type(
                detections,
                ComponentType.FRAMEWORK,
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
            ComponentType.TOOL,
        )
        == []
    )


def test_unrelated_constructors_are_not_emitted() -> None:
    source = """package main

import (
    "github.com/tmc/langchaingo/agents"
    "github.com/tmc/langchaingo/llms/openai"
    "github.com/tmc/langchaingo/tools"
)

func main() {
    otherAgent := other.NewOneShotAgent()
    otherModel := fake.New(fake.WithModel("wrong"))
    otherTool := other.Calculator{}
    _, _, _ = otherAgent, otherModel, otherTool
    _, _, _ = agents.Agent(nil), openai.Option(nil), tools.Tool(nil)
}
"""

    detections = _extract(source)

    assert (
        len(
            _by_type(
                detections,
                ComponentType.FRAMEWORK,
            )
        )
        == 1
    )
    assert (
        _by_type(
            detections,
            ComponentType.AGENT,
        )
        == []
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
            ComponentType.TOOL,
        )
        == []
    )


def test_extract_parses_content_when_parse_result_is_not_go_result() -> None:
    source = """package main
import "github.com/tmc/langchaingo/llms/openai"
func main() {
    llm, _ := openai.New(openai.WithModel("gpt-4o"))
    _ = llm
}
"""

    detections = _ADAPTER.extract(
        source,
        "main.go",
        None,
    )
    models = _by_type(
        detections,
        ComponentType.MODEL,
    )

    assert [model.display_name for model in models] == ["gpt-4o"]


def test_lookalike_fixture_emits_no_detections() -> None:
    fixture = _FIXTURES / "go_langchaingo_negative" / "main.go"
    source = fixture.read_text(encoding="utf-8")

    assert (
        _extract(
            source,
            str(fixture),
        )
        == []
    )
