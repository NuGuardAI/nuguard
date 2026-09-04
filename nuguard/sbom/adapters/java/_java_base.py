"""Shared base class and helpers for Java framework adapters."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from ...core.java_parser import JavaParseResult, parse_java
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter


class JavaFrameworkAdapter(FrameworkAdapter, ABC):
    """Base class for adapters consuming :class:`JavaParseResult`."""

    handles_packages: list[str] = []

    def _package_prefixes(self) -> list[str]:
        return self.handles_packages or self.handles_imports

    def can_handle(self, imports_present: set[str]) -> bool:
        for imported in imports_present:
            clean = imported.strip().rstrip(".*")
            for prefix in self._package_prefixes():
                prefix = prefix.strip().rstrip(".*")
                if clean == prefix or clean.startswith(prefix + "."):
                    return True
        return False

    @staticmethod
    def _parse_result(content: str, file_path: str, parse_result: Any) -> JavaParseResult:
        if isinstance(parse_result, JavaParseResult):
            return parse_result
        return parse_java(content, file_path)

    @abstractmethod
    def extract(self, content: str, file_path: str, parse_result: Any) -> list[ComponentDetection]:
        raise NotImplementedError

    def _fw_node(self, framework: str, file_path: str, line: int = 0) -> ComponentDetection:
        return ComponentDetection(
            component_type=ComponentType.FRAMEWORK,
            canonical_name=f"framework:{framework}",
            display_name=framework,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.96,
            metadata={"framework": framework, "language": "java"},
            file_path=file_path,
            line=line,
            snippet=f"import {framework}",
            evidence_kind="ast_import",
        )

    @staticmethod
    def _annotation_name(annotation: str) -> str:
        match = re.match(r"@([A-Za-z_$][\w$]*)", annotation.strip())
        return match.group(1) if match else ""

    @staticmethod
    def _annotation_value(annotation: str) -> str:
        match = re.search(r'\(\s*(?:value\s*=\s*|path\s*=\s*)?["\']([^"\']+)["\']', annotation)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _line_snippet(content: str, line: int, limit: int = 160) -> str:
        lines = content.splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()[:limit]
        return ""
