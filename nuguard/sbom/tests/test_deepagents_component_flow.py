from __future__ import annotations

from nuguard.sbom.adapters.python._deepagents_flow import (
    build_deep_agent_component_flow_index,
)
from nuguard.sbom.adapters.python.langgraph import LangGraphAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.types import ComponentType


def _design_agent_fixture() -> dict[str, str]:
    return {
        "src/app/assembly.py": """
from dataclasses import dataclass
from deepagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from app.subagents import build_subagents
from app.tools import build_tools

@dataclass(frozen=True)
class AgentComponents:
    model: BaseChatModel
    tools: tuple[BaseTool, ...]
    subagents: tuple[SubAgent, ...]

def build_agent_components() -> AgentComponents:
    model = ChatOpenAI(model="gpt-4o")
    external_tools = runtime_tools()
    tools: list[BaseTool] = [*build_tools(), *external_tools]
    subagents = build_subagents()
    return AgentComponents(
        model=model,
        tools=tuple(tools),
        subagents=tuple(subagents),
    )
""",
        "src/app/tools.py": """
from langchain_core.tools import BaseTool, StructuredTool

TOOL_NAME = "fetch_asset"

def build_tools() -> list[BaseTool]:
    async def search(): pass
    async def fetch(): pass
    return [
        StructuredTool.from_function(coroutine=search, name="search"),
        StructuredTool.from_function(coroutine=fetch, name=TOOL_NAME),
    ]

def unrelated_tools() -> list[BaseTool]:
    return [StructuredTool.from_function(coroutine=other, name="unrelated")]
""",
        "src/app/subagents.py": """
from dataclasses import dataclass
from deepagents import SubAgent
from vendor import GENERAL_PURPOSE_SUBAGENT

LEARNING = "learning"

@dataclass(frozen=True)
class Worker:
    effort: str

WORKERS = (Worker("low"), Worker(effort="max"))

def build_subagents() -> list[SubAgent]:
    general: SubAgent = {**GENERAL_PURPOSE_SUBAGENT}
    helpers: list[SubAgent] = [general, {"name": "reviewer"}]
    for worker in WORKERS:
        helpers.append({"name": worker.effort})
    if enabled():
        helpers.append({"name": LEARNING})
    helpers.append({"name": runtime_name()})
    return helpers

UNRELATED: SubAgent = {"name": "unrelated-agent"}
""",
        "src/app/runtime.py": """
from deepagents import create_deep_agent
from app.assembly import AgentComponents

def compile_graph(components: AgentComponents):
    return create_deep_agent(
        name="Design Agent",
        model=components.model,
        tools=list(components.tools),
        subagents=list(components.subagents) or None,
    )
""",
    }


def test_design_agent_shaped_component_flow_is_exact_and_has_no_fanout() -> None:
    sources = _design_agent_fixture()
    index = build_deep_agent_component_flow_index(sources)

    flow = index.resolve("src/app/runtime.py", 6, "create_deep_agent")

    assert flow is not None
    assert [target.display_name for target in flow.models] == ["gpt-4o"]
    assert [target.display_name for target in flow.tools] == ["search", "fetch_asset"]
    assert [target.display_name for target in flow.subagents] == [
        "reviewer",
        "low",
        "max",
        "learning",
    ]
    all_names = {
        target.display_name
        for targets in (flow.models, flow.tools, flow.subagents)
        for target in targets
    }
    assert "unrelated" not in all_names
    assert "unrelated-agent" not in all_names

    adapter = LangGraphAdapter()
    adapter.set_deep_agent_component_flow(index)
    runtime = sources["src/app/runtime.py"]
    detections = adapter.extract(runtime, "src/app/runtime.py", parse(runtime))
    agent = next(
        item
        for item in detections
        if item.component_type == ComponentType.AGENT and item.display_name == "Design Agent"
    )
    assert {
        (hint.target_type, hint.target_canonical, hint.relationship_type)
        for hint in agent.relationships
    } == {
        (ComponentType.MODEL, "gpt_4o", "USES"),
        (ComponentType.TOOL, "langchain_tool_search", "CALLS"),
        (ComponentType.TOOL, "langchain_tool_fetch_asset", "CALLS"),
        (ComponentType.AGENT, "langgraph_reviewer", "CALLS"),
        (ComponentType.AGENT, "langgraph_low", "CALLS"),
        (ComponentType.AGENT, "langgraph_max", "CALLS"),
        (ComponentType.AGENT, "langgraph_learning", "CALLS"),
    }


