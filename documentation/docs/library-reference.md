# NuGuard Python Library Reference

NuGuard exposes its platform-facing interfaces as async Python functions with JSON-safe Pydantic request/response models. This includes:

- SBOM generation/serialization APIs
- SBOM toolbox APIs
- Static analysis APIs
- Behavior APIs
- Redteam APIs
- Streaming contracts used by streaming APIs
- Target verification and target-session resolution APIs
- Report rendering/export APIs

```bash
pip install nuguard
```

## Core pattern

Most public entry points follow this shape:

- A `*Request` Pydantic model for JSON-safe run settings.
- One async entry point function that takes the request, plus domain objects as keyword args.
- A `*Result` Pydantic model.

Large domain objects such as SBOM and policy, and stateful collaborators like LLM clients, stay out of request models by design and are passed separately.

Error handling behavior is intentional:

- Full runs (analysis, behavior, redteam, verify) propagate meaningful runtime exceptions.
- Remediation-plan synthesis is best-effort and does not fail the whole run.

## Prerequisites

### AI-SBOM

```python
from pathlib import Path
from nuguard.sbom.generator import SbomGenerator
from nuguard.sbom.serializer import AiSbomSerializer

sbom = SbomGenerator().from_path(Path("./my_app"))
# or
sbom = AiSbomSerializer.from_json(Path("app.sbom.json").read_text())
```

### Cognitive policy

```python
from pathlib import Path
from nuguard.policy.loader import ensure_policy_controls

policy, controls = await ensure_policy_controls(Path("cognitive-policy.md"))
```

### LLM client

```python
from nuguard.common.llm_client import LLMClient

llm_client = LLMClient(model="azure/gpt-5.4-mini", api_key="...")
```

If no API key is provided, the client may return placeholder output. Validate client configuration before relying on LLM-dependent results.

## SBOM generation and serialization APIs

Module: `nuguard.sbom.public_api`

- `SbomGenerateRequest`
- `SbomGenerateResult`
- `SbomParseRequest`
- `SbomParseResult`
- `SbomRenderRequest`
- `SbomRenderResult`
- `SbomExportRequest`
- `SbomExportResult`
- `generate_sbom(request)`
- `parse_sbom_json(request)`
- `render_sbom(request, *, sbom)`
- `export_sbom(request, *, sbom)`

`SbomRenderRequest.format` supports:

- `json`
- `cyclonedx`
- `cyclonedx-ext`
- `markdown`

Example:

```python
from nuguard.sbom.public_api import SbomGenerateRequest, SbomRenderRequest, generate_sbom, render_sbom

generated = await generate_sbom(SbomGenerateRequest(source_path="./my_app"))
rendered = await render_sbom(SbomRenderRequest(format="json"), sbom=generated.sbom)
```

## SBOM toolbox APIs

Module: `nuguard.sbom.toolbox.public_api`

- `ToolboxListPluginsRequest`
- `ToolboxListPluginsResult`
- `ToolboxRunPluginRequest`
- `ToolboxRunPluginResult`
- `ToolboxRunAllRequest`
- `ToolboxRunAllResult`
- `list_toolbox_plugins(request)`
- `run_toolbox_plugin(request, *, sbom)`
- `run_toolbox_all(request, *, sbom)`

Example:

```python
from nuguard.sbom.toolbox.public_api import ToolboxRunPluginRequest, run_toolbox_plugin

result = await run_toolbox_plugin(
    ToolboxRunPluginRequest(plugin_name="dependency_analyze"),
    sbom=generated.sbom,
)
```

## Static analysis API

Module: `nuguard.analysis.public_api`

- `AnalysisRunRequest`
- `AnalysisRunResult`
- `run_analysis(request, *, sbom, policy=None, llm_client=None)`

Example:

```python
from nuguard.analysis.public_api import AnalysisRunRequest, run_analysis

result = await run_analysis(
    AnalysisRunRequest(min_severity="medium"),
    sbom=sbom,
    policy=policy,
    llm_client=llm_client,
)
```

`AnalysisRunResult` includes `findings`, `tool_status`, `nga_audit`, `sc_audit`, `token_usage`, and `remediation_plan`.

## Behavior APIs

Module: `nuguard.behavior.public_api`

- `BehaviorAnalysisRequest`
- `BehaviorRunRequest`
- `analyze_behavior(request, *, sbom=None, policy=None, controls=None, llm_client=None, remediation_llm_client=None)`
- `run_behavior_scenarios(request, *, sbom=None, policy=None, intent=None, llm_client=None, remediation_llm_client=None, judge_cache=None)`
- `discover_behavior_profile(config, *, sbom=None, policy=None, intent=None, llm_client=None)`
- `analyze_behavior_stream(...)`

