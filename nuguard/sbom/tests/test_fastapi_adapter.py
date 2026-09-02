"""Unit tests for the FastAPIAdapter.

Each test class targets one detection surface:

  TestEmptyPathDedup          — empty-path (@router.post("", ...)) routes don't collide
  TestEndpointMetadata        — endpoint metadata is set correctly, including for ""
  TestRouterPrefixComposition — cross-file app.include_router(prefix=...) composition
"""

from __future__ import annotations

import ast
from typing import Any

from nuguard.sbom.adapters.python.fastapi_adapter import (
    FastAPIAdapter,
    _collect_include_router_calls,
    _collect_router_declarations,
    _collect_router_imports,
)
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.types import ComponentType

_ADAPTER = FastAPIAdapter()


def _extract(code: str, file_path: str = "test_app.py") -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, file_path, pr)


def _endpoints(detections: list[Any]) -> list[Any]:
    return [d for d in detections if d.component_type == ComponentType.API_ENDPOINT]


class TestEmptyPathDedup:
    def test_two_files_with_empty_path_post_produce_distinct_canonical_names(self) -> None:
        code_a = (
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/chat')\n\n"
            "@router.post('')\n"
            "async def chat():\n"
            "    return {}\n"
        )
        code_b = (
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/templates')\n\n"
            "@router.post('')\n"
            "async def create_template():\n"
            "    return {}\n"
        )

        eps_a = _endpoints(_extract(code_a, "server/api/chat.py"))
        eps_b = _endpoints(_extract(code_b, "server/api/templates.py"))

        assert len(eps_a) == 1
        assert len(eps_b) == 1
        assert eps_a[0].canonical_name != eps_b[0].canonical_name
        assert eps_a[0].canonical_name == "endpoint:POST::server/api/chat.py"
        assert eps_b[0].canonical_name == "endpoint:POST::server/api/templates.py"

    def test_same_file_empty_path_and_normal_path_do_not_collide(self) -> None:
        code = (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n\n"
            "@router.post('')\n"
            "async def root():\n"
            "    return {}\n\n"
            "@router.get('/health')\n"
            "async def health():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert len(eps) == 2
        assert len({d.canonical_name for d in eps}) == 2

    def test_non_empty_paths_still_dedupe_across_files(self) -> None:
        """Regression guard: the fix must not break the intentional cross-file
        merge for real (non-empty) shared paths."""
        code_a = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/health')\n"
            "async def health():\n"
            "    return {}\n"
        )
        code_b = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/health')\n"
            "async def health_check():\n"
            "    return {}\n"
        )
        eps_a = _endpoints(_extract(code_a, "service_one/main.py"))
        eps_b = _endpoints(_extract(code_b, "service_two/main.py"))
        assert eps_a[0].canonical_name == eps_b[0].canonical_name == "endpoint:GET:/health"