def test_ambiguous_component_constructor_rejects_all_cross_file_flow() -> None:
    sources = _design_agent_fixture()
    sources["src/app/alternate.py"] = """
from app.assembly import AgentComponents

def alternate(model, tools, subagents):
    return AgentComponents(model=model, tools=tools, subagents=subagents)
"""

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 6, "create_deep_agent") is None


def test_untyped_or_lookalike_component_parameter_is_rejected() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = """
from deepagents import create_deep_agent
from local_types import AgentComponents

def compile_graph(components: AgentComponents):
    return create_deep_agent(model=components.model, tools=list(components.tools))
"""

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 5, "create_deep_agent") is None


def test_same_line_factory_calls_are_ambiguous_and_rejected() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = """
from deepagents import create_deep_agent
from app.assembly import AgentComponents

def compile_graph(components: AgentComponents):
    return (create_deep_agent(model=components.model), create_deep_agent(tools=list(components.tools)))
"""

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 5, "create_deep_agent") is None


def test_dynamic_and_mutable_subagent_names_are_not_emitted() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] = """
from dataclasses import dataclass
from deepagents import SubAgent

@dataclass
class Worker:
    effort: str

WORKERS = (Worker("mutable"),)

def build_subagents() -> list[SubAgent]:
    helpers: list[SubAgent] = []
    for worker in WORKERS:
        helpers.append({"name": worker.effort})
    helpers.append({"name": runtime_name})
    return helpers
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert flow.subagents == ()


def test_reassigned_immutable_worker_tuple_is_rejected() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] += '\nWORKERS += (Worker("later"),)\n'

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert [target.display_name for target in flow.subagents] == ["reviewer", "learning"]


def test_cyclic_tool_builders_stop_without_guessing() -> None:
    sources = _design_agent_fixture()
    sources["src/app/tools.py"] = """
from langchain_core.tools import BaseTool

def build_tools() -> list[BaseTool]:
    return other()

def other() -> list[BaseTool]:
    return build_tools()
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert flow.tools == ()


def test_factory_and_model_lookalike_imports_are_rejected() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = sources["src/app/runtime.py"].replace(
        "from deepagents import create_deep_agent",
        "from fake_deepagents import create_deep_agent",
    )
    assert (
        build_deep_agent_component_flow_index(sources).resolve(
            "src/app/runtime.py", 6, "create_deep_agent"
        )
        is None
    )

    sources = _design_agent_fixture()
    sources["src/app/assembly.py"] = sources["src/app/assembly.py"].replace(
        "from langchain_openai import ChatOpenAI",
        "from langchain_openai_lookalike import ChatOpenAI",
    )
    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )
    assert flow is not None
    assert flow.models == ()


def test_module_rebinding_invalidates_official_factory_import() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = sources["src/app/runtime.py"].replace(
        "from app.assembly import AgentComponents\n",
        "from app.assembly import AgentComponents\ncreate_deep_agent = fake_factory\n",
    )

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 7, "create_deep_agent") is None


def test_module_factory_attribute_mutation_invalidates_import_provenance() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = """
import deepagents as da
from app.assembly import AgentComponents

alias = da
alias.create_deep_agent = fake_factory

def compile_graph(components: AgentComponents):
    return da.create_deep_agent(model=components.model, tools=list(components.tools))
"""

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 9, "create_deep_agent") is None


def test_component_parameter_rebinding_rejects_all_flow() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = sources["src/app/runtime.py"].replace(
        "def compile_graph(components: AgentComponents):\n",
        "def compile_graph(components: AgentComponents):\n    components = runtime_components()\n",
    )

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 7, "create_deep_agent") is None


def test_component_attribute_mutation_rejects_only_affected_flow() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = sources["src/app/runtime.py"].replace(
        "def compile_graph(components: AgentComponents):\n",
        "def compile_graph(components: AgentComponents):\n    components.model = runtime_model()\n",
    )

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 7, "create_deep_agent"
    )

    assert flow is not None
    assert flow.models == ()
    assert [target.display_name for target in flow.tools] == ["search", "fetch_asset"]


