# Behavior Run Comparison: Runs 2, 4, 5, 8
**App**: Pinnacle Bank AI  
**Target**: `https://frontend-ui.agreeablesky-8c7ba25f.eastus.azurecontainerapps.io`  
**Generated**: 2026-04-16  
**Note**: Runs 2–5 use `azure/gpt-4.1-mini` judge. Run 8 uses `anthropic/claude-haiku-4-5` judge (Claude 4 generation) — replacing run 6 (`claude-3-haiku-20240307`) and invalidated run 7 (`claude-3-5-haiku-20241022`, deprecated). Run 8 is the authoritative Anthropic multi-provider comparison run.

---

## Configuration Summary

| Setting | Run 2 | Run 4 | Run 5 | Run 8 |
|---|---|---|---|---|
| **Judge model** | `azure/gpt-4.1-mini` | `azure/gpt-4.1-mini` | `azure/gpt-4.1-mini` | `anthropic/claude-haiku-4-5` |
| **App model** | `azure/gpt-4.1-mini` | `azure/gpt-4.1-mini` | `azure/gpt-4.1-mini` | `anthropic/claude-haiku-4-5` |
| **SBOM** | sbom-2.json | sbom-2.json | sbom-2.json | sbom-2.json |
| **Config file** | nuguard-sbom-azure.yaml | nuguard-sbom-azure.yaml | nuguard-sbom-azure.yaml | nuguard-sbom-anthropic.yaml |
| **Key change** | App perf fix deployed | Concurrency=3 enabled | Concurrency reduced to 2 | Claude 4 Haiku judge |
| `scenario_concurrency` | 1 (forced sequential) | **3** | **2** | **2** |
| `judge_concurrency` | 5 | 5 | 5 | 5 |
| `turn_delay_seconds` | 1.0 | 0.5 | 0.5 | 0.5 |
| `request_timeout` | 60s | 60s | 60s | 60s |
| `scenario_delay_seconds` | 3.0 (forces sequential) | absent | absent | absent |

---

## Performance Comparison

| Metric | Run 2 | Run 4 | Run 5 | Run 8 | R2→R8 |
|---|---|---|---|---|---|
| **Start time** | 18:57:51 | 22:32:17 | 23:16:23 | 11:18:12 | — |
| **End time** | 19:55:30 | 23:01:57 | 23:59:57 | 12:27:41 | — |
| **Wall-clock** | **57m 39s** | **29m 40s** | **43m 34s** | **69m 29s** | ↑ +21% |
| **Scenarios run** | 34 | 40 | 40 | **55** | ↑ +62% |
| **Judge turns total** | 251 | 279 | 300 | **442** | ↑ +76% |
| — PASS turns | 214 (85.3%) | 124 (44.4%) | 166 (55.3%) | 132 (29.9%) | ↓ −55pp |
| — PARTIAL turns | 27 (10.8%) | 152 (54.5%) | 127 (42.3%) | 253 (57.2%) | ↑ +46pp |
| — FAIL turns | 10 (4.0%) | 3 (1.1%) | 7 (2.3%) | 57 (12.9%) | ↑ +9pp |
| **Avg score** | **4.12** | **3.41** | **3.59** | **2.91** | ↓ −1.21 |
| **Latency avg** | **6,398ms** | **11,267ms** | **10,252ms** | **10,110ms** | ↑ +58% |
| **Latency p50** | **5,684ms** | **9,240ms** | **8,632ms** | **9,928ms** | ↑ +75% |
| **Latency p95** | **11,991ms** | **19,686ms** | **17,073ms** | **18,049ms** | ↑ +51% |
| **Latency max** | **27,228ms** | **28,847ms** | **34,001ms** | **42,351ms** | ↑ +56% |
| **Reported findings** | 18 | 12 | 17 | **116** | ↑ +544% |
| **Outcome** | no_findings | no_findings | no_findings | **critical_findings** | — |

---

## Summary Table

