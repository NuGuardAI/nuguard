"""Tests for tool_generic and tool_browser_automation adapter correctness.

Covers:
- DuckDuckGo (DDGS) detection as a TOOL — previously a false negative
- Playwright false-positive suppression in test dirs and package.json
- Playwright still detected in non-test code (Dockerfiles, agent Python files)
- Other web search tools (Tavily, SerpAPI, BraveSearch) detected correctly
"""

from __future__ import annotations

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType

# Config that picks up both Python and JS/JSON/Dockerfile file types
_ALL_TYPES = AiSbomConfig(
    include_extensions={".py", ".js", ".ts", ".json", ".yaml", ".yml", ""},
    enable_llm=False,
)
_PY_ONLY = AiSbomConfig(include_extensions={".py"}, enable_llm=False)


def _tool_names(doc) -> set[str]:
    return {n.name.lower() for n in doc.nodes if n.component_type == ComponentType.TOOL}


def _tool_adapters(doc) -> set[str]:
    return {
        n.metadata.extras.get("adapter", "")
        for n in doc.nodes
        if n.component_type == ComponentType.TOOL
    }


# ── DuckDuckGo false-negative fix ────────────────────────────────────────────


def test_ddgs_direct_usage_detected(tmp_path):
    """DDGS used directly (not via LangChain wrapper) must be detected as TOOL.

    The node is named "search" (canonical_name of tool_search adapter).
    """
    (tmp_path / "agent.py").write_text(
        "from duckduckgo_search import DDGS\n\n"
        "with DDGS() as ddgs:\n"
        "    results = list(ddgs.text('query', max_results=3))\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    tool_nodes = [n for n in doc.nodes if n.component_type == ComponentType.TOOL]
    assert tool_nodes, f"Expected DuckDuckGo TOOL node; got no TOOL nodes"
    adapters_seen = {n.metadata.extras.get("adapter") for n in tool_nodes}
    assert "tool_search" in adapters_seen, (
        f"Expected tool_search adapter; got adapters: {adapters_seen}"
    )


def test_duckduckgo_search_package_import_detected(tmp_path):
    """``import duckduckgo_search`` package import should trigger detection."""
    (tmp_path / "search.py").write_text(
        "import duckduckgo_search\n", encoding="utf-8"
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    tool_nodes = [n for n in doc.nodes if n.component_type == ComponentType.TOOL]
    assert tool_nodes, "Expected DuckDuckGo TOOL node"
    adapters_seen = {n.metadata.extras.get("adapter") for n in tool_nodes}
    assert "tool_search" in adapters_seen, (
        f"Expected tool_search adapter; got: {adapters_seen}"
    )


def test_langchain_duckduckgo_wrapper_detected(tmp_path):
    """LangChain DuckDuckGoSearchRun wrapper must also be detected as TOOL.

    The LangGraph AST adapter detects it via section 4b (tool from *.tools.*
    imports) with a higher-confidence ast_instantiation evidence kind.
    """
    (tmp_path / "tools.py").write_text(
        "from langchain_community.tools import DuckDuckGoSearchRun\n\n"
        "search = DuckDuckGoSearchRun()\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    tool_nodes = [n for n in doc.nodes if n.component_type == ComponentType.TOOL]
    assert tool_nodes, "Expected DuckDuckGoSearchRun to produce a TOOL node"


# ── Playwright false-positive fix ─────────────────────────────────────────────


def test_playwright_in_package_json_not_detected(tmp_path):
    """playwright in package.json devDependencies must NOT create a TOOL node."""
    (tmp_path / "package.json").write_text(
        '{"devDependencies": {"@playwright/test": "^1.57.0"}}\n',
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _ALL_TYPES)
    assert not any(
        "playwright" in name for name in _tool_names(doc)
    ), f"Playwright incorrectly detected as TOOL from package.json; got: {_tool_names(doc)}"


def test_playwright_in_test_dir_not_detected(tmp_path):
    """playwright in tests/ directory must NOT create a TOOL node (test runner FP)."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "voice-agent.spec.js").write_text(
        "const { test, expect } = require('@playwright/test');\n\n"
        "test('voice agent responds', async ({ page }) => { /* ... */ });\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _ALL_TYPES)
    assert not any(
        "playwright" in name for name in _tool_names(doc)
    ), f"Playwright incorrectly detected as TOOL from test file; got: {_tool_names(doc)}"


def test_playwright_only_in_test_and_json_not_detected(tmp_path):
    """Healthcare-style project: playwright in both package.json AND tests/ must not fire."""
    (tmp_path / "package.json").write_text(
        '{"devDependencies": {"@playwright/test": "^1.57.0"}}\n',
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "e2e.spec.js").write_text(
        "import { chromium } from 'playwright';\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _ALL_TYPES)
    assert not any(
        "playwright" in name for name in _tool_names(doc)
    ), f"Playwright incorrectly detected; got: {_tool_names(doc)}"


# ── Playwright still detected in legitimate agent code ────────────────────────


def test_playwright_in_agent_code_detected(tmp_path):
    """playwright in main agent code (not test dir, not JSON) must still be detected.

    The node name is "browser_automation" (canonical_name of tool_browser_automation).
    """
    (tmp_path / "browser_agent.py").write_text(
        "from playwright.sync_api import sync_playwright\n\n"
        "def scrape(url: str) -> str:\n"
        "    with sync_playwright() as p:\n"
        "        browser = p.chromium.launch()\n"
        "        page = browser.new_page()\n"
        "        page.goto(url)\n"
        "        return page.content()\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    tool_nodes = [n for n in doc.nodes if n.component_type == ComponentType.TOOL]
    assert tool_nodes, f"Expected Playwright TOOL node in agent code; got none"
    adapters_seen = {n.metadata.extras.get("adapter") for n in tool_nodes}
    assert "tool_browser_automation" in adapters_seen, (
        f"Expected tool_browser_automation adapter; got: {adapters_seen}"
    )


def test_playwright_in_dockerfile_detected(tmp_path):
    """playwright in Dockerfile RUN instruction must still create a TOOL node."""
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\n"
        "RUN playwright install --with-deps chromium\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(
        tmp_path,
        AiSbomConfig(include_extensions={""}, enable_llm=False),
    )
    assert any(
        "playwright" in name for name in _tool_names(doc)
    ), f"Expected Playwright TOOL node from Dockerfile; got: {_tool_names(doc)}"


# ── Other search tools ────────────────────────────────────────────────────────


def test_tavily_search_detected(tmp_path):
    """TavilyClient must be detected as TOOL (via tool_search adapter)."""
    (tmp_path / "agent.py").write_text(
        "from tavily import TavilyClient\n\n"
        "client = TavilyClient(api_key='tvly-xxx')\n"
        "results = client.search('query')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    tool_nodes = [n for n in doc.nodes if n.component_type == ComponentType.TOOL]
    assert tool_nodes, f"Expected Tavily TOOL node; got none"
    adapters_seen = {n.metadata.extras.get("adapter") for n in tool_nodes}
    assert "tool_search" in adapters_seen, (
        f"Expected tool_search adapter; got: {adapters_seen}"
    )


def test_serpapi_detected(tmp_path):
    """SerpAPIWrapper must be detected as TOOL (via tool_search adapter)."""
    (tmp_path / "tools.py").write_text(
        "from langchain_community.utilities import SerpAPIWrapper\n\n"
        "search = SerpAPIWrapper()\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    tool_nodes = [n for n in doc.nodes if n.component_type == ComponentType.TOOL]
    assert tool_nodes, "Expected SerpAPI TOOL node; got none"
    adapters_seen = {n.metadata.extras.get("adapter") for n in tool_nodes}
    assert "tool_search" in adapters_seen, (
        f"Expected tool_search adapter; got: {adapters_seen}"
    )


def test_perplexity_search_detected(tmp_path):
    """PerplexityClient must be detected as TOOL (via tool_search adapter)."""
    (tmp_path / "agent.py").write_text(
        "from perplexipy import PerplexityClient\n\n"
        "client = PerplexityClient(api_key='pplx-xxx')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_search" in adapters_seen, f"Expected tool_search; got: {adapters_seen}"


# ── OpenAI / Anthropic platform built-in tools ───────────────────────────────


def test_openai_web_search_tool_detected(tmp_path):
    """WebSearchTool (OpenAI Agents SDK) must be detected as TOOL."""
    (tmp_path / "agent.py").write_text(
        "from agents import WebSearchTool\n\n"
        "tools = [WebSearchTool()]\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_openai_builtin" in adapters_seen, f"Expected tool_openai_builtin; got: {adapters_seen}"


def test_openai_code_interpreter_detected(tmp_path):
    """code_interpreter type string (Responses API) must be detected as TOOL."""
    (tmp_path / "agent.py").write_text(
        'tools = [{"type": "code_interpreter"}]\n',
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_openai_builtin" in adapters_seen, f"Expected tool_openai_builtin; got: {adapters_seen}"


def test_anthropic_computer_use_detected(tmp_path):
    """computer_use beta identifier must be detected as TOOL."""
    (tmp_path / "agent.py").write_text(
        'tools = [{"type": "computer_use", "display_width_px": 1280}]\n',
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_openai_builtin" in adapters_seen, f"Expected tool_openai_builtin; got: {adapters_seen}"


# ── Workspace / SaaS connector tools ─────────────────────────────────────────


def test_gmail_toolkit_detected(tmp_path):
    """GmailToolkit must be detected as TOOL (via tool_workspace_connector)."""
    (tmp_path / "agent.py").write_text(
        "from langchain_community.agent_toolkits import GmailToolkit\n\n"
        "toolkit = GmailToolkit()\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_workspace_connector" in adapters_seen, f"Expected tool_workspace_connector; got: {adapters_seen}"


def test_dropbox_sdk_detected(tmp_path):
    """dropbox SDK import must be detected as TOOL."""
    (tmp_path / "agent.py").write_text(
        "import dropbox\n\n"
        "dbx = dropbox.Dropbox(oauth2_access_token=token)\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_workspace_connector" in adapters_seen, f"Expected tool_workspace_connector; got: {adapters_seen}"


def test_salesforce_detected(tmp_path):
    """SimpleSalesforce must be detected as TOOL."""
    (tmp_path / "agent.py").write_text(
        "from simple_salesforce import Salesforce\n\n"
        "sf = SimpleSalesforce(username='u', password='p', security_token='t')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_workspace_connector" in adapters_seen, f"Expected tool_workspace_connector; got: {adapters_seen}"


def test_hubspot_detected(tmp_path):
    """hubspot SDK import must be detected as TOOL."""
    (tmp_path / "agent.py").write_text(
        "from hubspot import HubSpot\n\n"
        "client = HubSpot(access_token='xxx')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_workspace_connector" in adapters_seen, f"Expected tool_workspace_connector; got: {adapters_seen}"


def test_airtable_detected(tmp_path):
    """pyairtable import must be detected as TOOL."""
    (tmp_path / "agent.py").write_text(
        "from pyairtable import Api\n\n"
        "api = Api('xxx')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_workspace_connector" in adapters_seen, f"Expected tool_workspace_connector; got: {adapters_seen}"


# ── Observability / tracing ───────────────────────────────────────────────────


def test_langfuse_detected(tmp_path):
    """langfuse import must be detected as TOOL (observability)."""
    (tmp_path / "agent.py").write_text(
        "from langfuse import Langfuse\n\n"
        "lf = Langfuse(public_key='pk', secret_key='sk')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_observability" in adapters_seen, f"Expected tool_observability; got: {adapters_seen}"


def test_langsmith_detected(tmp_path):
    """langsmith import must be detected as TOOL (observability)."""
    (tmp_path / "agent.py").write_text(
        "from langsmith import Client\n\n"
        "client = LangSmithClient(api_key='xxx')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_observability" in adapters_seen, f"Expected tool_observability; got: {adapters_seen}"


def test_mlflow_detected(tmp_path):
    """mlflow import must be detected as TOOL (observability)."""
    (tmp_path / "agent.py").write_text(
        "import mlflow\n\n"
        "mlflow.set_tracking_uri('http://localhost:5000')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_observability" in adapters_seen, f"Expected tool_observability; got: {adapters_seen}"


def test_helicone_detected(tmp_path):
    """helicone import must be detected as TOOL (observability)."""
    (tmp_path / "agent.py").write_text(
        "from helicone.openai import openai\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_observability" in adapters_seen, f"Expected tool_observability; got: {adapters_seen}"


def test_weave_init_detected(tmp_path):
    """weave.init() (W&B Weave) must be detected as TOOL (observability)."""
    (tmp_path / "agent.py").write_text(
        "import weave\n\n"
        "weave.init('my-project')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_observability" in adapters_seen, f"Expected tool_observability; got: {adapters_seen}"


# ── RPA ───────────────────────────────────────────────────────────────────────


def test_uipath_detected(tmp_path):
    """UiPath SDK usage must be detected as TOOL (RPA)."""
    (tmp_path / "agent.py").write_text(
        "from uipath import UiRobot\n\n"
        "robot = UiRobot(base_url='https://cloud.uipath.com')\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY_ONLY)
    adapters_seen = {n.metadata.extras.get("adapter") for n in doc.nodes if n.component_type == ComponentType.TOOL}
    assert "tool_rpa" in adapters_seen, f"Expected tool_rpa; got: {adapters_seen}"
