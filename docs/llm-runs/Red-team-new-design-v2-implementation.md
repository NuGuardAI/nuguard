# Red-Team V2 Implementation Plan

Date: 2026-05-30

## Purpose

This document is the implementation plan for `docs/llm-runs/Red-team-new-design-v2.md`. It maps every V2 design requirement onto the existing NuGuard codebase, identifies reuse opportunities, notes simplifications, and specifies the exact files and new modules needed for each delivery phase.

The plan was produced by scanning the full redteam package (`nuguard/redteam/`), the catalog layer (`nuguard/redteam/catalog/`), the executor layer (`nuguard/redteam/executor/`), the config schema (`nuguard/config.py`), and the existing framework adapters (`nuguard/redteam/target/framework_adapters/`).

## Design Assessment

### What the V2 design adds and why it improves efficacy

The existing implementation already has a strong catalog (84 specs), capability-aware scenario filtering, LLM-enriched payloads, adaptive mutation, guided conversations, canary scanning, and policy evaluation. The three gaps that V2 closes are:

1. **Execution ordering is heuristic, not declarative.** Destructive scenarios are sorted to the end of the run using a keyword match on the title (`_is_destructive_scenario()` in `orchestrator.py`). This is fragile — a destructive scenario with an unusual title escapes the check. V2 replaces the heuristic with a mandatory `execution_class` and `phase` field on every `ScenarioSpec`.

2. **Concurrency is global, not lock-aware.** The runner dispatches all scenarios through a flat `asyncio.Semaphore`. Two scenarios that write to the same memory scope, tenant object, or pending approval queue can corrupt each other's evidence. V2 adds explicit `resource_lock_templates` to every spec and a scheduler that prevents lock-conflicting scenarios from running in the same batch.

3. **Session isolation is implicit.** Auth is bootstrapped once and the same headers are shared across all scenarios. A memory-write scenario or a cross-session leak test that runs before a clean-slate authz test can leave residue that produces false positives or false negatives. V2 adds a per-scenario login mode for destructive and memory scenarios, with group login allowed only for read-only batches.

These three changes improve both **efficacy** (fewer false positives from contaminated state, fewer missed findings from sessions that already refused an earlier probe) and **efficiency** (safe scenarios run concurrently; unsafe ones run serially but only within their narrow lock scope, not globally serialised).

### What the V2 design keeps from V1

- All 84 catalog specs and their `ScenarioSpec` metadata remain unchanged.
- `AttackScenario`, `ExploitChain`, `GuidedConversation`, `Finding` models are unchanged.
- `AppCapabilityProfile` and `CapabilityDetector` stay as the single capability-detection entry point — extended, not replaced.
- `LLMResponseEvaluator`, `AdaptiveMutationStrategy`, `ConversationDirector`, `PolicyEvaluator` are unchanged.
- `TargetAppClient`, `CanaryScanner`, `ActionLogger` are unchanged.
- Google ADK and Google CES adapters already exist in `framework_adapters/` and are unchanged.
- The report format is extended, not replaced.

### Simplification wins

1. Delete `_is_destructive_scenario()` in `orchestrator.py` — keyword heuristic replaced by the declarative `execution_class` and `destructive` fields.
2. `ScenarioScheduler.build_batches()` replaces the flat semaphore loop — cleaner, deterministic, testable in isolation.
3. `AppCapabilityProfile` is extended in-place — all existing callers require zero changes.
4. `LockExpander` is intentionally minimal in v1 — uses `scenario_id` and `target_node_ids` as lock key components; avoids full identity-matrix complexity.
5. Framework adapters are optional plug-ins — the scheduler works with generic lock keys when no adapter is installed.

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Delivery scope | Full V2, all phases, phased merges | Keeps the catalog, scheduler, isolation, adapters, and reporting coherent |
| Framework adapters | LangGraph + OpenAI Agents SDK | Google ADK/CES already exist; these two cover the next most common targets |
| Destructive scope | S5 + S6, S6 gated behind `supports_destructive_fixtures: true` | Needed for real tool-boundary evidence; gate keeps it safe by default |
| Session isolation default | Per-scenario for destructive/memory only; shared otherwise | Avoids excessive auth round-trips while fixing the contamination risk |
| Backward compatibility | All new fields default-valued; existing YAML and specs unchanged | Zero migration burden |

---

## Delivery Phases

