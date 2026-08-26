"""Anti-drift contract test for NuGuard's public Pydantic API models.

This freezes the JSON schema surface exported by platform-facing public APIs so
future changes are explicit and reviewed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from nuguard.analysis.public_api import AnalysisRunRequest, AnalysisRunResult
from nuguard.behavior.public_api import BehaviorAnalysisRequest, BehaviorRunRequest
from nuguard.behavior.models import BehaviorAnalysisResult, BehaviorRunResult
from nuguard.common.discovery import TargetDiscoveryResult
from nuguard.common.streaming_models import (
    BehaviorProgressState,
    RedteamProgressState,
    StreamDeltaPayload,
    StreamEvent,
    StreamProgressPayload,
    StreamTerminalPayload,
)
from nuguard.common.target_verify_public_api import (
    TargetSessionResolveRequest,
    TargetSessionResolveResult,
    TargetVerifyCheck,
    TargetVerifyRequest,
    TargetVerifyResult,
)
from nuguard.output.public_api import (
    BehaviorReportRenderRequest,
    BehaviorReportRenderResult,
    RedteamReportRenderRequest,
    RedteamReportRenderResult,
    ValidationReportExportRequest,
    ValidationReportExportResult,
    ValidationReportMetaModel,
)
from nuguard.redteam.public_api import (
    RedteamExecutionResult,
    RedteamRunRequest,
    RedteamRunResult,
)
from nuguard.sbom.public_api import (
    SbomExportRequest,
    SbomExportResult,
    SbomGenerateRequest,
    SbomGenerateResult,
    SbomParseRequest,
    SbomParseResult,
    SbomRenderRequest,
    SbomRenderResult,
)
from nuguard.sbom.toolbox.public_api import (
    ToolboxListPluginsRequest,
    ToolboxListPluginsResult,
    ToolboxRunAllRequest,
    ToolboxRunAllResult,
    ToolboxRunPluginRequest,
    ToolboxRunPluginResult,
)

_SCHEMA_FILE = Path(__file__).parent / "public_api.schema.json"

_MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    "analysis.AnalysisRunRequest": AnalysisRunRequest,
    "analysis.AnalysisRunResult": AnalysisRunResult,
    "behavior.BehaviorAnalysisRequest": BehaviorAnalysisRequest,
    "behavior.BehaviorAnalysisResult": BehaviorAnalysisResult,
    "behavior.BehaviorRunRequest": BehaviorRunRequest,
    "behavior.BehaviorRunResult": BehaviorRunResult,
    "common.discovery.TargetDiscoveryResult": TargetDiscoveryResult,
    "common.streaming.BehaviorProgressState": BehaviorProgressState,
    "common.streaming.RedteamProgressState": RedteamProgressState,
    "common.streaming.StreamDeltaPayload": StreamDeltaPayload,
    "common.streaming.StreamEvent": StreamEvent,
    "common.streaming.StreamProgressPayload": StreamProgressPayload,
    "common.streaming.StreamTerminalPayload": StreamTerminalPayload,
    "common.target_verify.TargetSessionResolveRequest": TargetSessionResolveRequest,
    "common.target_verify.TargetSessionResolveResult": TargetSessionResolveResult,
    "common.target_verify.TargetVerifyCheck": TargetVerifyCheck,
    "common.target_verify.TargetVerifyRequest": TargetVerifyRequest,
    "common.target_verify.TargetVerifyResult": TargetVerifyResult,
    "output.BehaviorReportRenderRequest": BehaviorReportRenderRequest,
    "output.BehaviorReportRenderResult": BehaviorReportRenderResult,
    "output.RedteamReportRenderRequest": RedteamReportRenderRequest,
    "output.RedteamReportRenderResult": RedteamReportRenderResult,
    "output.ValidationReportExportRequest": ValidationReportExportRequest,
    "output.ValidationReportExportResult": ValidationReportExportResult,
    "output.ValidationReportMetaModel": ValidationReportMetaModel,
    "redteam.RedteamExecutionResult": RedteamExecutionResult,
    "redteam.RedteamRunRequest": RedteamRunRequest,
    "redteam.RedteamRunResult": RedteamRunResult,
    "sbom.SbomExportRequest": SbomExportRequest,
    "sbom.SbomExportResult": SbomExportResult,
    "sbom.SbomGenerateRequest": SbomGenerateRequest,
    "sbom.SbomGenerateResult": SbomGenerateResult,
    "sbom.SbomParseRequest": SbomParseRequest,
    "sbom.SbomParseResult": SbomParseResult,
    "sbom.SbomRenderRequest": SbomRenderRequest,
    "sbom.SbomRenderResult": SbomRenderResult,
    "sbom.toolbox.ToolboxListPluginsRequest": ToolboxListPluginsRequest,
    "sbom.toolbox.ToolboxListPluginsResult": ToolboxListPluginsResult,
    "sbom.toolbox.ToolboxRunAllRequest": ToolboxRunAllRequest,
    "sbom.toolbox.ToolboxRunAllResult": ToolboxRunAllResult,
    "sbom.toolbox.ToolboxRunPluginRequest": ToolboxRunPluginRequest,
    "sbom.toolbox.ToolboxRunPluginResult": ToolboxRunPluginResult,
}


def _live_schema_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in sorted(_MODEL_REGISTRY):
        snapshot[key] = _MODEL_REGISTRY[key].model_json_schema()
    return snapshot


def test_committed_public_api_schema_matches_models() -> None:
    """Committed public API schema must match current Pydantic model schemas."""
    assert _SCHEMA_FILE.exists(), f"Schema file not found: {_SCHEMA_FILE}"

    committed = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
    live = _live_schema_snapshot()

    assert committed == live, (
        "public_api.schema.json is out of sync with public Pydantic API models. "
        "Regenerate with: uv run python -c \"import json; "
        "from tests.contracts.test_public_api_schema_contract import _live_schema_snapshot; "
        "open('tests/contracts/public_api.schema.json','w',encoding='utf-8').write("
        "json.dumps(_live_schema_snapshot(), indent=2) + '\\n')\""
    )
