# Behavior Pipeline — Performance Improvement Opportunities

Scanned: April 16, 2026  
Branch: `feat/behavior-scenario-concurrency`  
Baseline: Run 5 (concurrency=2, 43m 34s, 40 scenarios, 55.3% PASS)

---

## Summary Table

| # | Area | File | Effort | Expected Impact |
|---|---|---|---|---|
| 1 | Remove `_TURN_SUFFIX` from non-coverage scenarios | `scenarios.py`, `coverage.py`, `runner.py` | Low | **High** — shorter app responses, lower generation latency |
| 2 | Expose `max_coverage_turns` in yaml config | `coverage.py` | Low | Medium — fewer trailing turns per scenario |
| 3 | Expose `max_turns_per_scenario` in yaml config | `runner.py` | Low | Medium — caps wasted turns for simple scenarios |
| 4 | Pre-generate coverage turns during build phase | `coverage.py`, `runner.py` | Medium | Medium — removes mid-run LLM overhead |
| 5 | Break early when coverage = 100% after scripted turns | `runner.py` | Low | Low-Medium — avoids unnecessary coverage loop |
| 6 | Skip reactive data-discovery turns on FAIL / short T1 | `runner.py` | Low | Low-Medium — avoids 3 wasted turns per failed probe |
| 7 | Add `max_scenarios` config cap | `scenarios.py` | Low | **High** — direct control of total run time |
| 8 | Separate judge LLM endpoint from target app endpoint | config / `judge.py` | Medium | **High** — eliminates rate-limit contention under concurrency |
| 9 | Adaptive judge prompt truncation | `judge.py` | Low | Low-Medium — smaller payloads for short responses |
| 10 | Expand fast-path verdicts to `component_coverage` | `judge.py` | Medium | Medium — saves LLM judge calls for obvious outcomes |
| 11 | Semantic dedup for near-identical scenarios | `runner.py`, `scenarios.py` | High | Low-Medium — reduces redundant scenario count |
| 12 | Reduce `turn_delay_seconds` to 0.1 or 0 | config | Low | Low — removes ~150s cumulative dead time |

---

## Detail

### 1. `_TURN_SUFFIX` forces tool enumeration on every message

**Files:** `nuguard/behavior/scenarios.py`, `nuguard/behavior/coverage.py`, `nuguard/behavior/runner.py`  
**Constant:** `_TURN_SUFFIX = " Please keep the response under 500 words and list all agents and tools involved in handling this request."`

Every single message sent to the app — across all scenario types and all turns — appends this suffix. This forces the app's LLM to enumerate its entire tool stack on every turn, producing longer responses, slower generation, and increased token cost. Under concurrent load this compounds because multiple scenarios simultaneously demand enumeration.

This suffix is only genuinely useful for `component_coverage` scenarios (where confirming tool involvement is the point). For `boundary_enforcement`, `invariant_probe`, and `data_discovery_probe`, it is actively counterproductive — the verbose enumeration can crowd out the actual answer being judged.

**Proposed fix:** Make `_TURN_SUFFIX` opt-in per scenario type. Strip it from all non-coverage scenarios.

---

### 2. `MAX_COVERAGE_TURNS = 5` is a hard-coded constant

**File:** `nuguard/behavior/coverage.py` (line ~22)

Up to 5 LLM-generated follow-up turns are appended to every scenario regardless of whether all components are already covered. This is a hard constant with no yaml knob.

For a 40-scenario run this is potentially 200 extra app calls (5 × 40), though in practice many scenarios don't reach the cap. Reducing to 2–3 would cut the tail significantly.

**Proposed fix:** Add `max_coverage_turns` to the yaml behavior config. Default to 5 for backward compatibility.

---

### 3. `_ADAPTIVE_SESSION_CAP = 10` is a hard-coded constant

**File:** `nuguard/behavior/runner.py` (line ~91)

