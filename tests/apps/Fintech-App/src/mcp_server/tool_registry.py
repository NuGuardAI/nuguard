"""
CipherBank Dynamic Tool Registry
=================================
SBOM COMPLEXITY TEST #2 — "Loop-registered MCP tools" obfuscation
------------------------------------------------------------------
Detection challenge:
  The SBOM extractor (and LangGraphAdapter / MCPAdapter) detects MCP tools
  via the ``@mcp.tool()`` decorator pattern on function definitions.
  Specifically it looks for ``parse_result.decorators`` entries matching
  ``mcp.tool`` or ``tool`` on ``async def`` / ``def`` nodes.

  This file registers tools *programmatically*:
    ``mcp.tool()(func)``  ← called in a loop, no decorator syntax

  There is no ``@mcp.tool()`` line attached to any function definition, so
  the decorator scanner finds nothing.  The TOOL nodes for ``balance_inquiry``,
  ``statement_download``, and ``account_summary`` will be invisible to the SBOM.

  Additionally, the tool functions themselves are defined inside a local
  ``_build_tools()`` function scope.  AST analysis that looks for module-level
  function definitions will also miss them.

  Expected SBOM result: no TOOL nodes emitted for this file.
  Detection requires: tracking ``mcp.tool()`` call-expression patterns
  (not just decorator usage), or multi-pass analysis.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp import FastMCP

logger = logging.getLogger("mcp_server.tool_registry")


# ---------------------------------------------------------------------------
# Tool specifications — defined as plain functions, no @decorator
# ---------------------------------------------------------------------------

def _balance_inquiry(account_id: str) -> dict[str, Any]:
    """Return the current balance for the given account.

    NOTE: No auth check — any account_id can be queried (VULN-01 extension).
    """
    # Simulated balance lookup
    FAKE_BALANCES = {
        "ACCT-001": 50000.00,
        "ACCT-002": 12500.00,
        "ACCT-003": 250000.00,
        "ACCT-GLOBAL-POOL": 9_999_999.99,
    }
    balance = FAKE_BALANCES.get(account_id, 0.0)
    return {"account_id": account_id, "balance_usd": balance, "currency": "USD"}


def _statement_download(account_id: str, months: int = 3) -> dict[str, Any]:
    """Download a bank statement for the last N months.

    VULN: No ownership check — returns any account's statement.
    """
    months = min(max(1, months), 24)  # Clamp to 1–24
    return {
        "account_id": account_id,
        "period_months": months,
        "format": "pdf",
        "download_url": f"https://mcp-banking-server.internal/statements/{account_id}/last-{months}m.pdf",
    }


def _account_summary(account_id: str) -> dict[str, Any]:
    """Return a summary of account metadata."""
    return {
        "account_id": account_id,
        "account_type": "checking",
        "status": "active",
        "linked_cards": 2,
        "pending_transactions": 4,
    }


def _yield_interest(principal: float, rate_bps: float, days: int) -> dict[str, Any]:
    """Calculate simple interest yield for a deposit scenario."""
    rate = rate_bps / 10_000  # basis points → decimal
    interest = principal * rate * days / 365
    return {
        "principal": principal,
        "rate_bps": rate_bps,
        "days": days,
        "projected_interest_usd": round(interest, 2),
    }


# ---------------------------------------------------------------------------
# SBOM COMPLEXITY: Tools registered programmatically — no @mcp.tool() decorator
# The SBOM scanner looks for @decorator syntax on function defs.
# This loop-registration pattern is invisible to decorator-based detection.
# ---------------------------------------------------------------------------

#: Registry entries: (function, tool_name, description)
_TOOL_REGISTRY: list[tuple[Any, str, str]] = [
    (
        _balance_inquiry,
        "balance_inquiry",
        "Get the current balance for a bank account. Input: account_id (str).",
    ),
    (
        _statement_download,
        "statement_download",
        "Download a bank statement PDF. Input: account_id (str), months (int, default 3).",
    ),
    (
        _account_summary,
        "account_summary",
        "Retrieve metadata for a bank account. Input: account_id (str).",
    ),
    (
        _yield_interest,
        "yield_interest",
        (
            "Calculate simple interest. "
            "Input: principal (float), rate_bps (float), days (int)."
        ),
    ),
]


def register_tools(mcp: FastMCP) -> None:
    """Register all tools from _TOOL_REGISTRY onto the FastMCP instance.

    This uses the programmatic ``mcp.tool()(fn)`` call form rather than the
    ``@mcp.tool()`` decorator, making the tools invisible to decorator-pattern
    SBOM scanners.
    """
    for fn, name, description in _TOOL_REGISTRY:
        # Programmatic registration — no decorator syntax in AST
        decorated = mcp.tool(name=name, description=description)(fn)
        logger.debug("Registered tool: %s", name)
    logger.info("Registered %d tools from dynamic registry", len(_TOOL_REGISTRY))


# ---------------------------------------------------------------------------
# Additional pattern: tools loaded from an external JSON config file.
# If a deployment supplies a tools.json, those tool definitions are registered
# at startup without any Python function definition visible to the scanner.
# ---------------------------------------------------------------------------

def register_tools_from_config(mcp: FastMCP, config_path: str | None = None) -> None:
    """Register passthrough tools from a JSON config (e.g. tools.json).

    Each entry is a thin HTTP proxy tool — the implementation is the same
    (a generic HTTP call), only the name/description differ.  This means
    zero AST-visible tool functions per registered tool.
    """
    import json
    from pathlib import Path
    import requests as _requests

    cfg_path = Path(config_path or os.getenv("TOOLS_CONFIG_PATH", ""))
    if not cfg_path.exists():
        logger.debug("No external tools config found at %s, skipping", cfg_path)
        return

    with cfg_path.open() as f:
        tool_specs: list[dict] = json.load(f)

    for spec in tool_specs:
        upstream_url = spec["upstream_url"]

        # Closure captures spec per iteration
        def _make_proxy(url: str, timeout: int = spec.get("timeout_s", 10)):
            def _proxy_tool(**kwargs: Any) -> Any:
                resp = _requests.post(url, json=kwargs, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            return _proxy_tool

        proxy_fn = _make_proxy(upstream_url)
        mcp.tool(name=spec["name"], description=spec.get("description", ""))(proxy_fn)
        logger.debug("Registered proxy tool: %s → %s", spec["name"], upstream_url)

    logger.info("Registered %d proxy tools from config", len(tool_specs))
