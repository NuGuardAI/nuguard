# Verbose Flag Specification (Cross-Command)

Status: Partially Implemented

Scope: `nuguard behavior`, `nuguard redteam`, `nuguard validate`, `nuguard policy check`

> **Status note (issue #163):** The contract in this spec is partially adopted.
> Today every scoped command accepts `--verbose/--no-verbose` with CLI flag
> > command config > default precedence (rendered as `CLI flag > command config > default`),
> persists `verbose` in report metadata via `ReportMeta`, and keeps terminal
> vs. report behavior separate. Detailed phases below (findings-invariance
> refactor, bounded diagnostics envelope, cap enforcement) are aspirational
> and not yet completed in full; this status will move to `Implemented` once
> they land.

## Purpose

Define a consistent contract for `--verbose` so users get:

- rich interactive diagnostics in terminal output
- stable machine-readable findings for CI and diffing
- optional report-depth expansion without signal drift

This spec intentionally separates **debug detail** from **security/compliance result semantics**.

## Design Principles

1. Core findings must be invariant across verbose modes.
2. Verbose primarily controls diagnostic depth, not detection logic.
3. Terminal output may be high-volume; persisted reports must be bounded and curated.
4. JSON and Markdown should expose equivalent diagnostic concepts.
5. Metadata must always declare whether verbose mode was active.

## Normative Contract

### 1) Flag Semantics

- `--verbose` means: increase observability detail.
- `--no-verbose` (where supported) means: minimize observability detail.
- Commands with config-driven verbosity should support CLI override with clear precedence:
  - CLI flag > command config > default

### 2) Findings Invariance

- The set of findings (identity, severity, status) must not change solely because `--verbose` is enabled.
- Verbose may add supporting context to a finding, but must not create or remove findings.

### 3) Report Metadata

All report formats that support metadata must include:

- `verbose: true|false`
- generation timestamp
- relevant runtime context already emitted by `ReportMeta`

### 4) Terminal vs Report Split

#### Terminal (ephemeral, high-volume)

Verbose terminal output may include:

- per-turn request/response blocks
- per-step judge/scoring details
- endpoint/bootstrap/discovery notes
- adaptation/retry traces

#### Report (durable, bounded)

Verbose reports may include:

- per-scenario summary diagnostics
- bounded trace appendices
- selected evidence excerpts

Verbose reports must not include unbounded raw logs.

## Output Format Rules

### Text

- Non-verbose: summary + findings.
- Verbose: summary + findings + bounded trace appendix.

### Markdown

- Non-verbose: human-readable summary + findings + core tables.
- Verbose: add explicit `Diagnostics` or `Scenario Traces` section with capped details.

### JSON

- Non-verbose: stable top-level findings payload.
- Verbose: add a dedicated diagnostics object, for example:
  - `diagnostics.scenario_traces`
  - `diagnostics.execution_notes`
- Keep `findings` schema stable regardless of verbose mode.

## Bounded Detail Requirements

To prevent report bloat, enforce caps for verbose-only sections:

- max scenarios included in trace appendix (or include all scenarios but capped turns)
- max turns per scenario
- max characters per request/response snippet
- max evidence lines per finding

Default cap recommendations:

- turns per scenario: 3-5
- snippet length: 500-1000 chars
- evidence lines: 3-5

## Command-Specific Guidance

### behavior

- Keep rich per-turn terminal traces under verbose.
- Set `ReportMeta.verbose` consistently.
- Add bounded diagnostics section for Markdown/JSON.
- Do not gate finding creation on verbose.

### redteam

- Keep existing strong verbose terminal tracing.
- Preserve finding semantics regardless of verbose.
- Put step-level/turn-level details into bounded report diagnostics.

### validate

- Support explicit CLI verbose override for parity.
- Keep terminal traces verbose-only.
- Keep current trace appendix pattern, but enforce caps and stable schema.

### policy check

- Treat verbose as presentation depth, not result selection.
- Do not add/remove findings based on verbose.
- If users need pass controls, expose as a separate explicit option or diagnostics subsection.

## Compatibility and Migration

1. Preserve existing top-level finding fields.
2. Add new verbose diagnostics under additive keys/sections.
3. Announce semantic change where verbose previously altered findings content.

## Testing Requirements

Add or update tests per command to verify:

1. finding invariance between verbose and non-verbose
2. `_meta.verbose` correctness
3. presence/absence of diagnostics sections by mode
4. cap enforcement in verbose report sections

Recommended test pattern:

- run same fixture input twice (`verbose=false`, `verbose=true`)
- compare normalized findings set
- assert verbose diagnostics exist only in verbose output

## Acceptance Criteria

This spec is satisfied when:

1. all four commands follow the same verbose contract
2. findings are mode-invariant
3. report metadata always reflects verbose state
4. verbose report sections are bounded and structured
5. docs clearly describe terminal-vs-report behavior

## Suggested Follow-up Work Items

1. Align command option surfaces (`--verbose/--no-verbose`) and precedence.
2. Normalize report metadata wiring across commands.
3. Refactor policy verbose path so PASS controls are not mixed into core findings.
4. Introduce shared diagnostics schema helpers for Markdown/JSON emitters.
5. Add regression tests for invariance and caps.

## Full Implementation Plan

Status: Ready for execution

### Goals

1. Enforce one cross-command verbose contract for `behavior`, `redteam`, `validate`, and `policy check`.
2. Guarantee finding invariance across verbose modes.
3. Provide bounded, structured diagnostics in Markdown and JSON.
4. Keep terminal diagnostics rich under verbose without persisting unbounded logs.

### Delivery Phases

### Phase 0 - Baseline and Inventory

Objective: capture current behavior and create a safety baseline before changing logic.

Tasks:

1. Record command option surfaces and precedence behavior for all four commands.
2. Capture golden outputs for each command with `verbose=false` and `verbose=true`.
3. Identify current report metadata paths and diagnostics wiring.
4. Document any places where verbose affects finding selection.

Outputs:

1. Baseline notes in this document (or linked issue).
2. Fixture set for A/B verbose comparison.

Exit criteria:

1. Current-state matrix exists for all commands and output formats.

### Phase 1 - CLI and Config Surface Alignment

Objective: make `--verbose` behavior and precedence consistent.

Tasks:

1. Ensure all scoped commands expose explicit verbose control.
2. Support `--no-verbose` where command style already supports dual-boolean options.
3. Enforce precedence: CLI flag > command config > default.
4. Update help text to match this spec.

Implementation notes:

1. Keep default mode non-verbose.
2. Avoid command-specific semantic meaning for verbose beyond observability depth.

Exit criteria:

1. Option and precedence behavior is consistent across all four commands.

### Phase 2 - Findings Invariance Refactor

Objective: ensure verbose cannot change finding identity, severity, or status.

Tasks:

1. Separate finding-generation logic from presentation/diagnostics branching.
2. Remove any verbose gates that include/exclude findings.
3. For `policy check`, move PASS/control-display behavior out of core findings selection and into explicit diagnostics or separate flags.
4. Add normalization helper used by tests to compare findings sets between modes.

Implementation notes:

1. Keep additive verbose context attached to finding evidence only.
2. Do not mutate finding schema by mode.

Exit criteria:

1. Verbose on/off produces equivalent normalized findings for all fixtures.

### Phase 3 - Report Metadata Normalization

Objective: consistently emit verbose state and runtime metadata.

Tasks:

1. Ensure report metadata includes `verbose: true|false` for every supported format with metadata.
2. Route all commands through shared metadata helper(s) where available.
3. Backfill missing metadata in any command-specific report emitters.

Implementation notes:

1. Keep metadata keys additive and backward compatible.
2. Reuse existing `ReportMeta` patterns rather than introducing parallel structures.

Exit criteria:

1. Metadata snapshot tests pass for all commands and output formats.

### Phase 4 - Bounded Diagnostics Schema and Emitters

Objective: add structured verbose diagnostics while keeping reports bounded.

Tasks:

1. Define shared diagnostics envelope for JSON:
  - `diagnostics.execution_notes`
  - `diagnostics.scenario_traces`
  - optional command-specific keys under `diagnostics.*`
2. Define Markdown diagnostics sections with stable headings:
  - `## Diagnostics`
  - `### Scenario Traces` (when applicable)
3. Implement caps for verbose-only details:
  - max turns per scenario
  - max snippet length
  - max evidence lines
4. Ensure truncation is explicit (for example, append `... [truncated]`).

Default caps:

1. turns per scenario: 4
2. snippet length: 800 chars
3. evidence lines: 4

Exit criteria:

1. Verbose reports include structured diagnostics.
2. Non-verbose reports omit diagnostics sections.
3. Cap enforcement tests pass.

### Phase 5 - Command-by-Command Integration

Objective: implement behavior details per command with shared rules.

`behavior`:

1. Preserve rich per-turn terminal output in verbose mode.
2. Add bounded per-scenario diagnostics in Markdown/JSON outputs.
3. Confirm findings and severity are invariant by mode.

`redteam`:

1. Preserve existing terminal trace richness in verbose mode.
2. Emit bounded step/turn traces in diagnostics appendices.
3. Confirm finding trigger behavior is invariant by mode.

`validate`:

1. Keep trace appendix pattern but enforce shared caps.
2. Ensure verbose-only sections are clearly separated from core results.
3. Confirm metadata and diagnostics parity with other commands.

`policy check`:

1. Decouple PASS/control presentation from finding selection.
2. Place extra presentation details under diagnostics.
3. Verify report semantics match non-verbose mode findings.

Exit criteria:

1. Each command satisfies the same contract with command-appropriate diagnostics content.

### Phase 6 - Test Plan (Required)

Objective: enforce invariance and bounded detail as regression protections.

Required tests per command:

1. `test_findings_invariant_verbose_toggle`
2. `test_meta_includes_verbose_flag`
3. `test_verbose_includes_diagnostics_section`
4. `test_non_verbose_omits_diagnostics_section`
5. `test_verbose_diagnostics_caps_enforced`

Cross-command tests:

1. schema stability for `findings` object between modes
2. diagnostics key presence only when verbose=true
3. markdown section heading consistency

Test method:

1. Run fixture twice (`verbose=false` and `verbose=true`).
2. Normalize and compare findings sets.
3. Assert diagnostics and truncation markers by mode.

Exit criteria:

1. New tests pass in CI with no flaky behavior.

### Phase 7 - Documentation and UX Updates

Objective: align user-facing docs and command help with final behavior.

Tasks:

1. Update CLI reference pages for all affected commands.
2. Update behavior and redteam guides with terminal-vs-report examples.
3. Add one compact before/after example per output format.
4. Add troubleshooting note for users expecting verbose to alter findings.

Exit criteria:

1. Docs reflect actual behavior and no conflicting guidance remains.

### Phase 8 - Rollout Strategy

Objective: release safely with backward compatibility.

Steps:

1. Merge behind additive changes only (no breaking schema removals).
2. Announce in changelog that verbose semantics are now explicitly invariant for findings.
3. Monitor issue reports for output diff regressions.
4. If needed, hotfix caps/formatting without changing findings schema.

Exit criteria:

1. First stable release includes no findings-semantic regressions.

### Work Breakdown by Change Type

CLI layer:

1. Option definitions and precedence handling.

Domain/result layer:

1. Finding generation independent of verbose mode.

Report layer:

1. metadata normalization
2. diagnostics envelope and cap enforcement

Tests:

1. invariance, metadata, diagnostics presence, truncation/caps

Docs:

1. command references and guides

### Risk Register and Mitigations

Risk: hidden coupling where verbose currently influences control flow.
Mitigation: Phase 0 inventory plus invariance tests before and after refactor.

Risk: report size growth from verbose diagnostics.
Mitigation: hard caps, truncation markers, and cap tests.

Risk: JSON consumer breakage from new keys.
Mitigation: additive-only diagnostics keys; keep `findings` schema unchanged.

Risk: inconsistent behavior across commands.
Mitigation: shared helpers and cross-command test suite.

### Definition of Done

1. All four commands implement this contract.
2. Findings are invariant across verbose modes in automated tests.
3. `_meta.verbose` is always present where metadata is supported.
4. Verbose diagnostics are bounded, structured, and format-parity aligned.
5. Documentation is updated and changelog entry is published.

