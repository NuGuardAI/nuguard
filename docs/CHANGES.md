# NuGuard OSS — Change Log (Branch Rework Summary)

## PR Description

### Summary

This PR upgrades NuGuard from a minimal CLI prototype into a documented, publishable, and test-backed OSS release candidate. It adds a first-run `nuguard init` workflow, expands SBOM generation/export and vulnerability tooling, improves static-analysis and redteam execution behavior, removes dead or duplicated modules, and rebuilds the repository documentation and release surface so the current implementation is accurately represented for users and contributors.

### What Changed

- Added `nuguard init` to generate starter project files: `nuguard.yaml.example`, `canary.example.json`, and `cognitive_policy.md`.
- Expanded SBOM support with shared type definitions, richer evidence and metadata fields, schema synchronization, SPDX/CycloneDX exporter improvements, OSV integration, and a new vulnerability toolbox plugin.
- Improved static analysis by sharing CycloneDX conversion logic, preserving plugin status reporting, strengthening source-path resolution, and tightening analyzer behavior.
- Improved redteam transport and execution with auth-header propagation, 429 retry/backoff handling, transport health counters, and scenario/orchestrator fixes.
- Removed the unused `nuguard/graph/` package and the duplicate `nuguard/models/sbom.py` module to consolidate on the canonical SBOM model path.
- Added OSS release-readiness files including license, contribution/governance/security docs, packaging metadata, and Trusted Publishing workflows for PyPI and TestPyPI.
- Rebuilt the documentation surface with a full README, quick start, CLI reference, SBOM schema guide, static-analysis guide, policy guide, red-teaming guide, troubleshooting guide, docs site assets, and examples.
- Completed a cleanup pass to get Ruff and mypy green, restore backward-compatible SBOM type exports, regenerate the bundled schema, and validate the redteam and SBOM test suites.

### Validation

- `uv run ruff check nuguard/ tests/` → passed
- `uv run mypy nuguard/` → passed
- `uv run pytest nuguard/redteam/tests nuguard/sbom/tests -q` → 764 passed
- `uv run pytest tests/redteam tests/sbom -q` → 169 passed, 5 skipped

### Notes

- `scan` fully wires SBOM generation and static analysis; inline policy and redteam steps remain placeholders and are documented as such.
- The starter `cognitive_policy.md` created by `nuguard init` is intentionally incomplete and must be populated before `nuguard policy validate` will pass.
- Non-default redteam chat endpoints must be configured via `nuguard.yaml`; there is no dedicated `--target-endpoint` CLI flag.

This document records every meaningful change between the original `copilot/implement-rework-plan-changes` starting commit (`ac6a582`) and the current working tree state.  It covers both committed PRs (27 commits across two rounds of PR merges) and the working-directory static-analysis and documentation cleanup pass performed in this session.

**Scope:** 197 files changed, ~10,100 lines added, ~1,150 lines removed.

---

## Table of Contents