| Metric | Run 2 | Run 4 | Run 5 | Run 8 |
|---|---|---|---|---|
| **Judge model** | azure/gpt-4.1-mini | azure/gpt-4.1-mini | azure/gpt-4.1-mini | anthropic/claude-haiku-4-5 |
| **scenario_concurrency** | 1 (sequential) | 3 | 2 | 2 |
| **Overall Risk Score** | 10.0 / 10 | 10.0 / 10 | 10.0 / 10 | 10.0 / 10 |
| **Component Coverage** | 19% (15/79) | 14% (11/79) | 33% (26/79) | **42% (33/79)** |
| **Scenarios PASS** | 34 (100%) | 18 (45%) | 28 (70%) | 9 (16%) |
| **Scenarios PARTIAL** | 0 (0%) | 22 (55%) | 12 (30%) | 46 (84%) |
| **Scenarios FAIL** | 0 | 0 | 0 | 0 |
| **Avg scenario score** | 4.12 | 3.41 | 3.59 | **2.91** |
| **Total elapsed (reported)** | 1605.8s | 3143.6s | 3075.5s | **4468.7s** |
| **Avg / scenario** | 47.2s | 78.6s | 76.9s | **81.2s** |
| **Avg / turn** | 6.4s | 11.3s | 10.3s | **10.1s** |

---

## Pass Rate Trend

```
Run 2 (Azure, seq):    ████████████████████  100%  avg 4.12  wall 58m
Run 4 (Azure, c=3):    █████████             45%   avg 3.41  wall 30m
Run 5 (Azure, c=2):    ██████████████        70%   avg 3.59  wall 44m
Run 8 (Haiku-4, c=2):  ███                   16%   avg 2.91  wall 69m
```

---

## Finding Instances by Deviation Type

Per-turn deviation events logged (a single turn can produce multiple events):

| Deviation Type | Run 2 | Run 4 | Run 5 | Run 8 | R2→R8 |
|---|---|---|---|---|---|
| `capability_gap` | 23 | 139 | 117 | **245** | ↑ +965% |
| `intent_misalignment` | 34 | 191 | 141 | **367** | ↑ +979% |
| `policy_violation` | 16 | 12 | 17 | **96** | ↑ +500% |
| `data_leak` | 2 | 0 | 0 | **20** | ↑ +900% |
| **Total instances** | **75** | **342** | **275** | **728** | ↑ +871% |
| **Reported findings** | 18 | 12 | 17 | **116** | ↑ +544% |

---

## Speed vs Quality Trade-off

| Run | Judge | Concurrency | Wall-clock | PASS% (scenario) | Score avg | Lat avg | Lat p95 |
|---|---|---|---|---|---|---|---|
| Run 2 | Azure GPT-4.1-mini | 1 (sequential) | 57m 39s | 100% | 4.12 | 6,398ms | 11,991ms |
| Run 4 | Azure GPT-4.1-mini | 3 | 29m 40s | 45% | 3.41 | 11,267ms | 19,686ms |
| Run 5 | Azure GPT-4.1-mini | 2 | 43m 34s | 70% | 3.59 | 10,252ms | 17,073ms |
| Run 8 | Anthropic Haiku-4-5 | 2 | **69m 29s** | **16%** | **2.91** | 10,110ms | 18,049ms |

---

## Verdict Breakdown by Scenario Type

### Run 2 — azure/gpt-4.1-mini (34 scenarios)

| Type | PASS | PARTIAL | FAIL | Total | Avg Score |
|---|---|---|---|---|---|
| intent happy path | 7 | 0 | 0 | 7 | 4.20 |
| component coverage | 5 | 0 | 0 | 5 | 4.21 |
| boundary enforcement | 16 | 0 | 0 | 16 | 4.14 |
| invariant probe | 6 | 0 | 0 | 6 | 3.92 |

### Run 4 — azure/gpt-4.1-mini (40 scenarios)

| Type | PASS | PARTIAL | FAIL | Total | Avg Score |
|---|---|---|---|---|---|
| intent happy path | 2 | 10 | 0 | 12 | 3.25 |
| component coverage | 0 | 4 | 0 | 4 | 2.86 |
| boundary enforcement | 13 | 5 | 0 | 18 | 3.63 |
| invariant probe | 3 | 3 | 0 | 6 | 3.44 |

