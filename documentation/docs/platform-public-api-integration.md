# NuGuard Platform Integration Guide (Public Pydantic APIs)

## Purpose

This guide is the single integration reference for NuGuard platform callers that must use stable, public Pydantic contracts instead of internal classes and direct internal module calls.

It covers:
- Public async entrypoints to call
- Request and response models to use
- SBOM generation/serialization/toolbox APIs
- Streaming API contracts
- Report and target verification APIs
- Migration steps from internal imports/calls

## Source of truth

Use these files as the canonical public API surface:
- `nuguard/analysis/public_api.py`
- `nuguard/behavior/public_api.py`
- `nuguard/redteam/public_api.py`
- `nuguard/sbom/public_api.py`
- `nuguard/sbom/toolbox/public_api.py`
- `nuguard/output/public_api.py`
- `nuguard/common/target_verify_public_api.py`
- `nuguard/common/streaming_models.py`
- `nuguard/common/stream_runtime.py`

Schema lock for platform-facing models:
- `tests/contracts/public_api.schema.json`
- `tests/contracts/test_public_api_schema_contract.py`

## Public API module map

| Domain | Public module | Entry points |
|---|---|---|
| Static analysis | `nuguard.analysis.public_api` | `run_analysis` |
| Behavior | `nuguard.behavior.public_api` | `analyze_behavior`, `run_behavior_scenarios`, `discover_behavior_profile`, `analyze_behavior_stream`, `analyze_behavior_static` |
| Redteam (v1) | `nuguard.redteam.public_api` | `run_redteam`, `run_redteam_stream` |
| SBOM generation/serialization | `nuguard.sbom.public_api` | `generate_sbom`, `parse_sbom_json`, `render_sbom`, `export_sbom` |
| SBOM toolbox | `nuguard.sbom.toolbox.public_api` | `list_toolbox_plugins`, `run_toolbox_plugin`, `run_toolbox_all` |
| Report rendering/export | `nuguard.output.public_api` | `render_redteam_report`, `render_behavior_report`, `export_validation_report` |
| Target verification/session | `nuguard.common.target_verify_public_api` | `verify_target`, `resolve_target_session_public` |
| Stream contracts | `nuguard.common.streaming_models` | `StreamEvent`, `StreamProgressPayload`, `StreamDeltaPayload`, `StreamTerminalPayload`, reducers |

## Request and response contracts

### Analysis

Module: `nuguard.analysis.public_api`

Request model:
- `AnalysisRunRequest`

Response model:
- `AnalysisRunResult`

Entry point:
- `await run_analysis(request, sbom=..., policy=..., llm_client=...)`

### Behavior

Module: `nuguard.behavior.public_api`

Request models:
- `BehaviorAnalysisRequest`
- `BehaviorRunRequest`

Response models:
- `BehaviorAnalysisResult`
- `BehaviorRunResult`

Entry points:
- `await analyze_behavior(request, sbom=..., policy=..., controls=..., llm_client=...)`
- `await run_behavior_scenarios(request, sbom=..., policy=..., intent=..., llm_client=...)`
- `await discover_behavior_profile(config, sbom=..., policy=..., intent=..., llm_client=...)`
- `await analyze_behavior_stream(request, ...)` (returns `StreamRunHandle[BehaviorRunResult]`)

### Redteam (v1 engine)

Module: `nuguard.redteam.public_api`

Request model:
- `RedteamRunRequest`

Response models:
- `RedteamRunResult`
- `RedteamExecutionResult` (stream final result type)

Entry points:
- `await run_redteam(request, sbom=..., policy=..., redteam_llm=..., eval_llm=...)`
- `await run_redteam_stream(request, sbom=..., ...)` (returns `StreamRunHandle[RedteamExecutionResult]`)

Note:
- This public API wraps redteam v1 orchestration. v2 is out of scope for this contract.

### SBOM generation/serialization contracts

Module: `nuguard.sbom.public_api`

Request models:
- `SbomGenerateRequest`
- `SbomParseRequest`
- `SbomRenderRequest`
- `SbomExportRequest`

Response models:
- `SbomGenerateResult`
- `SbomParseResult`
- `SbomRenderResult`
- `SbomExportResult`

Entry points:
- `await generate_sbom(request)`
- `await parse_sbom_json(request)`
- `await render_sbom(request, sbom=...)`
- `await export_sbom(request, sbom=...)`

Supported render/export formats:
- `json`
- `cyclonedx`
- `cyclonedx-ext`
- `markdown`

### SBOM toolbox contracts

