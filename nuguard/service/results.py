"""Assemble :class:`ScanResult` documents and render them as SARIF."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from nuguard.models.finding import Finding, Severity
from nuguard.models.tool_run import ToolRun, ToolRunStatus
from nuguard.sbom.models import AiSbomDocument
from nuguard.service import fingerprint as fp
from nuguard.service.models import (
    Coverage,
    InventorySummary,
    Provenance,
    ScanFinding,
    ScanJob,
    ScanResult,
    ScanStatus,
    ToolVersion,
)

TOOLS_JSON_ENV = "NUGUARD_TOOLS_JSON"
DEFAULT_TOOLS_JSON = Path("/etc/nuguard/tools.json")

_SEVERITY_RANK = {
    Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3, Severity.INFO: 4,
}
_COVERED = (ToolRunStatus.OK, ToolRunStatus.NOT_APPLICABLE)


class ScanBody(BaseModel):
    """What a worker produces; the API process adds the job envelope and hash."""

    status: ScanStatus
    commit_sha: str | None
    provenance: Provenance
    coverage: Coverage
    summary: dict[Severity, int]
    findings: list[ScanFinding]
    inventory: InventorySummary
    sbom: dict | None = None


# ── findings ─────────────────────────────────────────────────────────────────


def project_findings(
    findings: list[Finding],
    *,
    repo: str,
    scan_root: Path | None,
    include_posture: bool,
) -> list[ScanFinding]:
    """Project NuGuard findings to ``ScanFinding`` with stable fingerprints."""
    staged: list[tuple[ScanFinding, str]] = []
    for finding in findings:
        kind = finding.kind or "posture"
        if kind == "posture" and not include_posture:
            continue
        rule_id = finding.rule_id or finding.finding_id.rsplit("-", 1)[0]
        tool = finding.source_tool or "nuguard"
        key = fp.identity_key(finding, kind, scan_root)
        staged.append(
            (
                ScanFinding(
                    fingerprint=fp.compute(repo, kind, tool, rule_id, key),
                    rule_id=rule_id,
                    source_tool=tool,
                    kind=kind,
                    severity=finding.severity,
                    title=finding.title,
                    description=finding.description,
                    locations=finding.locations,
                    affected_component=finding.affected_component,
                    references=finding.references,
                    owasp_llm_ref=finding.owasp_llm_ref,
                    owasp_asi_ref=finding.owasp_asi_ref,
                    mitre_atlas_technique=finding.mitre_atlas_technique,
                    remediation=finding.remediation,
                    raw=finding,
                ),
                key,
            )
        )

    # Two findings can share an identity (same rule twice in one function).
    # Keep the first fingerprint and suffix the rest by stable position.
    groups: dict[str, list[tuple[ScanFinding, str]]] = {}
    for item in staged:
        groups.setdefault(item[0].fingerprint, []).append(item)
    out: list[ScanFinding] = []
    for members in groups.values():
        members.sort(key=lambda m: (_position(m[0]), m[0].title, m[0].description))
        for index, (scan_finding, key) in enumerate(members):
            if index:
                scan_finding = scan_finding.model_copy(
                    update={"fingerprint": fp.compute(
                        repo, scan_finding.kind, scan_finding.source_tool, scan_finding.rule_id,
                        f"{key}#{index + 1}",
                    )}
                )
            out.append(scan_finding)
    out.sort(key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.rule_id, f.fingerprint))
    return out


def _position(finding: ScanFinding) -> tuple[str, int]:
    loc = finding.locations[0] if finding.locations else None
    return (loc.path, loc.start_line or 0) if loc else ("", 0)


def severity_summary(findings: list[ScanFinding]) -> dict[Severity, int]:
    counts = Counter(f.severity for f in findings)
    return {sev: counts.get(sev, 0) for sev in Severity}


# ── coverage / status ────────────────────────────────────────────────────────


def build_coverage(
    tool_runs: list[ToolRun],
    requested: list[str],
    *,
    languages: list[str],
    files_scanned: int,
    files_skipped: int,
) -> Coverage:
    """One ToolRun per requested tool; anything not ok/not_applicable is a gap."""
    by_tool = {r.tool: r for r in tool_runs}
    runs: list[ToolRun] = []
    for tool in requested:
        run = by_tool.get(tool) or ToolRun(
            tool=tool, status=ToolRunStatus.FAILED, reason="tool did not report a result"
        )
        runs.append(run)
    gaps = [
        f"{r.tool}: {r.status.value} ({r.reason})" for r in runs if r.status not in _COVERED
    ]
    return Coverage(
        tool_runs=runs,
        languages_detected=languages,
        files_scanned=files_scanned,
        files_skipped=files_skipped,
        gaps=gaps,
    )


def status_for(coverage: Coverage) -> ScanStatus:
    """``succeeded`` only when every requested tool ended ok or not_applicable."""
    if all(r.status in _COVERED for r in coverage.tool_runs):
        return ScanStatus.SUCCEEDED
    return ScanStatus.PARTIAL


# ── inventory / provenance ───────────────────────────────────────────────────


def inventory_from(doc: AiSbomDocument) -> InventorySummary:
    from nuguard.common.soft_reject import is_soft_rejected  # noqa: PLC0415

    nodes = [n for n in doc.nodes if not is_soft_rejected(n)]
    counts = Counter(str(getattr(n.component_type, "value", n.component_type)) for n in nodes)
    frameworks = {n.name for n in nodes if str(getattr(n.component_type, "value", "")) == "FRAMEWORK"}
    if doc.summary is not None:
        frameworks.update(doc.summary.frameworks or [])
    providers: set[str] = set()
    for node in nodes:
        if str(getattr(node.component_type, "value", "")) != "MODEL":
            continue
        meta = node.metadata.model_dump() if node.metadata else {}
        provider = meta.get("provider") or (meta.get("extras") or {}).get("provider")
        if provider:
            providers.add(str(provider).lower())
    return InventorySummary(
        node_counts=dict(sorted(counts.items())),
        edge_count=len(doc.edges),
        frameworks=sorted(frameworks),
        model_providers=sorted(providers),
    )


def rulepack_sha256() -> str:
    """Hash of the bundled Semgrep rules and the NGA rule table."""
    from nuguard.analysis.plugins.nga_rules import _RULE_META  # noqa: PLC0415

    digest = hashlib.sha256()
    rules_dir = resources.files("nuguard.analysis.plugins").joinpath("semgrep_rules")
    for entry in sorted(rules_dir.iterdir(), key=lambda e: e.name):
        if entry.name.endswith((".yaml", ".yml")):
            digest.update(entry.name.encode())
            digest.update(entry.read_bytes())
    digest.update(json.dumps(_RULE_META, sort_keys=True).encode())
    return digest.hexdigest()


def tool_versions(tool_runs: list[ToolRun]) -> list[ToolVersion]:
    """Versions from the image's tools.json when present, else what the runs reported."""
    pinned: dict[str, str] = {}
    path = Path(os.environ.get(TOOLS_JSON_ENV) or DEFAULT_TOOLS_JSON)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            pinned = {str(k): str(v) for k, v in data.items() if v}
    except (OSError, ValueError):
        pinned = {}
    names = sorted({*pinned, *(r.tool for r in tool_runs)})
    by_run = {r.tool: r.version for r in tool_runs}
    return [ToolVersion(name=n, version=pinned.get(n) or by_run.get(n)) for n in names]


