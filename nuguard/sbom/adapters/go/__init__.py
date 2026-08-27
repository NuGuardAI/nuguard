"""Golang SBOM framework adapters."""

from __future__ import annotations

from nuguard.sbom.adapters.go._go_base import GoFrameworkAdapter
from nuguard.sbom.adapters.go.gorilla_mux import GorillaMuxAdapter
from nuguard.sbom.adapters.go.gqlgen import GqlgenAdapter
from nuguard.sbom.adapters.go.http_router import ChiAdapter, EchoAdapter, GinAdapter
from nuguard.sbom.adapters.go.mcp_server import MCPGoServerAdapter
from nuguard.sbom.adapters.go.net_http import NetHTTPAdapter

__all__ = [
    "GoFrameworkAdapter",
    "MCPGoServerAdapter",
    "GinAdapter",
    "EchoAdapter",
    "ChiAdapter",
    "NetHTTPAdapter",
    "GorillaMuxAdapter",
    "GqlgenAdapter",
]