Module: `nuguard.sbom.toolbox.public_api`

Request models:
- `ToolboxListPluginsRequest`
- `ToolboxRunPluginRequest`
- `ToolboxRunAllRequest`

Response models:
- `ToolboxListPluginsResult`
- `ToolboxRunPluginResult`
- `ToolboxRunAllResult`

Entry points:
- `await list_toolbox_plugins(request)`
- `await run_toolbox_plugin(request, sbom=...)`
- `await run_toolbox_all(request, sbom=...)`

### Report/export contracts

Module: `nuguard.output.public_api`

Models:
- `ValidationReportMetaModel`
- `RedteamReportRenderRequest`, `RedteamReportRenderResult`
- `BehaviorReportRenderRequest`, `BehaviorReportRenderResult`
- `ValidationReportExportRequest`, `ValidationReportExportResult`

Entry points:
- `await render_redteam_report(request, run_result=...)`
- `await render_behavior_report(request, run_result=...)`
- `await export_validation_report(request, redteam_run_result=..., behavior_run_result=...)`

### Target verify/session contracts

Module: `nuguard.common.target_verify_public_api`

Models:
- `TargetVerifyRequest`, `TargetVerifyCheck`, `TargetVerifyResult`
- `TargetSessionResolveRequest`, `TargetSessionResolveResult`
- Literal types: `TargetVerifyStatus`, `EndpointSource`

Entry points:
- `await verify_target(request, sbom=...)`
- `await resolve_target_session_public(request, sbom=...)`

When an SBOM is supplied, `resolve_target_session_public()` resolves a static-hosting target URL to the SBOM deployment URL before endpoint probing and session bootstrap. Platform callers should pass their configured target URL and consume `effective_target_url` from the result rather than duplicate this resolution logic.

## Streaming contracts

Use shared event contracts from `nuguard.common.streaming_models`:
- `StreamEvent`
- `StreamProgressPayload`
- `StreamDeltaPayload`
- `StreamTerminalPayload`
- `BehaviorProgressState`, `RedteamProgressState`
- Reducers: `apply_event_to_behavior_state`, `apply_event_to_redteam_state`

Handle contract from `nuguard.common.stream_runtime`:
- `StreamRunHandle[T]` with stream iterator, final result retrieval, non-blocking `cancel()`, and `await wait_closed(timeout=...)`.

Redteam and behavior streams emit typed events while scenarios execute:
- `scenario_plan_ready` establishes the initial scenario total and can revise it upward when redteam adds an escalation pass.
- `scenario_started` identifies the scenario being executed.
- `scenario_progress` identifies the completed scenario and reports monotonic completion counts.
- `findings_delta` emits redteam findings and behavior turn reports as scenarios complete.
- `completed` or `failed` is emitted once as the terminal event.

`StreamProgressPayload` includes `scenario_id`, `scenario_title`, `scenario_status`, `current_scenario_type`, and progress counts. With concurrent execution, event order reflects runtime completion order rather than planned scenario order. Apply every event with the matching shared reducer; the final result remains authoritative if scan-level aggregation refines findings.

Call `handle.cancel()` to request cancellation. A cancelled stream emits `failed` with `failure_stage="cancelled"`; `await handle.final_result()` raises `asyncio.CancelledError`. Use `await handle.wait_closed(timeout=...)` to bound only the caller's wait; a timeout does not cancel the underlying scan.

## Migration map: internal calls to public contracts

| Existing internal usage pattern | Replace with public contract |
|---|---|
| Constructing `StaticAnalyzer` directly and calling `.analyze()` | Build `AnalysisRunRequest` and call `run_analysis` |
| Constructing `BehaviorAnalyzer` directly and calling `.analyze()` | Build `BehaviorAnalysisRequest` and call `analyze_behavior` |
| Constructing `BehaviorRunner` directly for external integration orchestration | Build `BehaviorRunRequest` and call `run_behavior_scenarios` |
| Calling redteam orchestrator internals from platform code | Build `RedteamRunRequest` and call `run_redteam` |
| Calling `SbomGenerator` or extractor internals directly from platform code | Build `SbomGenerateRequest` and call `generate_sbom` |
| Parsing/rendering SBOMs with direct serializer internals | Use `parse_sbom_json`, `render_sbom`, and `export_sbom` |
| Running toolbox plugins through CLI wrappers | Use `list_toolbox_plugins`, `run_toolbox_plugin`, or `run_toolbox_all` |
| Building custom report payloads via internal report modules | Use `render_redteam_report`, `render_behavior_report`, or `export_validation_report` |
| Calling endpoint probe/session resolver internals directly | Use `verify_target` and `resolve_target_session_public` |
| Custom ad hoc stream envelopes | Use `StreamEvent` and the shared payload models/reducers |

