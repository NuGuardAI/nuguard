"""Golang SBOM framework adapters."""

from __future__ import annotations

from nuguard.sbom.adapters.go._go_base import GoFrameworkAdapter
from nuguard.sbom.adapters.go.llm_clients import GoLLMClientsAdapter

__all__ = ["GoFrameworkAdapter", "GoLLMClientsAdapter"]