# ── result envelope ──────────────────────────────────────────────────────────


def _drop_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    """Fields that legitimately differ between two runs of the same scan."""
    data = copy.deepcopy(payload)
    data.pop("result_sha256", None)
    data.pop("sbom", None)  # node UUIDs and generated_at differ per run
    job = data.get("job") or {}
    for key in ("id", "created_at", "started_at", "finished_at", "links", "client_reference"):
        job.pop(key, None)
    for run in (data.get("coverage") or {}).get("tool_runs") or []:
        run.pop("duration_ms", None)
    for finding in data.get("findings") or []:
        (finding.get("raw") or {}).pop("finding_id", None)
    return data


def canonical_sha256(payload: dict[str, Any]) -> str:
    text = json.dumps(
        _drop_volatile(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assemble_result(body: ScanBody, job: ScanJob) -> ScanResult:
    draft = ScanResult(
        job=job,
        provenance=body.provenance,
        coverage=body.coverage,
        summary=body.summary,
        findings=body.findings,
        inventory=body.inventory,
        sbom=body.sbom,
        result_sha256="",
    )
    return draft.model_copy(
        update={"result_sha256": canonical_sha256(draft.model_dump(mode="json"))}
    )


# ── SARIF ────────────────────────────────────────────────────────────────────


def render_sarif(result: ScanResult) -> str:
    """SARIF 2.1.0 via ``nuguard.output``, with repo-relative locations and fingerprints."""
    from nuguard.output.sarif_generator import _driver_for, generate_sarif  # noqa: PLC0415

    inputs: list[dict[str, Any]] = []
    for finding in result.findings:
        data = finding.raw.model_dump(mode="json")
        data["rule_id"] = finding.rule_id
        data["source"] = finding.source_tool
        # nuguard.output reads file locations from "affected" entries ("path:line").
        data["affected"] = [
            f"{loc.path}:{loc.start_line}" if loc.start_line else loc.path
            for loc in finding.locations
        ]
        inputs.append(data)
    document = json.loads(generate_sarif(inputs, sbom_path="nuguard-ai-sbom.json"))

    # generate_sarif groups by driver (sorted) and keeps input order within a
    # group; walk the same grouping to attach each result's fingerprint.
    by_driver: dict[str, list[ScanFinding]] = {}
    for finding, data in zip(result.findings, inputs):
        by_driver.setdefault(_driver_for(data), []).append(finding)
    for run in document.get("runs", []):
        driver = run.get("tool", {}).get("driver", {}).get("name", "")
        for sarif_result, finding in zip(run.get("results", []), by_driver.get(driver, [])):
            sarif_result["partialFingerprints"] = {"nuguardFingerprint/v1": finding.fingerprint}
            sarif_result.setdefault("properties", {})["kind"] = finding.kind
    return json.dumps(document, indent=2)
