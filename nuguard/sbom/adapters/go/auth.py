"""Go auth mechanism adapters — real call-site evidence.

Mirrors the intent of ``typescript/auth_detector.py``: give AUTH nodes a
real construction/signing call site instead of relying on the bare-word
regex fallback (``auth_jwt``/``auth_oauth`` in ``adapters/registry.py``),
which can only anchor on wherever the word "jwt"/"oauth2" happens to appear
in the file. Canonical names (``auth:jwt``, ``auth:oauth2``) match that
regex fallback's so a structural hit and a text hit for the same file
dedupe into one node rather than producing two.

- ``golang-jwt/jwt`` (any major version, plus the legacy
  ``dgrijalva/jwt-go`` fork): ``jwt.NewWithClaims(...)`` /
  ``jwt.Parse(...)`` / ``jwt.ParseWithClaims(...)`` → ``auth:jwt``.
- ``golang.org/x/oauth2``: an ``oauth2.Config{...}`` struct literal or
  ``oauth2.NewClient(...)`` call → ``auth:oauth2``.
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...types import ComponentType
from ..base import ComponentDetection
from ._go_base import GoFrameworkAdapter

_JWT_MODULES = (
    "github.com/golang-jwt/jwt",
    "github.com/dgrijalva/jwt-go",
)
_JWT_CALLS = {"NewWithClaims", "Parse", "ParseWithClaims"}

_OAUTH2_MODULE = "golang.org/x/oauth2"
_OAUTH2_STRUCT = "oauth2.Config"
_OAUTH2_CALL = "NewClient"


class GoJWTAdapter(GoFrameworkAdapter):
    """Detect ``golang-jwt/jwt`` sign/parse call sites."""

    name = "go_jwt"
    priority = 55
    handles_imports = list(_JWT_MODULES)

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = (
            parse_result
            if isinstance(parse_result, GoParseResult)
            else parse_go(content, file_path)
        )
        matched_import = self._matching_import(result)
        if matched_import is None:
            return []

        for call in result.function_calls:
            if call.receiver == "jwt" and call.function_name in _JWT_CALLS:
                return [
                    ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name="auth:jwt",
                        display_name="JWT",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.9,
                        metadata={
                            "auth_type": "jwt",
                            "framework": "go_jwt",
                            "language": "golang",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.source_snippet or f"jwt.{call.function_name}(...)",
                        evidence_kind="ast_call",
                    )
                ]

        return []


class GoOAuth2Adapter(GoFrameworkAdapter):
    """Detect ``golang.org/x/oauth2`` configuration/client construction."""

    name = "go_oauth2"
    priority = 55
    handles_imports = [_OAUTH2_MODULE]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = (
            parse_result
            if isinstance(parse_result, GoParseResult)
            else parse_go(content, file_path)
        )
        matched_import = self._matching_import(result)
        if matched_import is None:
            return []

        for inst in result.instantiations:
            if inst.class_name == _OAUTH2_STRUCT:
                return [
                    ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name="auth:oauth2",
                        display_name="OAuth2",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.9,
                        metadata={
                            "auth_type": "oauth2",
                            "framework": "go_oauth2",
                            "language": "golang",
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=inst.source_snippet or "oauth2.Config{...}",
                        evidence_kind="ast_instantiation",
                    )
                ]

        for call in result.function_calls:
            if call.receiver == "oauth2" and call.function_name == _OAUTH2_CALL:
                return [
                    ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name="auth:oauth2",
                        display_name="OAuth2",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "auth_type": "oauth2",
                            "framework": "go_oauth2",
                            "language": "golang",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.source_snippet or "oauth2.NewClient(...)",
                        evidence_kind="ast_call",
                    )
                ]

        return []


__all__ = ["GoJWTAdapter", "GoOAuth2Adapter"]
