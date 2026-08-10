"""Base class for all Golang framework adapters."""

from __future__ import annotations

import re
from typing import Any

from nuguard.sbom.adapters.base import ComponentDetection, FrameworkAdapter
from nuguard.sbom.types import ComponentType

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class GoFrameworkAdapter(FrameworkAdapter):
    """Base class for Go AI framework adapters.

    Subclasses define `module_path` (e.g. "github.com/tmc/langchaingo") or
    `module_paths` set, and override `extract()`.
    """

    name: str = "go_base"
    priority: int = 100
    module_path: str = ""
    module_paths: set[str] = set()

    def can_handle(self, imports: set[str] | list[str] | Any) -> bool:
        """Return True if any import matches the target Go module path(s).

        Supports passing a set/list of import strings, or a GoParseResult object.
        Substring/prefix matching is used (e.g., "github.com/tmc/langchaingo/llms"
        matches "github.com/tmc/langchaingo").
        """
        if hasattr(imports, "imports"):
            raw_imports = getattr(imports, "imports", [])
            import_set = {imp.path if hasattr(imp, "path") else str(imp) for imp in raw_imports}
        elif isinstance(imports, (set, list)):
            import_set = set(imports)
        else:
            return False

        target_paths = self.module_paths or ({self.module_path} if self.module_path else set())
        if not target_paths:
            return False

        for imp in import_set:
            imp_lower = imp.lower()
            for target in target_paths:
                if target.lower() in imp_lower:
                    return True
        return False

    # ---------------------------------------------------------------------------
    # Shared Helper Utilities
    # ---------------------------------------------------------------------------

    def _clean(self, text: str) -> str:
        """Strip enclosing quotes and clean whitespace."""
        cleaned = text.strip()
        if (cleaned.startswith('"') and cleaned.endswith('"')) or (
            cleaned.startswith("`") and cleaned.endswith("`")
        ):
            return cleaned[1:-1].strip()
        return cleaned

    def _resolve(self, val: str, var_map: dict[str, str]) -> str:
        """Resolve a variable reference or return the cleaned literal string."""
        cleaned = self._clean(val)
        return var_map.get(cleaned, cleaned)

    def _fw_node(
        self,
        file_path: str,
        confidence: float = 0.95,
        display_name: str | None = None,
    ) -> ComponentDetection:
        """Emit a FRAMEWORK node for this Go adapter."""
        canonical = f"framework:{self.name}"
        name = display_name or self.name.replace("_", " ").title()
        return ComponentDetection(
            canonical_name=canonical,
            display_name=name,
            component_type=ComponentType.FRAMEWORK,
            adapter_name=self.name,
            priority=self.priority,
            confidence=confidence,
            file_path=file_path,
            metadata={"language": "golang", "framework": self.name},
        )

    def _template_vars(self, text: str) -> list[str]:
        """Extract {{ variable }} placeholder names from Go prompt templates."""
        return _TEMPLATE_VAR_RE.findall(text)

    def extract(
        self,
        code: str,
        file_path: str,
        parse_result: Any = None,
    ) -> list[ComponentDetection]:
        """Extract SBOM components from Go source code."""
        if parse_result is None or not self.can_handle(parse_result):
            return []
        return [self._fw_node(file_path)]
