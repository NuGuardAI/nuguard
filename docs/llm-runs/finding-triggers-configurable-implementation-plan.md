# Configurable Redteam Finding Triggers - Implementation Plan

## Objective
Make redteam finding trigger behavior configurable so users can control when findings are emitted, while preserving backward compatibility by default.

## Design Decisions (Locked)

1. Trigger scope applies to both execution paths:
- Standard step-based findings generated via `_build_findings`.
- Guided conversation findings generated via `_conv_to_finding` / `_run_guided_scenario`.

2. Config integration follows existing flattened config architecture:
- Keep flat `NuGuardConfig` fields as the source of truth for settings resolution.
- Parse nested YAML keys under `redteam.finding_triggers.*` and map to flat config fields.
- Environment variable overrides are supported via flat field names and existing pydantic-settings behavior.

3. Finding precedence is deterministic and non-overlapping:
- `canary_hits` first.
- `policy_violations` second.
- `critical_success_hits` third (existing fallback behavior).
- `any_inject_success` last, only when no prior trigger produced findings.

4. Disabling all triggers is allowed:
- Scan still runs.
- Findings may be empty by design.
- Emit a user-facing warning in CLI output and include trigger state in report metadata.

## Proposed Config Shape
Add a new block under `redteam`:

```yaml
redteam:
  finding_triggers:
    canary_hits: true
    policy_violations: true
    critical_success_hits: true
    any_inject_success: false
```

Internal flattened mapping in `NuGuardConfig`:
- `redteam_trigger_canary_hits: bool = True`
- `redteam_trigger_policy_violations: bool = True`
- `redteam_trigger_critical_success_hits: bool = True`
- `redteam_trigger_any_inject_success: bool = False`

## Step-by-Step Plan

1. Define trigger model and defaults
- Add a lightweight typed container in `nuguard/config.py` for internal handoff (for example `RedteamFindingTriggers`), but keep `NuGuardConfig` flattened fields as canonical.
- Include fields:
  - `canary_hits: bool = True`
  - `policy_violations: bool = True`
  - `critical_success_hits: bool = True`
  - `any_inject_success: bool = False`
- Keep defaults equivalent to current behavior.

2. Parse YAML into trigger config
- Extend YAML flattening logic in `nuguard/config.py` to map `redteam.finding_triggers.*` values into flat keys:
  - `redteam_trigger_canary_hits`
  - `redteam_trigger_policy_violations`
  - `redteam_trigger_critical_success_hits`
  - `redteam_trigger_any_inject_success`
- Ensure missing keys fall back to defaults.
- Keep handling resilient to unknown keys (ignore extras).
- Document expected env var names for each flat field.

3. Thread trigger config through CLI redteam path
- In `nuguard/cli/commands/redteam.py`, read resolved trigger config from `cfg` and construct a typed trigger object for orchestration.
- Pass trigger config into `RedteamOrchestrator` construction.
- Keep initial scope config-only (no new CLI flags required for first version).
- If all triggers are disabled, log and print an explicit warning.

4. Add orchestrator support for trigger controls
- Add a constructor argument in `nuguard/redteam/executor/orchestrator.py` for trigger settings.
- Store trigger settings on the orchestrator instance.
- Preserve defaults for backward compatibility.
- Ensure trigger settings are applied to both standard and guided execution paths.

5. Gate finding creation by trigger settings
- Update `_build_findings` in `nuguard/redteam/executor/orchestrator.py`:
  - Emit canary findings only when `canary_hits` is enabled.
  - Emit policy violation findings only when `policy_violations` is enabled.
  - Emit existing fallback/critical-hit findings only when `critical_success_hits` is enabled.
  - Emit `any_inject_success` findings only when no canary/policy/critical findings were emitted.
- Add guided-path gating in `_run_guided_scenario` or `_conv_to_finding`:
  - Respect trigger policy before returning guided findings.
  - Prevent bypass of configured trigger rules.
- Keep severity/remediation logic unchanged for this iteration.
- Prevent duplicates by enforcing trigger precedence and single fallback path.

6. Add report metadata visibility
- Extend report metadata in `nuguard/cli/report_meta.py` to include active trigger configuration (booleans only).
- Wire this metadata from `nuguard/cli/commands/redteam.py`.
- Ensure visibility across output modes:
  - JSON: include trigger block under `_meta`.
  - Markdown: include trigger line(s) in metadata header.
  - Text: include concise trigger summary in the top line.
- This improves explainability of empty/non-empty findings.

7. Add tests for trigger behavior matrix
- Add tests in `tests/redteam/` covering:
  - Default behavior unchanged.
  - Canary trigger off suppresses canary findings.
  - Policy trigger off suppresses policy findings.
  - Critical trigger off suppresses fallback findings.
  - `any_inject_success=true` can produce findings in no-canary/no-policy/no-critical cases.
- Add at least one regression test for empty findings with conservative triggers.
- Add guided execution tests:
  - Guided findings honor trigger settings.
  - Guided path does not emit findings when equivalent triggers are disabled.
- Add CI escalation tests:
  - Escalation pass still honors trigger settings.
  - No duplicate findings across main pass + escalation + fallback.
- Add all-triggers-disabled test:
  - Scan completes with empty findings and warning emitted.

8. Update docs and examples
- Update `nuguard.yaml.example` with new trigger block.
- Update `README.md` and redteam docs with conservative vs aggressive examples.
- Include guidance for when to use `any_inject_success`.
- Add explicit semantics section documenting trigger precedence and duplicate suppression.
- Add a note that all-triggers-disabled is valid but intentionally suppresses findings.

9. Validate end-to-end
- Run lint and tests.
- Run one conservative profile scan and one aggressive profile scan.
- Verify report schema stability (`_meta` + `findings`) and expected findings behavior.
- Run one guided-conversations-enabled scan to verify guided-path trigger gating.
- Verify output parity across text/json/markdown for trigger metadata visibility.

10. Rollout strategy
- Release as backward-compatible feature.
- Document in changelog as opt-in detection flexibility.
- Optionally add CLI-level finding profile toggles in a future iteration.
- Keep CLI-level profile toggles explicitly out-of-scope for this PR.

## Acceptance Criteria
- Existing users see unchanged behavior with no config updates.
- Users can opt into aggressive findings using `any_inject_success`.
- Redteam report metadata reflects active trigger settings.
- Trigger settings are enforced in both standard and guided execution paths.
- Trigger precedence is deterministic and prevents duplicate fallback findings.
- Tests cover trigger combinations, guided mode, CI escalation, and regressions.
