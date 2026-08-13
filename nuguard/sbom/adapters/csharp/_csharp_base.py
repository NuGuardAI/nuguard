"""Shared base class for C# framework adapters."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from ...core.csharp_parser import CSharpParseResult, parse_csharp
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter


class CSharpFrameworkAdapter(
    FrameworkAdapter,
    ABC,
):
    """Base class for adapters that consume C# parse results.

    Subclasses declare namespace prefixes in ``handles_namespaces`` or in the
    inherited ``handles_imports`` alias and implement :meth:`extract`.
    """

    handles_namespaces: list[str] = []

    def _namespace_prefixes(self) -> list[str]:
        return self.handles_namespaces or self.handles_imports

    @staticmethod
    def _normalize_namespace(
        namespace: str,
    ) -> str:
        return namespace.strip().removeprefix("global::").rstrip(".")

    def can_handle(
        self,
        imports_present: set[str],
    ) -> bool:
        """Return whether an imported namespace matches this adapter."""
        prefixes = [
            self._normalize_namespace(prefix)
            for prefix in self._namespace_prefixes()
            if prefix.strip()
        ]

        for imported in imports_present:
            namespace = self._normalize_namespace(imported)

            for prefix in prefixes:
                if namespace == prefix or namespace.startswith(prefix + "."):
                    return True

        return False

    def _detect(
        self,
        result: CSharpParseResult,
    ) -> bool:
        """Return whether the parse result imports a handled namespace."""
        return self.can_handle({directive.namespace for directive in result.using_directives})

    @staticmethod
    def _parse_result(
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> CSharpParseResult:
        """Return a typed result, parsing content when necessary."""
        if isinstance(
            parse_result,
            CSharpParseResult,
        ):
            return parse_result

        return parse_csharp(
            content,
            file_path,
        )

    @abstractmethod
    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        """Extract component detections from one C# source file."""
        raise NotImplementedError

    def _fw_node(
        self,
        file_path: str,
        line: int = 0,
    ) -> ComponentDetection:
        """Emit a C# framework-presence node."""
        return ComponentDetection(
            component_type=ComponentType.FRAMEWORK,
            canonical_name=f"framework:{self.name}",
            display_name=f"framework:{self.name}",
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.95,
            metadata={
                "framework": self.name,
                "language": "csharp",
            },
            file_path=file_path,
            line=line,
            snippet=f"using {self.name}",
            evidence_kind="ast_import",
        )

    @staticmethod
    def _clean(value: Any) -> str:
        """Strip common C# string delimiters and placeholders."""
        if value is None:
            return ""

        text = str(value).strip()

        if text.startswith("$("):
            return ""

        text = re.sub(
            r"^(?:\$@|@\$|\$+|@)",
            "",
            text,
        )

        if text.startswith('"""') and text.endswith('"""'):
            text = text[3:-3]
        else:
            text = text.strip("'\" ")

        if text.startswith("$(") or text in {
            "<complex>",
            "<lambda>",
            "<dict>",
            "<list>",
        }:
            return ""

        return text

    @staticmethod
    def _assignment_name(
        source: str,
        line: int,
    ) -> str | None:
        """Return the assignment target on a one-based source line."""
        lines = source.splitlines()

        if line < 1 or line > len(lines):
            return None

        match = re.search(
            r"(?:\b(?:const|var|"
            r"[A-Za-z_]\w*"
            r"(?:[.<>,?\[\] ]+[A-Za-z_]\w*)?)\s+)?"
            r"(?P<name>@?[A-Za-z_]\w*)\s*=",
            lines[line - 1],
        )

        return match.group("name").removeprefix("@") if match else None

    @staticmethod
    def _template_vars(
        text: str,
    ) -> list[str]:
        """Extract unique expressions from interpolation braces."""
        variables: list[str] = []
        seen: set[str] = set()

        for match in re.finditer(
            r"(?<!\{)\{\s*([^{}]+?)\s*\}(?!\})",
            text,
        ):
            expression = match.group(1).split(":", 1)[0].strip()

            if expression and expression not in seen:
                seen.add(expression)
                variables.append(expression)

        return variables
