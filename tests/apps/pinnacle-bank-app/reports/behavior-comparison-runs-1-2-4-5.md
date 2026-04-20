# Behavior Runs — Comparison Report: Runs 1, 2, 4, 5

**App:** Pinnacle Bank AI  
**Target:** `https://frontend-ui.agreeablesky-8c7ba25f.eastus.azurecontainerapps.io`  
**LLM Judge:** `azure/gpt-4.1-mini`  
**Date:** 2026-04-15  
**Note:** Run 3 (30s timeout under concurrency) is excluded — its results were a configuration artifact, not representative of app quality.

---

## Configuration Summary

| Setting | Run 1 (Baseline) | Run 2 (Perf Fix) | Run 4 (Concurrency=3) | Run 5 (Concurrency=2) |
|---|---|---|---|---|
| **Key change** | Baseline | App perf fix deployed | Concurrency enabled | Concurrency reduced to 2 |
| `scenario_delay_seconds` | 3.0 (forces sequential) | 3.0 (forces sequential) | absent | absent |
| `scenario_concurrency` | 1 (forced) | 1 (forced) | **3** | **2** |
| `judge_concurrency` | 5 | 5 | 5 | 5 |
| `turn_delay_seconds` | 1.0 | 1.0 | 0.5 | 0.5 |
| `request_timeout` | 60s | 60s | 60s | 60s |

---

## Performance Comparison

| Metric | Run 1 | Run 2 | Run 4 | Run 5 | R1→R5 | R2→R5 | R4→R5 |
|---|---|---|---|---|---|---|---|
| **Start** | 17:28:10 | 18:57:51 | 22:32:17 | 23:16:23 | — | — | — |
| **End** | 18:44:56 | 19:55:30 | 23:01:57 | 23:59:57 | — | — | — |
| **Wall-clock** | **76m 46s** | **57m 39s** | **29m 40s** | **43m 34s** | ↓ −43% | ↓ −24% | ↑ +47% |
| **Scenarios run** | 44 | 34 | 40 | 40 | ↓ −9% | ↑ +18% | — |
| **Judge turns** | 331 | 251 | 279 | 300 | ↓ −9% | ↑ +20% | ↑ +8% |
| — PASS | 262 (79.2%) | 214 (85.3%) | 124 (44.4%) | 166 (55.3%) | ↓ −23.9pp | ↓ −30.0pp | ↑ +10.9pp |
| — PARTIAL | 57 (17.2%) | 27 (10.8%) | 152 (54.5%) | 127 (42.3%) | ↑ +25.1pp | ↑ +31.5pp | ↓ −12.2pp |
| — FAIL | 12 (3.6%) | 10 (4.0%) | 3 (1.1%) | 7 (2.3%) | ↓ −1.3pp | ↓ −1.7pp | ↑ +1.2pp |
| **Score avg** | **3.95** | **4.13** | **3.40** | **3.58** | ↓ −0.37 | ↓ −0.55 | ↑ +0.18 |
| **Latency avg** | **6,602ms** | **6,397ms** | **11,267ms** | **10,251ms** | ↑ +55% | ↑ +60% | ↓ −9% |
| **Latency p95** | **12,561ms** | **11,991ms** | **19,686ms** | **17,073ms** | ↑ +36% | ↑ +42% | ↓ −13% |
| **Latency max** | **18,641ms** | **27,228ms** | **28,847ms** | **34,001ms** | ↑ | ↑ | ↑ |
| **Reported findings** | 24 | 18 | 12 | 17 | ↓ −29% | ↓ −6% | ↑ +42% |
| **Outcome** | critical_findings | critical_findings | critical_findings | critical_findings | — | — | — |

---

## Finding Instances by Deviation Type

Per-turn deviation events logged (a single turn can produce multiple events):

| Deviation Type | Run 1 | Run 2 | Run 4 | Run 5 |
|---|---|---|---|---|
| `capability_gap` | 56 | 23 | 139 | 117 |
| `intent_misalignment` | 73 | 34 | 191 | 141 |
| `policy_violation` | 23 | 16 | 12 | 17 |
| `data_leak` | 1 | 2 | 0 | 0 |
| **Total instances** | **153** | **75** | **342** | **275** |
| **Reported findings** | 24 | 18 | 12 | 17 |

