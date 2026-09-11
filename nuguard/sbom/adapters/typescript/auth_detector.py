"""TypeScript/NestJS AUTH mechanism detector — real call-site evidence.

Unlike Python's ``fastapi_adapter.py``, there was previously no dedicated
deterministic AUTH detector for TypeScript at all: the ``JWT``/``OAuth2``/
``Bearer Auth`` nodes seen in real-world extractions came entirely from the
LLM gap-fill pass (``ComponentType.AUTH`` is gap-filled "only if absent" —
see ``nuguard/sbom/core/gap_fill/categories.py``), which — lacking any
structural signal to anchor on — picked *a* plausible line (an ``import``
statement, a DTO field reference) rather than the real instantiation/
signing/verification call site (docs/sbom-fix2.md #2).

This adapter covers the three mechanisms found missing/misattributed there,
each keyed to its actual call site, never an import line:

- ``new OAuth2Client(...)`` / ``.verifyIdToken(...)`` (``google-auth-library``)
  → ``OAuth2`` node.
- ``jwksClient(...)`` / ``new JwksClient(...)`` (``jwks-rsa``), keyed by the
  ``jwksUri`` value so a distinct issuer (e.g. Apple Sign-In's
  ``appleid.apple.com``) gets a distinctly named node rather than being
  merged with other JWKS-backed auth.
- A ``generateTokens``/``refreshToken``-named method whose body contains a
  ``.sign(...)`` call (``jsonwebtoken``'s ``jwt.sign``, ``@nestjs/jwt``'s
  ``JwtService.sign``, ...) → ``JWT`` node, evidence at the signing call,
  never the import.

Once this adapter emits >=1 AUTH node for a document, gap-fill's AUTH pass
is skipped entirely (``_GAP_FILL_ONLY_IF_ABSENT`` in ``categories.py``), so
landing this directly fixes the evidence-quality problem rather than just
adding a second, competing source of AUTH nodes.
"""

from __future__ import annotations

import re
from typing import Any

from ...types import ComponentType
from ..base import ComponentDetection
from ._class_scan import _find_class_body_span, _line_index_at
from ._ts_regex import TSFrameworkAdapter

_AUTH_PACKAGES = ["google-auth-library", "jwks-rsa", "jsonwebtoken", "@nestjs/jwt"]

_CONFIDENCE = 0.85

_OAUTH2_INSTANTIATION_RE = re.compile(r"new\s+OAuth2Client\s*\(")
_VERIFY_ID_TOKEN_RE = re.compile(r"\.verifyIdToken\s*\(")

_JWKS_CLIENT_RE = re.compile(r"\b(?:jwksClient|new\s+JwksClient)\s*\(")
_JWKS_URI_RE = re.compile(r"jwksUri\s*:\s*['\"]([^'\"]+)['\"]")

_TOKEN_METHOD_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?"
    r"(generateTokens|refreshToken)\s*\("
)
_SIGN_CALL_RE = re.compile(r"\.sign(?:Async)?\s*\(")

# jsonwebtoken's `jwt.verify(token, secretOrPublicKey[, options])` accepts
# ANY algorithm the token itself claims unless `options.algorithms` pins an
# explicit allow-list — the classic alg-confusion / alg:none bypass surface.
_JWT_VERIFY_CALL_RE = re.compile(r"\bjwt\.verify\s*\(")
_ALGORITHMS_OPTION_RE = re.compile(r"\balgorithms\s*:")
# Bound the "same call" window rather than balancing parens (this adapter is
# regex-based throughout, not an AST) — generous enough for a multi-line
# verify(...) call with an inline options object.
_VERIFY_CALL_WINDOW = 300


def _line_at(content: str, offset: int) -> int:
    """1-indexed line number for a character offset into *content*."""
    return _line_index_at(content, offset) + 1


