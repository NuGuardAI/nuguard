"""Lightweight, regex-only frontend call-site scanner.

Locates frontend source files that call a known backend endpoint path (login
or chat), so their raw text can be fed as extra context into the LLM
auth/chat-schema inference pass (see ``auth_schema_inference.py``). This
module does *no* extraction itself — no token-key/response-shape guessing —
it only selects candidate files/lines, mirroring gap-fill's ``evidence_files``
shape. There is no other frontend-source-scanning capability anywhere in
NuGuard's SBOM pipeline; the runtime bundle-URL scraper
(``nuguard/common/endpoint_probe.py::discover_api_origin_from_frontend_bundle``)
and the Playwright-driven browser-login tool
(``nuguard/common/browser_login/``) both operate on a *live deployed* target,
never on repo source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FRONTEND_EXTENSIONS = (".tsx", ".jsx", ".ts", ".js")

# Call-site patterns, applied per-line. Each pattern either embeds the URL
# directly in the match (fetch/axios-style) or just flags a hook line whose
# surrounding lines are scanned separately for a URL literal.
_FETCH_RE = re.compile(r"\bfetch\(\s*[`'\"]([^`'\"]*)[`'\"]")
_HTTP_CLIENT_CALL_RE = re.compile(
    r"\b(?:axios|api|client|http)\.(?:post|get|put|patch|delete)\(\s*[`'\"]([^`'\"]*)[`'\"]",
    re.IGNORECASE,
)
_REACT_QUERY_HOOK_RE = re.compile(r"\b(?:useMutation|useQuery)\(")

_URL_MATCH_WINDOW = 3  # lines to look around a bare useMutation/useQuery call


@dataclass(frozen=True)
class FrontendEvidenceMatch:
    file_path: str
    line: int
    matched_pattern: str
    endpoint_path: str


def _is_frontend_file(path: str) -> bool:
    return path.endswith(_FRONTEND_EXTENSIONS)


def find_frontend_call_sites(
    file_contents: dict[str, str],
    endpoint_path: str,
    exclude_paths: set[str] | None = None,
) -> list[FrontendEvidenceMatch]:
    """Scan *file_contents* for frontend call sites referencing *endpoint_path*.

    Only files not already claimed as a backend adapter's own evidence
    location (*exclude_paths*) and with a frontend-shaped extension are
    considered. Returns matches sorted by file path then line number;
    callers typically only need the first one or two for LLM context.
    """
    if not endpoint_path:
        return []
    exclude_paths = exclude_paths or set()
    matches: list[FrontendEvidenceMatch] = []

    for path, content in file_contents.items():
        if path in exclude_paths or not _is_frontend_file(path):
            continue
        if endpoint_path not in content:
            continue

        lines = content.splitlines()
        for idx, line in enumerate(lines):
            fm = _FETCH_RE.search(line)
            if fm and endpoint_path in fm.group(1):
                matches.append(
                    FrontendEvidenceMatch(path, idx + 1, "fetch", endpoint_path)
                )
                continue

            hm = _HTTP_CLIENT_CALL_RE.search(line)
            if hm and endpoint_path in hm.group(1):
                matches.append(
                    FrontendEvidenceMatch(path, idx + 1, "http_client", endpoint_path)
                )
                continue

            if _REACT_QUERY_HOOK_RE.search(line):
                lo = max(0, idx - _URL_MATCH_WINDOW)
                hi = min(len(lines), idx + _URL_MATCH_WINDOW + 1)
                window_text = "\n".join(lines[lo:hi])
                if endpoint_path in window_text:
                    matches.append(
                        FrontendEvidenceMatch(path, idx + 1, "react_query_hook", endpoint_path)
                    )

    matches.sort(key=lambda m: (m.file_path, m.line))
    return matches
