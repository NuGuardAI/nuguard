"""Unit tests for Google ADK adapters (Python and TypeScript).

Covers:
  TestPythonADKCanHandle           — adapter activates on google.adk imports
  TestPythonADKAgents              — Agent/LlmAgent/etc. instantiation → AGENT nodes
  TestPythonADKModels              — Gemini(model=...) → MODEL nodes
  TestPythonADKTools               — tools= list + create_xxx_tools() factory detection
  TestPythonADKRunnerFallback      — Runner(agent=...) → AGENT fallback
  TestPythonADKModelNormalization  — os.environ.get("VAR", "default") handling
  TestTSADKModelNormalization      — process.env.X || 'default' → clean model name
  TestTSADKFactoryToolDetection    — createXxxTools import → TOOL node
  TestTSADKRunnerFallback          — runner.run() without new Agent(...) → AGENT fallback
  TestTSADKNegatives               — no false positives
"""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.sbom.adapters.python.google_adk import GoogleADKPythonAdapter
from nuguard.sbom.adapters.typescript.google_adk import GoogleADKAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.core.ts_parser import parse_typescript
from nuguard.sbom.types import ComponentType

_PY_ADAPTER = GoogleADKPythonAdapter()
_TS_ADAPTER = GoogleADKAdapter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _py_extract(code: str) -> list[Any]:
    pr = parse(code)
    return _PY_ADAPTER.extract(code, "agents.py", pr)


def _ts_extract(code: str) -> list[Any]:
    pr = parse_typescript(code, "agents.ts")
    return _TS_ADAPTER.extract(code, "agents.ts", pr)


def _by_type(detections: list[Any], ctype: ComponentType) -> list[Any]:
    return [d for d in detections if d.component_type == ctype]


# ---------------------------------------------------------------------------
# Python ADK — can_handle
# ---------------------------------------------------------------------------


class TestPythonADKCanHandle:
    @pytest.mark.parametrize(
        "module",
        [
            "google.adk",
            "google.adk.agents",
            "google.adk.models",
            "google.adk.tools",
            "google.adk.runners",
        ],
    )
    def test_activates_on_google_adk(self, module: str) -> None:
        assert _PY_ADAPTER.can_handle({module})

    def test_does_not_activate_on_unrelated(self) -> None:
        assert not _PY_ADAPTER.can_handle({"openai", "langchain", "fastapi"})

    def test_none_parse_result_returns_empty(self) -> None:
        assert _PY_ADAPTER.extract("", "x.py", None) == []


# ---------------------------------------------------------------------------
# Python ADK — Agent detection
# ---------------------------------------------------------------------------


