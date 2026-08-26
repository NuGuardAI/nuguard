"""Public Pydantic report/export contracts for behavior and redteam outputs."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from nuguard.cli.report_meta import ReportMeta
from nuguard.output.validation_report import (
    extract_behavior_scenario_details,
    extract_redteam_scenario_details,
)

if TYPE_CHECKING:
    from nuguard.behavior.models import BehaviorAnalysisResult, BehaviorRunResult
    from nuguard.redteam.public_api import RedteamRunResult


class ValidationReportMetaModel(BaseModel):
    llm_models: list[str] = Field(default_factory=list)
    target_url: str | None = None
    target_endpoint: str | None = None


class RedteamReportRenderRequest(BaseModel):
    run_id: str
    include_markdown: bool = True
    include_json_summary: bool = True
    include_scenario_details: bool = True
    turn_report_alias: bool = True
    meta: ValidationReportMetaModel | None = None


class RedteamReportRenderResult(BaseModel):
    run_id: str
    markdown: str | None = None
    json_summary: dict[str, Any] | None = None
    scenario_details: list[dict[str, Any]] = Field(default_factory=list)


class BehaviorReportRenderRequest(BaseModel):
    run_id: str
    include_markdown: bool = True
    include_json_summary: bool = True
    include_turn_report: bool = True
    turn_report_alias: bool = True
    meta: ValidationReportMetaModel | None = None


class BehaviorReportRenderResult(BaseModel):
    run_id: str
    markdown: str | None = None
    json_summary: dict[str, Any] | None = None
    turn_report: list[dict[str, Any]] = Field(default_factory=list)
    turn_records: list[dict[str, Any]] = Field(default_factory=list)


class ValidationReportExportRequest(BaseModel):
    run_id: str
    run_type: Literal["redteam", "behavior"]
    export_format: Literal["markdown", "json"]
    meta: ValidationReportMetaModel | None = None


class ValidationReportExportResult(BaseModel):
    run_id: str
    run_type: Literal["redteam", "behavior"]
    export_format: Literal["markdown", "json"]
    content: str
    media_type: str
    filename: str


def _to_report_meta(meta: ValidationReportMetaModel | None) -> ReportMeta:
    if meta is None:
        return ReportMeta()
    return ReportMeta(
        llm_models=list(meta.llm_models),
        target_url=meta.target_url or "",
        target_endpoint=meta.target_endpoint or "",
        effective_endpoint=meta.target_endpoint or "",
    )


def _behavior_simple_markdown(run_result: "BehaviorRunResult") -> str:
    lines = [
        "# Behavior Run Report",
        "",
        f"- Run ID: `{run_result.run_id}`",
        f"- Scan Outcome: `{run_result.scan_outcome}`",
        f"- Scenarios Executed: {run_result.scenarios_executed}",
        f"- Findings: {len(run_result.findings)}",
        "",
    ]
    return "\n".join(lines)


async def render_redteam_report(
    request: RedteamReportRenderRequest,
    *,
    run_result: "RedteamRunResult",
) -> RedteamReportRenderResult:
    from nuguard.redteam import report as redteam_report

    meta = _to_report_meta(request.meta)
    markdown: str | None = None
    json_summary: dict[str, Any] | None = None
    scenario_details: list[dict[str, Any]] = []

    if request.include_markdown:
        markdown = redteam_report.to_markdown(
            run_result.findings,
            meta,
            remediation_plan=run_result.remediation_plan,
            scenario_records=run_result.scenario_records,
            catalog_coverage=run_result.catalog_coverage,
            coverage_tracker=run_result.coverage_tracker,
        )

    if request.include_json_summary:
        json_summary = json.loads(
            redteam_report.to_json(
                run_result.findings,
                meta,
                remediation_plan=run_result.remediation_plan,
                scan_outcome=run_result.scan_outcome,
                input_tokens_used=run_result.input_tokens_used,
                output_tokens_used=run_result.output_tokens_used,
                token_usage=run_result.token_usage,
                scenario_records=run_result.scenario_records,
            )
        )

    if request.include_scenario_details:
        scenario_details = [asdict(item) for item in extract_redteam_scenario_details(run_result.scenario_records)]

    return RedteamReportRenderResult(
        run_id=request.run_id,
        markdown=markdown,
        json_summary=json_summary,
        scenario_details=scenario_details,
    )


async def render_behavior_report(
    request: BehaviorReportRenderRequest,
    *,
    run_result: "BehaviorAnalysisResult | BehaviorRunResult",
) -> BehaviorReportRenderResult:
    from nuguard.behavior import report as behavior_report
    from nuguard.behavior.models import BehaviorAnalysisResult

    meta = _to_report_meta(request.meta)
    markdown: str | None = None
    json_summary: dict[str, Any] | None = None
    turn_records: list[dict[str, Any]] = []

    if isinstance(run_result, BehaviorAnalysisResult):
        if request.include_markdown:
            markdown = behavior_report.to_markdown(run_result, meta=meta)
        if request.include_json_summary:
            json_summary = json.loads(behavior_report.to_json(run_result, meta=meta))
        turn_records = [
            asdict(turn)
            for scenario in extract_behavior_scenario_details(run_result.scenario_results)
            for turn in scenario.turns
        ]
    else:
        if request.include_markdown:
            markdown = _behavior_simple_markdown(run_result)
        if request.include_json_summary:
            json_summary = run_result.model_dump(mode="json")
        turn_records = [turn.model_dump(mode="json") for turn in run_result.turn_records]

    turn_report = list(turn_records) if request.turn_report_alias and request.include_turn_report else []
    if not request.include_turn_report:
        turn_records = []

    return BehaviorReportRenderResult(
        run_id=request.run_id,
        markdown=markdown,
        json_summary=json_summary,
        turn_report=turn_report,
        turn_records=turn_records,
    )


async def export_validation_report(
    request: ValidationReportExportRequest,
    *,
    redteam_run_result: "RedteamRunResult | None" = None,
    behavior_run_result: "BehaviorAnalysisResult | BehaviorRunResult | None" = None,
) -> ValidationReportExportResult:
    if request.run_type == "redteam":
        if redteam_run_result is None:
            raise ValueError("redteam_run_result is required when run_type='redteam'")
        redteam_rendered = await render_redteam_report(
            RedteamReportRenderRequest(
                run_id=request.run_id,
                include_markdown=request.export_format == "markdown",
                include_json_summary=request.export_format == "json",
                include_scenario_details=False,
                meta=request.meta,
            ),
            run_result=redteam_run_result,
        )
        if request.export_format == "markdown":
            content = redteam_rendered.markdown or ""
            media_type = "text/markdown"
            filename = f"redteam-{request.run_id}.md"
        else:
            content = json.dumps(redteam_rendered.json_summary or {}, indent=2)
            media_type = "application/json"
            filename = f"redteam-{request.run_id}.json"
    else:
        if behavior_run_result is None:
            raise ValueError("behavior_run_result is required when run_type='behavior'")
        behavior_rendered = await render_behavior_report(
            BehaviorReportRenderRequest(
                run_id=request.run_id,
                include_markdown=request.export_format == "markdown",
                include_json_summary=request.export_format == "json",
                include_turn_report=False,
                meta=request.meta,
            ),
            run_result=behavior_run_result,
        )
        if request.export_format == "markdown":
            content = behavior_rendered.markdown or ""
            media_type = "text/markdown"
            filename = f"behavior-{request.run_id}.md"
        else:
            content = json.dumps(behavior_rendered.json_summary or {}, indent=2)
            media_type = "application/json"
            filename = f"behavior-{request.run_id}.json"

    return ValidationReportExportResult(
        run_id=request.run_id,
        run_type=request.run_type,
        export_format=request.export_format,
        content=content,
        media_type=media_type,
        filename=filename,
    )