Hard cap of 10 turns per scenario regardless of scenario type. A simple `intent_happy_path` scenario typically needs 2–3 scripted messages. The remaining 7–8 slots are all coverage turns. Most scenarios are complete well before turn 10.

**Proposed fix:** Add `max_turns_per_scenario` to yaml config. Optionally allow per-scenario-type overrides (e.g. `happy_path=4`, `boundary_enforcement=3`, `invariant_probe=3`).

---

### 4. `generate_coverage_turns` makes an LLM call mid-execution

**File:** `nuguard/behavior/coverage.py` (line ~155)

After scripted turns complete, `generate_coverage_turns()` calls the LLM to craft contextual follow-up messages for uncovered components. This LLM round-trip happens *inside* the running scenario — it blocks the next turn from firing until the generation LLM responds. A template fallback exists but is only used when no LLM client is configured.

**Proposed fix:** Pre-generate coverage turns during the `build_scenarios` phase, not at runtime. The coverage messages are generic enough to be pre-built. As a simpler alternative, always use `_template_message()` and skip the LLM call entirely — the template messages are already effective for component coverage.

---

### 5. Coverage loop runs even when coverage = 100%

**File:** `nuguard/behavior/runner.py` (lines ~371–384)

`component_coverage` scenarios already target a specific component. After their scripted turns complete, they still enter the coverage turn loop and check all uncovered components — even when the targeted component was fully covered in turn 1.

**Proposed fix:** After scripted turns complete, if `coverage_state.coverage_pct >= 1.0`, break immediately rather than entering the coverage turn loop.

---

### 6. `data_discovery_probe` injects 3 reactive turns unconditionally

**File:** `nuguard/behavior/runner.py` (lines ~451–462)

After Turn 1 of every `DATA_DISCOVERY_PROBE` scenario, 3 additional turns are injected:
- Turn 2: happy path — explore revealed data
- Turn 3: IDOR probe — request same data for a different user
- Turn 4: write-access probe — attempt to modify a revealed field

If Turn 1 verdict is FAIL (the agent refuses or responds with nothing useful), these 3 turns probe against a non-functional surface and produce no signal. They still incur the full app latency + judge cost.

**Proposed fix:** Only inject reactive turns if the Turn 1 verdict is PASS or PARTIAL, or if the response length exceeds a threshold (e.g. > 200 chars). Skip entirely on FAIL or empty responses.

---

### 7. No `max_scenarios` config cap

**File:** `nuguard/behavior/scenarios.py` (`build_scenarios` entry point)

Scenario count is driven entirely by SBOM size:
- Layer 2: 1 scenario per AGENT/TOOL node
- Layer 3: 1 scenario per policy control
- Layer 4: 1 scenario per HITL trigger
- Layer 5: 1 scenario per data-holding agent

For a moderately complex SBOM this generates 40+ scenarios. There is no yaml knob to limit the total. Users cannot trade scenario coverage for faster runs without modifying code.

**Proposed fix:** Add `max_scenarios` (integer) to the yaml behavior config. Apply as a final slice in `build_scenarios` after deduplication. Order of priority: L1, L2, L3, L4, L5.

---

### 8. Judge LLM calls compete with app calls on the same Azure endpoint

**File:** `nuguard/behavior/judge.py`, behavior config  
**Config:** `judge_concurrency: 5`

The judge sends calls to `azure/gpt-4.1-mini` — the same Azure OpenAI deployment the Pinnacle Bank app uses internally. Under `scenario_concurrency: 2` with `judge_concurrency: 5`, up to 7 simultaneous requests hit the same rate-limit bucket. This is the most likely explanation for the PARTIAL spike observed under concurrency (Run 4: 44% PASS, Run 5: 55% PASS vs 85% sequential).

**Proposed fix (option A):** Configure the judge to use a different model endpoint (e.g. a separate Azure deployment, or OpenAI directly) so judge and app traffic don't share a rate-limit bucket.  
**Proposed fix (option B):** Reduce `judge_concurrency` to 2 or 3 as a short-term mitigation.

