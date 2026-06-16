# Verbose Flag Specification (Cross-Command)

Status: Proposed

Scope: `nuguard behavior`, `nuguard redteam`, `nuguard validate`, `nuguard policy check`

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
