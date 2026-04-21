# Redteam V3 — Design Adapted to the NuGuard Module

**Date:** 2026-04-14
**Basis:** `docs/llm-runs/redteam-agent-design.md` (generic ARTS spec)
**Adapted for:** `nuguard/redteam/` (existing 9k+ LOC implementation)
**Prior iterations:** `redteam-v2.md` (empirical improvements, landed 2026-04-13)

---

## 0. Why This Doc Exists

The reference doc (`redteam-agent-design.md`) describes a generic **AI Red Team System (ARTS)** from first principles: profiler, template library, AI enricher, execution runners, judge, report. NuGuard already implements ~85% of that vision under different names. This v3 doc does three things:

1. **Crosswalk** — map ARTS components onto the NuGuard implementation so future work references the right files.
2. **Gap analysis** — identify what ARTS proposes that NuGuard lacks.
3. **Incremental plan** — adopt only the deltas that move the needle, not a rewrite.

The goal is to raise ASR against customer-facing agentic apps (openai-cs-agents-demo, marketing-campaign, healthcare-voice-agent fixtures) without destabilising the 195-test redteam suite.

---

## 1. ARTS → NuGuard Crosswalk

| ARTS Component | NuGuard Equivalent | Path | Status |
|---|---|---|---|
| **App Profiler** (schema, tool inventory, MCP discovery) | SBOM extractor + `_discover_chat_config()` + `probe_chat_endpoints` | `nuguard/sbom/`, `nuguard/redteam/executor/orchestrator.py:265`, `nuguard/common/endpoint_probe.py` | **Done** — SBOM already emits agent topology, tool inventory, MCP servers, and API endpoints with inferred payload shape. |
| **Application Profile JSON** | `AiSbomDocument` + framework/policy metadata | `nuguard/sbom/models.py` | **Done** — richer than ARTS schema (node types, edges, confidence, evidence). |
| **Template Library** | Scenario builders (one file per goal type) | `nuguard/redteam/scenarios/` (jailbreak, mcp_attacks, prompt_injection, privilege_escalation, tool_abuse, policy_violations, data_exfiltration, api_attacks, sbom_driven) | **Done** — parameterised scenarios already exist for RT-001..RT-015 coverage. |
| **AI Enricher** (LLM customises templates to profile) | `enrichment/auto_enricher.py` + `llm_engine/prompt_generator.py` + `scenarios/sbom_driven.py` | `nuguard/redteam/enrichment/`, `nuguard/redteam/llm_engine/prompt_generator.py` | **Partial** — `prompt_generator` enriches single payloads; no bulk multi-variant generation from profile. |
| **Test Case Pool** | `AttackScenario` list built in `generator.py` | `nuguard/redteam/scenarios/generator.py` | **Done**. |
| **Single-turn Runner** | `AttackExecutor` | `nuguard/redteam/executor/executor.py` | **Done**. |
| **Multi-turn / Agentic Chain Runner** | `GuidedAttackExecutor` + `ConversationDirector` | `nuguard/redteam/executor/guided_executor.py`, `nuguard/redteam/llm_engine/conversation_director.py` | **Done** — adaptive multi-turn with tactic selection, self-disclosure exploitation, code_gen → code_exec tactic chaining (v2). |
| **LLM-as-Judge** | `LLMResponseEvaluator` | `nuguard/redteam/llm_engine/response_evaluator.py` | **Done** — includes goal-type hints + heuristic code-pattern scanner (v2). |
| **Rule-based Classifier** | Policy detectors + regex patterns in evaluator | `nuguard/redteam/policy_engine/detectors/`, `response_evaluator.py:127` | **Done**. |
| **ASR Tracker** | Findings aggregation + summary generator | `nuguard/redteam/llm_engine/summary_generator.py`, `nuguard/models/redteam_run.py` | **Done** — per-goal and per-scenario outcome counts. |
| **Risk Report (md)** | `nuguard/output/markdown.py` + redteam reporter | `nuguard/output/` | **Done**. |
| **SARIF / CI gating** | `output/sarif.py` + `ci_policy` config | `nuguard/output/sarif.py`, `nuguard/config.py:615` | **Done**. |
| **Automated Test Script output (pytest)** | — | — | **Gap**. |
| **TAP (Tree of Attacks with Pruning)** | — | — | **Gap**. |
| **PAIR (Iterative Refinement)** | Partial in `adaptive_mutation.py` | `nuguard/redteam/llm_engine/adaptive_mutation.py` | **Partial** — simple mutation, no judge-feedback loop. |
| **Multimodal Injection** | — | — | **Gap** (not a priority for current text-only fixtures). |
| **Session State Manipulation** | — | — | **Gap**. |
| **MCP Server Impersonation** | `poison_server.py` | `nuguard/redteam/executor/poison_server.py` | **Done**. |