The implementation is structured in five phases that can be reviewed and merged independently. Each phase is a self-contained vertical slice.

---

### Phase 0 — Universal Target Model Extension

**Goal**: Extend `AppCapabilityProfile` with framework type, deployment type, and application type detection. No new module. Existing callers are zero-touch.

**Steps** (all parallel):

1. Add `FrameworkType`, `DeploymentType`, `AppType` string enums to `nuguard/redteam/catalog/taxonomy.py`. Use the same `str, Enum` pattern as the existing `ScenarioCategory`, `DeliveryChannel`, etc.

   | Enum | Values |
   |------|--------|
   | `FrameworkType` | `ANY`, `LANGGRAPH`, `GOOGLE_ADK`, `AUTOGEN`, `OPENAI_AGENTS`, `SEMANTIC_KERNEL`, `CREWAI`, `MCP`, `CUSTOM` |
   | `DeploymentType` | `ANY`, `CLOUD_API`, `KUBERNETES`, `SERVERLESS`, `ON_PREM`, `BROWSER_DESKTOP`, `CI_RUNNER`, `EDGE_DEVICE` |
   | `AppType` | `RAG_CHAT`, `CUSTOMER_SUPPORT`, `FINTECH`, `HEALTHCARE`, `PRODUCTIVITY`, `MULTI_AGENT_WORKFLOW`, `CODING_AGENT`, `BROWSER_AGENT`, `VOICE_AGENT`, `EDGE_ASSISTANT` |

2. Extend `AppCapabilityProfile` dataclass in `nuguard/redteam/catalog/capability.py` with three new fields — all have safe defaults:

   ```python
   framework_type: FrameworkType = FrameworkType.ANY
   deployment_type: DeploymentType = DeploymentType.ANY
   app_types: frozenset[AppType] = field(default_factory=frozenset)
   skipped_reasons: dict[str, str] = field(default_factory=dict)
   ```

   `skipped_reasons` maps scenario ID → human-readable reason string (e.g. `"D04: requires MULTI_SESSION capability"`). Used by reporting to distinguish skipped-by-capability from other skip reasons.

3. Extend `CapabilityDetector` to populate the new fields:
   - `framework_type`: inspect `sbom.summary.frameworks_detected` (already present in the SBOM model); map known framework name strings to `FrameworkType` values.
   - `deployment_type`: inspect `sbom.summary.deployment_type` if present; fall back to `ANY`.
   - `app_types`: derive from the detected domain (`capability.py` already tracks `domain`) and tool surface (e.g. navigation → `EDGE_ASSISTANT`, repo/CI tools → `CODING_AGENT`).

**Files modified**: `nuguard/redteam/catalog/taxonomy.py`, `nuguard/redteam/catalog/capability.py`

---

### Phase 0.5 — Framework Adapters: LangGraph + OpenAI Agents SDK

**Goal**: Add two new adapter modules following the existing `google_adk.py` / `google_ces.py` pattern. Google ADK and CES adapters already exist and are unchanged.

**Steps** (all parallel with Phase 0):

4. Create `nuguard/redteam/target/framework_adapters/langgraph.py` — `LangGraphAdapter`:
   - Detects by `FrameworkType.LANGGRAPH` from capability profile.
   - State surfaces: `thread_id`, `checkpoint_namespace`, `graph_state`.
   - Lock keys emitted: `checkpoint:{thread_id}`, `memory:{checkpoint_namespace}`.
   - `snapshot()` → calls LangGraph REST API (`/threads/{thread_id}/state`) when `langgraph_base_url` is in config; returns empty dict otherwise.
   - `reset_thread(thread_id)` → stub that logs a warning (full implementation deferred to when a LangGraph test fixture exists).

5. Create `nuguard/redteam/target/framework_adapters/openai_agents.py` — `OpenAIAgentsAdapter`:
   - Detects by `FrameworkType.OPENAI_AGENTS`.
   - State surfaces: `session_id`, `run_id`, `pending_tool_approvals`.
   - Lock keys emitted: `session:{session_id}`, `approval:{identity_id}`.
   - `snapshot()` → calls OpenAI Agents API run state endpoint when `openai_agents_base_url` is in config; returns empty dict otherwise.
   - `reset_run(run_id)` → stub.

