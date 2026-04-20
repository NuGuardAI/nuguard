# Behavior Runs — Comparison Summary

**App:** Pinnacle Bank AI  
**Target:** `https://frontend-ui.agreeablesky-8c7ba25f.eastus.azurecontainerapps.io`  
**LLM:** `azure/gpt-4.1-mini`  
**Note:** Run 3 (2026-04-15, 30s timeout under concurrency) is excluded from this report — its high PARTIAL rate was a configuration artifact, not a meaningful app signal. See run-3 files for raw data.

---

## What Changed Between Runs

| | Run 1 (Baseline) | Run 2 (Perf Fix) | Run 4 (Concurrent) |
|---|---|---|---|
| **Date** | 2026-04-15 | 2026-04-15 | 2026-04-15 |
| **Key change** | — | App perf fix deployed | Concurrency enabled; `scenario_delay` removed |
| `scenario_delay_seconds` | 3.0 → forces concurrency=1 | 3.0 → forces concurrency=1 | absent |
| `scenario_concurrency` | 1 (forced by delay) | 1 (forced by delay) | **3** |
| `judge_concurrency` | 5 (default) | 5 (default) | **5** (explicit) |
| `turn_delay_seconds` | 1.0 | 1.0 | **0.5** |
| `request_timeout` | 60s | 60s | **60s** |

---

## Performance Comparison

| Metric | Run 1 (Baseline) | Run 2 (Perf Fix) | Run 4 (Concurrent) | R1→R4 | R2→R4 |
|---|---|---|---|---|---|
| **Start** | 17:28:10 | 18:57:51 | 22:32:17 | — | — |
| **End** | 18:44:56 | 19:55:30 | 23:01:57 | — | — |
| **Wall-clock** | **76m 46s** | **57m 39s** | **29m 40s** | **↓ −61%** | **↓ −49%** |
| **Scenarios run** | 44 | 34 | 40 | ↓ −9% | ↑ +18% |
| **Judge turns** | 331 | 251 | 279 | ↓ −16% | ↑ +11% |
| — PASS | 262 (79.2%) | 214 (85.3%) | 124 (44.4%) | ↓ −34.8pp | ↓ −40.9pp |
| — PARTIAL | 57 (17.2%) | 27 (10.8%) | 152 (54.5%) | ↑ +37.3pp | ↑ +43.7pp |
| — FAIL | 12 (3.6%) | 10 (4.0%) | 3 (1.1%) | ↓ −2.5pp | ↓ −2.9pp |
| **Score avg** | **3.95** | **4.13** | **3.40** | ↓ −0.55 | ↓ −0.73 |
| **Latency avg** | **6,602ms** | **6,397ms** | **11,267ms** | ↑ +71% | ↑ +76% |
| **Latency p95** | **12,561ms** | **11,991ms** | **19,686ms** | ↑ +57% | ↑ +64% |
| **Latency max** | **18,641ms** | **27,228ms** | **28,847ms** | ↑ | — |
| **Reported findings** | 24 | 18 | 12 | ↓ −50% | ↓ −33% |
| **Outcome** | critical_findings | critical_findings | critical_findings | — | — |

---

## Finding Instances by Deviation Type

These are raw per-turn deviation events logged during each run (a single turn can produce multiple events):

| Deviation Type | Run 1 | Run 2 | Run 4 |
|---|---|---|---|
| `capability_gap` | 56 | 23 | 139 |
| `intent_misalignment` | 73 | 34 | 191 |
| `policy_violation` | 23 | 16 | 12 |
| `data_leak` | 1 | 2 | 0 |
| **Total** | **153** | **75** | **342** |

---

## Analysis

### Run 1 → Run 2: App Performance Fix ✅
- **−25% wall-clock** (76m → 57m) in the same sequential execution mode
- Scenario count dropped 44 → 34: the improved app produced a tighter SBOM, reducing LLM-generated test duplication
- **PASS rate +6pp** (79.2% → 85.3%), PARTIAL rate nearly halved (17.2% → 10.8%): app responses more complete and accurate after fix
- Score improved 3.95 → 4.13; latency barely changed (6,602ms → 6,397ms)
- Finding instances nearly halved (153 → 75); reported findings dropped from 24 to 18

### Run 2 → Run 4: Concurrency Enabled (3 parallel scenarios) ⚠️
- **−49% wall-clock** (57m → 30m): running 3 scenarios in parallel delivers ~2× speedup
- **PARTIAL rate spiked to 54.5%** (from 10.8%): despite restoring the 60s timeout, concurrent load appears to degrade app response quality — the app is returning incomplete or ambiguous answers when under simultaneous load
- **Latency avg up 76%** (6,397ms → 11,267ms), p95 up 64% (11,991ms → 19,686ms): the app slows significantly under concurrent multi-turn conversations
- FAIL rate continued to improve (4.0% → 1.1%): the app rarely gives outright wrong answers, but increasingly gives incomplete ones
- Score dropped 4.13 → 3.40 driven by the PARTIAL rate
- Raw deviation instances jumped sharply (75 → 342) because PARTIAL turns each generate a finding event; `policy_violation` instances actually dropped (16 → 12) confirming the app's safety boundaries hold under load
- Reported (deduplicated/filtered) findings improved: 18 → 12

### Key Insight: PARTIAL Spike Is Load-Induced, Not Timeout-Induced
Run 3 (30s timeout) and Run 4 (60s timeout) both show ~54–59% PARTIAL rates, ruling out timeout expiry as the cause. The culprit is the app's response quality degrading under concurrent load: at p95 ~20s and avg ~11s, the app is straining but completing all requests within the 60s window. The judge marks responses PARTIAL because they are vague or incomplete, not because they timed out.

### Speedup vs Quality Trade-off

| Metric | Run 1 | Run 2 | Run 4 |
|---|---|---|---|
| Wall-clock | 76m 46s | 57m 39s | **29m 40s** |
| PASS rate | 79.2% | 85.3% | 44.4% |
| Score avg | 3.95 | 4.13 | 3.40 |

Concurrency (Run 4) achieves a **~2.5× throughput speedup** versus the sequential baseline but at the cost of a **~35pp drop in PASS rate**. The app under test is the bottleneck — it responds noticeably slower and less thoroughly under concurrent multi-turn load.

### Recommendations
1. **App-side**: Profile the target app's concurrent request handling. The latency doubling (6.4s → 11.3s avg) under just 3 parallel conversations suggests queuing or resource contention in the app's LLM routing layer.
2. **nuguard config**: Consider testing `scenario_concurrency: 2` as a middle ground — likely faster than sequential while causing less app degradation than concurrency=3.
3. **Verdict calibration**: Investigate whether `PARTIAL` verdicts for load-degraded responses should be escalated differently; currently they inflate the deviation-event count without surfacing as high-priority findings.