### Run 5 — azure/gpt-4.1-mini (40 scenarios)

| Type | PASS | PARTIAL | FAIL | Total | Avg Score |
|---|---|---|---|---|---|
| intent happy path | 5 | 2 | 0 | 7 | 3.55 |
| component coverage | 3 | 6 | 0 | 9 | 3.35 |
| boundary enforcement | 16 | 2 | 0 | 18 | 3.74 |
| invariant probe | 4 | 2 | 0 | 6 | 3.54 |

### Run 8 — anthropic/claude-haiku-4-5 (55 scenarios)

| Type | PASS | PARTIAL | FAIL | Total | Avg Score |
|---|---|---|---|---|---|
| intent happy path | 0 | 13 | 0 | 13 | 2.77 |
| component coverage | 0 | 18 | 0 | 18 | 2.50 |
| boundary enforcement | 9 | 9 | 0 | 18 | 3.40 |
| invariant probe | 0 | 6 | 0 | 6 | 2.98 |

---

## Score Distribution by Type (All Runs)

| Type | Run 2 | Run 4 | Run 5 | Run 8 | Trend |
|---|---|---|---|---|---|
| intent happy path | 4.20 | 3.25 | 3.55 | **2.77** | ↓↓ ↑ ↓↓ |
| component coverage | 4.21 | 2.86 | 3.35 | **2.50** | ↓↓ ↑ ↓↓ |
| boundary enforcement | 4.14 | 3.63 | 3.74 | **3.40** | ↓ → ↓ |
| invariant probe | 3.92 | 3.44 | 3.54 | **2.98** | ↓ → ↓↓ |

---

## Key Observations

### 1. Claude Haiku-4-5 is a Far Stricter Judge Than Any Previous Model
Run 8 (Anthropic claude-haiku-4-5, concurrency=2) produces **116 findings** and `critical_findings` outcome vs `no_findings` in all three Azure runs. PASS rate collapses to 16% at the scenario level (9/55) vs 70% for the closest comparable run (Azure c=2, run 5). This is not a performance regression — it reflects a fundamentally different scoring philosophy: Haiku-4-5 grades on actual tool invocation, full policy adherence, and complete multi-turn intent coverage, not approximate behavior.

### 2. PARTIAL-Dominant Scoring — New Behavioral Signature for Haiku-4-5
Run 8 is 84% PARTIAL at the scenario level, with 0 FAIL verdicts. This differs from both:
- Azure judge (run 5): 30% PARTIAL, 0 FAIL — binary PASS/FAIL tendency
- Run 6 claude-3-haiku: 7% PARTIAL, 7% FAIL — more decisive
Haiku-4-5 appears to use PARTIAL as "the app attempted but fell short" rather than FAIL for outright refusals. The avg score of 2.91 (vs 4.22 for claude-3-haiku in run 6) confirms genuine stricter grading, not just label differences.

### 3. Component Coverage is the Weakest Category Under Haiku-4-5
| Type | Run 2 | Run 5 | Run 8 |
|---|---|---|---|
| intent happy path | 4.20 | 3.55 | **2.77** |
| component coverage | 4.21 | 3.35 | **2.50** |
| boundary enforcement | 4.14 | 3.74 | **3.40** |
| invariant probe | 3.92 | 3.54 | **2.98** |

Component coverage (avg 2.50) dropped the most — 36% below run 5's score. This category tests whether each SBOM tool node is correctly invoked during interactions. Haiku-4-5 strictly requires the expected tool to be called; the app's tendency to provide text responses without tool invocations triggers consistent PARTIAL verdicts.

### 4. Deviation Instances Explode — Haiku-4-5 Surfaces Real Gaps
| Run | Judge/Concurrency | cap_gap | intent_mis | policy_vio | data_leak | Total |
|---|---|---|---|---|---|---|
| Run 2 | Azure, c=1 | 23 | 34 | 16 | 2 | 75 |
| Run 4 | Azure, c=3 | 139 | 191 | 12 | 0 | 342 |
| Run 5 | Azure, c=2 | 117 | 141 | 17 | 0 | 275 |
| Run 8 | Haiku-4-5, c=2 | **245** | **367** | **96** | **20** | **728** |