6. Register both adapters in `nuguard/redteam/target/framework_adapters/factory.py`:
   - Add entries in the adapter factory map under keys `"langgraph"` and `"openai_agents"`.
   - Factory selects adapter by `AppCapabilityProfile.framework_type`.

**Files created**: `nuguard/redteam/target/framework_adapters/langgraph.py`, `nuguard/redteam/target/framework_adapters/openai_agents.py`

**Files modified**: `nuguard/redteam/target/framework_adapters/factory.py`

---

### Phase A — Execution Metadata in Catalog

**Goal**: Promote phase, execution class, state impact, resource locks, and session isolation requirements from design doc to first-class fields on `ScenarioSpec` and `AttackScenario`. All new fields are default-valued so existing specs compile unchanged.

**Steps** (depend on Phase 0 enums):

7. Add three new enums to `nuguard/redteam/catalog/taxonomy.py`:

   | Enum | Values |
   |------|--------|
   | `ExecutionClass` | `S0_STATIC`, `S1_STATELESS_PROMPT`, `S2_READ_ONLY_TOOL`, `S3_EGRESS_TRAP`, `S4_STATEFUL_CONTEXT`, `S5_HIGH_IMPACT_DRY_RUN`, `S6_DESTRUCTIVE_REAL` |
   | `StateImpact` | `NONE`, `READ`, `EGRESS_TRAP`, `FIXTURE_WRITE`, `MEMORY_WRITE`, `BUSINESS_WRITE_DRY_RUN`, `BUSINESS_WRITE_REAL` |
   | `DestructiveMode` | `NOT_APPLICABLE`, `TRACE_ONLY`, `DRY_RUN_REQUIRED`, `EMULATOR_REQUIRED`, `FIXTURE_REQUIRED`, `REAL_ALLOWED` |

8. Extend `ScenarioSpec` in `nuguard/redteam/catalog/spec.py` with 17 new fields — all have safe defaults that preserve current behaviour:

   | Field | Type | Default | Purpose |
   |-------|------|---------|---------|
   | `phase` | `int` | `2` | Mandatory scheduler phase (0–9). |
   | `execution_class` | `ExecutionClass` | `S1_STATELESS_PROMPT` | Side-effect class used by scheduler. |
   | `state_impact` | `StateImpact` | `NONE` | Expected state interaction. |
   | `destructive` | `bool` | `False` | True when the objective would be destructive in production. |
   | `destructive_mode` | `DestructiveMode` | `NOT_APPLICABLE` | Safe execution constraint for destructive scenarios. |
   | `requires_fresh_login` | `bool` | `False` | Forces session re-bootstrap before this scenario. |
   | `requires_fresh_conversation` | `bool` | `True` | Forces a new conversation thread. |
   | `allow_group_login` | `bool` | `True` | Permits auth session reuse for read-only groups. |
   | `concurrency_group` | `str` | `""` | Scheduler label for serial grouping. |
   | `resource_lock_templates` | `tuple[str, ...]` | `()` | Lock key templates; expanded per scenario instance. |
   | `run_after_phases` | `tuple[int, ...]` | `()` | Phase dependencies. |
   | `reset_after` | `str` | `""` | Cleanup action required after scenario. |
   | `app_types` | `tuple[AppType, ...]` | `()` | Application type applicability filter. |
   | `frameworks` | `tuple[FrameworkType, ...]` | `(FrameworkType.ANY,)` | Framework applicability. |
   | `deployments` | `tuple[DeploymentType, ...]` | `(DeploymentType.ANY,)` | Deployment applicability. |
   | `expected_control` | `str` | `""` | Runtime control that should fire (approval, refusal, DLP…). |
   | `rollback_strategy` | `str` | `""` | Reset or no-op verification approach. |

9. Add `__post_init__` validation in `ScenarioSpec` (the dataclass already uses `frozen=True`, so add a `__post_init__` that calls a `_validate_execution_fields()` function):
   - `destructive=True` → `safe_execution` must be set and `destructive_mode != NOT_APPLICABLE`.
   - `execution_class in (S5, S6)` → `resource_lock_templates` must be non-empty.
   - `execution_class in (S4, S5, S6)` → `requires_fresh_conversation` must be `True`.
   - `execution_class == S6` → `destructive_mode == REAL_ALLOWED`.

