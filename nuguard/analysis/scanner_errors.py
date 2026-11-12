"""Typed failure raised by scanner clients in strict mode.

Clients such as ``query_osv`` and ``query_grype_sbom`` historically return an
empty list on any error, which is indistinguishable from "no vulnerabilities".
With ``strict=True`` they raise :class:`ScannerError` instead so callers can
report the tool as failed (or timed out) rather than clean.
"""
from __future__ import annotations

import socket
import subprocess
import urllib.error
from typing import Any


class ScannerError(RuntimeError):
    """A scanner could not complete. ``reason`` is short and safe to show users."""

    def __init__(
        self,
        reason: str,
        *,
        timeout: bool = False,
        partial_findings: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.timeout = timeout
        # Findings gathered before the failure (e.g. other images that scanned fine).
        self.partial_findings: list[dict[str, Any]] = list(partial_findings or [])


def scanner_error_from(exc: BaseException) -> ScannerError:
    """Map a low-level exception to a sanitized :class:`ScannerError`.

    Never includes the exception text: it can carry URLs, paths, or stderr.
    """
    if isinstance(exc, ScannerError):
        return exc
    if isinstance(exc, (TimeoutError, socket.timeout, subprocess.TimeoutExpired)):
        return ScannerError("timed out", timeout=True)
    if isinstance(exc, urllib.error.HTTPError):
        return ScannerError(f"data source returned HTTP {exc.code}")
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return ScannerError("timed out", timeout=True)
        return ScannerError("network unreachable")
    if isinstance(exc, (ConnectionError, OSError)):
        return ScannerError("network unreachable")
    if isinstance(exc, ValueError):
        return ScannerError("invalid response from data source")
    return ScannerError("unexpected error")