**Summary:** NuGuard covers 14 of the 19 ARTS techniques (RT-001 through RT-015 plus most reporting/gating). The remaining gaps are RT-016 (TAP), RT-017 (PAIR-complete), RT-018 (multimodal), RT-019 (session), plus the generated pytest output format.

---

## 2. Technique Coverage Map (RT-xxx → NuGuard scenario files)

| ARTS ID | Technique | NuGuard Implementation |
|---|---|---|
| RT-001 | Direct Prompt Injection | `scenarios/prompt_injection.py`, `scenarios/jailbreak.py` |
| RT-002 | Indirect Prompt Injection | `scenarios/mcp_attacks.py` (via `poison_server`), `scenarios/sbom_driven.py` (RAG paths) |
| RT-003 | RAG / Memory Poisoning | `scenarios/mcp_attacks.py` (MCP_RAG), poisoning helpers in `executor/poison_server.py` |
| RT-004 | Roleplay / Persona Jailbreak | `scenarios/jailbreak.py` (persona templates) + `conversation_director._select_tactic` `narrative` branch |
| RT-005 | Logic Trap | `scenarios/jailbreak.py` (hypothetical/conditional templates) |
| RT-006 | Encoding Obfuscation | `scenarios/data_exfiltration.py` (base64, zero-width), `adaptive_mutation.py` |
| RT-007 | Crescendo | `conversation_director.py` tactic ladder (benign → mild → targeted) |
| RT-008 | GOAT (adaptive multi-turn) | `conversation_director.py` + `guided_executor.py` — tactic selection adapts to response |
| RT-009 | M2S (multi → single-turn collapse) | **Partial** — covered implicitly in some `jailbreak.py` templates, not an explicit scenario type |
| RT-010 | Tool Hijacking | `scenarios/tool_abuse.py` + `code_gen` tactic (v2) |
| RT-011 | MCP Server Impersonation | `executor/poison_server.py` + `scenarios/mcp_attacks.py` |
| RT-012 | Sub-Agent Trust Escalation | `scenarios/privilege_escalation.py` + handoff probes in `sbom_driven.py` |
| RT-013 | Cross-Agent Exfiltration | `scenarios/data_exfiltration.py` (cross-tenant scenarios) |
| RT-014 | System Prompt Extraction | `scenarios/jailbreak.py` (`SYSTEM_PROMPT_EXTRACTION` scenario type) |
| RT-015 | Full Kill Chain Chaining | `executor/chain_assembler.py` + `ExploitChain` multi-step scenarios |
| RT-016 | TAP | **Gap** |
| RT-017 | PAIR | **Partial** — `adaptive_mutation.py` mutates but doesn't consume judge signal |
| RT-018 | Multimodal Injection | **Gap** |
| RT-019 | Session State Manipulation | **Gap** |

---

## 3. What Actually Needs Work

Three deltas justify code changes in v3. The rest is crosswalk documentation, not implementation.

### 3.1 PAIR-style judge-feedback loop (RT-017)

**Problem:** `adaptive_mutation.py` rewrites failed payloads but doesn't feed the judge's `evidence` and `severity_signal` back into the mutation prompt. So mutations drift randomly instead of converging on the weakness the judge found.

**Change:**
- Extend `LLMResponseEvaluator.evaluate()` to return a structured `refusal_reason` field alongside `evidence`. Use a **closed taxonomy** of 5–10 values: `content_filter`, `policy_detector`, `hitl_check`, `topic_guardrail`, `identity_check`, `tool_permission`, `format_enforcement`, `uncertainty_deflection`, `other`. An optional `refusal_note` free-form string captures edge cases for later taxonomy tuning.
- `adaptive_mutation.mutate()` accepts the prior `EvaluationResult` and injects `refusal_reason` + `evidence` into the mutator prompt: *"The previous attempt failed because the agent cited {refusal_reason}. Specifically: {evidence}. Generate a variant that avoids this signal by ..."*
- Cap at 3 iterations per scenario to bound cost.