10. **Backfill `nuguard/redteam/catalog/registry.py`**: assign execution metadata to all 84 specs per the V2 overlay table. The overlay is reproduced below for reference:

    | Category | Scenario IDs | `execution_class` | `phase` | `destructive` | `concurrency_group` |
    |----------|-------------|-------------------|---------|---------------|---------------------|
    | Data Exfiltration | D01–D03, D05–D08 | `S2_READ_ONLY_TOOL` | 3 | False | `read_only_data` |
    | Cross-session Exfil | D04 | `S4_STATEFUL_CONTEXT` | 6 | False | `cross_session_pair` |
    | Covert Exfiltration | C01–C08 | `S3_EGRESS_TRAP` | 4 | False | `egress_trap` |
    | Destructive Tool Actions | T01–T08 | `S5_HIGH_IMPACT_DRY_RUN` | 7 | True | `write_or_delete` |
    | Real Destructive Fixtures | T01–T08 (sandbox only) | `S6_DESTRUCTIVE_REAL` | 8 | True | `real_destructive` |
    | Authorization Reads | A01, A03–A05, A08 | `S2_READ_ONLY_TOOL` | 3 | False | `api_read_only` |
    | Authorization Writes/Approvals | A02, A06–A07 | `S5_HIGH_IMPACT_DRY_RUN` | 7 | True | `approval_boundary` |
    | Indirect Prompt Injection | I01–I08 | `S4_STATEFUL_CONTEXT` | 5 | False | `indirect_fixture_write` |
    | MCP Metadata Read | M01–M02, M07 | `S2_READ_ONLY_TOOL` | 3 | False | `mcp_metadata_read` |
    | MCP Toxic Flow / Egress | M03–M06, M08 | `S3_EGRESS_TRAP` / `S4_STATEFUL_CONTEXT` | 4–5 | False | `egress_trap` / `indirect_fixture_write` |
    | Memory and Persistence | P01–P06 | `S4_STATEFUL_CONTEXT` | 6 | False | `memory_persistence` |
    | Multi-Agent Trust | G01–G06 | `S4_STATEFUL_CONTEXT` / `S5_HIGH_IMPACT_DRY_RUN` | 6–7 | True (G06) | `same_conversation_multi_turn` / `approval_boundary` |
    | Jailbreak and Policy Bypass | J01–J06 | `S1_STATELESS_PROMPT` | 2 | False | `prompt_stateless` |
    | Evasion and Robustness | E01–E06 | `S1_STATELESS_PROMPT` / `S2_READ_ONLY_TOOL` | 2–3 | False | `prompt_stateless` |
    | Business Logic and Safety | B01–B06 | `S1`, `S2`, or `S5` by sink | 2, 3, or 7 | True (B01, B03, B05) | varies |
    | Coding and Automation Agents | K01–K06 | `S2`, `S4`, or `S5` | 3, 5, or 7 | True (K05–K06) | `coding_static` / `write_or_delete` |

11. Mirror execution fields onto `AttackScenario` in `nuguard/redteam/scenarios/scenario_types.py`. These fields are copied from the resolved `ScenarioSpec` inside `catalog/selector.py` when building scenario instances. Fields not sourced from the catalog default to the same safe values as `ScenarioSpec`.

12. **Simplification win**: delete `_is_destructive_scenario()` in `nuguard/redteam/executor/orchestrator.py` and remove the call site that uses it to sort destructive scenarios to the end. The `execution_class` and `phase` fields replace this heuristic entirely.

**Files modified**: `nuguard/redteam/catalog/taxonomy.py`, `nuguard/redteam/catalog/spec.py`, `nuguard/redteam/catalog/registry.py`, `nuguard/redteam/scenarios/scenario_types.py`, `nuguard/redteam/executor/orchestrator.py`

---

### Phase B — Phase-Ordered Lock-Aware Scheduler

**Goal**: Replace the flat `asyncio.Semaphore` dispatch in `_run_scenarios` with a phase-ordered, resource-lock-conflict-aware batch builder.

**Steps** (depend on Phase A for `execution_class`, `phase`, `resource_lock_templates`):

