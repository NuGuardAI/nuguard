# NuGuard Python Library Reference

NuGuard exposes its three dynamic-analysis capabilities — red-teaming, behavioral testing, and static analysis — as a set of `async` Python functions that take a JSON-safe Pydantic *request* model and return a JSON-safe Pydantic *result* model. The CLI is itself a caller of these same functions, so there is no drift between what `nuguard redteam` / `nuguard behavior` / `nuguard analyze` do and what an embedding application gets by calling this interface directly.

```bash
pip install nuguard
```

No extras are required for the functions covered on this page.

## The pattern

Each of the three modules below follows the same structural convention:

- A `*Request` Pydantic model holding JSON-safe run configuration — scalars, enums, small nested config objects.
- One or more `async def` entry-point functions, called as `fn(request, *, sbom=..., policy=..., llm_client=..., ...)`.
- A `*Result` Pydantic model returned on success.

Large domain objects (the AI-SBOM, the parsed policy) and live/stateful collaborators (the LLM client) are deliberately kept out of the request model and passed as separate keyword arguments instead, since they're either too large for a clean JSON payload or aren't JSON-serializable at all.

**This is the structural shape only** — the exact keyword arguments differ between the three functions (for example, red-team's `sbom` argument is required while behavior's is optional, and red-team splits its LLM client into two roles where behavior and analysis use one). Each section below shows the real, complete signature.

**Error handling**: these functions are not best-effort probes — real exceptions (`TargetUnavailableError`, auth failures, etc.) propagate to the caller. The one deliberate exception is remediation-plan synthesis, which every one of these functions runs internally and which is explicitly best-effort: failures there are logged and swallowed, and `result.remediation_plan` is simply empty rather than raising.

## Prerequisites

### AI-SBOM

Generate one from source, or load an existing file:

```python
from pathlib import Path
from nuguard.sbom.generator import SbomGenerator
from nuguard.sbom.serializer import AiSbomSerializer

# Generate from source
sbom = SbomGenerator().from_path(Path("./my_app"))

# ...or load a previously generated one
sbom = AiSbomSerializer.from_json(Path("app.sbom.json").read_text())
```

`SbomGenerator` is synchronous — no `await` needed.

### Cognitive Policy

```python
from pathlib import Path
from nuguard.policy.loader import ensure_policy_controls

policy, controls = await ensure_policy_controls(Path("cognitive-policy.md"))
```

Returns a `CognitivePolicy` and a compiled `list[PolicyControl]` (both from `nuguard.models.policy`). Loads the compiled `.json` sidecar if one already exists next to the `.md` file; otherwise compiles it and writes the sidecar for next time. Pass `use_llm=True` and an `llm_client` to allow LLM-assisted compilation when the sidecar doesn't exist yet.

### LLM client

```python
from nuguard.common.llm_client import LLMClient

llm_client = LLMClient(model="azure/gpt-5.4-mini", api_key="...")
```

Read the API key from an environment variable rather than hardcoding it in application code. **If `api_key` is `None`, `LLMClient` does not raise — it silently returns canned placeholder responses.** Any capability that depends on LLM output (attack-payload generation, response judging, remediation text) will run to completion but produce mock content if the key is missing, so verify the client is configured before relying on its output.

## Red-team

```python
from nuguard.redteam.public_api import RedteamRunRequest, run_redteam

request = RedteamRunRequest(
    target_url="https://my-app.example.com",
    profile="ci",  # "ci" (fast, high-signal) or "full" (comprehensive)
)

result = await run_redteam(
    request,
    sbom=sbom,                        # required
    policy=policy,                    # optional
    policy_controls=controls,         # optional
    redteam_llm=llm_client,           # optional — attack-payload generation
    eval_llm=llm_client,              # optional — response judging
    remediation_llm_client=llm_client,  # optional — falls back to eval_llm if omitted
)

print(result.scan_outcome)   # "critical_findings" | "high_findings" | "findings" |
                              # "aborted_target_unavailable" | "inconclusive_target_errors" | "no_findings"
for finding in result.findings:
    print(finding.severity, finding.title, finding.remediation)
```