**Files:**
- `nuguard/redteam/llm_engine/response_evaluator.py` — add `refusal_reason` classification (small LLM prompt addition).
- `nuguard/redteam/llm_engine/adaptive_mutation.py` — accept and use prior evaluation.
- `nuguard/redteam/executor/guided_executor.py` — thread the evaluation back into the next mutation.

**Expected impact:** +5–15% ASR on GUARDRAIL_BYPASS and POLICY_VIOLATION scenarios where the agent's refusal is consistent (current failure mode per v2 analysis).

---

### 3.2 TAP (Tree of Attacks with Pruning) for PROMPT_DRIVEN_THREAT (RT-016)

**Problem:** System-prompt extraction and jailbreak scenarios linearly try one tactic per turn. High-ASR attacks in the literature explore a branching tree, prune losers, and deepen winners.

**Change:** Introduce an optional `tree_exploration: true` flag per scenario. When set, `GuidedAttackExecutor` runs:
1. **Breadth layer** — fire N (default 3) tactic variants in parallel (e.g., `direct`, `roleplay`, `hypothetical`).
2. **Score** — evaluate each with `LLMResponseEvaluator`; keep top-K (default 1–2) by `severity_signal + confidence`.
3. **Depth layer** — for each surviving branch, generate 2 follow-up variants informed by the agent's response. Repeat up to `tree_max_depth` (default 3).
4. **Prune** — drop branches whose judge score doesn't improve over the parent.

**Files:**
- New: `nuguard/redteam/llm_engine/tree_explorer.py` (~200 LOC).
- `scenarios/jailbreak.py` — mark `SYSTEM_PROMPT_EXTRACTION` and high-severity scenarios with `tree_exploration=True`.
- `executor/guided_executor.py` — branch on the flag; default path stays linear.
- Config: add `redteam.tree_max_depth` and `redteam.tree_breadth`.
  - **`profile: ci`** → breadth 2, depth 2 (bounded cost, still worthwhile exploration).
  - **`profile: full`** → breadth 3, depth 3 (default per §3.2 above).
  - Both profiles can override via explicit yaml values.

**Expected impact:** +10–25% ASR on SYSTEM_PROMPT_EXTRACTION, which currently hovers near 0% (per `openai-cs-redteam.md`).

---

### 3.3 Pytest script emitter for CI regression (ARTS §4.6a)

**Problem:** NuGuard already emits SARIF/markdown but not runnable regression tests. For customers integrating red-team findings into CI, they need a file they can `pytest` after every model/config change to verify a fix stuck.

**Change:** Add `output/pytest_emitter.py` that, given a `RedteamRun` with findings:
1. For each successful attack (`outcome == HIT`) where the judge reported `severity_signal >= 0.5`, emit a pytest function parameterised on `(scenario_id, payload_history, expected_refusal)`. Soft-failure (low-confidence) attacks are intentionally skipped to keep the regression suite small and deterministic.
2. Include the numeric `severity_signal` in the test function name (e.g., `test_sev_092_data_exfiltration_base64_pii`) so the most critical regressions sort first and are obvious in CI output.
3. Use the live target URL from config; auth via the same env-var interpolation.
4. Include an assertion: agent response must be a refusal per the same `LLMResponseEvaluator` rubric, OR the response must not contain the extracted evidence string.
5. Group by goal type into one file per goal (e.g., `test_regression_data_exfiltration.py`).
6. Wire it behind `output.emit_pytest: true`.

**Files:**
- New: `nuguard/output/pytest_emitter.py`.
- `nuguard/cli/commands/redteam.py` — call emitter when flag is set.
- `nuguard/config.py` — add `OutputConfig.emit_pytest: bool = False`.

**Expected impact:** Closes the ARTS §4.6a gap, making findings actionable as regression tests. Adoption-driver feature, not an ASR lever.

---

## 4. What NOT to Build

