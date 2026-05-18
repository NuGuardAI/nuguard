"""ANSI terminal summary reporter for nuguard static analysis.

Renders a structured, coloured summary to stdout using only the stdlib —
no ``rich`` dependency.  Falls back gracefully to plain text when a TTY is
not detected (e.g. CI log capture).

Output sections
---------------
1. **Header** — scan target, timestamp, nuguard version
2. **Finding summary** — table: Tool | CRITICAL | HIGH | MEDIUM | LOW | INFO
3. **Top findings** — top 10 by severity
4. **Tool status** — which tools ran / were skipped
5. **Next steps** — remediation pointers for the top finding categories

Usage
-----
::

    from nuguard.analysis.plugins.terminal_reporter import print_terminal_report
    from nuguard.models.finding import Finding

    print_terminal_report(
        findings=findings,
        tool_status={"nga": "ok", "osv": "ok", "grype": "skipped"},
        scan_target="./my-ai-app",
    )
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

# ANSI colour codes — emitted only when writing to a real TTY
_USE_COLOR = sys.stdout.isatty()

_RESET  = "\033[0m"   if _USE_COLOR else ""
_BOLD   = "\033[1m"   if _USE_COLOR else ""
_RED    = "\033[31m"  if _USE_COLOR else ""
_ORANGE = "\033[33m"  if _USE_COLOR else ""
_YELLOW = "\033[93m"  if _USE_COLOR else ""
_GREEN  = "\033[32m"  if _USE_COLOR else ""
_CYAN   = "\033[36m"  if _USE_COLOR else ""
_DIM    = "\033[2m"   if _USE_COLOR else ""

_SEV_COLOR: dict[str, str] = {
    "critical": _RED,
    "high":     _ORANGE,
    "medium":   _YELLOW,
    "low":      _GREEN,
    "info":     _DIM,
}

_SEV_LABEL: dict[str, str] = {
    "critical": "CRIT",
    "high":     "HIGH",
    "medium":   "MED ",
    "low":      "LOW ",
    "info":     "INFO",
}

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

_TOOL_DISPLAY: dict[str, str] = {
    "nuguard-nga":   "NGA rules    ",
    "nga":           "NGA rules    ",
    "nga-rules":     "NGA rules    ",
    "atlas":         "ATLAS        ",
    "atlas-native":  "ATLAS        ",
    "nuguard-atlas": "ATLAS        ",
    "osv":           "OSV deps     ",
    "nuguard-osv":   "OSV deps     ",
    "grype":         "Grype        ",
    "nuguard-grype": "Grype        ",
    "checkov":       "Checkov (IaC)",
    "trivy":         "Trivy        ",
    "trivy-misconfig": "Trivy      ",
    "semgrep":       "Semgrep      ",
}


def _norm_sev(raw: Any) -> str:
    """Return lowercase severity string from a Finding or raw dict value."""
    s = str(raw).lower().split(".")[-1]
    return s if s in _SEV_ORDER else "low"


def _norm_finding(f: Any) -> dict[str, Any]:
    if hasattr(f, "model_dump"):
        d = f.model_dump()
        d["severity"] = _norm_sev(d.get("severity", "low"))
        return d
    d = dict(f)
    d["severity"] = _norm_sev(d.get("severity", "low"))
    return d


def print_terminal_report(
    findings: list[Any],
    tool_status: dict[str, str] | None = None,
    scan_target: str = ".",
    version: str = "dev",
    output: Any = None,
) -> None:
    """Print the terminal summary to *output* (defaults to ``sys.stdout``).

    Parameters
    ----------
    findings:
        List of ``Finding`` objects or raw finding dicts from all analysis tools.
    tool_status:
        Mapping of tool name → status string (``"ok"``, ``"warning"``,
        ``"skipped"``, ``"error"``).  Used to render the tool status table.
    scan_target:
        Human-readable scan target path shown in the header.
    version:
        nuguard version string shown in the header.
    output:
        File-like object to write to (default: ``sys.stdout``).
    """
    out = output or sys.stdout

    def w(line: str = "") -> None:
        out.write(line + "\n")

    normed = [_norm_finding(f) for f in findings]
    normed.sort(key=lambda x: _SEV_ORDER.get(x["severity"], 99))

    tool_status = tool_status or {}

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Header ────────────────────────────────────────────────────────────────
    w()
    w(f"{_BOLD}{'─' * 70}{_RESET}")
    w(f"{_BOLD}  nuguard {version}  —  AI Security Static Analysis{_RESET}")
    w(f"{_DIM}  Target: {scan_target}   Timestamp: {ts}{_RESET}")
    w(f"{_BOLD}{'─' * 70}{_RESET}")
    w()

    # ── Finding summary table ─────────────────────────────────────────────────
    w(f"{_BOLD}{'Tool':<16} {'CRITICAL':>8} {'HIGH':>6} {'MEDIUM':>7} {'LOW':>6} {'INFO':>5}{_RESET}")
    w(f"{'─' * 16} {'─' * 8} {'─' * 6} {'─' * 7} {'─' * 6} {'─' * 5}")

    sources: dict[str, str] = {}  # normalise → display name
    counts: dict[str, dict[str, int]] = {}

    for f in normed:
        src_raw = str(f.get("source") or "nga").lower()
        src = _TOOL_DISPLAY.get(src_raw, src_raw[:14] + " ")
        sources[src_raw] = src
        counts.setdefault(src, {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0})
        sev = f["severity"]
        if sev in counts[src]:
            counts[src][sev] += 1

    totals: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    for src_display in sorted(counts.keys()):
        row = counts[src_display]
        for s in totals:
            totals[s] += row[s]

        def _col(s: str) -> str:
            n = row[s]
            if n == 0:
                return f"{_DIM}{'—':>6}{_RESET}"
            return f"{_SEV_COLOR.get(s, '')}{n:>6}{_RESET}"

        w(f"{src_display:<16} {_col('critical'):>8} {_col('high'):>6} {_col('medium'):>7} {_col('low'):>6} {_col('info'):>5}")

    w(f"{'─' * 16} {'─' * 8} {'─' * 6} {'─' * 7} {'─' * 6} {'─' * 5}")
    def _tot(s: str) -> str:
        n = totals[s]
        if n == 0:
            return f"{'—':>6}"
        return f"{_BOLD}{_SEV_COLOR.get(s, '')}{n:>6}{_RESET}"

    w(f"{'TOTAL':<16} {_tot('critical'):>8} {_tot('high'):>6} {_tot('medium'):>7} {_tot('low'):>6} {_tot('info'):>5}")
    w()

    # ── Top findings ──────────────────────────────────────────────────────────
    top = normed[:10]
    if top:
        w(f"{_BOLD}Top Findings (by severity){_RESET}")
        w(f"{'─' * 70}")
        for f in top:
            sev   = f["severity"]
            col   = _SEV_COLOR.get(sev, "")
            label = _SEV_LABEL.get(sev, sev[:4].upper())
            rid   = f.get("rule_id") or f.get("finding_id") or "?"
            title = f.get("title") or f.get("summary") or rid
            aff   = f.get("affected_component") or ""
            if not aff:
                affl = f.get("affected") or []
                if isinstance(affl, list) and affl:
                    aff = ", ".join(str(a) for a in affl[:2])
            if len(str(title)) > 50:
                title = str(title)[:47] + "..."
            aff_str = f"  {_DIM}→ {aff[:40]}{_RESET}" if aff else ""
            w(f"  {col}{label}{_RESET}  {_BOLD}{rid:<12}{_RESET}  {title}{aff_str}")
        if len(normed) > 10:
            w(f"  {_DIM}… and {len(normed) - 10} more findings{_RESET}")
        w()

    # ── Tool status ──────────────────────────────────────────────────────────
    if tool_status:
        w(f"{_BOLD}Tool Status{_RESET}")
        w(f"{'─' * 70}")
        _status_color = {
            "ok":      _GREEN,
            "warning": _YELLOW,
            "skipped": _DIM,
            "error":   _RED,
        }
        _status_icon = {"ok": "✓", "warning": "⚠", "skipped": "–", "error": "✗"}
        for tool, status in sorted(tool_status.items()):
            display = _TOOL_DISPLAY.get(tool.lower(), tool)
            col  = _status_color.get(status.lower(), "")
            icon = _status_icon.get(status.lower(), "?")
            hint = ""
            if status.lower() == "skipped":
                _install_hints = {
                    "grype":   "  (install: https://github.com/anchore/grype)",
                    "checkov": "  (install: pip install checkov)",
                    "trivy":   "  (install: https://aquasecurity.github.io/trivy)",
                    "semgrep": "  (install: pip install semgrep)",
                }
                hint = _install_hints.get(tool.lower(), "")
            w(f"  {col}{icon}{_RESET}  {display.strip():<14}  {col}{status.upper()}{_RESET}{_DIM}{hint}{_RESET}")
        w()

    # ── Next steps ───────────────────────────────────────────────────────────
    crit_high = [f for f in normed if f["severity"] in ("critical", "high")]
    if crit_high:
        rule_ids = list(dict.fromkeys(
            f.get("rule_id", "").split("-")[0] + "-" + f.get("rule_id", "").split("-")[1]
            if "-" in str(f.get("rule_id", ""))
            else str(f.get("rule_id", ""))
            for f in crit_high[:5]
        ))
        w(f"{_BOLD}Next Steps{_RESET}")
        w(f"{'─' * 70}")
        w(f"  Address the {len(crit_high)} CRITICAL/HIGH finding(s) first.")
        for rid in rule_ids[:3]:
            w(f"  • {rid}: see remediation guidance in findings output")
        w("  Full report: nuguard-reports/report.md")
        w("  SARIF:       nuguard-reports/findings.sarif  (upload to GitHub Code Scanning)")
        w()

    # ── Footer ────────────────────────────────────────────────────────────────
    total_all = sum(totals.values())
    if total_all == 0:
        w(f"{_GREEN}{_BOLD}  ✓ No findings detected.{_RESET}")
    else:
        crit = totals["critical"]
        high = totals["high"]
        if crit > 0:
            w(f"{_RED}{_BOLD}  ✗ {crit} CRITICAL finding(s) — immediate action required.{_RESET}")
        elif high > 0:
            w(f"{_ORANGE}{_BOLD}  ⚠ {high} HIGH finding(s) — review before merging.{_RESET}")
    w(f"{_BOLD}{'─' * 70}{_RESET}")
    w()