---

## Speed vs Quality Trade-off Summary

| Run | Concurrency | Wall-clock | PASS rate | Score avg | Lat avg |
|---|---|---|---|---|---|
| Run 1 | 1 (sequential) | 76m 46s | 79.2% | 3.95 | 6,602ms |
| Run 2 | 1 (sequential) | 57m 39s | 85.3% | 4.13 | 6,397ms |
| Run 4 | 3 | 29m 40s | 44.4% | 3.40 | 11,267ms |
| Run 5 | 2 | 43m 34s | **55.3%** | **3.58** | 10,251ms |

---

## Analysis

### Run 1 → Run 2: App Performance Fix ✅
- **−25% wall-clock** (76m → 57m) in the same sequential execution mode
- Scenario count dropped 44 → 34: improved app produced a tighter SBOM, reducing LLM-generated test duplication
- **PASS rate +6pp** (79.2% → 85.3%), PARTIAL rate nearly halved (17.2% → 10.8%)
- Score improved 3.95 → 4.13; latency flat (6,602ms → 6,397ms)
- Deviation instances nearly halved (153 → 75); reported findings 24 → 18

### Run 2 → Run 4: Concurrency=3 — Speed Win, Quality Hit ⚠️
- **−49% wall-clock** (57m → 30m): ~2× speedup from 3 parallel scenarios
- **PARTIAL rate spiked to 54.5%** (from 10.8%): app backend struggles under 3 simultaneous multi-turn conversations
- Latency avg nearly doubled (6,397ms → 11,267ms); p95 up 64%
- Score dropped 4.13 → 3.40; FAIL rate actually improved (4.0% → 1.1%)
- Raw deviation instances jumped (75 → 342) — PARTIAL turns each generate a deviation event; `policy_violation` dropped (16 → 12), confirming safety boundaries hold under load

### Run 4 → Run 5: Concurrency=2 — Partial Quality Recovery ✅
- **+47% wall-clock** (30m → 44m): expected cost of reducing concurrency from 3 to 2
- **PASS rate improved +10.9pp** (44.4% → 55.3%): lower concurrent load noticeably improves response quality
- **PARTIAL rate dropped −12.2pp** (54.5% → 42.3%): still elevated vs sequential but clearly better than concurrency=3
- Score improved 3.40 → 3.58; latency avg down 9% (11,267ms → 10,251ms)
- Reported findings up (12 → 17): with better quality responses, the judge can evaluate more meaningfully — fewer turns are PARTIAL noise, more are judged fully as findings
- Deviation instances down (342 → 275): fewer PARTIAL turns means fewer per-turn deviation events

### The Concurrency Saturation Point
The data reveals a clear saturation threshold for this app's Azure OpenAI backend:

| Concurrency | PASS rate | Score avg | Lat avg | Wall-clock |
|---|---|---|---|---|
| 1 (post-fix) | 85.3% | 4.13 | 6,397ms | 57m |
| 2 | 55.3% | 3.58 | 10,251ms | 44m |
| 3 | 44.4% | 3.40 | 11,267ms | 30m |

Latency rises steeply between concurrency=1 and concurrency=2 (+60%), then only marginally from 2→3 (+10%). This suggests the app's LLM routing layer saturates at 2 simultaneous requests — adding a 3rd barely affects latency but continues degrading response quality (PASS rate drops a further 11pp).

### Recommendations

1. **Optimal concurrency for this app: 2** — gives ~24% wall-clock reduction vs sequential (44m vs 58m) with a meaningful quality penalty (55% vs 85% PASS). Use when throughput matters more than perfect fidelity.

2. **Use sequential (concurrency=1) for authoritative quality gates** — 85% PASS rate and score 4.13 represent the app's true behaviour without load-induced degradation.

3. **App-side investigation warranted** — the latency doubling between sequential and concurrent=2 (6.4s → 10.3s avg) indicates resource contention in the app's Azure OpenAI call path. Profiling under 2 simultaneous requests could identify whether this is token rate limiting, connection pooling, or session state locking.

4. **PARTIAL as a load signal** — the elevated PARTIAL rate under concurrency is not noise; it reflects real degradation in response completeness. Consider tracking PARTIAL rate as a secondary SLO for the app under test alongside standard latency p95.
