"""Base class for nuguard analysis plugins."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import AnalysisResult


class AnalysisPlugin(ABC):
    """Base class for all nuguard static-analysis plugins.

    Each plugin receives a raw SBOM dict and a config dict, and returns an
    AnalysisResult.  Plugins must not raise exceptions for expected failure
    modes (binary not found, network timeout, etc.) — they should return an
    AnalysisResult with status="skipped" or status="warning" instead.
    """

    name: str  # unique plugin name used as the key in StaticAnalyzer

    @abstractmethod
    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        raise NotImplementedError
