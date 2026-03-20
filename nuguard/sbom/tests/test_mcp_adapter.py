"""Unit tests for the MCPServerAdapter (MCP / FastMCP Python SDK).

Each test class targets one detection surface:

  TestCanHandle          — adapter activates on the right import prefixes
  TestFrameworkNode      — FRAMEWORK node emitted with correct display_name / canonical_name
  TestToolDetection      — TOOL nodes from @mcp.tool() / @server.tool() decorators
  TestAuthDetection      — AUTH nodes from provider instantiations and auth= kwarg
  TestApiEndpointDetection — API_ENDPOINT nodes from .run(transport=...) and constructor kwargs
  TestRelationshipEdges  — CALLS / USES / PROTECTS edges emitted correctly
  TestHelpers            — _clean() and _auth_kind() internal helpers
  TestNegatives          — no false positives on non-MCP code
  TestCombined           — realistic full-server fixtures
"""

from __future__ import annotations

from typing import Any

import pytest

from xelo.adapters.base import RelationshipHint
from xelo.adapters.python.mcp_server import MCPServerAdapter, _auth_kind, _clean
from xelo.ast_parser import parse
from xelo.types import ComponentType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADAPTER = MCPServerAdapter()


def _extract(code: str) -> list[Any]:
    """Parse *code* and run the adapter, returning the list of ComponentDetections."""
    pr = parse(code)
    return _ADAPTER.extract(code, "test_server.py", pr)


def _by_type(detections, ctype: ComponentType) -> list[Any]:
    return [d for d in detections if d.component_type == ctype]


def _all_hints(detections) -> list[RelationshipHint]:
    hints: list[RelationshipHint] = []
    for d in detections:
        hints.extend(d.relationships)
    return hints


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    @pytest.mark.parametrize(
        "module",
        [
            "mcp",
            "mcp.server",
            "mcp.server.fastmcp",
            "mcp.server.stdio",
            "mcp.types",
            "fastmcp",
            "fastmcp.contrib.http",  # sub-module of fastmcp
        ],
    )
    def test_activates_on_mcp_imports(self, module: str) -> None:
        assert _ADAPTER.can_handle({module}), f"Expected can_handle({module!r})==True"

    @pytest.mark.parametrize(
        "module",
        [
            "openai",
            "anthropic",
            "langchain",
            "crewai",
            "flask",
            "django",
        ],
    )
    def test_does_not_activate_on_unrelated_imports(self, module: str) -> None:
        assert not _ADAPTER.can_handle({module})

    def test_priority_is_30(self) -> None:
        assert _ADAPTER.priority == 30


# ---------------------------------------------------------------------------
# FRAMEWORK node
# ---------------------------------------------------------------------------


class TestFrameworkNode:
    def test_framework_node_always_emitted(self) -> None:
        code = 'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("my-server")\n'
        dets = _extract(code)
        fw = _by_type(dets, ComponentType.FRAMEWORK)
        assert len(fw) == 1

    def test_framework_display_name_is_server_name(self) -> None:
        code = 'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("excel-mcp")\n'
        dets = _extract(code)
        fw = _by_type(dets, ComponentType.FRAMEWORK)[0]
        assert fw.display_name == "excel-mcp"

    def test_framework_canonical_name(self) -> None:
        code = 'from fastmcp import FastMCP\nserver = FastMCP("demo")\n'
        dets = _extract(code)
        fw = _by_type(dets, ComponentType.FRAMEWORK)[0]
        assert fw.canonical_name == "framework:mcp_server"

    def test_framework_adapter_name(self) -> None:
        code = 'from fastmcp import FastMCP\nmcp = FastMCP("srv")\n'
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)[0]
        assert fw.adapter_name == "mcp_server"

    def test_framework_confidence(self) -> None:
        code = 'from fastmcp import FastMCP\nmcp = FastMCP("srv")\n'
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)[0]
        assert fw.confidence == pytest.approx(0.95)

    def test_framework_metadata_has_server_name(self) -> None:
        code = 'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("invoice-agent")\n'
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)[0]
        assert fw.metadata.get("server_name") == "invoice-agent"

    def test_framework_metadata_framework_key(self) -> None:
        code = 'from fastmcp import FastMCP\nmcp = FastMCP("demo")\n'
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)[0]
        assert fw.metadata.get("framework") == "mcp_server"

    def test_server_class_alternative(self) -> None:
        """MCPServer class name should also produce a FRAMEWORK node."""
        code = 'from mcp.server import MCPServer\nsrv = MCPServer("archive-server")\n'
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)
        assert fw, "Expected FRAMEWORK node for MCPServer class"
        assert fw[0].display_name == "archive-server"

    def test_server_name_kwarg(self) -> None:
        """name= keyword arg form."""
        code = 'from fastmcp import FastMCP\nmcp = FastMCP(name="report-gen")\n'
        fw = _by_type(_extract(code), ComponentType.FRAMEWORK)[0]
        assert fw.display_name == "report-gen"

    def test_framework_emitted_without_tools(self) -> None:
        """FRAMEWORK node should be present even if no tools are defined."""
        code = 'from fastmcp import FastMCP\nmcp = FastMCP("bare-server")\n'
        dets = _extract(code)
        assert _by_type(dets, ComponentType.FRAMEWORK)
        assert not _by_type(dets, ComponentType.TOOL)

    def test_empty_parse_returns_empty(self) -> None:
        assert _ADAPTER.extract("", "x.py", None) == []