13. Create `nuguard/redteam/executor/scheduler.py` with three classes:

    **`LockExpander`**
    - `expand(scenario: AttackScenario) → frozenset[str]`: materialises `resource_lock_templates` into concrete lock key strings.
    - Template substitution rules (v1, intentionally simple):
      - `{scenario_id}` → `scenario.scenario_id`
      - `{goal_type}` → `scenario.goal_type.value`
      - `{target_node_id}` → first `scenario.target_node_ids` entry (or `"any"`)
      - `{concurrency_group}` → `scenario.concurrency_group`
    - Framework-adapter-supplied extra locks are unioned in when a framework adapter is registered.

    **`ResourceLockManager`**
    - `async def try_acquire(locks: frozenset[str]) → bool`: atomically checks whether all locks are free and acquires them. Uses a single `asyncio.Lock` to protect the `_held: set[str]` set.
    - `def release(locks: frozenset[str])`: releases all held locks.

    **`ScenarioScheduler`**
    - `build_batches(scenarios: list[AttackScenario], max_concurrency: int) → list[list[AttackScenario]]`:
      1. Sort scenarios by `phase` (ascending), then `impact_score` (descending).
      2. Group by phase into phase buckets.
      3. Within each phase bucket, greedily assign scenarios to batches: a scenario joins the current batch only when `LockExpander.expand(s)` is disjoint from the union of lock sets already in the batch.
      4. Start a new batch when `len(current_batch) >= max_concurrency` or no remaining scenario can join without a lock conflict.
      5. Return the ordered list of batches across all phases.
    - `batch_metadata(batch: list[AttackScenario]) → dict`: returns phase, batch_id (UUID), and concurrency_group summary for logging.

14. Add fields to `ScenarioRecord` in `nuguard/redteam/executor/orchestrator.py`:
    - `phase: int = 0`
    - `batch_id: str = ""`
    - `concurrency_group: str = ""`
    - `resource_locks: list[str] = field(default_factory=list)`
    - `isolation_mode: str = "shared"`
    - `skipped_reason: str = ""`

15. Refactor `RedteamOrchestrator._run_scenarios()`:
    - Call `ScenarioScheduler.build_batches(scenarios, self._concurrency)` to get ordered batches.
    - For each batch, dispatch scenarios concurrently using the existing `asyncio.gather` pattern (the `ResourceLockManager` manages the acquire/release lifecycle across batches).
    - After each phase transitions, call `_run_phase_integrity_check()` (Phase D stub).
    - **S6 gate**: before scheduling any `S6_DESTRUCTIVE_REAL` scenario, check `self._supports_destructive_fixtures`. If false, mark the scenario record `chain_status="skipped"`, `skipped_reason="s6_requires_destructive_fixtures_not_enabled"`, and do not execute.
    - **Capability skip**: propagate `AppCapabilityProfile.skipped_reasons` into `ScenarioRecord.skipped_reason` for scenarios filtered out during generation.

16. Add `supports_destructive_fixtures: bool = False` to the redteam config section in `nuguard/config.py` (alongside the existing `scenario_timeout`, `turn_delay_seconds`, etc.).

**Files created**: `nuguard/redteam/executor/scheduler.py`

**Files modified**: `nuguard/redteam/executor/orchestrator.py`, `nuguard/config.py`

---

### Phase C — Session Isolation Modes

**Goal**: Enforce per-scenario fresh login for destructive and memory scenarios. Allow group login for read-only batches. Expose isolation settings in `nuguard.yaml`.

**Steps** (depend on Phase A for `requires_fresh_login`, `state_impact`, `execution_class`):

17. Add `IsolationConfig` Pydantic model and `isolation:` YAML section to `nuguard/config.py`:

    ```yaml
    redteam:
      isolation:
        auth_session: shared                       # shared | per_scenario
        conversation: per_scenario                 # per_scenario | per_group
        force_fresh_login_for_destructive: true    # S4 / S5 / S6
        force_fresh_login_for_memory: true         # state_impact == MEMORY_WRITE
        allow_group_login_for_readonly: false      # S1 / S2 / S3 only
    ```

    Defaults: `auth_session=shared`, `conversation=per_scenario`, `force_fresh_login_for_destructive=true`, `force_fresh_login_for_memory=true`, `allow_group_login_for_readonly=false`.

    These defaults implement the V2 spec recommendation: "default per-scenario for destructive/memory scenarios only" while keeping group login for read-only batches (when explicitly opted in) to avoid excessive auth round-trips.

18. Add `resolved_redteam_isolation() → IsolationConfig` helper to `NuGuardConfig` in `nuguard/config.py`, following the same pattern as `resolved_redteam_finding_triggers()`.

