"""Golang SBOM framework adapters."""

from __future__ import annotations

from nuguard.sbom.adapters.go._go_base import GoFrameworkAdapter
from nuguard.sbom.adapters.go.anthropic_sdk import AnthropicSDKGoAdapter
from nuguard.sbom.adapters.go.auth import GoJWTAdapter, GoOAuth2Adapter
from nuguard.sbom.adapters.go.datastores import GoDatastoreAdapter
from nuguard.sbom.adapters.go.go_openai import GoOpenAIAdapter
from nuguard.sbom.adapters.go.google_genai import GoogleGenAIAdapter
from nuguard.sbom.adapters.go.gorilla_mux import GorillaMuxAdapter
from nuguard.sbom.adapters.go.gqlgen import GqlgenAdapter
from nuguard.sbom.adapters.go.http_router import ChiAdapter, EchoAdapter, GinAdapter
from nuguard.sbom.adapters.go.langchaingo import LangChainGoAdapter
from nuguard.sbom.adapters.go.mcp_server import MCPGoServerAdapter
from nuguard.sbom.adapters.go.net_http import NetHTTPAdapter
from nuguard.sbom.adapters.go.prompts import extract_go_prompt_constants

__all__ = [
    "GoFrameworkAdapter",
    "MCPGoServerAdapter",
    "GinAdapter",
    "EchoAdapter",
    "ChiAdapter",
    "NetHTTPAdapter",
    "GorillaMuxAdapter",
    "GqlgenAdapter",
    "LangChainGoAdapter",
    "GoOpenAIAdapter",
    "AnthropicSDKGoAdapter",
    "GoogleGenAIAdapter",
    "GoDatastoreAdapter",
    "GoJWTAdapter",
    "GoOAuth2Adapter",
    "extract_go_prompt_constants",
]
