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
