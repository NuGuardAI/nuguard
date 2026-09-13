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
- `nuguard/policy/public_api.py`
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
| Behavior | `nuguard.behavior.public_api` | `analyze_behavior`, `analyze_behavior_analysis_stream`, `run_behavior_scenarios`, `discover_behavior_profile`, `analyze_behavior_stream`, `analyze_behavior_static` |
| Redteam (v1) | `nuguard.redteam.public_api` | `run_redteam`, `run_redteam_stream` |
| Cognitive policy | `nuguard.policy.public_api` | `parse_cognitive_policy` |
| SBOM generation/serialization | `nuguard.sbom.public_api` | `generate_sbom`, `parse_sbom_json`, `render_sbom`, `export_sbom`, `enrich_sbom` |
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

`AnalysisRunResult.remediation_plan` is synthesized from findings against the (required) `sbom`
argument. It is `[]` only when there are no findings to synthesize against; `llm_client` is
optional and only upgrades template-based remediation text with LLM-authored content when
supplied. An actual synthesis failure (e.g. a broken `llm_client`) propagates out of
`run_analysis` as a visible exception rather than degrading to an empty plan.

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
- `await analyze_behavior_analysis_stream(request, ...)` (returns `StreamRunHandle[BehaviorAnalysisResult]`)

The analysis stream invokes the same complete orchestration as `analyze_behavior` exactly once;
callers do not need to plan scenarios or combine internal results.

`BehaviorRunResult.remediation_plan` (from `run_behavior_scenarios`) follows the same contract as
analysis: `[]` only when there are no findings, otherwise a synthesis failure propagates as a
visible exception.

**Resume (`BehaviorConfig.resume`).** Both `run_behavior_scenarios` and `BehaviorRunner.run()`
support resuming a previously-aborted run: set `resume` on the `BehaviorConfig` passed via
`BehaviorRunRequest.config` to the path of a checkpoint file. On a mid-run failure with at least
one completed scenario, `BehaviorRunner.run()` raises
`nuguard.common.run_checkpoint.PartialRunError` (chained from the original exception) and writes a
checkpoint under `config.prompt_cache_dir`; pass that path back as `resume` to skip
already-completed scenarios. Unlike redteam (below), `run_behavior_scenarios` does not currently
populate `PartialRunError.partial_result` — platform callers catching this error for behavior runs
should read `exc.checkpoint_path` / `exc.cause` rather than expect a ready-made `BehaviorRunResult`.
A checkpoint is fingerprinted against the sbom/policy it was created with; a mismatched resume
raises `CheckpointMismatchError` instead of silently combining incompatible results.

### Redteam (v1 engine)

Module: `nuguard.redteam.public_api`

Request model:
- `RedteamRunRequest`
- `RedteamAuthConfig`, with `RedteamLoginFlowConfig` for login-based authentication

Response models:
- `RedteamRunResult`
- `RedteamExecutionResult` (stream final result type)

Entry points:
- `await run_redteam(request, sbom=..., policy=..., redteam_llm=..., eval_llm=...)`
- `await run_redteam_stream(request, sbom=..., ...)` (returns `StreamRunHandle[RedteamExecutionResult]`)

Note:
- This public API wraps redteam v1 orchestration. v2 is out of scope for this contract.
- `RedteamRunRequest.auth_config` uses secret-safe public `RedteamAuthConfig` and also accepts
    internal `AuthConfig` and application `AppAuthConfig` values for compatibility. Bearer,
    API-key, basic, login-flow, cookie-file, and no-auth variants remain JSON-safe, but ordinary
    request dumps redact credentials and are diagnostic rather than replayable transport. Resolve
    platform secret references before constructing the request; runtime values are revealed only
    when the public wrapper invokes the internal orchestrator.
- `RedteamRunResult.remediation_plan` follows the same error contract as analysis/behavior: `[]`
    only when there's no sbom/findings, otherwise a synthesis failure propagates.

**Resume (`RedteamRunRequest.resume_from`).** Set `resume_from` to the path of a checkpoint file
from a previous aborted run; already-completed scenarios are skipped and the final result combines
checkpointed and newly-run scenarios/findings. On a mid-run failure with at least one completed
scenario, `run_redteam` raises `nuguard.common.run_checkpoint.PartialRunError` (chained from the
original exception) after writing a checkpoint under `prompt_cache_dir` (or `resume_from`'s parent
directory when resuming). Unlike a bare exception, this error carries everything needed to persist
and continue the run:
- `exc.checkpoint_path` — pass back as `resume_from` on the next call.
- `exc.partial_result` — a fully-formed `RedteamRunResult` (JSON-safe, `scan_outcome` reflecting
    the abort reason) built from progress made so far, ready to persist/report as-is.
- `exc.cause` / `exc.partial_payload` — the original exception and raw checkpoint payload, for
    diagnostics.

A checkpoint is fingerprinted against the sbom/policy it was created with; resuming against
different inputs raises `CheckpointMismatchError` (also from `nuguard.common.run_checkpoint`)
rather than silently combining incompatible results. Both errors should be imported from
`nuguard.common.run_checkpoint`, not from `nuguard.redteam.public_api`.

### Cognitive policy parsing

Module: `nuguard.policy.public_api`

- Request: `CognitivePolicyParseRequest`
- Result: `CognitivePolicyParseResult`, with `CognitivePolicyParseDetail` diagnostics
- Entry point: `parse_cognitive_policy(request)`