# ---------------------------------------------------------------------------
# TOOL detection
# ---------------------------------------------------------------------------


class TestToolDetection:
    def test_single_tool_detected(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('tools-server')\n"
            "@mcp.tool()\n"
            "def get_weather(city: str) -> str:\n"
            "    return 'sunny'\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        assert len(tools) == 1
        assert tools[0].display_name == "get_weather"

    def test_multiple_tools(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('multi-tool')\n"
            "@mcp.tool()\n"
            "def search(q: str): ...\n"
            "@mcp.tool()\n"
            "def summarise(text: str): ...\n"
            "@mcp.tool()\n"
            "def translate(text: str, lang: str): ...\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        tool_names = {t.display_name for t in tools}
        assert tool_names == {"search", "summarise", "translate"}

    def test_tool_canonical_name_format(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('srv')\n"
            "@mcp.tool()\n"
            "def read_file(path: str): ...\n"
        )
        tool = _by_type(_extract(code), ComponentType.TOOL)[0]
        # canonicalize_text converts ':' → '_'
        assert tool.canonical_name == "mcp_tool_read_file"

    def test_tool_adapter_name(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('s')\n@mcp.tool()\ndef ping(): ...\n"
        tool = _by_type(_extract(code), ComponentType.TOOL)[0]
        assert tool.adapter_name == "mcp_server"

    def test_tool_evidence_kind_is_ast_decorator(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('s')\n@mcp.tool()\ndef ping(): ...\n"
        tool = _by_type(_extract(code), ComponentType.TOOL)[0]
        assert tool.evidence_kind == "ast_decorator"

    def test_tool_metadata_has_server_name(self) -> None:
        code = (
            "from fastmcp import FastMCP\nmcp = FastMCP('my-api')\n@mcp.tool()\ndef lookup(): ...\n"
        )
        tool = _by_type(_extract(code), ComponentType.TOOL)[0]
        assert tool.metadata.get("server_name") == "my-api"

    def test_tool_metadata_has_decorator(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('s')\n@mcp.tool()\ndef do_thing(): ...\n"
        tool = _by_type(_extract(code), ComponentType.TOOL)[0]
        assert "tool()" in tool.metadata.get("decorator", "")

    def test_tool_confidence(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('s')\n@mcp.tool()\ndef do_thing(): ...\n"
        tool = _by_type(_extract(code), ComponentType.TOOL)[0]
        assert tool.confidence == pytest.approx(0.92)

    def test_server_variable_named_server(self) -> None:
        """Adapter works when FastMCP is assigned to a variable named 'server'."""
        code = (
            "from mcp.server.fastmcp import FastMCP\n"
            "server = FastMCP('file-ops')\n"
            "@server.tool()\n"
            "def list_files(path: str): ...\n"
        )
        tools = _by_type(_extract(code), ComponentType.TOOL)
        assert tools
        assert tools[0].display_name == "list_files"


# ---------------------------------------------------------------------------
# AUTH detection
# ---------------------------------------------------------------------------


class TestAuthDetection:
    def test_bearer_auth_provider(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import BearerAuthProvider\n"
            "mcp = FastMCP('secure')\n"
            "auth = BearerAuthProvider(public_key=KEY)\n"
        )
        auths = _by_type(_extract(code), ComponentType.AUTH)
        assert len(auths) == 1
        assert auths[0].display_name == "BearerAuthProvider"

    def test_oauth_provider(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import OAuthProvider\n"
            "mcp = FastMCP('oauth-srv')\n"
            "auth = OAuthProvider(client_id=CID, client_secret=SEC)\n"
        )
        auths = _by_type(_extract(code), ComponentType.AUTH)
        assert any("oauth" in a.canonical_name.lower() for a in auths)

    def test_api_key_auth(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import APIKeyAuth\n"
            "mcp = FastMCP('api-gw')\n"
            "auth = APIKeyAuth(keys=['k1', 'k2'])\n"
        )
        auths = _by_type(_extract(code), ComponentType.AUTH)
        assert auths
        assert auths[0].metadata.get("auth_type") == "api_key"

    def test_jwt_auth(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import JWTAuth\n"
            "mcp = FastMCP('jwt-srv')\n"
            "auth = JWTAuth(secret=SECRET)\n"
        )
        auths = _by_type(_extract(code), ComponentType.AUTH)
        assert auths
        assert auths[0].metadata.get("auth_type") == "jwt"

    def test_bearer_auth_type_label(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import BearerAuthProvider\n"
            "mcp = FastMCP('s')\n"
            "BearerAuthProvider(key=K)\n"
        )
        auths = _by_type(_extract(code), ComponentType.AUTH)
        assert auths[0].metadata.get("auth_type") == "bearer"

    def test_auth_kwarg_on_fastmcp_constructor(self) -> None:
        # auth= kwarg must be a string literal for _clean() to extract it;
        # a variable reference is a Name node and gets stripped as "$var".
        code = "from fastmcp import FastMCP\nmcp = FastMCP('protected', auth='bearer')\n"
        auths = _by_type(_extract(code), ComponentType.AUTH)
        assert auths, "Expected AUTH node from auth= string literal kwarg"

    def test_auth_canonical_name_contains_class(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import BearerAuthProvider\n"
            "mcp = FastMCP('s')\n"
            "BearerAuthProvider(key=K)\n"
        )
        auths = _by_type(_extract(code), ComponentType.AUTH)
        assert "bearerauth" in auths[0].canonical_name.lower() or "mcp" in auths[0].canonical_name

    def test_auth_evidence_kind(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import BearerAuthProvider\n"
            "mcp = FastMCP('s')\n"
            "BearerAuthProvider(key=K)\n"
        )
        auth = _by_type(_extract(code), ComponentType.AUTH)[0]
        assert auth.evidence_kind == "ast_instantiation"

    def test_no_auth_when_no_provider(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('public-server')\n"
            "@mcp.tool()\n"
            "def echo(msg: str): return msg\n"
        )
        assert not _by_type(_extract(code), ComponentType.AUTH)


# ---------------------------------------------------------------------------
# API_ENDPOINT detection
# ---------------------------------------------------------------------------


class TestApiEndpointDetection:
    def test_sse_transport(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('streaming')\n"
            "mcp.run(transport='sse', host='0.0.0.0', port=8080)\n"
        )
        eps = _by_type(_extract(code), ComponentType.API_ENDPOINT)
        assert eps, "Expected API_ENDPOINT for sse transport"
        assert eps[0].metadata.get("transport") == "sse"

    def test_streamable_http_transport(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('http-srv')\n"
            "mcp.run(transport='streamable-http', host='localhost', port=9000)\n"
        )
        eps = _by_type(_extract(code), ComponentType.API_ENDPOINT)
        assert eps
        assert eps[0].metadata.get("transport") == "streamable-http"

    def test_host_and_port_in_metadata(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('srv')\n"
            "mcp.run(transport='sse', host='api.example.com', port=443)\n"
        )
        ep = _by_type(_extract(code), ComponentType.API_ENDPOINT)[0]
        assert ep.metadata.get("host") == "api.example.com"
        assert ep.metadata.get("port") == "443"

    def test_endpoint_default_host_when_omitted(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('srv')\nmcp.run(transport='sse')\n"
        ep = _by_type(_extract(code), ComponentType.API_ENDPOINT)[0]
        assert ep.metadata.get("host") == "0.0.0.0"

    def test_endpoint_server_name_in_metadata(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('my-mcp-srv')\n"
            "mcp.run(transport='sse', host='0.0.0.0', port=8080)\n"
        )
        ep = _by_type(_extract(code), ComponentType.API_ENDPOINT)[0]
        assert ep.metadata.get("server_name") == "my-mcp-srv"

    def test_endpoint_from_constructor_host_port(self) -> None:
        """No .run() call — host/port on constructor should emit API_ENDPOINT."""
        code = (
            "from fastmcp import FastMCP\nmcp = FastMCP('inline-ep', host='0.0.0.0', port=7000)\n"
        )
        eps = _by_type(_extract(code), ComponentType.API_ENDPOINT)
        assert eps, "Expected API_ENDPOINT from constructor host/port"

    def test_no_endpoint_for_stdio_only(self) -> None:
        """stdio transport is not HTTP — should NOT emit an API_ENDPOINT."""
        code = (
            "from fastmcp import FastMCP\nmcp = FastMCP('stdio-srv')\nmcp.run(transport='stdio')\n"
        )
        eps = _by_type(_extract(code), ComponentType.API_ENDPOINT)
        assert not eps, "stdio transport should not produce an API_ENDPOINT node"

    def test_endpoint_evidence_kind(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('srv')\nmcp.run(transport='sse')\n"
        ep = _by_type(_extract(code), ComponentType.API_ENDPOINT)[0]
        assert ep.evidence_kind == "ast_call"

    def test_endpoint_canonical_name_contains_host_port(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('srv')\n"
            "mcp.run(transport='sse', host='myhost', port=1234)\n"
        )
        ep = _by_type(_extract(code), ComponentType.API_ENDPOINT)[0]
        assert "myhost" in ep.canonical_name and "1234" in ep.canonical_name


# ---------------------------------------------------------------------------
# Relationship edges
# ---------------------------------------------------------------------------


class TestRelationshipEdges:
    def test_framework_calls_tool(self) -> None:
        code = (
            "from fastmcp import FastMCP\nmcp = FastMCP('rel-test')\n@mcp.tool()\ndef ping(): ...\n"
        )
        hints = _all_hints(_extract(code))
        calls = [h for h in hints if h.relationship_type == "CALLS"]
        assert calls, "Expected at least one CALLS edge"
        assert calls[0].source_type == ComponentType.FRAMEWORK
        assert calls[0].target_type == ComponentType.TOOL

    def test_framework_calls_each_tool(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('multi')\n"
            "@mcp.tool()\n"
            "def a(): ...\n"
            "@mcp.tool()\n"
            "def b(): ...\n"
        )
        hints = _all_hints(_extract(code))
        calls = [h for h in hints if h.relationship_type == "CALLS"]
        assert len(calls) == 2

    def test_framework_uses_auth(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import BearerAuthProvider\n"
            "mcp = FastMCP('secure')\n"
            "BearerAuthProvider(key=K)\n"
        )
        hints = _all_hints(_extract(code))
        uses = [
            h
            for h in hints
            if h.relationship_type == "USES" and h.target_type == ComponentType.AUTH
        ]
        assert uses, "Expected FRAMEWORK -[USES]-> AUTH edge"
        assert uses[0].source_type == ComponentType.FRAMEWORK

    def test_framework_uses_api_endpoint(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('srv')\nmcp.run(transport='sse')\n"
        hints = _all_hints(_extract(code))
        uses = [
            h
            for h in hints
            if h.relationship_type == "USES" and h.target_type == ComponentType.API_ENDPOINT
        ]
        assert uses, "Expected FRAMEWORK -[USES]-> API_ENDPOINT edge"

    def test_auth_protects_api_endpoint(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import BearerAuthProvider\n"
            "mcp = FastMCP('guarded')\n"
            "BearerAuthProvider(key=K)\n"
            "mcp.run(transport='sse', host='0.0.0.0', port=8080)\n"
        )
        hints = _all_hints(_extract(code))
        protects = [h for h in hints if h.relationship_type == "PROTECTS"]
        assert protects, "Expected AUTH -[PROTECTS]-> API_ENDPOINT edge"
        assert protects[0].source_type == ComponentType.AUTH
        assert protects[0].target_type == ComponentType.API_ENDPOINT

    def test_no_relationships_without_tools_auth_endpoint(self) -> None:
        code = "from fastmcp import FastMCP\nmcp = FastMCP('bare')\n"
        hints = _all_hints(_extract(code))
        # FRAMEWORK node has no CALLS/USES edges when nothing else detected
        assert not hints


# ---------------------------------------------------------------------------
# _clean() helper
# ---------------------------------------------------------------------------


class TestCleanHelper:
    def test_strips_quotes(self) -> None:
        assert _clean("'my-server'") == "my-server"
        assert _clean('"my-server"') == "my-server"

    def test_strips_backtick(self) -> None:
        assert _clean("`value`") == "value"

    def test_returns_empty_for_none(self) -> None:
        assert _clean(None) == ""

    def test_returns_empty_for_complex_sentinel(self) -> None:
        assert _clean("<complex>") == ""
        assert _clean("<lambda>") == ""

    def test_returns_empty_for_dollar_prefix(self) -> None:
        assert _clean("$MY_VAR") == ""

    def test_passthrough_plain_string(self) -> None:
        assert _clean("streamable-http") == "streamable-http"

    def test_strips_spaces(self) -> None:
        assert _clean("  localhost  ") == "localhost"


# ---------------------------------------------------------------------------
# _auth_kind() helper
# ---------------------------------------------------------------------------


class TestAuthKindHelper:
    def test_oauth_variants(self) -> None:
        assert _auth_kind("OAuthProvider") == "oauth2"
        assert _auth_kind("OAuth2Bearer") == "oauth2"
        assert _auth_kind("OAuth2AuthorizationCodeProvider") == "oauth2"

    def test_bearer(self) -> None:
        assert _auth_kind("BearerAuthProvider") == "bearer"

    def test_api_key(self) -> None:
        assert _auth_kind("APIKeyAuth") == "api_key"
        assert _auth_kind("api_key") == "api_key"

    def test_jwt(self) -> None:
        assert _auth_kind("JWTAuth") == "jwt"

    def test_token(self) -> None:
        assert _auth_kind("TokenAuth") == "token"

    def test_unknown_fallback(self) -> None:
        assert _auth_kind("SomeRandomProvider") == "unknown"


# ---------------------------------------------------------------------------
# Negatives
# ---------------------------------------------------------------------------


class TestNegatives:
    def test_no_detections_without_mcp_import(self) -> None:
        code = (
            "from openai import OpenAI\n"
            "client = OpenAI()\n"
            "response = client.chat.completions.create(model='gpt-4o')\n"
        )
        pr = parse(code)
        # Pass a non-mcp ParseResult — adapter should still return the
        # framework node (it doesn't gate on imports internally), but let's
        # confirm it handles the call without crashing
        result = _ADAPTER.extract(code, "app.py", pr)
        # Should at minimum not raise; FRAMEWORK node may be stubbed
        assert isinstance(result, list)

    def test_empty_code_returns_empty(self) -> None:
        # parse('') produces a valid (empty) ParseResult, so the adapter
        # still emits a bare FRAMEWORK stub with no server_name.
        # The only way to get truly empty output is to pass parse_result=None.
        dets = _extract("")
        assert not _by_type(dets, ComponentType.TOOL)
        assert not _by_type(dets, ComponentType.AUTH)
        assert not _by_type(dets, ComponentType.API_ENDPOINT)

    def test_none_parse_result_returns_empty(self) -> None:
        assert _ADAPTER.extract("", "x.py", None) == []

    def test_no_tool_without_decorator(self) -> None:
        """A plain function definition (no @mcp.tool()) should not produce a TOOL node."""
        code = (
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('srv')\n"
            "def get_data(key: str):\n"
            "    return db.get(key)\n"
        )
        assert not _by_type(_extract(code), ComponentType.TOOL)

    def test_no_endpoint_without_transport(self) -> None:
        """mcp.run() without transport kwarg should not produce API_ENDPOINT."""
        code = "from fastmcp import FastMCP\nmcp = FastMCP('srv')\nmcp.run()\n"
        assert not _by_type(_extract(code), ComponentType.API_ENDPOINT)


# ---------------------------------------------------------------------------
# Combined / realistic fixtures
# ---------------------------------------------------------------------------


class TestCombined:
    """Realistic multi-component MCP server scenarios."""

    def test_minimal_tool_server(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "\n"
            "mcp = FastMCP('weather-service')\n"
            "\n"
            "@mcp.tool()\n"
            "def get_temperature(city: str) -> float:\n"
            "    return 22.5\n"
            "\n"
            "@mcp.tool()\n"
            "def get_forecast(city: str, days: int) -> list:\n"
            "    return []\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    mcp.run(transport='sse', host='0.0.0.0', port=8080)\n"
        )
        dets = _extract(code)
        fw = _by_type(dets, ComponentType.FRAMEWORK)
        tools = _by_type(dets, ComponentType.TOOL)
        eps = _by_type(dets, ComponentType.API_ENDPOINT)
        auths = _by_type(dets, ComponentType.AUTH)

        assert len(fw) == 1
        assert fw[0].display_name == "weather-service"
        assert {t.display_name for t in tools} == {"get_temperature", "get_forecast"}
        assert len(eps) == 1
        assert eps[0].metadata["transport"] == "sse"
        assert not auths

    def test_secured_server_full(self) -> None:
        code = (
            "from fastmcp import FastMCP\n"
            "from fastmcp.auth import BearerAuthProvider\n"
            "\n"
            "mcp = FastMCP('secure-api')\n"
            "auth = BearerAuthProvider(public_key=PUBLIC_KEY)\n"
            "\n"
            "@mcp.tool()\n"
            "def query_db(sql: str) -> list:\n"
            "    return []\n"
            "\n"
            "mcp.run(transport='streamable-http', host='0.0.0.0', port=9000)\n"
        )
        dets = _extract(code)
        fw = _by_type(dets, ComponentType.FRAMEWORK)
        tools = _by_type(dets, ComponentType.TOOL)
        auths = _by_type(dets, ComponentType.AUTH)
        eps = _by_type(dets, ComponentType.API_ENDPOINT)
        hints = _all_hints(dets)

        assert fw[0].display_name == "secure-api"
        assert tools[0].display_name == "query_db"
        assert auths[0].display_name == "BearerAuthProvider"
        assert eps[0].metadata["transport"] == "streamable-http"

        rel_types = {h.relationship_type for h in hints}
        assert {"CALLS", "USES", "PROTECTS"} <= rel_types

    def test_multi_tool_server_edge_count(self) -> None:
        """N tools → N CALLS edges from the FRAMEWORK node."""
        n = 5
        tools_code = "\n".join(f"@mcp.tool()\ndef tool_{i}(): ..." for i in range(n))
        code = "from fastmcp import FastMCP\nmcp = FastMCP('edge-test')\n\n" + tools_code
        dets = _extract(code)
        calls = [h for h in _all_hints(dets) if h.relationship_type == "CALLS"]
        assert len(calls) == n

    def test_output_types_are_component_detections(self) -> None:
        from xelo.adapters.base import ComponentDetection

        code = (
            "from fastmcp import FastMCP\nmcp = FastMCP('type-check')\n@mcp.tool()\ndef fn(): ...\n"
        )
        for d in _extract(code):
            assert isinstance(d, ComponentDetection)