`sbom` is the only required keyword argument. Note the LLM client is split into two roles (`redteam_llm` for generating attack payloads, `eval_llm` for judging responses) — they can be the same `LLMClient` instance, as above, or different ones.

`RedteamRunRequest` has many optional fields beyond `target_url`/`profile` — scenario filtering (`scenario_filter: list[str]`), auth (`auth_config: AuthConfig`), canary tracking (`canary_config: CanaryConfig`), finding-trigger tuning (`finding_triggers: RedteamFindingTriggers`), guided-conversation controls, and more — all with defaults matching `nuguard redteam`'s own CLI defaults.

`RedteamRunResult` includes `findings: list[Finding]`, `remediation_plan: list[RemediationArtefact]`, `scan_outcome`, `token_usage`, `scenario_records`, `llm_executive_summary`, `llm_coding_brief`, and coverage/health-check details. Like remediation synthesis, `llm_coding_brief` generation (only attempted when `eval_llm` and `findings` are both present) is independently best-effort — a failure there is logged and leaves the field `None` rather than raising.

## Behavior

```python
from nuguard.behavior.public_api import BehaviorAnalysisRequest, analyze_behavior
from nuguard.config import BehaviorConfig

config = BehaviorConfig(target="https://my-app.example.com")

request = BehaviorAnalysisRequest(
    config=config,
    mode="static+dynamic",  # "static" | "dynamic" | "static+dynamic"
)

result = await analyze_behavior(
    request,
    sbom=sbom,                          # optional
    policy=policy,                      # optional
    controls=controls,                  # optional
    llm_client=llm_client,              # optional
    remediation_llm_client=llm_client,  # optional
)

for finding in result.findings:
    print(finding.severity, finding.title, finding.remediation)
```

`BehaviorConfig` fields are all technically optional (they default to empty). `target` is the URL of the running application being tested. The CLI validates it upfront and errors clearly if it's missing for `dynamic`/`static+dynamic` mode — but `analyze_behavior()` itself does not raise if `target` is empty: it logs a warning and silently skips the entire dynamic portion, returning a result with zero dynamic findings rather than failing. A caller invoking this directly should check `target` themselves before calling, rather than relying on an exception to catch the mistake.

A second, lower-level entry point is available for running a specific, caller-supplied list of scenarios rather than the full static+dynamic pipeline:

```python
from nuguard.behavior.public_api import BehaviorRunRequest, run_behavior_scenarios
from nuguard.behavior.models import BehaviorScenario, BehaviorScenarioType

scenarios = [
    BehaviorScenario(
        scenario_type=BehaviorScenarioType.INTENT_HAPPY_PATH,
        name="check-balance",
        messages=["What's my account balance?"],
    ),
]

result = await run_behavior_scenarios(
    BehaviorRunRequest(config=config, scenarios=scenarios),
    sbom=sbom, policy=policy, llm_client=llm_client,
)
```

## Static analysis

```python
from nuguard.analysis.public_api import AnalysisRunRequest, run_analysis

request = AnalysisRunRequest(min_severity="medium")

result = await run_analysis(
    request,
    sbom=sbom,               # required
    policy=policy,           # optional
    llm_client=llm_client,   # optional — used for remediation-text enrichment only
)

for finding in result.findings:
    print(finding.severity, finding.title, finding.remediation)
```

`sbom` is required. Unlike red-team and behavior, this entry point does not take a `controls` argument — only `policy`. `AnalysisRunRequest` also controls which detectors and external scanners run (`enable_atlas`, `enable_osv`, `enable_grype`, `enable_checkov`, `enable_trivy`, `enable_semgrep`, `enable_supply_chain`), each on by default.

`AnalysisRunResult` includes `findings`, `remediation_plan`, `tool_status` (per-scanner pass/fail), `nga_audit`, `sc_audit`, and `token_usage`.
