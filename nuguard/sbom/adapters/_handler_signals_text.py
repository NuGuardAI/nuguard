"""Regex-based handler evidence for the non-Python HTTP adapters.

Counterpart of :mod:`nuguard.sbom.adapters.python._handler_signals` for languages
where the adapter works on source text (NestJS/TypeScript, Spring/Java,
ASP.NET Core/C#). Produces the same ``metadata["handler_signals"]`` shape that
:mod:`nuguard.sbom.http_semantics` consumes. Only identifiers are matched,
never argument values.
"""

from __future__ import annotations

import re
from typing import Any

_AUDIT_RE = re.compile(r"audit", re.IGNORECASE)


def regex_handler_signals(
    text: str,
    *,
    identity_patterns: tuple[re.Pattern[str], ...],
    cookie_patterns: tuple[re.Pattern[str], ...] = (),
    analyze_body: bool = True,
) -> dict[str, Any]:
    """Evidence dict from a handler's source *text*.

    ``audit_logged`` / ``sets_cookie`` are only reported when *analyze_body* is
    true (the adapter actually has the handler body); otherwise the keys are
    omitted so the enricher leaves the fields ``None`` instead of asserting a
    negative it never checked.
    """
    signals: dict[str, Any] = {}
    injection = list(
        dict.fromkeys(m.group(0).strip() for p in identity_patterns for m in p.finditer(text))
    )
    if injection:
        signals["identity_injection"] = injection
    if analyze_body:
        signals["audit_logged"] = bool(_AUDIT_RE.search(text))
        if cookie_patterns:
            signals["sets_cookie"] = any(p.search(text) for p in cookie_patterns)
    return signals