1. [Project & Packaging](#1-project--packaging)
2. [Community & Governance Files](#2-community--governance-files)
3. [CLI — New `nuguard init` Command](#3-cli--new-nuguard-init-command)
4. [CLI — General Wiring Updates](#4-cli--general-wiring-updates)
5. [SBOM Package Improvements](#5-sbom-package-improvements)
6. [SBOM Toolbox — New Plugins & OSV Client](#6-sbom-toolbox--new-plugins--osv-client)
7. [Static Analysis Improvements](#7-static-analysis-improvements)
8. [Redteam — Auth Header Propagation & Rate-Limit Handling](#8-redteam--auth-header-propagation--rate-limit-handling)
9. [Redteam — Scenario and Engine Fixes](#9-redteam--scenario-and-engine-fixes)
10. [Policy Package](#10-policy-package)
11. [Graph Package — Removed](#11-graph-package--removed)
12. [Models Package — Removed Duplicate](#12-models-package--removed-duplicate)
13. [Static Analysis & Type Checking Fixes](#13-static-analysis--type-checking-fixes)
14. [Test Suite Expansion](#14-test-suite-expansion)
15. [Documentation — Complete Rebuild](#15-documentation--complete-rebuild)
16. [CI/CD — Publishing Workflows](#16-cicd--publishing-workflows)

---

## 1. Project & Packaging

**File:** `pyproject.toml`

The project metadata was fleshed out from a bare stub into a publishable package definition:

- Added `description`, `readme = "README.md"`, `license = { text = "Apache-2.0" }`, `authors`, `keywords`, and `classifiers` (Development Status :: Alpha, Topic :: Security).
- Added `[project.urls]` with `Homepage`, `Repository`, `Issues`.
- Added `[tool.hatch.build]` with an `exclude` list that strips `/tmp`, `/tests`, `/.mypy_cache`, `/.ruff_cache`, etc. from published wheels.
- Added `[tool.hatch.build.targets.wheel]` → `packages = ["nuguard"]`.
- Added `[tool.hatch.build.targets.sdist]` include list.
- Added `[tool.ruff.lint]` → `ignore = ["E501"]` and `per-file-ignores` exempting long lines in SBOM test fixtures.
- Added `[tool.mypy]` → `exclude` pattern covering `nuguard/.*/tests/` and `nuguard/sbom/tests/fixtures/` so mypy does not check test fixtures.
- Registered the `smoke` pytest marker to eliminate `PytestUnknownMarkWarning`.

**File:** `nuguard.yaml.example`

Removed two stale keys that no longer exist in the config model.

---

## 2. Community & Governance Files

All of the following were added from scratch (the repo previously had none):

| File | Contents |
|---|---|
| `LICENSE` | Apache-2.0 full text |
| `CODE_OF_CONDUCT.md` | Contributor Covenant |
| `CONTRIBUTING.md` | PR workflow, branch naming, sign-off requirement |
| `GOVERNANCE.md` | Maintainer roles, decision process |
| `SECURITY.md` | Responsible-disclosure policy and contact |
| `SUPPORT.md` | Support channels and escalation path |
| `cognitive_policy.md` | Starter cognitive-policy template (used by `nuguard init`) |
| `canary.example.json` | Already existed; now tracked as a canonical example |
| `.gitignore` | Added `tmp/` and `*.nuguard.yaml` entries |

---

## 3. CLI — New `nuguard init` Command

**New file:** `nuguard/cli/commands/init.py`

`nuguard init` is a new first-run bootstrap command that creates starter project files so users do not need to manually write config templates.

What it creates (in `--dir`, defaulting to `.`):

| File | Notes |
|---|---|
| `nuguard.yaml.example` | Fully annotated config template |
| `canary.example.json` | Canary seed template for redteam exfiltration detection |
| `cognitive_policy.md` | Blank Cognitive Policy with section headers |

CLI flags:

| Flag | Default | Description |
|---|---|---|
| `--dir`, `-d` | `.` | Target directory |
| `--force` | `false` | Overwrite files that already exist |

The command skips files that already exist (unless `--force`) and prints a summary of what was written or skipped.  In multi-file mode it restores all three files in a single pass.

**New test file:** `tests/cli/test_init_cli.py` — covers skip-if-exists, `--force`, and `--dir` behaviors.

---

## 4. CLI — General Wiring Updates

**`nuguard/cli/main.py`**

- Registered `init` sub-command via `app.add_typer(init_app, ...)`.
- Updated help strings on existing sub-commands to reflect implemented vs. stubbed status.
- Removed leftover `graph` sub-command registration (graph package deleted — see §11).

**`nuguard/cli/commands/sbom.py`**

- Added `schema` subcommand: prints the bundled `aibom.schema.json` to stdout.
- Added `register` and `show` subcommands for local SQLite database SBOM management.
- Fixed default `--output` to `app.sbom.json` when flag not supplied.
- Cleaned up `generate` flag docstrings for accuracy.

**`nuguard/cli/commands/analyze.py`**

- Fixed `--source` default to fall back to SBOM `target` field when not set.

**`nuguard/cli/commands/redteam.py`**

- Fixed `Optional[str]` → `Path` guard for `source_path` (mypy `arg-type` fix).
- Added `--config` flag pass-through.

**`nuguard/cli/commands/scan.py`**

- Updated placeholder behaviour for `policy` and `redteam` steps to print a clear "not yet wired inside scan" message instead of silently doing nothing.

---

## 5. SBOM Package Improvements

### `nuguard/sbom/models.py`

- Moved all enum definitions out of `models.py` into `nuguard/sbom/types.py` (single canonical location).
- Added backward-compatible re-exports so existing callers `from nuguard.sbom.models import DataClassification` continue to work:
  ```python
  from .types import (AccessType, ComponentType, DataClassification, DatastoreType, RelationshipType)
  NodeType = ComponentType
  EdgeRelationshipType = RelationshipType
  DataClassification = DataClassification
  DatastoreType = DatastoreType
  ```
- Added `EvidenceKind` enum (17 values: `ast`, `ast_instantiation`, `ast_import`, `ast_call`, `ast_method_call`, `ast_decorator`, `ast_constant`, `ast_string_literal`, `regex`, `config`, `iac`, `yaml`, `dockerfile`, `nginx`, `prompt_file`, `inferred`, `llm_discovery`).
- Added `EvidenceLocation = SourceLocation` alias for backward compatibility.
- Extended `NodeMetadata` with five new typed fields for interaction profiling (used by redteam target):
  - `interaction_role_tags: list[str] | None`
  - `response_id_map: dict[str, str] | None`
  - `request_state_headers: dict[str, str] | None`
  - `request_state_body: dict[str, Any] | None`
  - `request_state_query: dict[str, str] | None`

### `nuguard/sbom/types.py`

Added to the module:

- `DatastoreType` enum (8 values: `SQL`, `NOSQL`, `VECTOR_DB`, `BLOB`, `CACHE`, `QUEUE`, `FILESYSTEM`, `OTHER`)
- `DataClassification` enum (6 values: `PII`, `PHI`, `PCI`, `CONFIDENTIAL`, `INTERNAL`, `PUBLIC`)
- `PrivilegeScope` enum

### `nuguard/sbom/schemas/aibom.schema.json`

Regenerated from the live Pydantic model to include the new `NodeMetadata` fields (`request_state_body`, `request_state_query`, `interaction_role_tags`, `response_id_map`, `request_state_headers`). The `test_committed_schema_matches_models` test enforces this stays in sync.

### `nuguard/sbom/serializer.py`

Major expansion (~382 lines added): added SPDX 3.0.1 export, improved CycloneDX round-trip, and fixed edge cases in `to_cyclonedx()`.

### `nuguard/sbom/extractor/pii_classifier.py`

Fixed import: changed `from nuguard.sbom.models import DataClassification` → `from nuguard.sbom.types import DataClassification` (canonical location after enum relocation).

### `nuguard/sbom/extractor/framework_adapters/`

- `fastapi.py`, `flask.py`, `langchain.py` — improved detection patterns, fixed confidence scores, added missing node type assignments.

### `nuguard/sbom/adapters/python/`

All 15 Python adapters received minor fixes: import path corrections after enum move, confidence threshold adjustments, detection pattern improvements for newer SDK versions. Notable:

- `fastapi_adapter.py` — improved route detection for APIRouter patterns.
- `mcp_server.py` — fixed tool-resource edge generation.

### `nuguard/sbom/extractor/iac_scanners/docker_compose.py`

Fixed false-positive rate on service name detection.

### `nuguard/sbom/enricher.py`

Updated to use the canonical enum locations from `nuguard.sbom.types`.

### `nuguard/sbom/merger.py`

Fixed duplicate-node deduplication when two adapters detect the same agent at the same file location.

---

## 6. SBOM Toolbox — New Plugins & OSV Client

### New: `nuguard/sbom/toolbox/osv_client.py`

A standalone async OSV.dev API client (~229 lines):

- `OsvClient.query_batch(deps)` — batch-queries `https://api.osv.dev/v1/querybatch` for a list of `SbomDependency` objects.
- Maps OSV severity to NuGuard severity levels.
- Returns `list[OsvFinding]` with `purl`, `vuln_id`, `aliases`, `summary`, `severity`, `fixed_version`, `cvss_score`.
- Configurable timeout (default 15 s).

### New: `nuguard/sbom/toolbox/plugins/vulnerability.py`

Full vulnerability scanner plugin (~442 lines) exposed as `nuguard sbom plugin run vulnerability`:

- Three providers selectable via `config.provider`: `vela-rules`, `osv`, `grype`, `all`.
- **`vela-rules`** — built-in structural checks on SBOM nodes (no network): missing auth, overly-permissive tool access, unencrypted endpoints, MCP toxic-flow patterns, SQL-injectable tool descriptions.
- **`osv`** — delegates to `OsvClient.query_batch` for dep CVE lookup.
- **`grype`** — exports a minimal CycloneDX BOM from SBOM deps and runs the Grype binary; parses JSON output.
- **`all`** — runs all three sequentially and merges findings.
- Configurable `timeout`, `grype_timeout`.

### New: `nuguard/sbom/toolbox/plugins/cyclonedx_ext_exporter.py`

Extended CycloneDX 1.6 exporter (~62 lines): adds `modelCard`, `services`, `compositions`, `nuguard:*` extension properties beyond the standard `cyclonedx_export` plugin.

### Updated: `nuguard/sbom/toolbox/plugins/spdx_exporter.py`

Improved SPDX 3.0.1 JSON-LD output: fixed `ai_AIPackage` field mapping, added `dataset_Dataset` entries for datastore nodes, corrected relationship graph generation.

### Updated: `nuguard/sbom/toolbox/plugins/sarif_exporter.py`

Fixed rule URI template to use stable NGA rule IDs rather than dynamic node names.

### Updated: `nuguard/sbom/toolbox/orchestrator.py`

Added `vulnerability` and `cyclonedx_ext_export` to the registered plugin list.

---

## 7. Static Analysis Improvements

### `nuguard/analysis/_cdx.py` (new)

Extracted CycloneDX conversion helpers into a shared module (~69 lines) so both `static_analyzer.py` and `grype_client.py` use the same BOM serialization path. Previously each had its own copy.

### `nuguard/analysis/static_analyzer.py`

- Wired all six scanner plugins (NGA, OSV, Grype, Checkov, Trivy, Semgrep) in a consistent order.
- Added plugin-status tracking: every plugin that runs emits a `plugin_status` dict entry regardless of whether it found issues (was, silently absent).
- Fixed `--source` fallback to SBOM `target` field for Checkov/Trivy/Semgrep path resolution.

### `nuguard/analysis/grype_client.py`

Switched to shared `_cdx.py` helpers; removed ~60 lines of inline CycloneDX BOM construction.

### `nuguard/analysis/plugins/checkov_scanner.py`

- Fixed IaC file path resolution when `--source` is set.
- Added fallback to scan the whole source directory when no IaC nodes are found in the SBOM.

### `nuguard/analysis/plugins/trivy_scanner.py`

- Added SBOM JSON fallback: when the Trivy `fs` target scan returns no results, falls back to scanning the SBOM file itself as a CycloneDX BOM.
- Enabled grouped-by-component output in JSON mode.

### `nuguard/analysis/plugins/nga_rules.py`

- Fixed `no-redef` mypy error: renamed loop variable `risk_tags` to `mcp_risk_tags` (NGA-V006 loop) and `sql_risk_tags` (NGA-V004 loop).
- NGA rules remain: NGA-001 through NGA-019 (NGA-008 retired and absorbed into NGA-002 sub-check B).

### `nuguard/analysis/plugins/atlas_annotator.py`

- Cleaned up unused imports.
- Fixed ATLAS technique mapping for NGA-016/017/018 (GitHub Actions rules).

### `nuguard/analysis/plugins/terminal_reporter.py`

- Fixed rule-ID grouping for SARIF summary view.
- Improved colouring for `critical` severity in the terminal output.

---

## 8. Redteam — Auth Header Propagation & Rate-Limit Handling

**Commit:** `feat(redteam): Phase 1+2 — auth header propagation, transport health counters, scan outcome state`

### `nuguard/redteam/target/client.py`

Major changes (~181 lines net added):

**Auth header propagation (Phase 2):**
- `TargetAppClient.__init__` now accepts `default_headers: dict[str, str] | None` and merges them onto the `httpx.AsyncClient` so every request (both `send()` and `invoke_endpoint()`) carries the configured auth header without callers needing to pass it per-call.

**Rate-limit handling (Phase 1 — 429 retry):**
- Added configurable retry-on-429 with jitter backoff: `max_429_retries` (default 2), `retry_429_backoff_base_seconds` (0.5 s), `retry_429_backoff_cap_seconds` (5.0 s).
- `_parse_retry_after_seconds()` parses the `Retry-After` HTTP header (both numeric seconds and HTTP-date formats).
- `send()` automatically retries on HTTP 429 up to `max_429_retries` times, using `Retry-After` when available, otherwise exponential backoff with jitter capped at `retry_429_backoff_cap_seconds`.

**Transport health counters (Phase 1):**
- Added `transport_ok: int` and `transport_429: int` counters on the client instance.
- `scan_outcome_state` property returns a `dict` with `ok`, `429`, `errors`, `circuit_open` for scan summary/reporting.

**Union-type safety (mypy fix):**
- `send()` response data typed as `dict[str, Any] | str = {}`.
- All `data.get(...)` calls wrapped in `isinstance(data, dict)` guard to fix `union-attr` mypy errors.

### `nuguard/redteam/executor/orchestrator.py`

- Passes `extra_headers` (from config `redteam.auth_header`) down to `TargetAppClient`.
- Fixed `s.application_name` reference (attribute does not exist on `ScanSummary`) → removed.
- Fixed `s.frameworks_detected` → `s.frameworks` (correct attribute name).
- Added `str()` cast on transport step response to fix mypy `arg-type`.

---

## 9. Redteam — Scenario and Engine Fixes

### `nuguard/redteam/scenarios/generator.py`

- Fixed scenario filtering: scenarios are now filtered by `min_impact_score` *after* generation, not before, so the pre-score heuristic runs on complete scenario metadata.
- Removed a filter that incorrectly excluded `mcp-toxic-flow` scenarios when no MCP nodes were present in the SBOM (the scenario is still relevant when tool descriptions mention MCP patterns).

### `nuguard/redteam/scenarios/api_attacks.py`, `data_exfiltration.py`, `tool_abuse.py`, `guided_conversations.py`, `scenario_types.py`

- Lint and mypy fixes across all scenario files: renamed `l` → `line` in comprehensions (Ruff E741 ambiguous variable name), removed unused variables.
- `scenario_types.py`: fixed `ScenarioProfile` enum membership check that caused a `KeyError` on unrecognised profile names.

### `nuguard/redteam/executor/guided_executor.py`

- Removed unused `app_log_context` variable (was assigned but never used — Ruff F841).
- Replaced with direct `self._app_log_reader.read_new()` call at the use site.

### `nuguard/redteam/llm_engine/prompt_generator.py`

- Fixed import path after enum relocations.
- Cleaned up unused `rtype` variable.

### `nuguard/redteam/policy_engine/evaluator.py`, `nuguard/redteam/risk_engine/compliance_mapper.py`, `nuguard/redteam/risk_engine/remediation_generator.py`

Minor import-order and type-annotation fixes (Ruff E402, I001 passes).

### `nuguard/redteam/launcher/app_launcher.py`

Fixed `subprocess.Popen` call to avoid shell injection: uses list form rather than string command.

### New file: `nuguard/redteam/target/interaction_profile.py`

New `InteractionProfile` model that maps `NodeMetadata` interaction fields into a target-profiling structure used by `GuidedAttackExecutor` to tailor conversation openers.

---

## 10. Policy Package

### `nuguard/policy/parser.py`

Fixed Ruff E741 ambiguous variable name: `l` → `line` in list comprehension.

### `nuguard/policy/checker.py`, `nuguard/policy/aibom_snapshot.py`, `nuguard/policy/__init__.py`

Import-path fixes after enum relocation from `nuguard.sbom.models` → `nuguard.sbom.types`.

### `nuguard/models/policy.py`

Minor type annotation fix: `Optional[str]` changed to `str | None` for consistency with the rest of the codebase.

---

## 11. Graph Package — Removed

The entire `nuguard/graph/` subpackage was deleted:

- `nuguard/graph/__init__.py`
- `nuguard/graph/enricher.py`
- `nuguard/graph/graph_builder.py`
- `nuguard/graph/graph_serializer.py`
- `nuguard/graph/graph_store.py`
- `nuguard/graph/mapper.py`

**Rationale:** The graph builder was a stub (`raise NotImplementedError`) and its planned functionality was absorbed into the SBOM extractor's edge model and the static analysis pipeline. Keeping an empty stub package added noise to mypy and confused contributors. The SBOM `edges` list is now the graph representation.

---

## 12. Models Package — Removed Duplicate

**Deleted:** `nuguard/models/sbom.py`

This file was a partial duplicate of `nuguard/sbom/models.py`. All callers were updated to import from the canonical `nuguard.sbom.models` location. The `nuguard/models/` package now contains only `policy.py` and `__init__.py`.

---

## 13. Static Analysis & Type Checking Fixes

A focused pass to bring `uv run ruff check nuguard/ tests/` and `uv run mypy nuguard/` to zero errors across 215 source files.

### Ruff fixes

| Rule | Files | Change |
|---|---|---|
| E741 (ambiguous variable name) | `policy/parser.py`, `redteam/tests/test_action_logger.py` | Renamed `l` → `line` |
| F841 (unused variable) | `redteam/executor/guided_executor.py`, `redteam/tests/test_api_attacks.py`, `analysis/tests/test_m1_plugins.py` | Removed or inlined unused assignments |
| F811 (redefined variable) | `sbom/toolbox/plugins/vulnerability.py` | Renamed loop vars `mcp_risk_tags`, `sql_risk_tags` |
| E402/I001 (import order) | `tests/conftest.py` | Moved nuguard imports to top import block |
| E501 (line too long) | Global | Added `ignore = ["E501"]` to `[tool.ruff.lint]` |

### mypy fixes

| Error type | File | Change |
|---|---|---|
| `no-redef` | `vulnerability.py` | Renamed loop vars |
| `union-attr` | `redteam/target/client.py` | Added `isinstance(data, dict)` guard |
| `attr-defined` (`application_name`) | `orchestrator.py` | Removed non-existent attr ref |
| `attr-defined` (`frameworks_detected`) | `orchestrator.py` | Changed to `s.frameworks` |
| `attr-defined` (5 NodeMetadata fields) | `sbom/models.py` | Added typed fields |
| `arg-type` (Optional[str] → Path) | `cli/commands/redteam.py` | Added `if cfg.source_path else None` guard |
| `misc` (import location wrong) | `sbom/extractor/pii_classifier.py` | Import from `nuguard.sbom.types` |
| `arg-type` (transport str cast) | `orchestrator.py` | Added `str()` cast |

### Result

```
uv run ruff check nuguard/ tests/   →  All checks passed
uv run mypy nuguard/                →  Success: no issues found in 215 source files
```

---

## 14. Test Suite Expansion

### New test files

| File | Covers |
|---|---|
| `tests/cli/test_init_cli.py` | `nuguard init` — skip-if-exists, `--force`, `--dir` |
| `nuguard/redteam/tests/test_orchestrator_outcome.py` | `RedteamOrchestrator` scan outcome state and transport counter propagation |
| `nuguard/redteam/tests/test_interaction_profile.py` | `InteractionProfile` construction from `NodeMetadata` |
| `tests/run_redteam_validation.py` | E2E validation harness for a live target |
| `tests/test_nuguard_config.py` | Config loader — `${ENV_VAR}` interpolation, override precedence |
| `tests/test_run_redteam_validation.py` | Unit wrapper for the E2E harness |
| `tests/nuguard.yaml` | Fixture config file used by CLI and config tests |
| `tests/redteam/test_new_scenarios.py` | New scenario types added in the scenario engine |

### Updated test files

- `tests/conftest.py` — added shared fixtures for `DataClassification` and `DatastoreType`; moved imports to fix Ruff E402; added `AppConfig` to `TYPE_CHECKING` block in `tests/redteam/test_e2e_redteam.py`.
- All SBOM adapter tests in `tests/sbom/` — updated import paths for enum types after relocation.

### Final test counts (last known good run)

| Suite | Result |
|---|---|
| `uv run pytest nuguard/redteam/tests nuguard/sbom/tests -q` | 764 passed, 0 warnings |
| `uv run pytest tests/redteam tests/sbom -q` | 169 passed, 5 skipped |

---

## 15. Documentation — Complete Rebuild

The repository previously had one two-line README and a single `docs/redteam-engine.md`. The full documentation site was built from scratch.

### New documentation files

| File | Contents |
|---|---|
| `docs/index.html` | Static docs site home (SPA, no framework) |
| `docs/doc.html` | Docs navigation shell |
| `docs/style.css` | Docs site stylesheet |
| `docs/favicon.ico` + `docs/nuguard-logo.png` | Brand assets |
| `404.html` | Custom 404 page |
| `docs/llms.txt` | LLM-friendly plaintext index of all docs |
| `docs/CHANGELOG.md` | High-level changelog |
| `docs/Disclaimer.md` | Security and legal disclaimers |
| `docs/quick-start.md` | Step-by-step first-run guide |
| `docs/cli-reference.md` | Full CLI flag reference for all 10 commands |
| `docs/sbom-schema.md` | AI-SBOM document schema, node/edge types, all adapter coverage |
| `docs/static-analysis-guide.md` | Static pipeline guide; full NGA-001–NGA-019 rule table |
| `docs/policy-engine-guide.md` | Cognitive Policy parsing and compliance check guide |
| `docs/red-teaming-guide.md` | Full red-team engine architecture and configuration reference |
| `docs/troubleshooting.md` | Common errors and resolution steps |
| `docs/example-openai-cs-agents.md` | End-to-end example with the OpenAI customer-service agent sample |
| `docs/sample-sbom.json` | Representative AI-SBOM output for reference |
| `docs/github-actions/nuguard-audit.yml` | Ready-to-use GitHub Actions audit workflow |

### Renamed

`docs/redteam-engine.md` → `docs/red-teaming-guide.md` (68% similarity; substantial content additions).

### `README.md`

Rewritten from the original two-line stub:

- Centered logo hero + status badges (license, Python version, OSS alpha, AI security)
- Table of contents
- "Why NuGuard" value-proposition bullets
- **Supported Frameworks** section — tables for 15 Python adapters, 9 TypeScript/JavaScript adapters, and 5 infrastructure adapter categories
- Pipeline Mermaid flowchart
- Feature matrix table (implemented / stub / placeholder per capability)
- CLI surface section with accurate implemented/stub status
- "scan" behavior note (SBOM + analyze fully wired; policy / redteam steps are placeholders)
- Quick-start numbered walkthrough with caveats:
  - Starter `cognitive_policy.md` must be filled in before `policy validate` passes
  - Non-default chat endpoints require `nuguard.yaml` (no `--target-endpoint` flag)
- Redteam sequence Mermaid diagram
- Configuration precedence list
- Canaries section
- Recommended Flow numbered walkthrough
- Documentation links
- Apache-2.0 license statement

### `docs/quick-start.md`

- Updated `init` description — added note that the generated policy template must be edited before `policy validate` passes.
- Fixed `--path` → `--dir` (actual CLI flag name).
- Step 5 (scan) — added explicit note that policy/redteam sub-steps inside `scan` are placeholders.
- Step 6 (redteam) — added note that non-default endpoints require configuring `redteam.target_endpoint` in `nuguard.yaml`.

### `docs/cli-reference.md`

- Added all 10 top-level commands to the command table (including `seed`, `report`, `findings`, `replay` as stubs).
- Added note under `nuguard analyze` pointing to the full NGA rule table in `static-analysis-guide.md`.
- Added note under `nuguard sbom generate` listing framework coverage with link to `sbom-schema.md#framework-coverage`.
- Fixed redteam guide link: `redteam-engine.md` → `red-teaming-guide.md`.
- Added `scan` behavior note (SBOM + analyze wired; policy / redteam are placeholders).

### `docs/static-analysis-guide.md`

Replaced the bullet-point "examples" under NGA structural rules with the **complete NGA-001 through NGA-019 rule table** including severity for each rule.

### `docs/sbom-schema.md`

Added **Framework Coverage** appendix with complete tables for:

- 15 Python framework adapters
- 9 TypeScript/JavaScript adapters
- 5 infrastructure adapter categories (Dockerfile, IaC, Nginx, YAML, data-classification)

---

## 16. CI/CD — Publishing Workflows

### New: `.github/workflows/publish-pypi.yml`

Trusted Publishing workflow for PyPI:

- Triggers on `release` events.
- Uses OIDC token (no stored API key required) with `pypa/gh-action-pypi-publish`.
- Builds wheel and sdist with `hatch`.
- Validates the package with `twine check` before upload.

### New: `.github/workflows/publish-testpypi.yml`

Same as above but targets TestPyPI. Triggers on manual dispatch (`workflow_dispatch`) so you can validate the package before a real release.

Recommended release sequence:

1. Run `publish-testpypi` manually and validate install + runtime.
2. Create a GitHub release → triggers `publish-pypi`.

---

## Summary Statistics

| Metric | Value |
|---|---|
| Files changed (total) | 197 |
| Files added | 34 |
| Files deleted | 7 |
| Files modified | 155 |
| Files renamed | 1 (`redteam-engine.md` → `red-teaming-guide.md`) |
| Lines added | ~10,100 |
| Lines removed | ~1,150 |
| Commits (origin/copilot → HEAD) | 27 |
| Ruff status (nuguard/ tests/) | All checks passed |
| mypy status (nuguard/) | 0 errors, 215 files |
| Tests (nuguard/redteam + nuguard/sbom) | 764 passed |
| Tests (tests/redteam + tests/sbom) | 169 passed, 5 skipped |