19. Add `_build_scenario_session(scenario, bootstrapper, effective_headers) → dict[str, str]` in `nuguard/redteam/executor/orchestrator.py`:
    - Checks whether re-bootstrapping is needed:
      - `scenario.requires_fresh_login == True`, OR
      - `isolation.force_fresh_login_for_destructive == True` AND `scenario.execution_class in (S4, S5, S6)`, OR
      - `isolation.force_fresh_login_for_memory == True` AND `scenario.state_impact == MEMORY_WRITE`.
    - When re-bootstrapping is needed: calls `bootstrap_auth_runtime()` with a fresh run ID and returns the new session headers.
    - Otherwise: returns shared `effective_headers` unchanged (current behaviour — zero cost for the common path).
    - The returned headers are passed to `AttackExecutor` or `GuidedAttackExecutor` for that specific scenario execution.

20. Add `IdentityMatrix` dataclass to `nuguard/redteam/target/session.py`:

    ```python
    @dataclass
    class IdentityMatrix:
        owner_user: str = ""
        other_user: str = ""
        low_priv_user: str = ""
        admin_user: str = ""
        tenant_a_user: str = ""
        tenant_b_user: str = ""
        memory_seed_user: str = ""
        memory_probe_user: str = ""
    ```

    Populated from `config.credentials` using well-known key names. Passed into scenario builders that need multi-identity authz scenarios (A01–A08, D04, P03). The identity matrix is optional — when credentials are not configured, authz scenarios skip the multi-identity variant.

21. Record `isolation_mode` (one of `"shared"`, `"fresh_login"`, `"fresh_login_destructive"`, `"fresh_login_memory"`) in `ScenarioRecord` for each executed scenario.

**Files modified**: `nuguard/config.py`, `nuguard/redteam/executor/orchestrator.py`, `nuguard/redteam/target/session.py`

---

### Phase D — Fixture Integrity Check Stubs

**Goal**: Add the skeleton for between-phase fixture integrity checking. Wire stubs now; framework adapters fill them in incrementally.

**Steps** (depend on Phase B for phase lifecycle hooks):

22. Create `nuguard/redteam/executor/fixture_integrity.py`:

    ```python
    @dataclass
    class FixtureSnapshot:
        phase: int
        timestamp: datetime
        adapter_data: dict  # framework-specific; empty when no adapter

    @dataclass
    class IntegrityViolation:
        scope: str          # lock key prefix, e.g. "memory:user_123"
        description: str
        suspected_scenario_id: str

    class FixtureIntegrityChecker:
        def snapshot(self, phase: int) -> FixtureSnapshot: ...
        def diff(self, before: FixtureSnapshot, after: FixtureSnapshot) -> list[IntegrityViolation]: ...
        def should_quarantine(self, violations: list[IntegrityViolation]) -> set[str]: ...
    ```

    - `snapshot()` delegates to `framework_adapter.snapshot()` if an adapter is installed; otherwise returns an empty `FixtureSnapshot` (no-op).
    - `diff()` compares `adapter_data` keys. An unexpected mutation in a non-fixture-write scope raises a violation.
    - `should_quarantine()` returns the set of lock scope prefixes that should be excluded from later phases.

23. Add `_run_phase_integrity_check(phase: int, before: FixtureSnapshot)` in `orchestrator.py`:
    - Takes a `FixtureSnapshot` captured before the phase started.
    - Calls `checker.snapshot(phase)` to get the after snapshot.
    - Calls `checker.diff(before, after)` to find violations.
    - On violations: logs a warning, adds violated lock scopes to an internal `_quarantined_scopes` set, marks any later scenario whose `resource_locks` overlap the quarantined scopes as `skipped_reason="fixture_integrity_failed"`.
    - No-op when both snapshots are empty (adapter not installed).

**Files created**: `nuguard/redteam/executor/fixture_integrity.py`

**Files modified**: `nuguard/redteam/executor/orchestrator.py`

---

### Phase E — Reporting Enhancements

**Goal**: Emit coverage by phase, batch execution mode, isolation decisions, and destructive scenario disposition into the existing report format.

**Steps** (depend on Phase B and D for populated `ScenarioRecord` fields):