## Platform migration steps

1. Inventory current platform imports
- Find and replace imports from internal runner/orchestrator/report modules in platform code.

2. Introduce request-builder adapters
- Convert platform input DTOs into:
  - `AnalysisRunRequest`
  - `BehaviorAnalysisRequest` or `BehaviorRunRequest`
  - `RedteamRunRequest`
    - `SbomGenerateRequest`, `SbomRenderRequest`, `SbomExportRequest`
    - `ToolboxRunPluginRequest` / `ToolboxRunAllRequest`
  - report and target verify request models

3. Replace execution calls
- Move platform execution to public async entrypoints only.
- Keep `sbom`, `policy`, and LLM clients as explicit keyword args where required by each API.

4. Replace report generation glue
- Stop constructing report JSON/Markdown from internal modules.
- Use `nuguard.output.public_api` wrappers.

5. Adopt stream handle contract
- For progressive UI updates, call stream APIs and consume `StreamEvent`.
- Use shared reducers to build platform-side progress state.

6. Add schema drift gate in platform CI
- Vendor or fetch `tests/contracts/public_api.schema.json` and compare against expected integration assumptions.
- Track NuGuard upgrades with schema diff review.

## Minimal usage examples

### Redteam run

```python
from nuguard.redteam.public_api import RedteamRunRequest, run_redteam

request = RedteamRunRequest(
    target_url="https://example-app/api",
    profile="ci",
    chat_path="/chat",
)

result = await run_redteam(
    request,
    sbom=sbom_doc,
    policy=policy_doc,
    redteam_llm=redteam_llm_client,
    eval_llm=eval_llm_client,
)
```

### Behavior analysis

```python
from nuguard.behavior.public_api import BehaviorAnalysisRequest, analyze_behavior

request = BehaviorAnalysisRequest(config=behavior_config, mode="static+dynamic")
result = await analyze_behavior(
    request,
    sbom=sbom_doc,
    policy=policy_doc,
    controls=policy_controls,
    llm_client=behavior_llm_client,
)
```

### Render redteam report

```python
from nuguard.output.public_api import RedteamReportRenderRequest, render_redteam_report

request = RedteamReportRenderRequest(
    run_id="platform-run-123",
    include_markdown=True,
    include_json_summary=True,
)

rendered = await render_redteam_report(request, run_result=redteam_result)
```

### Verify target

```python
from nuguard.common.auth import LoginFlowConfig
from nuguard.common.target_verify_public_api import TargetVerifyRequest, verify_target

request = TargetVerifyRequest(
    target_url="https://example-app",
    chat_path="/chat",
    auth_type="login_flow",
    login_flow=LoginFlowConfig(
        endpoint="/api/auth/login",
        payload={"username": "${APP_USERNAME}", "password": "${APP_PASSWORD}"},
        token_response_key="access_token",
    ),
)

verify_result = await verify_target(request, sbom=sbom_doc)
```

### Generate and export SBOM

```python
from nuguard.sbom.public_api import SbomExportRequest, SbomGenerateRequest, export_sbom, generate_sbom

generated = await generate_sbom(
    SbomGenerateRequest(source_path="./app")
)

exported = await export_sbom(
    SbomExportRequest(format="cyclonedx", output_path="./reports/app.cdx.json"),
    sbom=generated.sbom,
)
```

### Run toolbox plugin

```python
from nuguard.sbom.toolbox.public_api import ToolboxRunPluginRequest, run_toolbox_plugin

result = await run_toolbox_plugin(
    ToolboxRunPluginRequest(plugin_name="dependency_analyze"),
    sbom=generated.sbom,
)
```

## Contract governance

When public Pydantic models change intentionally:
1. Regenerate `tests/contracts/public_api.schema.json`
2. Ensure `tests/contracts/test_public_api_schema_contract.py` passes
3. Review schema diff as a platform-impacting change

Reference command:

```bash
uv run pytest tests/contracts/test_public_api_schema_contract.py -q
```

## Current known gaps to account for in platform code

- There is no single generated external docs site page yet that auto-docs all request/response fields.
- The schema snapshot is the stable machine-readable source for exact field shapes.
- Redteam and behavior report identity fields are currently not fully harmonized for cross-artifact correlation; platform should rely on explicit run context until identifier harmonization lands.