---

### 9. Judge prompt sends up to 1500 chars of response unconditionally

**File:** `nuguard/behavior/judge.py` (lines ~565–566)  
**Truncation:** `response[:1500]`, `prompt[:800]`

Many turns produce short responses (refusals, errors, single-sentence confirmations) where 1500 chars is far more than needed. Every judge call sends the full allocation regardless of actual response length, increasing input token count and LLM latency unnecessarily.

**Proposed fix:** Adaptive truncation — use `min(len(response), 800)` for responses under 500 chars, and only use the 1500-char cap for long responses. Similarly reduce `prompt[:800]` to `prompt[:500]` for short prompts.

---

### 10. Fast-path verdicts don't cover `component_coverage` scenarios

**File:** `nuguard/behavior/judge.py` (`_fast_verdict` function, lines ~288–310)

`_fast_verdict()` applies regex pre-checks to skip the LLM judge for obvious cases. It handles `intent_happy_path` (clear refusals → instant FAIL) and `invariant_probe` / `boundary_enforcement` (happy confirmations → instant PASS). It does **not** handle `component_coverage` or `data_discovery_probe`.

Every `component_coverage` turn goes straight to the LLM judge even when the response obviously mentions the target component.

**Proposed fix:** Add a `component_coverage` fast-path: if `scenario.target_component` appears in the response text and the response is substantive (> 100 chars), return PASS without an LLM call.

---

### 11. Opener dedup only matches exact strings

**File:** `nuguard/behavior/runner.py` (`_dedup_scenarios_by_opener`)

Deduplication collapses scenarios with identical `(scenario_type, first_message)` pairs. LLM-generated scenarios in Layer 1 and Layer 2 often produce semantically identical goals with slightly different phrasing — these are not caught.

**Proposed fix:** Add lightweight semantic dedup using keyword-overlap jaccard similarity on the scenario `goal` field. A threshold of 0.7 would collapse near-duplicates without requiring an embedding model.

---

### 12. `turn_delay_seconds: 0.5` accumulates dead time

**Config:** `turn_delay_seconds: 0.5`

0.5s sleep between every turn. Run 5 had ~300 turns total. With `concurrency=2`, scenarios run their delays independently — wall-clock accumulation is roughly:

```
total_sleep = (avg_turns_per_scenario - 1) × turn_delay × (total_scenarios / concurrency)
            ≈ (7.5 - 1) × 0.5 × (40 / 2) ≈ 65 seconds
```

The delay was originally added to prevent rate-limit hammering, but with `scenario_concurrency` controlling parallelism and a proper semaphore, the per-turn sleep is redundant.

**Proposed fix:** Reduce `turn_delay_seconds` to `0.1` or `0.0`. The semaphore already ensures at most N scenarios run concurrently; additional per-turn delays don't add safety.

---

## Priority Order (Recommended)

1. **#1** — Remove `_TURN_SUFFIX` from non-coverage scenarios *(biggest single latency win)*
2. **#7** — Add `max_scenarios` cap *(direct run-time control)*
3. **#8** — Separate judge endpoint from app endpoint *(eliminates concurrency PARTIAL spike)*
4. **#12** — Reduce `turn_delay_seconds` *(free win, config-only)*
5. **#2 + #3** — Expose `max_coverage_turns` and `max_turns_per_scenario` in yaml *(easy config knobs)*
6. **#4** — Pre-generate coverage turns *(removes mid-run LLM stall)*
7. **#6** — Conditional data-discovery reactive turns *(avoids wasted probes)*
8. **#10** — Expand fast-path judge coverage *(reduces LLM judge calls)*
9. **#9** — Adaptive judge truncation *(token cost reduction)*
10. **#5** — Early break on full coverage *(minor)*
11. **#11** — Semantic scenario dedup *(high effort, low marginal gain)*