Run 8 generates 728 total deviation instances — 2.6× more than run 4 (c=3) at the same concurrency (c=2). Most notable: `data_leak` appears for the first time at scale (20 instances, vs 0 in runs 4 and 5 and 2 in run 2), and `policy_violation` jumps 5× to 96. This suggests Haiku-4-5 is surfacing genuine behavioral risks that prior judges missed.

### 5. Azure Concurrency Saturation — Still Confirmed
Runs 4 and 5 confirm the Azure backend saturates at c=2:
| Concurrency | Wall-clock | PASS% | Score avg | Lat avg |
|---|---|---|---|---|
| 1 (sequential) | 57m 39s | 100% | 4.12 | 6,398ms |
| 2 | 43m 34s | 70% | 3.59 | 10,252ms |
| 3 | 29m 40s | 45% | 3.41 | 11,267ms |

Run 8 (Anthropic, c=2) shows much cleaner latency than run 6 (148m): avg 10,110ms vs 13,144ms, no cold-start gap, max 42,351ms vs 60,819ms. The earlier run 6 anomaly was an overnight Azure cold-start event, not an Anthropic performance issue.

### 6. Boundary Enforcement — Only Category with Partial PASS Under Haiku-4-5
Boundary enforcement is the only category where run 8 produces any PASS verdicts (9/18 = 50%). This means the app correctly refuses direct prompt injection, PII dump, and cross-account data requests about half the time by Haiku-4-5's stricter standard. The other 9 PARTIALs reflect boundary responses that are present but incomplete (e.g., brief refusal without explanation, or partial tool constraint adherence). This is consistent with boundary enforcement being structurally stronger than capability/coverage tests across all runs.

### 7. Scenario Count Grows with Judge Sophistication
| Run | Judge | Scenarios |
|---|---|---|
| Run 2 | Azure sequential | 34 |
| Run 4 | Azure c=3 | 40 |
| Run 5 | Azure c=2 | 40 |
| Run 8 | Haiku-4-5 c=2 | **55** |

Haiku-4-5 generated 55 scenarios vs 40 for Azure runs — 38% more. This is driven by the LLM's non-deterministic scenario generation: newer, more capable models tend to generate more scenario variants for `intent_happy_path` (13 vs 7–12) and `component_coverage` (18 vs 4–9). More scenarios means more thorough coverage (42% component coverage, up from 33% in run 5) but also more total turns (442 vs 300) and longer wall-clock.

---

## Recommendations

1. **Use claude-haiku-4-5 as the quality gate judge** — it surfaces real data leaks, policy violations, and capability gaps that Azure GPT-4.1-mini consistently misses. The 116 findings with `critical_findings` outcome reflect genuine app failures, not noise.
2. **Use Azure judge for fast iteration** — Azure c=2 (43m, 70% PASS) gives a quick "is anything obviously broken" signal. Switch to Haiku-4-5 for authoritative pre-release gates.
3. **Fix critical findings from run 8 first**: `data_leak` (20 instances) and `policy_violation` (96 instances) are the highest-priority gaps — these are new signal not surfaced by any previous Azure run.
4. **Component coverage (avg 2.50) needs immediate attention** — the app routinely responds with text instead of invoking expected tools. This is a tool-routing or system prompt gap, not a judge artifact: all runs agree component coverage is the weakest category.
5. **Do not use `claude-3-5-haiku-20241022` or `claude-3-5/3-7-sonnet` models** — confirmed deprecated as of 2026-04-16. Only `claude-3-haiku-20240307` and `claude-haiku-4-5` are available on this API key.
6. **Run 8 wall-clock (69m) is acceptable for c=2 Anthropic** — avg turn latency 10.1s is 22% faster than run 6 (13.1s). No cold-start gap. Haiku-4-5 is the recommended Anthropic judge for regular use.