class TestPythonADKAgents:
    def test_agent_class_emits_agent(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "agent = Agent(name='car_assistant', model='gemini-2.0-flash')\n"
        )
        agents = _by_type(_py_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT from Agent(...)"
        assert any("car_assistant" in a.display_name for a in agents)

    def test_llm_agent_emits_agent(self) -> None:
        code = (
            "from google.adk.agents import LlmAgent\n"
            "agent = LlmAgent(name='helper', model='gemini-2.0-flash')\n"
        )
        agents = _by_type(_py_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT from LlmAgent(...)"

    def test_agent_with_model_emits_model(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "agent = Agent(name='car_assistant', model='gemini-2.0-flash')\n"
        )
        models = _by_type(_py_extract(code), ComponentType.MODEL)
        assert models, "Expected MODEL from Agent(model=...)"
        assert any("gemini" in m.display_name.lower() for m in models)


# ---------------------------------------------------------------------------
# Python ADK — Model detection
# ---------------------------------------------------------------------------


class TestPythonADKModels:
    def test_gemini_class_emits_model(self) -> None:
        code = (
            "from google.adk.models import Gemini\n"
            "model = Gemini(model='gemini-2.0-flash-001')\n"
        )
        models = _by_type(_py_extract(code), ComponentType.MODEL)
        assert models, "Expected MODEL from Gemini(...)"
        assert any("gemini" in m.display_name.lower() for m in models)

    def test_const_model_var_resolved(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "MODEL = 'gemini-2.0-flash-001'\n"
            "agent = Agent(name='assistant', model=MODEL)\n"
        )
        models = _by_type(_py_extract(code), ComponentType.MODEL)
        assert models, "Expected MODEL resolved from constant"
        assert any("gemini" in m.display_name.lower() for m in models)


# ---------------------------------------------------------------------------
# Python ADK — Tool detection (function refs + factory functions)
# ---------------------------------------------------------------------------


class TestPythonADKTools:
    def test_tools_list_function_refs_emitted(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "def search(q: str): return ''\n"
            "def navigate(dest: str): return ''\n"
            "agent = Agent(name='car', model='gemini-2.0-flash', tools=[search, navigate])\n"
        )
        tools = _by_type(_py_extract(code), ComponentType.TOOL)
        tool_names = {t.display_name for t in tools}
        assert "search" in tool_names
        assert "navigate" in tool_names

    def test_factory_function_create_xxx_tools_emits_tool(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "from tools.navigation import create_navigation_tools\n"
            "\n"
            "nav_tools = create_navigation_tools()\n"
            "agent = Agent(name='car', model='gemini-2.0-flash', tools=nav_tools)\n"
        )
        tools = _by_type(_py_extract(code), ComponentType.TOOL)
        tool_names = {t.display_name for t in tools}
        assert any("navigation" in n for n in tool_names), (
            f"Expected navigation tool from create_navigation_tools(); got {tool_names}"
        )

    def test_multiple_factory_tools_detected(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "\n"
            "weather_tools = create_weather_tools()\n"
            "climate_tools = create_climate_tools()\n"
            "media_tools = create_media_tools()\n"
        )
        tools = _by_type(_py_extract(code), ComponentType.TOOL)
        tool_names = {t.display_name for t in tools}
        assert any("weather" in n for n in tool_names)
        assert any("climate" in n for n in tool_names)
        assert any("media" in n for n in tool_names)

    def test_factory_tool_confidence(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "tools = create_communication_tools()\n"
        )
        tools = _by_type(_py_extract(code), ComponentType.TOOL)
        factory_tools = [t for t in tools if "communication" in t.display_name]
        assert factory_tools, "Expected communication tool"
        assert factory_tools[0].confidence == 0.80
        assert factory_tools[0].evidence_kind == "ast_call"

    def test_no_duplicate_factory_tools(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "tools1 = create_search_tools()\n"
            "tools2 = create_search_tools()\n"
        )
        tools = _by_type(_py_extract(code), ComponentType.TOOL)
        search_tools = [t for t in tools if "search" in t.display_name]
        assert len(search_tools) == 1, "Expected deduplication of same factory"


# ---------------------------------------------------------------------------
# Python ADK — Runner fallback
# ---------------------------------------------------------------------------


class TestPythonADKRunnerFallback:
    def test_runner_with_agent_arg_emits_agent(self) -> None:
        code = (
            "from google.adk.runners import Runner\n"
            "from google.adk.agents import Agent\n"
            "\n"
            "car_agent = Agent(name='car', model='gemini-2.0-flash')\n"
            "runner = Runner(agent=car_agent)\n"
        )
        # car_agent is already detected via Agent(...); runner fallback won't fire
        agents = _by_type(_py_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT"

    def test_runner_without_explicit_agent_emits_fallback(self) -> None:
        code = (
            "from google.adk.runners import Runner\n"
            "\n"
            "runner = Runner(agent=my_agent, app_name='car_app')\n"
        )
        agents = _by_type(_py_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT fallback from Runner(agent=...)"
        assert agents[0].confidence == 0.78
        assert agents[0].evidence_kind == "ast_instantiation"

    def test_runner_app_name_kwarg_captured_in_metadata(self) -> None:
        """Runner(app_name=...) must be stored in metadata so redteam can bypass /list-apps."""
        code = (
            "from google.adk.runners import Runner\n"
            "\n"
            "runner = Runner(agent=my_agent, app_name='car_app')\n"
        )
        agents = _by_type(_py_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT fallback from Runner(agent=...)"
        assert agents[0].metadata.get("adk_app_name") == "car_app"

    def test_runner_without_app_name_has_no_adk_app_name_metadata(self) -> None:
        code = (
            "from google.adk.runners import Runner\n"
            "\n"
            "runner = Runner(agent=my_agent)\n"
        )
        agents = _by_type(_py_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT fallback"
        assert "adk_app_name" not in agents[0].metadata


# ---------------------------------------------------------------------------
# Python ADK — Model name normalization
# ---------------------------------------------------------------------------


class TestPythonADKModelNormalization:
    def test_const_map_resolves_module_level_var(self) -> None:
        code = (
            "from google.adk.agents import Agent\n"
            "GEMINI_MODEL = 'gemini-2.0-flash'\n"
            "agent = Agent(name='car', model=GEMINI_MODEL)\n"
        )
        models = _by_type(_py_extract(code), ComponentType.MODEL)
        assert models, "Expected MODEL"
        assert any("gemini-2.0-flash" in m.display_name for m in models)


# ---------------------------------------------------------------------------
# TypeScript ADK — model name normalization
# ---------------------------------------------------------------------------


class TestTSADKModelNormalization:
    def test_env_default_expression_normalized(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "const agent = new Agent({\n"
            "  name: 'car_assistant',\n"
            "  model: process.env.VITE_GEMINI_MODEL || 'gemini-2.0-flash',\n"
            "});\n"
        )
        models = _by_type(_ts_extract(code), ComponentType.MODEL)
        model_names = {m.display_name for m in models}
        assert any("gemini-2.0-flash" in n for n in model_names), (
            f"Expected 'gemini-2.0-flash' but got {model_names}"
        )
        assert not any("process.env" in n for n in model_names), (
            "Expected env expression stripped but found raw expression"
        )

    def test_plain_model_string_unchanged(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "const agent = new Agent({\n"
            "  name: 'car',\n"
            "  model: 'gemini-2.0-flash-exp',\n"
            "});\n"
        )
        models = _by_type(_ts_extract(code), ComponentType.MODEL)
        model_names = {m.display_name for m in models}
        assert any("gemini-2.0-flash-exp" in n for n in model_names)


# ---------------------------------------------------------------------------
# TypeScript ADK — factory tool import detection
# ---------------------------------------------------------------------------


class TestTSADKFactoryToolDetection:
    def test_create_navigation_tools_import_emits_tool(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "import { createNavigationTools } from './tools/navigation';\n"
            "import { createWeatherTools } from './tools/weather';\n"
        )
        tools = _by_type(_ts_extract(code), ComponentType.TOOL)
        tool_names = {t.display_name for t in tools}
        assert any("navigation" in n for n in tool_names), (
            f"Expected navigation tool; got {tool_names}"
        )
        assert any("weather" in n for n in tool_names), (
            f"Expected weather tool; got {tool_names}"
        )

    def test_all_six_gemini_auto_tools_detected(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "import { createCommunicationTools } from './tools/communication';\n"
            "import { createSearchTools } from './tools/search';\n"
            "import { createNavigationTools } from './tools/navigation';\n"
            "import { createWeatherTools } from './tools/weather';\n"
            "import { createClimateTools } from './tools/climate';\n"
            "import { createMediaTools } from './tools/media';\n"
        )
        tools = _by_type(_ts_extract(code), ComponentType.TOOL)
        tool_names = {t.display_name for t in tools}
        expected_caps = {"communication", "search", "navigation", "weather", "climate", "media"}
        for cap in expected_caps:
            assert any(cap in n for n in tool_names), (
                f"Expected '{cap}' tool not found in {tool_names}"
            )

    def test_factory_tool_confidence_is_0_80(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "import { createNavigationTools } from './tools/navigation';\n"
        )
        tools = _by_type(_ts_extract(code), ComponentType.TOOL)
        factory_tools = [t for t in tools if "navigation" in t.display_name]
        assert factory_tools, "Expected navigation tool"
        assert factory_tools[0].confidence == 0.80
        assert factory_tools[0].evidence_kind == "ast_import"

    def test_no_duplicate_factory_tool_from_same_import(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "import { createSearchTools, createSearchTools as st2 } from './tools/search';\n"
        )
        tools = _by_type(_ts_extract(code), ComponentType.TOOL)
        search_tools = [t for t in tools if "search" in t.display_name]
        assert len(search_tools) == 1, "Expected deduplication"

    def test_non_tool_imports_not_emitted(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "import { createAgent } from './agent_factory';\n"
            "import { buildTools } from './tools/builder';\n"
        )
        tools = _by_type(_ts_extract(code), ComponentType.TOOL)
        # createAgent doesn't match create.*Tools; buildTools doesn't start with create
        assert not tools or all("agent" not in t.display_name for t in tools)


# ---------------------------------------------------------------------------
# TypeScript ADK — runner.run() agent fallback
# ---------------------------------------------------------------------------


class TestTSADKRunnerFallback:
    def test_runner_run_without_new_agent_emits_agent(self) -> None:
        code = (
            "import { Runner } from '@google/adk';\n"
            "const runner = new Runner({ agent: myAgent });\n"
            "runner.run(userId, sessionId, message);\n"
        )
        agents = _by_type(_ts_extract(code), ComponentType.AGENT)
        assert agents, "Expected AGENT from runner.run() pattern"
        assert agents[0].confidence == 0.75
        assert agents[0].evidence_kind == "ast_call"

    def test_runner_run_with_explicit_agent_no_duplicate(self) -> None:
        code = (
            "import { Agent, Runner } from '@google/adk';\n"
            "const agent = new Agent({ name: 'car', model: 'gemini-2.0-flash' });\n"
            "const runner = new Runner({ agent });\n"
            "runner.run(sessionId, message);\n"
        )
        agents = _by_type(_ts_extract(code), ComponentType.AGENT)
        # Agent(...) was found directly → runner fallback should NOT add another
        agent_names = {a.display_name for a in agents}
        assert "car" in agent_names
        # runner fallback fires only when no AGENT nodes were found initially
        # — with explicit Agent() present, only 1 AGENT node expected
        assert len(agents) == 1, f"Expected exactly 1 agent, got {agent_names}"


# ---------------------------------------------------------------------------
# TypeScript ADK — negatives
# ---------------------------------------------------------------------------


class TestTSADKNegatives:
    def test_no_tool_from_non_create_tools_pattern(self) -> None:
        code = (
            "import { Agent } from '@google/adk';\n"
            "import { buildNavigationTools } from './tools/nav';\n"
            "import { NavigationTools } from './tools/nav';\n"
        )
        tools = _by_type(_ts_extract(code), ComponentType.TOOL)
        assert not tools, f"Expected no tools from non-createXxxTools imports; got {[t.display_name for t in tools]}"

    def test_empty_source_no_crash(self) -> None:
        dets = _ts_extract("")
        assert isinstance(dets, list)

    def test_unrelated_ts_file_no_detections(self) -> None:
        code = (
            "import express from 'express';\n"
            "const app = express();\n"
            "app.get('/', (req, res) => res.send('hello'));\n"
        )
        dets = _ts_extract(code)
        assert dets == []