Successful parse results can be passed directly as `policy=` to behavior and red-team entry
points. Failures contain stable sanitized details rather than source Markdown or raw validation
exceptions.

### SBOM generation/serialization contracts

Module: `nuguard.sbom.public_api`

Request models:
- `SbomGenerateRequest`
- `SbomParseRequest`
- `SbomRenderRequest`
- `SbomExportRequest`
- `SbomEnrichmentRequest`, with `SbomEnrichmentLlmConfig` and `SbomProbeAuthConfig`

Response models:
- `SbomGenerateResult`
- `SbomParseResult`
- `SbomRenderResult`
- `SbomExportResult`
- `SbomEnrichmentResult`

Entry points:
- `await generate_sbom(request)`
- `await parse_sbom_json(request)`
- `await render_sbom(request, sbom=...)`
- `await export_sbom(request, sbom=...)`
- `await enrich_sbom(request)`

Enrichment validates input and output, operates copy-on-write, and writes only when
`output_path` is explicitly supplied. The deterministic `cache_fingerprint` supports
platform-managed caching and excludes API keys and authorization values. Set the opaque
`cache_version` to invalidate cached work when secret-dependent or external state changes.
Computed and externally cached values use the same `SbomEnrichmentResult` schema.

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

WebSocket chat endpoints are handled transparently: when SBOM or live discovery identifies the
chat route as a WebSocket, `verify_target` (and the redteam/behavior request path) switches to a
persistent WebSocket connection internally instead of per-request HTTP POSTs. There is no field on
`TargetVerifyRequest` to force this — it is auto-discovery only, selected via an internal sentinel
value on the resolved payload key, not something platform callers set directly.

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
- `run_started` identifies the accepted run.
- `scenario_plan_ready` establishes the initial scenario total and can revise it upward when redteam adds an escalation pass.
- `scenario_started` identifies the scenario being executed.
- `scenario_progress` identifies the completed scenario and reports monotonic completion counts.
- `findings_delta` emits redteam findings and behavior turn reports as scenarios complete.
- `heartbeat` may be emitted during idle long-running phases.
- `completed` or `failed` is emitted once as the terminal event.

`StreamProgressPayload` includes `scenario_id`, `scenario_title`, `scenario_status`, `current_scenario_type`, and progress counts. With concurrent execution, event order reflects runtime completion order rather than planned scenario order. Apply every event with the matching shared reducer; the final result remains authoritative if scan-level aggregation refines findings.

Call `handle.cancel()` to request cancellation. A cancelled stream emits `failed` with stable
sanitized metadata and `final_result()` raises an `asyncio.CancelledError` subclass. Generic
worker failures raise `StreamExecutionError` and expose no raw exception message, target response,
or credentials. Use `await handle.wait_closed(timeout=...)` to bound only the caller's wait; a
timeout does not cancel the underlying scan. Heartbeats and progress updates may be dropped under
queue pressure, while lifecycle and terminal events are retained.

## Migration map: internal calls to public contracts

| Existing internal usage pattern | Replace with public contract |
|---|---|
| Constructing `StaticAnalyzer` directly and calling `.analyze()` | Build `AnalysisRunRequest` and call `run_analysis` |
| Constructing `BehaviorAnalyzer` directly and calling `.analyze()` | Build `BehaviorAnalysisRequest` and call `analyze_behavior` |
| Constructing `BehaviorRunner` directly for external integration orchestration | Build `BehaviorRunRequest` and call `run_behavior_scenarios` |
| Calling redteam orchestrator internals from platform code | Build `RedteamRunRequest` and call `run_redteam` |
| Calling `SbomGenerator` or extractor internals directly from platform code | Build `SbomGenerateRequest` and call `generate_sbom` |
| Calling `maybe_auto_enrich_sbom` or private credential resolvers | Build `SbomEnrichmentRequest` and call `enrich_sbom` |
| Calling `parse_policy` from an implementation module | Build `CognitivePolicyParseRequest` and call `parse_cognitive_policy` |
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

### Resume an aborted redteam run

```python
from nuguard.common.run_checkpoint import PartialRunError
from nuguard.redteam.public_api import RedteamRunRequest, run_redteam

request = RedteamRunRequest(target_url="https://example-app/api", profile="ci", chat_path="/chat")

try:
    result = await run_redteam(request, sbom=sbom_doc, policy=policy_doc)
except PartialRunError as exc:
    # exc.partial_result is a ready-to-persist RedteamRunResult; exc.checkpoint_path
    # is what to pass back as resume_from on the next attempt.
    persist_partial_report(exc.partial_result)
    retry_request = request.model_copy(update={"resume_from": str(exc.checkpoint_path)})
    result = await run_redteam(retry_request, sbom=sbom_doc, policy=policy_doc)
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
- `PartialRunError.partial_result` (checkpoint/resume flow, see Redteam and Behavior sections above) is populated by `run_redteam` but not by `run_behavior_scenarios` — behavior callers must reconstruct/report partial state from `exc.checkpoint_path` and `exc.cause` themselves. Treat this as CLI-only parity today; a library caller doing behavior resume gets less than the redteam path.
- There is no `nuguard.yaml` / request-model field to hand-configure WebSocket auth-message or completion-key framing for a non-standard WS chat protocol — it's auto-discovery only (see Target verify/session contracts above).