class TestEndpointMetadata:
    def test_empty_path_still_sets_endpoint_metadata(self) -> None:
        code = (
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/api/audit')\n\n"
            "@router.post('')\n"
            "async def log_audit():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert len(eps) == 1
        assert eps[0].metadata["endpoint"] == ""

    def test_normal_path_sets_endpoint_metadata(self) -> None:
        code = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/status')\n"
            "async def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert eps[0].metadata["endpoint"] == "/status"


class TestSecurityMisconfigDetection:
    def test_wildcard_cors_with_credentials_flagged(self) -> None:
        code = (
            "from fastapi import FastAPI\n"
            "from fastapi.middleware.cors import CORSMiddleware\n"
            "app = FastAPI()\n"
            "app.add_middleware(\n"
            "    CORSMiddleware, allow_origins=['*'], allow_credentials=True,\n"
            ")\n\n"
            "@app.get('/status')\n"
            "async def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        cors = eps[0].metadata["cors_policy"]
        assert cors["origin"] == "*"
        assert cors["wildcard_with_credentials"] is True

    def test_explicit_origin_not_flagged_as_wildcard(self) -> None:
        code = (
            "from fastapi import FastAPI\n"
            "from fastapi.middleware.cors import CORSMiddleware\n"
            "app = FastAPI()\n"
            "app.add_middleware(\n"
            "    CORSMiddleware, allow_origins=['https://example.com'],\n"
            ")\n\n"
            "@app.get('/status')\n"
            "async def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        cors = eps[0].metadata["cors_policy"]
        assert cors["wildcard_with_credentials"] is False

    def test_debug_true_flagged(self) -> None:
        code = (
            "from fastapi import FastAPI\n"
            "app = FastAPI(debug=True)\n\n"
            "@app.get('/status')\n"
            "async def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert eps[0].metadata["debug_error_leak"] is True

    def test_no_debug_flag_not_set(self) -> None:
        code = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/status')\n"
            "async def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert "debug_error_leak" not in eps[0].metadata

    def test_missing_security_headers_flagged_by_default(self) -> None:
        code = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/status')\n"
            "async def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert eps[0].metadata["security_headers_detail"]["missing"] == [
            "csp", "x_frame_options", "hsts",
        ]

    def test_recognized_header_middleware_clears_missing_headers(self) -> None:
        code = (
            "from fastapi import FastAPI\n"
            "from secure import SecureHeaders\n"
            "app = FastAPI()\n"
            "app.add_middleware(SecureHeaders)\n\n"
            "@app.get('/status')\n"
            "async def status():\n"
            "    return {}\n"
        )
        eps = _endpoints(_extract(code))
        assert "security_headers_detail" not in eps[0].metadata


class TestWebSocketRoutes:
    """WebSocket routes (@router.websocket / @app.websocket) must be detected as
    API_ENDPOINT nodes with method='WEBSOCKET', not silently dropped."""

    def test_unprefixed_app_websocket_route_detected(self) -> None:
        code = (
            "from fastapi import FastAPI, WebSocket\n"
            "app = FastAPI()\n\n"
            "@app.websocket('/ws/{token}')\n"
            "async def ws_endpoint(websocket: WebSocket, token: str):\n"
            "    await websocket.accept()\n"
        )
        eps = _endpoints(_extract(code))
        assert len(eps) == 1
        assert eps[0].metadata["method"] == "WEBSOCKET"
        assert eps[0].metadata["endpoint"] == "/ws/{token}"
        # HTTP-only schema detection must not run for WebSocket routes.
        assert "request_body_schema" not in eps[0].metadata

    def test_router_websocket_route_composes_prefix(self, tmp_path) -> None:
        source_dir = tmp_path / "sample-app"
        (source_dir / "server").mkdir(parents=True)

        (source_dir / "server" / "ws.py").write_text(
            "from fastapi import APIRouter, WebSocket\n\n"
            "router = APIRouter()\n\n"
            "@router.websocket('/voice/{bot_id}')\n"
            "async def voice_ws(websocket: WebSocket, bot_id: str):\n"
            "    await websocket.accept()\n",
            encoding="utf-8",
        )
        (source_dir / "server" / "server.py").write_text(
            "from fastapi import FastAPI\n"
            "from server.ws import router as ws_router\n\n"
            "app = FastAPI()\n"
            "app.include_router(ws_router, prefix='/ws')\n",
            encoding="utf-8",
        )

        config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
        doc = AiSbomExtractor().extract_from_path(source_dir, config)

        ws_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.API_ENDPOINT
            and n.metadata.endpoint == "/ws/voice/{bot_id}"
        ]
        assert len(ws_nodes) == 1
        assert ws_nodes[0].metadata.method == "WEBSOCKET"

    def test_no_auth_websocket_route_omits_auth_type(self) -> None:
        """A public WebSocket route with no Depends()-injected auth callable must
        not carry an auth_type — the same "no auth" signal HTTP endpoints get."""
        code = (
            "from fastapi import FastAPI, WebSocket\n"
            "app = FastAPI()\n\n"
            "@app.websocket('/public/bot/ws/{token}')\n"
            "async def public_ws(websocket: WebSocket, token: str):\n"
            "    await websocket.accept()\n"
        )
        eps = _endpoints(_extract(code))
        assert len(eps) == 1
        assert "auth_type" not in eps[0].metadata


class TestRouterPrefixComposition:
    """Unit tests for the AST helpers driving the cross-file prefix pre-pass."""

    def test_collect_router_declarations_captures_own_prefix(self) -> None:
        tree = ast.parse(
            "from fastapi import APIRouter\n"
            "router = APIRouter(prefix='/mcp')\n"
        )
        assert _collect_router_declarations(tree) == {"router": "/mcp"}

    def test_collect_router_declarations_defaults_to_empty_prefix(self) -> None:
        tree = ast.parse("from fastapi import APIRouter\nrouter = APIRouter()\n")
        assert _collect_router_declarations(tree) == {"router": ""}

    def test_collect_include_router_calls(self) -> None:
        tree = ast.parse(
            "app.include_router(config_router, prefix='/api/config')\n"
        )
        assert _collect_include_router_calls(tree) == [
            ("app", "config_router", "/api/config")
        ]

    def test_collect_include_router_calls_module_attribute_style(self) -> None:
        """include_router(chat.router, ...) — the pattern used when a router is
        imported via its owning submodule (`from server.api import chat`) rather
        than directly (`from server.api.chat import router`)."""
        tree = ast.parse(
            "app.include_router(chat.router, prefix='/api/chat')\n"
        )
        assert _collect_include_router_calls(tree) == [
            ("app", "chat.router", "/api/chat")
        ]

    def test_collect_router_imports_relative(self) -> None:
        tree = ast.parse("from .config import router as config_router\n")
        assert _collect_router_imports(tree) == {"config_router": (1, "config", "router")}

    def test_endpoint_metadata_uses_composed_path_when_prefix_injected(self) -> None:
        """Direct adapter-level check: given an injected prefix map (as core.py's
        pre-pass would build), the endpoint path/canon reflect the composed path."""
        adapter = FastAPIAdapter()
        adapter.set_global_router_prefixes({"server/config/mcp.py::router": "/api/config"})
        code = (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n\n"
            "@router.get('/mcp')\n"
            "async def get_mcp():\n"
            "    return {}\n"
        )
        pr = parse(code)
        eps = _endpoints(adapter.extract(code, "server/config/mcp.py", pr))
        assert len(eps) == 1
        assert eps[0].metadata["endpoint"] == "/api/config/mcp"
        assert eps[0].canonical_name == "endpoint:GET:/api/config/mcp"


class TestRouterPrefixEndToEnd:
    """Regression fixture mirroring Phlox's two-level router nesting:
    config/__init__.py declares a router that server.py mounts with a prefix."""

    def test_two_level_nested_router_prefix_composes(self, tmp_path) -> None:
        source_dir = tmp_path / "sample-app"
        (source_dir / "config").mkdir(parents=True)

        (source_dir / "config" / "__init__.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.get('/mcp')\n"
            "async def get_mcp_config():\n"
            "    return {}\n",
            encoding="utf-8",
        )
        (source_dir / "server.py").write_text(
            "from fastapi import FastAPI\n"
            "from config import router as config_router\n\n"
            "app = FastAPI()\n"
            "app.include_router(config_router, prefix='/api/config')\n",
            encoding="utf-8",
        )

        config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
        doc = AiSbomExtractor().extract_from_path(source_dir, config)

        endpoint_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.API_ENDPOINT
            and n.metadata.endpoint == "/api/config/mcp"
        ]
        assert len(endpoint_nodes) == 1

    def test_module_attribute_include_router_composes_prefix(self, tmp_path) -> None:
        """Regression fixture mirroring Phlox's actual shape: server/api/chat.py
        declares a bare router, and server/server.py does
        `from server.api import chat` then `app.include_router(chat.router,
        prefix="/api/chat")` — the module-attribute style, not a direct
        `from server.api.chat import router` import."""
        source_dir = tmp_path / "sample-app"
        (source_dir / "server" / "api").mkdir(parents=True)
        (source_dir / "server" / "api" / "__init__.py").write_text("", encoding="utf-8")

        (source_dir / "server" / "api" / "chat.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/respond-visual')\n"
            "async def respond_visual():\n"
            "    return {}\n",
            encoding="utf-8",
        )
        (source_dir / "server" / "server.py").write_text(
            "from fastapi import FastAPI\n"
            "from server.api import chat\n\n"
            "app = FastAPI()\n"
            "app.include_router(chat.router, prefix='/api/chat')\n",
            encoding="utf-8",
        )

        config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
        doc = AiSbomExtractor().extract_from_path(source_dir, config)

        endpoint_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.API_ENDPOINT
            and n.metadata.endpoint == "/api/chat/respond-visual"
        ]
        assert len(endpoint_nodes) == 1