def test_component_alias_and_object_setattr_reject_affected_flow() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = """
from deepagents import create_deep_agent
from app.assembly import AgentComponents

def compile_graph(components: AgentComponents):
    alias = components
    object.__setattr__(alias, "model", runtime_model())
    return create_deep_agent(
        model=components.model,
        tools=list(components.tools),
        subagents=list(components.subagents),
    )
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 8, "create_deep_agent"
    )

    assert flow is not None
    assert flow.models == ()
    assert [target.display_name for target in flow.tools] == ["search", "fetch_asset"]
    assert [target.display_name for target in flow.subagents] == [
        "reviewer",
        "low",
        "max",
        "learning",
    ]


def test_component_escape_through_alias_rejects_all_flow() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = """
from deepagents import create_deep_agent
from app.assembly import AgentComponents

def compile_graph(components: AgentComponents):
    alias = components
    mutate([alias])
    return create_deep_agent(
        model=components.model,
        tools=list(components.tools),
        subagents=list(components.subagents),
    )
"""

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 8, "create_deep_agent") is None


def test_test_only_constructor_cannot_supply_production_flow() -> None:
    sources = _design_agent_fixture()
    sources["src/app/assembly.py"] = (
        sources["src/app/assembly.py"]
        .replace(
            "def build_agent_components() -> AgentComponents:",
            "def not_a_constructor() -> object:",
        )
        .replace("return AgentComponents(\n", "return object(\n")
    )
    sources["tests/test_components.py"] = """
from app.assembly import AgentComponents
from langchain_openai import ChatOpenAI

def fixture():
    return AgentComponents(
        model=ChatOpenAI(model="test-only"), tools=[], subagents=[]
    )
"""

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 6, "create_deep_agent") is None


def test_subagent_name_allows_unpack_only_before_explicit_name() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] = """
from deepagents import SubAgent

def build_subagents() -> list[SubAgent]:
    helpers: list[SubAgent] = [{**runtime_mapping, "name": "proven"}]
    return helpers
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert [target.display_name for target in flow.subagents] == ["proven"]


def test_subagent_name_rejects_later_unpack_and_duplicate_key() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] = """
from deepagents import SubAgent

def build_subagents() -> list[SubAgent]:
    helpers: list[SubAgent] = []
    helpers.append({"name": "fabricated", **runtime_mapping})
    helpers.append({"name": "first", "name": runtime_name()})
    return helpers
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert flow.subagents == ()


def test_unreachable_collection_append_is_ignored() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] = """
from deepagents import SubAgent

def build_subagents() -> list[SubAgent]:
    helpers: list[SubAgent] = []
    return helpers
    helpers.append({"name": "unreachable"})
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert flow.subagents == ()


def test_unreachable_collection_return_is_ignored() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] = """
from deepagents import SubAgent

def build_subagents() -> list[SubAgent]:
    return []
    return [{"name": "unreachable"}]
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert flow.subagents == ()


def test_unreachable_component_constructor_is_ignored() -> None:
    sources = _design_agent_fixture()
    sources["src/app/assembly.py"] = sources["src/app/assembly.py"].replace(
        '    model = ChatOpenAI(model="gpt-4o")',
        '    return object()\n    model = ChatOpenAI(model="gpt-4o")',
    )

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 6, "create_deep_agent") is None


def test_constant_false_component_constructor_is_ignored() -> None:
    sources = _design_agent_fixture()
    sources["src/app/assembly.py"] = sources["src/app/assembly.py"].replace(
        "def build_agent_components() -> AgentComponents:\n",
        "def build_agent_components() -> AgentComponents:\n    if False:\n",
    ).replace(
        '    model = ChatOpenAI(model="gpt-4o")',
        '        model = ChatOpenAI(model="gpt-4o")',
    ).replace(
        "    external_tools = runtime_tools()",
        "        external_tools = runtime_tools()",
    ).replace(
        "    tools: list[BaseTool] = [*build_tools(), *external_tools]",
        "        tools: list[BaseTool] = [*build_tools(), *external_tools]",
    ).replace(
        "    subagents = build_subagents()",
        "        subagents = build_subagents()",
    ).replace(
        "    return AgentComponents(\n",
        "        return AgentComponents(\n",
    ).replace(
        "        model=model,\n        tools=tuple(tools),\n        subagents=tuple(subagents),\n    )",
        "            model=model,\n            tools=tuple(tools),\n"
        "            subagents=tuple(subagents),\n        )\n    return object()",
    )

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 6, "create_deep_agent") is None


def test_constant_false_collection_mutation_is_ignored_but_unknown_is_kept() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] = """
from deepagents import SubAgent

