"""Base class for Golang framework adapters."""

from __future__ import annotations

import re

from nuguard.sbom.adapters.base import ComponentDetection, FrameworkAdapter
from nuguard.sbom.core.go_parser import (
    GoFunctionCall,
    GoImport,
    GoInstantiation,
    GoParseResult,
)
from nuguard.sbom.types import ComponentType

_TEMPLATE_VAR_RE = re.compile(
    r"\$\{(?P<dollar>[A-Za-z_]\w*)\}"
    r"|\{\{\s*(?:[.$])?(?P<go>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\}\}"
    r"|(?<![$\{])\{(?P<brace>[A-Za-z_]\w*)\}(?!\})"
)


class GoFrameworkAdapter(FrameworkAdapter):
    """Shared base for adapters that inspect :class:`GoParseResult` values.

    Subclasses declare Go module roots through the inherited ``handles_imports``
    field and implement ``extract()``. Module matching is case-sensitive and
    accepts only an exact module root or a slash-delimited subpackage.
    """

    @staticmethod
    def _matches_module_path(import_path: str, module_path: str) -> bool:
        """Return whether *import_path* belongs to *module_path*."""
        return import_path == module_path or import_path.startswith(f"{module_path}/")

    @staticmethod
    def _import_paths(imports_present: object) -> tuple[str, ...]:
        """Normalize supported import containers into module-path strings."""
        if isinstance(imports_present, GoParseResult):
            return tuple(item.path for item in imports_present.imports)

        if isinstance(imports_present, (set, frozenset, list, tuple)):
            paths: list[str] = []
            for item in imports_present:
                if isinstance(item, GoImport):
                    paths.append(item.path)
                elif isinstance(item, str):
                    paths.append(item)
            return tuple(paths)

        return ()

    def can_handle(self, imports_present: object) -> bool:
        """Return whether any import is this adapter's module or a subpackage."""
        return any(
            self._matches_module_path(import_path, target)
            for import_path in self._import_paths(imports_present)
            for target in self.handles_imports
            if target
        )

    def _matching_import(self, parse_result: GoParseResult) -> GoImport | None:
        """Return the first import that activates this adapter."""
        for imported in parse_result.imports:
            if any(
                self._matches_module_path(imported.path, target)
                for target in self.handles_imports
                if target
            ):
                return imported
        return None

    @staticmethod
    def _clean(value: object) -> str:
        """Normalize textual parser values and reject unresolved/structured values."""
        if not isinstance(value, str):
            return ""

        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'", "`"}:
            cleaned = cleaned[1:-1].strip()

        if not cleaned or cleaned.startswith("$"):
            return ""
        if cleaned in {"<complex>", "<lambda>", "<dict>", "<list>"}:
            return ""
        return cleaned

    @classmethod
    def _resolve(
        cls,
        inst_or_call: GoInstantiation | GoFunctionCall,
        *keys: str | int,
    ) -> str:
        """Return the first usable named or positional parser argument.

        String keys read ``args``; non-negative integer keys read
        ``positional_args``. Values have already been symbol-resolved by
        ``parse_go()``.
        """
        named = inst_or_call.args
        positional = inst_or_call.positional_args

        for key in keys:
            if isinstance(key, int):
                if key < 0 or key >= len(positional):
                    continue
                value = positional[key]
            else:
                if key not in named:
                    continue
                value = named[key]

            cleaned = cls._clean(value)
            if cleaned:
                return cleaned

        return ""

    def _fw_node(
        self,
        file_path: str,
        matched_import: GoImport,
        confidence: float = 0.95,
        display_name: str | None = None,
    ) -> ComponentDetection:
        """Emit a framework node using provenance from *matched_import*."""
        alias = f"{matched_import.alias} " if matched_import.alias else ""
        return ComponentDetection(
            component_type=ComponentType.FRAMEWORK,
            canonical_name=f"framework:{self.name}",
            display_name=display_name or self.name.replace("_", " ").title(),
            adapter_name=self.name,
            priority=self.priority,
            confidence=confidence,
            metadata={"framework": self.name, "language": "golang"},
            file_path=file_path,
            line=matched_import.line,
            snippet=f'import {alias}"{matched_import.path}"',
            evidence_kind="ast_import",
        )

    @staticmethod
    def _template_vars(text: str) -> list[str]:
        """Extract unique simple variables from Go and prompt-template forms.

        Supported forms are ``{{ .Name }}``, ``{{ $name }}``, ``{{ name }}``,
        ``${name}``, and ``{name}``. Dotted Go fields are returned without the
        leading dot, for example ``{{ .User.Name }}`` becomes ``User.Name``.
        """
        variables: list[str] = []
        seen: set[str] = set()

        for match in _TEMPLATE_VAR_RE.finditer(text):
            value = match.group("dollar") or match.group("go") or match.group("brace")
            if value is None:
                continue
            if value not in seen:
                seen.add(value)
                variables.append(value)

        return variables
