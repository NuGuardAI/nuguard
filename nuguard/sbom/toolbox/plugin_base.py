from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import ToolResult


class ToolPlugin(ABC):
    name: str

    @abstractmethod
    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
