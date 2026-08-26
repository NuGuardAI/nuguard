"""Pydantic-only public entry point for the analysis package.

A thin async wrapper around :class:`StaticAnalyzer` for callers outside the
CLI: a plain, JSON-serializable request model in, a JSON-serializable result
model out. ``StaticAnalyzer`` itself, its constructor, and ``analyze()`` are
untouched — everything here is additive.

Like :mod:`nuguard.behavior.public_api` and :mod:`nuguard.redteam.public_api`,
this does not catch or suppress exceptions raised while running an analysis
pass — a full analysis run is not a best-effort probe. Only remediation-plan
synthesis is best-effort, mirroring those two modules exactly.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from nuguard.analysis.static_analyzer import StaticAnalyzer
from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity
from nuguard.models.token_usage import TokenUsage
from nuguard.remediation.backfill import backfill_finding_remediation
from nuguard.remediation.models import RemediationArtefact

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)


class AnalysisRunRequest(BaseModel):
    """JSON-safe input for :func:`run_analysis`.

    Mirrors ``StaticAnalyzer.__init__``'s parameters with identical defaults.
    ``source_path`` is a plain string here (``Path`` is not JSON-safe) and is
    converted back to a ``Path`` inside :func:`run_analysis`.
    """

    enable_atlas: bool = True
    enable_osv: bool = True
    enable_grype: bool = True
    enable_checkov: bool = True
    enable_trivy: bool = True
    enable_semgrep: bool = True
    enable_supply_chain: bool = True
    source_path: str | None = None
    atlas_config: dict[str, Any] | None = None
    min_severity: Severity = Severity.LOW
    verbose: bool = False
    grype_timeout: float = 180.0
    grype_retries: int = 3
    supply_chain_profile: str = "standard"
    supply_chain_verify_artifacts: str = "off"
    supply_chain_threat_intel_feeds: list[str] | None = None


class AnalysisRunResult(BaseModel):
    """JSON-safe result of :func:`run_analysis`.

    Bundles ``StaticAnalyzer.analyze()``'s return value together with the
    side-effect instance attributes the CLI reads separately today
    (``tool_status``, ``nga_audit``, ``sc_audit``, ``token_usage``), so
    external callers get one self-contained object instead of having to know
    which attributes to read off the analyzer instance.
    """

    findings: list[Finding]
    tool_status: dict[str, dict[str, str]] = Field(default_factory=dict)
    nga_audit: list[dict[str, Any]] = Field(default_factory=list)
    sc_audit: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    remediation_plan: list[RemediationArtefact] = Field(default_factory=list)
    """Concrete, SBOM-node-specific remediation artefacts for ``findings``.

    Synthesized best-effort from ``findings`` via the same
    ``RemediationSynthesizer`` behavior's and redteam's public APIs use.
    Empty when synthesis fails or no SBOM is available.
    """


async def _build_remediation_plan(
    findings: list[Finding],
    *,
    sbom: "AiSbomDocument | None",
    policy: "CognitivePolicy | None",
    llm_client: "LLMClient | None",
) -> list[RemediationArtefact]:
    """Synthesize per-SBOM-node remediation artefacts from analysis findings.

    Mirrors ``nuguard.redteam.public_api._build_remediation_plan``.
    Best-effort: returns ``[]`` on missing SBOM, no findings, or any failure.
    """
    if sbom is None or not findings:
        return []
    try:
        from nuguard.remediation.synthesizer import RemediationSynthesizer  # noqa: PLC0415

        synthesizer = RemediationSynthesizer(sbom=sbom, policy=policy, llm_client=llm_client)
        finding_dicts = [
            {
                "finding_id": f.finding_id,
                "title": f.title,
                "description": f.description or "",
                "affected_component": f.affected_component or "unknown",
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
            }
            for f in findings
        ]
        return await synthesizer.synthesize_findings_async(finding_dicts)
    except Exception as exc:  # noqa: BLE001
        _log.warning("run_analysis: remediation synthesis failed — skipping plan: %s", exc)
        return []


async def run_analysis(
    request: AnalysisRunRequest,
    *,
    sbom: "AiSbomDocument",
    policy: "CognitivePolicy | None" = None,
    llm_client: "LLMClient | None" = None,
) -> AnalysisRunResult:
    """Run a full static analysis pass from a JSON-safe request.

    Thin wrapper around ``StaticAnalyzer(...).analyze(sbom)`` — which itself
    runs its independent, I/O-bound detector steps (OSV/Grype/Checkov/Trivy/
    Semgrep) concurrently — followed by awaiting remediation synthesis.
    """
    _log.debug("run_analysis: min_severity=%s", request.min_severity)
    analyzer = StaticAnalyzer(
        enable_atlas=request.enable_atlas,
        enable_osv=request.enable_osv,
        enable_grype=request.enable_grype,
        enable_checkov=request.enable_checkov,
        enable_trivy=request.enable_trivy,
        enable_semgrep=request.enable_semgrep,
        enable_supply_chain=request.enable_supply_chain,
        source_path=Path(request.source_path) if request.source_path else None,
        atlas_config=request.atlas_config,
        min_severity=request.min_severity,
        verbose=request.verbose,
        grype_timeout=request.grype_timeout,
        grype_retries=request.grype_retries,
        supply_chain_profile=request.supply_chain_profile,
        supply_chain_verify_artifacts=request.supply_chain_verify_artifacts,
        supply_chain_threat_intel_feeds=request.supply_chain_threat_intel_feeds,
    )
    findings = await analyzer.analyze(sbom)

    remediation_plan = await _build_remediation_plan(
        findings, sbom=sbom, policy=policy, llm_client=llm_client
    )
    backfill_finding_remediation(findings, remediation_plan)

    return AnalysisRunResult(
        findings=findings,
        tool_status=analyzer.tool_status,
        nga_audit=analyzer.nga_audit,
        sc_audit=analyzer.sc_audit,
        token_usage=analyzer.token_usage,
        remediation_plan=remediation_plan,
    )