| ARTS component | Reason to skip |
|---|---|
| **RT-018 Multimodal Injection** | Current fixtures are text-only. Revisit when a multimodal target fixture lands. |
| **RT-019 Session State Manipulation** | Deprioritised until a NuGuard fixture maintains turn-to-turn state and exposes it via an API. Once such a fixture exists, design scenarios that attempt state manipulation for persistence or privilege escalation across sessions. Until then, no target surface to exercise. |
| **Separate App Profiler from SBOM** | SBOM *is* the profiler. Reinventing it splits the truth source. |
| **Dedicated Template YAML library** | Python scenario builders already parameterise cleanly; YAML adds a second config language without gain. |
| **M2S as explicit scenario type** | Already emerges naturally from `conversation_director`'s `inject` + `code_gen` tactics; separate type would duplicate. |

---

## 5. Trust & Isolation — Already in Place

ARTS §6 lists isolation controls. NuGuard status:

| Control | NuGuard |
|---|---|
| Enricher isolation (no target network access) | **Yes** — `prompt_generator` only receives SBOM JSON; no target client handle. |
| Test execution sandbox | **Yes** — `app_launcher.py` runs fixtures in subprocess with scoped env. |
| Judge isolation | **Yes** — `eval_llm` is a separate LiteLLM config from the attack `llm` (see `nuguard.yaml.example`). |
| Credential separation | **Yes** — `.env` pattern + `${VAR}` interpolation. |
| Immutable audit log | **Yes** — per-run reports under `reports/agentic-test-*.log`. |
| Rate limiting | **Partial** — `guided_concurrency` caps parallelism but no RPS limiter. Add `redteam.rate_limit_rps` if a target complains. |

---

## 6. Phased Adoption (Concrete)

| Phase | Work | Files Touched | ETA |
|---|---|---|---|
| **P1** (this sprint) | PAIR feedback loop (§3.1) | 3 files, ~150 LOC | 1–2 days |
| **P2** | TAP exploration (§3.2) | 4 files, ~300 LOC + tests | 3–5 days |
| **P3** | Pytest emitter (§3.3) | 3 files, ~200 LOC + a fixture test | 2 days |
| **P4** (future) | Rate limiter + multimodal scaffold | when needed | deferred |

Each phase ships independently, stays behind a config flag until a test-suite run shows parity, and is measured against the v2 baseline ASR from `openai-cs-redteam.md`.

---

## 7. Success Metrics

Measured on the three fixture apps (`openai-cs-agent`, `openai-cs-agents-demo`, `marketing-campaign-validation`):

| Metric | Baseline (v2) | Target (v3 post-P2) |
|---|---|---|
| Overall ASR | ~7% | ≥15% |
| SYSTEM_PROMPT_EXTRACTION ASR | ~0% | ≥10% |
| POLICY_VIOLATION ASR | ~10% | ≥20% |
| DATA_EXFILTRATION ASR (semantic, not keyword) | ~6% | ≥12% |
| Redteam test suite pass rate | 195/195 | 195/195 (no regressions) |
| Per-run LLM cost | baseline | ≤1.3× (PAIR+TAP bounded by depth caps) |

---

## 8. Resolved Decisions

All four open questions from the draft are now settled. Decisions are reflected in §3 and §4; recorded here for traceability.

| # | Question | Decision |
|---|---|---|
| 1 | TAP breadth/depth vs. cost | **Run in both profiles.** `ci` uses breadth 2 / depth 2 to cap LLM spend; `full` uses breadth 3 / depth 3. Both are overridable via `redteam.tree_breadth` and `redteam.tree_max_depth` in yaml. See §3.2. |
| 2 | PAIR refusal-reason taxonomy | **Closed enum with escape hatch.** Initial values: `content_filter`, `policy_detector`, `hitl_check`, `topic_guardrail`, `identity_check`, `tool_permission`, `format_enforcement`, `uncertainty_deflection`, `other`. Optional `refusal_note` free-form string captures outliers for taxonomy tuning. See §3.1. |
| 3 | Pytest emitter scope | **Severity-gated, named by score.** Emit only for findings with `severity_signal >= 0.5`. Embed the numeric score in the test name (e.g., `test_sev_092_*`) so critical regressions sort to the top in CI output. Soft failures stay in the markdown report but are excluded from the regression suite. See §3.3. |
| 4 | RT-019 Session State Manipulation | **Deferred.** No NuGuard fixture currently exposes turn-to-turn session state via an API. When one lands, design scenarios for state manipulation aimed at persistence and cross-session privilege escalation. See §4. |
---

*End of Document*
