"""Playwright-driven browser login fallback for target discovery.

See ``nuguard target discover-browser`` (nuguard/cli/commands/target_browser.py)
for the CLI entry point that uses this package.
"""
from __future__ import annotations

from nuguard.common.browser_login.config import BrowserDiscoveryConfig
from nuguard.common.browser_login.public_api import (
    BrowserDiscoveryRequest,
    BrowserDiscoveryResult,
    discover_browser,
)
from nuguard.common.browser_login.session import BrowserLoginResult, BrowserLoginSession
from nuguard.common.errors import BrowserLoginError

__all__ = [
    "BrowserDiscoveryConfig",
    "BrowserDiscoveryRequest",
    "BrowserDiscoveryResult",
    "BrowserLoginError",
    "BrowserLoginResult",
    "BrowserLoginSession",
    "discover_browser",
]