24. Extend `nuguard/redteam/report.py` with three new sections in the Markdown report:

    **"Execution Coverage by Phase"** — table:

    | Phase | Name | Generated | Skipped (capability) | Skipped (safety) | Skipped (integrity) | Executed serial | Executed concurrent |
    |-------|------|-----------|----------------------|------------------|---------------------|-----------------|---------------------|

    **"Scenario Execution Details"** — table (one row per `ScenarioRecord`):

    | Scenario | Phase | Batch | Concurrency Group | Isolation Mode | Skipped Reason |
    |----------|-------|-------|-------------------|----------------|----------------|

    **"Destructive Scenario Summary"** — inline stats:
    - Count deferred to phase 7+ (S5/S6)
    - Count skipped (no destructive fixtures configured)
    - Count executed as dry-run

25. Extend `CatalogCoverage` in `nuguard/redteam/catalog/coverage.py`:
    - Add `per_phase_coverage: dict[int, int]` — phase → count of executed scenarios.
    - Add `skipped_by_reason: dict[str, int]` — reason string → count.
    - Populate from `scenario_records` in the orchestrator at end-of-run.

26. Surface isolation mode decisions and S6 gate status in `config_notes` at the top of the report (already rendered by the existing report header).

**Files modified**: `nuguard/redteam/report.py`, `nuguard/redteam/catalog/coverage.py`

---

## File Change Summary

| File | Change type | Phase |
|------|-------------|-------|
| `nuguard/redteam/catalog/taxonomy.py` | Modified — add 6 new enums | 0, A |
| `nuguard/redteam/catalog/capability.py` | Modified — extend `AppCapabilityProfile` with framework/deployment/app_type detection | 0 |
| `nuguard/redteam/target/framework_adapters/langgraph.py` | **New** — `LangGraphAdapter` | 0.5 |
| `nuguard/redteam/target/framework_adapters/openai_agents.py` | **New** — `OpenAIAgentsAdapter` | 0.5 |
| `nuguard/redteam/target/framework_adapters/factory.py` | Modified — register 2 new adapters | 0.5 |
| `nuguard/redteam/catalog/spec.py` | Modified — 17 new fields + `_validate_execution_fields()` | A |
| `nuguard/redteam/catalog/registry.py` | Modified — backfill execution metadata for all 84 specs | A |
| `nuguard/redteam/scenarios/scenario_types.py` | Modified — mirror execution fields from `ScenarioSpec` | A |
| `nuguard/redteam/executor/orchestrator.py` | Modified — scheduler integration, session isolation helper, integrity hooks; delete `_is_destructive_scenario` | A, B, C, D |
| `nuguard/redteam/executor/executor.py` | Modified — accept per-scenario auth headers parameter | C |
| `nuguard/redteam/executor/scheduler.py` | **New** — `LockExpander`, `ResourceLockManager`, `ScenarioScheduler` | B |
| `nuguard/redteam/executor/fixture_integrity.py` | **New** — `FixtureSnapshot`, `IntegrityViolation`, `FixtureIntegrityChecker` | D |
| `nuguard/redteam/target/session.py` | Modified — add `IdentityMatrix` | C |
| `nuguard/config.py` | Modified — `IsolationConfig`, `supports_destructive_fixtures`, `resolved_redteam_isolation()` | B, C |
| `nuguard/redteam/catalog/coverage.py` | Modified — `per_phase_coverage`, `skipped_by_reason` | E |
| `nuguard/redteam/report.py` | Modified — 3 new report sections | E |

---

## API and Schema Compatibility

All changes are additive and backward-compatible:

- **`ScenarioSpec`**: 17 new fields all have defaults → existing `registry.py` entries that are not yet backfilled compile unchanged and are scheduled using their default phase (2) and class (`S1_STATELESS_PROMPT`).
- **`AttackScenario`**: new fields sourced from `ScenarioSpec` at build time; callers that construct `AttackScenario` directly (e.g. legacy builders, tests) receive safe defaults.
- **`nuguard.yaml`**: the `isolation:` block is optional; existing config files that omit it get the V2 defaults (which match current behaviour for the shared-session path).
- **`ScenarioRecord`**: new fields have defaults; existing code that reads records (report, tests) works unchanged.
- **`AppCapabilityProfile`**: extended with default-valued fields; all existing callers (`selector.py`, `sbom_driven.py`, `generator.py`) work unchanged.
- **`CatalogCoverage`**: extended; report callers that access only existing fields are unaffected.
- **Snapshot tests** (`test_catalog_snapshot.py`): these will need to be regenerated after the registry backfill (step 10) since the snapshot captures the full set of spec fields. This is expected and intentional.