class TestAuthSchemeDedup:
    """Two files instantiating the same auth scheme (same class, same
    resolvable scheme-key argument) should collapse into one AUTH node with
    multiple evidence locations, not two undeduped nodes."""

    def test_same_token_url_produces_shared_canonical_name(self) -> None:
        code_a = (
            "from fastapi.security import OAuth2PasswordBearer\n"
            "oauth2_scheme = OAuth2PasswordBearer(tokenUrl='token')\n"
        )
        code_b = (
            "from fastapi.security import OAuth2PasswordBearer\n"
            "oauth2_scheme = OAuth2PasswordBearer(tokenUrl='token')\n"
        )
        auth_a = [
            d for d in _extract(code_a, "api/core/security.py")
            if d.component_type == ComponentType.AUTH
        ]
        auth_b = [
            d for d in _extract(code_b, "api/core/auth.py")
            if d.component_type == ComponentType.AUTH
        ]
        assert len(auth_a) == 1 and len(auth_b) == 1
        assert auth_a[0].canonical_name == auth_b[0].canonical_name

    def test_different_token_url_stays_distinct(self) -> None:
        code_a = (
            "from fastapi.security import OAuth2PasswordBearer\n"
            "scheme = OAuth2PasswordBearer(tokenUrl='token')\n"
        )
        code_b = (
            "from fastapi.security import OAuth2PasswordBearer\n"
            "scheme = OAuth2PasswordBearer(tokenUrl='admin/token')\n"
        )
        auth_a = [
            d for d in _extract(code_a, "svc_a/security.py")
            if d.component_type == ComponentType.AUTH
        ]
        auth_b = [
            d for d in _extract(code_b, "svc_b/security.py")
            if d.component_type == ComponentType.AUTH
        ]
        assert auth_a[0].canonical_name != auth_b[0].canonical_name

    def test_unresolvable_token_url_falls_back_to_per_file_canonical(self) -> None:
        """A dynamically-built tokenUrl (not a string literal) can't be
        compared across files — must fall back to the old per-file/var
        canonical name rather than risk merging two different schemes."""
        code = (
            "from fastapi.security import OAuth2PasswordBearer\n"
            "scheme = OAuth2PasswordBearer(tokenUrl=settings.TOKEN_URL)\n"
        )
        auth = [
            d for d in _extract(code, "svc/security.py")
            if d.component_type == ComponentType.AUTH
        ]
        assert len(auth) == 1
        assert auth[0].canonical_name == "fastapi:auth:svc/security.py:scheme"

    def test_bearer_scheme_dedups_across_files_with_no_scheme_arg(self) -> None:
        """HTTPBearer has no distinguishing constructor argument — any two
        instantiations count as the same scheme."""
        code_a = "from fastapi.security import HTTPBearer\nbearer = HTTPBearer()\n"
        code_b = "from fastapi.security import HTTPBearer\nbearer = HTTPBearer(auto_error=False)\n"
        auth_a = [
            d for d in _extract(code_a, "a.py") if d.component_type == ComponentType.AUTH
        ]
        auth_b = [
            d for d in _extract(code_b, "b.py") if d.component_type == ComponentType.AUTH
        ]
        assert auth_a[0].canonical_name == auth_b[0].canonical_name

    def test_cross_file_merge_produces_single_node_with_two_evidence_entries(
        self, tmp_path
    ) -> None:
        source_dir = tmp_path / "sample-app"
        (source_dir / "api" / "core").mkdir(parents=True)

        (source_dir / "api" / "core" / "security.py").write_text(
            "from fastapi.security import OAuth2PasswordBearer\n"
            "oauth2_scheme = OAuth2PasswordBearer(tokenUrl='token')\n",
            encoding="utf-8",
        )
        (source_dir / "api" / "core" / "auth.py").write_text(
            "from fastapi.security import OAuth2PasswordBearer\n"
            "oauth2_scheme = OAuth2PasswordBearer(tokenUrl='token')\n",
            encoding="utf-8",
        )

        config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
        doc = AiSbomExtractor().extract_from_path(source_dir, config)

        auth_nodes = [n for n in doc.nodes if n.component_type == ComponentType.AUTH]
        assert len(auth_nodes) == 1
        assert len(auth_nodes[0].evidence) == 2