Example (full analysis):

```python
from nuguard.behavior.public_api import BehaviorAnalysisRequest, analyze_behavior
from nuguard.config import BehaviorConfig

result = await analyze_behavior(
    BehaviorAnalysisRequest(
        config=BehaviorConfig(target="https://my-app.example.com"),
        mode="static+dynamic",
    ),
    sbom=sbom,
    policy=policy,
    controls=controls,
    llm_client=llm_client,
)
```

Example (scenario-level run):

```python
from nuguard.behavior.models import BehaviorScenario, BehaviorScenarioType
from nuguard.behavior.public_api import BehaviorRunRequest, run_behavior_scenarios
from nuguard.config import BehaviorConfig

result = await run_behavior_scenarios(
    BehaviorRunRequest(
        config=BehaviorConfig(target="https://my-app.example.com"),
        scenarios=[
            BehaviorScenario(
                scenario_type=BehaviorScenarioType.INTENT_HAPPY_PATH,
                name="check-balance",
                messages=["What is my account balance?"],
            )
        ],
    ),
    sbom=sbom,
    policy=policy,
    llm_client=llm_client,
)
```

## Redteam APIs

Module: `nuguard.redteam.public_api`

- `RedteamRunRequest`
- `RedteamRunResult`
- `RedteamExecutionResult`
- `run_redteam(request, *, sbom, policy=None, policy_controls=None, redteam_llm=None, eval_llm=None, remediation_llm_client=None, app_log_reader=None, catalog=None, log_path=None, prompt_cache_dir=None)`
- `run_redteam_stream(...)`

Example:

```python
from nuguard.redteam.public_api import RedteamRunRequest, run_redteam

result = await run_redteam(
    RedteamRunRequest(target_url="https://my-app.example.com", profile="ci"),
    sbom=sbom,
    policy=policy,
    policy_controls=controls,
    redteam_llm=llm_client,
    eval_llm=llm_client,
)
```

`RedteamRunResult.scan_outcome` is one of:

- `critical_findings`
- `high_findings`
- `findings`
- `aborted_target_unavailable`
- `inconclusive_target_errors`
- `no_findings`

## Streaming contracts

Module: `nuguard.common.streaming_models`

- `StreamEvent`
- `StreamProgressPayload`
- `StreamDeltaPayload`
- `StreamTerminalPayload`
- `RedteamProgressState`
- `BehaviorProgressState`

Behavior and redteam streaming APIs return a typed `StreamRunHandle` that emits `StreamEvent` envelopes and resolves to the final result model.

## Target verify and session APIs

Module: `nuguard.common.target_verify_public_api`

- `TargetVerifyRequest`
- `TargetVerifyCheck`
- `TargetVerifyResult`
- `TargetSessionResolveRequest`
- `TargetSessionResolveResult`
- `TargetVerifyStatus` (`ok`, `auth_failed`, `target_unavailable`, `skipped`)
- `verify_target(request, *, sbom=None)`
- `resolve_target_session_public(request, *, sbom=None)`

These APIs handle endpoint selection (config/SBOM/probe/default), auth bootstrapping, and target health checks before scans.

## Output report/export APIs

Module: `nuguard.output.public_api`

- `ValidationReportMetaModel`
- `RedteamReportRenderRequest`
- `RedteamReportRenderResult`
- `BehaviorReportRenderRequest`
- `BehaviorReportRenderResult`
- `ValidationReportExportRequest`
- `ValidationReportExportResult`
- `render_redteam_report(request, *, run_result)`
- `render_behavior_report(request, *, run_result)`
- `export_validation_report(request, *, redteam_run_result=None, behavior_run_result=None)`

These provide stable in-process report rendering/export contracts without requiring CLI execution.

## Schema contract and anti-drift

Public Pydantic surface is frozen by the contract test in `tests/contracts/test_public_api_schema_contract.py` against `tests/contracts/public_api.schema.json`.

When intentional public-model changes are made, regenerate the snapshot:

```bash
uv run python -c "import json; from tests.contracts.test_public_api_schema_contract import _live_schema_snapshot; open('tests/contracts/public_api.schema.json','w',encoding='utf-8').write(json.dumps(_live_schema_snapshot(), indent=2) + '\\n')"
```

Then run:

```bash
uv run pytest tests/contracts/test_public_api_schema_contract.py -v
```
