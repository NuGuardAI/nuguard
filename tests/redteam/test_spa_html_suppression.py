"""Tests for _is_spa_html_response — the SPA-catch-all false-positive guard.

Regression coverage for a real false-positive seen in production: a target
app's HTML responses were prefixed with a UTF-8 BOM (U+FEFF), which
str.lstrip() does not strip, so the SPA-shell prefix check silently failed to
match and 6 auth-bypass findings were reported against endpoints that only
ever returned the frontend's index.html.
"""
from __future__ import annotations

from nuguard.redteam.executor.executor import _is_spa_html_response

_SPA_SHELL = (
    "<!DOCTYPE html>\n"
    '<html lang="en"><head><title>Some App</title></head>'
    "<body>App shell</body></html>"
)


def test_spa_html_detected_without_bom():
    assert _is_spa_html_response(_SPA_SHELL, "/accounts") is True


def test_spa_html_detected_with_leading_bom():
    """BOM-prefixed SPA shell must still be recognized (the reported bug)."""
    bom_prefixed = "﻿" + _SPA_SHELL
    assert _is_spa_html_response(bom_prefixed, "/accounts") is True


def test_spa_html_detected_with_leading_whitespace_then_bom():
    padded = "  \n﻿" + _SPA_SHELL
    assert _is_spa_html_response(padded, "/cards") is True


def test_real_api_path_never_suppressed_even_with_bom():
    """/api/ prefixed paths are genuine routes — never suppress, BOM or not."""
    bom_prefixed = "﻿" + _SPA_SHELL
    assert _is_spa_html_response(bom_prefixed, "/api/agents") is False


def test_non_html_response_not_suppressed():
    assert _is_spa_html_response('{"accounts": []}', "/accounts") is False


def test_no_target_path_not_suppressed():
    assert _is_spa_html_response(_SPA_SHELL, None) is False