def _jwt_verify_algorithm_restriction(content: str) -> bool | None:
    """Whether the first `jwt.verify(...)` call site in *content* pins an
    explicit `algorithms:` allow-list.  Returns None when no verify call is
    found at all — most call sites live in signing-only files, and "not
    found in this file" is not evidence of a missing restriction elsewhere.
    """
    m = _JWT_VERIFY_CALL_RE.search(content)
    if not m:
        return None
    window = content[m.end() : m.end() + _VERIFY_CALL_WINDOW]
    return bool(_ALGORITHMS_OPTION_RE.search(window))


class NestJSAuthTSAdapter(TSFrameworkAdapter):
    """Detects OAuth2/JWKS/JWT auth mechanisms in TS via real call-site regex."""

    name = "nestjs_auth"
    priority = 55
    handles_imports = _AUTH_PACKAGES

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if not content or not content.strip():
            return []

        lines = content.splitlines()
        detected: list[ComponentDetection] = []

        # --- OAuth2 (google-auth-library) ---
        inst_m = _OAUTH2_INSTANTIATION_RE.search(content)
        if inst_m:
            verify_m = _VERIFY_ID_TOKEN_RE.search(content, inst_m.end())
            ev_offset = verify_m.start() if verify_m else inst_m.start()
            ev_line = _line_at(content, ev_offset)
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    # Same canonical convention as registry.py's generic
                    # `auth_oauth` keyword RegexAdapter (`auth:oauth2`) so
                    # this higher-confidence, real-call-site evidence merges
                    # into the same node and outranks the generic one's
                    # first-keyword-match evidence, rather than coexisting
                    # as a second, redundant AUTH node.
                    canonical_name="auth:oauth2",
                    display_name="OAuth2",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata={
                        "framework": "google-auth-library",
                        "auth_type": "oauth2",
                        "auth_detail": {"protocols": ["oauth2"]},
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=ev_line,
                    snippet=lines[ev_line - 1].strip()[:160],
                    evidence_kind="regex",
                )
            )

        # --- JWKS (jwks-rsa), Apple Sign-In gets a distinct name ---
        for m in _JWKS_CLIENT_RE.finditer(content):
            window = content[m.end() : m.end() + 300]
            uri_m = _JWKS_URI_RE.search(window)
            uri = uri_m.group(1) if uri_m else ""
            is_apple = "apple" in uri.lower()
            ev_line = _line_at(content, m.start())
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    canonical_name=(
                        "auth:apple-sign-in-jwks" if is_apple else f"auth:jwks:{uri or file_path}"
                    ),
                    display_name="Apple Sign-In (JWKS)" if is_apple else "JWKS",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata={
                        "framework": "jwks-rsa",
                        "auth_type": "jwks",
                        "auth_detail": {"protocols": ["jwks"], "jwks_uri": uri},
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=ev_line,
                    snippet=lines[ev_line - 1].strip()[:160],
                    evidence_kind="regex",
                )
            )
            break  # one JWKS mechanism per file is enough

        # --- JWT signing (generateTokens/refreshToken methods) ---
        for idx, line in enumerate(lines):
            if not _TOKEN_METHOD_RE.search(line):
                continue
            body_start, body_end = _find_class_body_span(lines, idx)
            sign_line: int | None = None
            for j in range(body_start, body_end + 1):
                if _SIGN_CALL_RE.search(lines[j]):
                    sign_line = j
                    break
            if sign_line is None:
                continue
            auth_detail: dict[str, Any] = {"protocols": ["jwt"]}
            algo_restricted = _jwt_verify_algorithm_restriction(content)
            if algo_restricted is not None:
                auth_detail["jwt_algorithm_restricted"] = algo_restricted
            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    # Same canonical convention as registry.py's generic
                    # `auth_jwt` keyword RegexAdapter (`auth:jwt`) — see the
                    # comment on the OAuth2 node above.
                    canonical_name="auth:jwt",
                    display_name="JWT",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE,
                    metadata={
                        "framework": "jsonwebtoken",
                        "auth_type": "jwt",
                        "auth_detail": auth_detail,
                        "language": "typescript",
                    },
                    file_path=file_path,
                    line=sign_line + 1,
                    snippet=lines[sign_line].strip()[:160],
                    evidence_kind="regex",
                )
            )
            break  # one JWT node per file is enough

        return detected