---

## Test Plan

New test files to add:

| Test file | What it covers |
|-----------|----------------|
| `tests/redteam/test_scheduler.py` | Phase ordering: T01–T08 always sort after phases 1–6. Lock conflict: two scenarios sharing a `memory:` lock cannot batch together; two with disjoint `identity:` locks can. S6 gate: `S6_DESTRUCTIVE_REAL` scenarios are skipped when `supports_destructive_fixtures=False`. Batch size cap respected. |
| `tests/redteam/test_session_isolation.py` | Fresh login triggered when `requires_fresh_login=True`. Fresh login triggered for S4/S5/S6 when `force_fresh_login_for_destructive=True`. Shared session used for S1/S2/S3 group. Fresh login triggered for `MEMORY_WRITE` when `force_fresh_login_for_memory=True`. `IdentityMatrix` populated from credentials dict. |
| `tests/redteam/test_fixture_integrity.py` | Empty snapshots produce zero violations. Unexpected mutation in a non-fixture-write scope raises a violation. `should_quarantine()` returns correct lock scope prefixes. Later scenarios with overlapping lock scopes are marked `fixture_integrity_failed`. |

Existing tests to update:

| Test file | Required update |
|-----------|----------------|
| `tests/redteam/test_catalog_snapshot.py` | Regenerate snapshot after registry backfill (step 10). Snapshot must include all new `ScenarioSpec` fields. |
| `tests/redteam/test_catalog_capability.py` | Add assertions that `AppCapabilityProfile.framework_type` and `deployment_type` are populated when SBOM has `frameworks_detected`. |

Regression tests that must continue to pass unchanged:

- `tests/redteam/test_finding_triggers.py`
- `tests/redteam/test_executor_early_stop.py`
- `tests/redteam/test_guided_executor.py`
- `tests/redteam/test_conversation_director.py`
- `tests/redteam/test_new_scenarios.py`
- `tests/redteam/test_v3_features.py`

---

## Verification Commands

```bash
# After Phase A: schema and snapshot
uv run pytest tests/redteam/test_catalog_snapshot.py -v --snapshot-update
uv run pytest tests/redteam/test_catalog_capability.py -v

# After Phase B: scheduler unit tests
uv run pytest tests/redteam/test_scheduler.py -v

# After Phase C: isolation unit tests
uv run pytest tests/redteam/test_session_isolation.py -v

# After Phase D: fixture integrity unit tests
uv run pytest tests/redteam/test_fixture_integrity.py -v

# Full regression after all phases
uv run pytest tests/redteam/ -v

# Lint and type check
uv run ruff check nuguard/
uv run mypy nuguard/

# CLI smoke test
uv run nuguard redteam --help
```

Integration validation points (requires a live target fixture):

- Destructive scenarios (T01–T08) appear in phase 7 in the report.
- Phase coverage table is present in the Markdown report.
- S6 scenarios are logged as `skipped` with reason `s6_requires_destructive_fixtures_not_enabled` by default.
- C01–C08 covert exfiltration scenarios run concurrently in phase 4 with unique trap URLs.
- P01–P06 memory persistence scenarios run serially in phase 6.
- LangGraph fixture: `checkpoint:{thread_id}` lock prevents two thread-mutating scenarios from batching together.

---

## Open Items

These are explicitly out of scope for V2 implementation and recorded here for the next design iteration:

1. **Full identity matrix execution**: `IdentityMatrix` is defined and populated (Phase C), but scenario builders for A01–A08 and D04 multi-identity variants are not wired until a separate builder update.
2. **LangGraph and OpenAI Agents SDK reset hooks**: stubs only in Phase 0.5. Full implementation deferred until framework test fixtures exist.
3. **Deployment adapters** (Kubernetes, serverless, CI runner): specified in V2 but no adapters are created in this plan. Framework adapters are the higher priority.
4. **Real S6 reset hooks**: `S6_DESTRUCTIVE_REAL` is gated by config and the gate is tested, but the actual reset-and-verify loop for sandbox fixtures is out of scope.
5. **Promptfoo-style grading examples per scenario**: V2 mentions explicit pass/fail detector examples; this is a catalog enrichment task separate from the scheduler and isolation work.

---

*Companion document to `docs/llm-runs/Red-team-new-design-v2.md`. Authored 2026-05-30.*
