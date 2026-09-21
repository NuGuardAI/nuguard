#!/usr/bin/env python3
"""Submit a NuGuard pentest report to VulnerableApp's /scanner/benchmark endpoint.

VulnerableApp (https://github.com/SasanLabs/VulnerableApp) exposes a benchmark
comparator that scores a scanner's findings against its own ground truth:
``POST <base-url>/scanner/benchmark`` with
``{tool, scanType: "DAST", findings: [{url, cwe, type}]}`` returns a coverage
report (detected/missed/unmatched); see
https://github.com/SasanLabs/VulnerableApp/blob/master/benchmarks/README.md
for the full schema and matching rules. This script maps a
``nuguard pentest --format json`` report into that schema and submits it.

Matching only uses ``cwe`` (from Nuclei's own classification metadata, when a
template declares one) — VulnerableApp's canonical ``type`` vocabulary (e.g.
BLIND_SQL_INJECTION) has no reliable mapping from Nuclei's template tags, and
the benchmark's matching rule already treats type/cwe/wascId as "any one axis
is enough", so omitting ``type`` only weakens matching for findings that have
no CWE classification at all, rather than risking a wrong ``type`` guess.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_CWE_RE = re.compile(r"(\d+)")


def _cwe_from_classification(classification: dict) -> str | None:
    values = classification.get("cwe-id")
    if not values:
        return None
    if isinstance(values, str):
        values = [values]
    for value in values:
        match = _CWE_RE.search(str(value))
        if match:
            return f"CWE-{match.group(1)}"
    return None


def _relative_url(matched_at: str, base_path: str) -> str:
    """Strip VulnerableApp's context-root path (e.g. '/VulnerableApp') from a matched URL.

    The benchmark endpoint expects paths relative to the app context, e.g.
    "/BlindSQLInjectionVulnerability/LEVEL_1", not a full URL.
    """
    path = urlsplit(matched_at).path or "/"
    base_path = base_path.rstrip("/")
    if base_path and path.startswith(base_path):
        path = path[len(base_path):]
    return path or "/"


def build_findings(report: dict, *, base_path: str) -> list[dict]:
    """Map a pentest JSON report's findings into the benchmark's DAST finding schema."""
    findings: list[dict] = []
    seen: set[tuple[str, str | None]] = set()

    for finding in report.get("findings", []):
        matched_at = finding.get("matched_at") or finding.get("target") or ""
        if not matched_at:
            continue

        entry: dict[str, str] = {"url": _relative_url(matched_at, base_path)}
        classification = (finding.get("metadata") or {}).get("classification") or {}
        cwe = _cwe_from_classification(classification)
        if cwe:
            entry["cwe"] = cwe

        key = (entry["url"], entry.get("cwe"))
        if key in seen:
            continue
        seen.add(key)
        findings.append(entry)

    return findings


def submit_benchmark(base_url: str, tool: str, findings: list[dict]) -> dict:
    payload = json.dumps({"tool": tool, "scanType": "DAST", "findings": findings}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/scanner/benchmark",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", required=True, help="Path to a `nuguard pentest --format json` report."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="VulnerableApp base URL, e.g. http://host:9090/VulnerableApp",
    )
    parser.add_argument("--tool", default="NuGuard", help="Tool name reported to the benchmark.")
    parser.add_argument("--output", help="Optional path to save the coverage report JSON.")
    args = parser.parse_args()

    with open(args.report, encoding="utf-8") as handle:
        report = json.load(handle)

    base_path = urlsplit(args.base_url).path
    findings = build_findings(report, base_path=base_path)

    if not findings:
        print(
            "No findings with a usable URL were found in the report; submitting an empty set.",
            file=sys.stderr,
        )

    try:
        coverage = submit_benchmark(args.base_url, args.tool, findings)
    except urllib.error.URLError as exc:
        print(f"Benchmark submission failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(coverage, indent=2, sort_keys=True))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(coverage, handle, indent=2, sort_keys=True)
            handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