def build_subagents(enabled: bool) -> list[SubAgent]:
    helpers: list[SubAgent] = []
    if False:
        helpers.append({"name": "unreachable"})
    for _ in ():
        helpers.append({"name": "also-unreachable"})
    if enabled:
        helpers.append({"name": "possible"})
    return helpers
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert [target.display_name for target in flow.subagents] == ["possible"]


def test_collection_append_after_terminal_if_is_ignored() -> None:
    sources = _design_agent_fixture()
    sources["src/app/subagents.py"] = """
from deepagents import SubAgent

def build_subagents(enabled: bool) -> list[SubAgent]:
    helpers: list[SubAgent] = []
    if enabled:
        return helpers
    else:
        raise RuntimeError
    helpers.append({"name": "unreachable"})
"""

    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )

    assert flow is not None
    assert flow.subagents == ()


def test_shadowed_factory_wrapper_and_builder_names_are_rejected_individually() -> None:
    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = """
from deepagents import create_deep_agent
from app.assembly import AgentComponents

def compile_graph(components: AgentComponents, create_deep_agent):
    return create_deep_agent(model=components.model)
"""
    assert (
        build_deep_agent_component_flow_index(sources).resolve(
            "src/app/runtime.py", 5, "create_deep_agent"
        )
        is None
    )

    sources = _design_agent_fixture()
    sources["src/app/runtime.py"] = sources["src/app/runtime.py"].replace(
        "def compile_graph(components: AgentComponents):",
        "def compile_graph(components: AgentComponents, list):",
    )
    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )
    assert flow is not None
    assert [target.display_name for target in flow.models] == ["gpt-4o"]
    assert flow.tools == ()
    assert flow.subagents == ()

    sources = _design_agent_fixture()
    sources["src/app/assembly.py"] = sources["src/app/assembly.py"].replace(
        "def build_agent_components() -> AgentComponents:",
        "def build_agent_components(build_tools) -> AgentComponents:",
    )
    flow = build_deep_agent_component_flow_index(sources).resolve(
        "src/app/runtime.py", 6, "create_deep_agent"
    )
    assert flow is not None
    assert flow.tools == ()


def test_component_field_types_require_official_import_provenance() -> None:
    sources = _design_agent_fixture()
    sources["src/app/assembly.py"] = sources["src/app/assembly.py"].replace(
        "from langchain_core.tools import BaseTool",
        "from local_types import BaseTool",
    )

    index = build_deep_agent_component_flow_index(sources)

    assert index.resolve("src/app/runtime.py", 6, "create_deep_agent") is None


def test_extractor_injects_component_flow_and_resolves_only_proven_edges(tmp_path) -> None:
    sources = _design_agent_fixture()
    for relative, source in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    document = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20),
    )

    nodes = {(node.component_type, node.name): node for node in document.nodes}
    lead = nodes[(ComponentType.AGENT, "Design Agent")]
    expected = {
        (ComponentType.MODEL, "gpt-4o", "USES"),
        (ComponentType.TOOL, "Search", "CALLS"),
        (ComponentType.TOOL, "Fetch Asset", "CALLS"),
        (ComponentType.AGENT, "Reviewer", "CALLS"),
        (ComponentType.AGENT, "Low", "CALLS"),
        (ComponentType.AGENT, "Max", "CALLS"),
        (ComponentType.AGENT, "Learning", "CALLS"),
    }
    by_id = {node.id: node for node in document.nodes}
    actual = {
        (
            by_id[edge.target].component_type,
            by_id[edge.target].name,
            edge.relationship_type.value,
        )
        for edge in document.edges
        if edge.source == lead.id
    }
    assert actual == expected
    assert (ComponentType.TOOL, "Unrelated", "CALLS") not in actual
    assert (ComponentType.AGENT, "Unrelated Agent", "CALLS") not in actual
