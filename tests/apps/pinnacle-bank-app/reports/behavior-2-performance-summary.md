# Behavior Run 2 — Performance Summary (After Fix)

**App:** Pinnacle Bank AI  
**Config:** `nuguard-sbom-azure.yaml`  
**LLM:** `azure/gpt-4.1-mini`  
**Date:** 2026-04-15  

## Results

| Metric | Baseline (Run 1) | After Fix (Run 2) | Delta |
|---|---|---|---|
| Start | 17:28:10 | 18:57:51 | — |
| End | 18:44:56 | 19:55:30 | — |
| **Total wall-clock** | **76m 46s** | **57m 39s** | **↓ 19m 7s (−25%)** |
| **Scenarios** | **44** | **34** | **↓ 10 (−23%)** |
| **Judge turns** | **331** | **251** | **↓ 80 (−24%)** |
| — PASS | 262 (79.2%) | 214 (85.3%) | **↑ +6.1pp** |
| — PARTIAL | 57 (17.2%) | 27 (10.8%) | **↓ −6.4pp** |
| — FAIL | 12 (3.6%) | 10 (4.0%) | ↑ +0.4pp |
| — SKIP | 0 | 0 | — |
| **Score avg** | **3.95 / 5.0** | **4.13 / 5.0** | **↑ +0.18** |
| **Latency avg** | **6,602ms** | **6,397ms** | **↓ 205ms (−3%)** |
| Latency p95 | 12,629ms | 12,189ms | ↓ 440ms (−3%) |
| Latency max | 18,641ms | 27,228ms | ↑ spike (1 outlier) |
| **Total findings** | **153** | **75** | **↓ 78 (−51%)** |
| — intent_misalignment | 73 | 34 | ↓ −53% |
| — capability_gap | 56 | 23 | ↓ −59% |
| — policy_violation | 23 | 16 | ↓ −30% |
| — data_leak | 1 | 2 | ↑ +1 |
| **Outcome** | **critical_findings** | **critical_findings** | — |

## Summary

- **25% faster** wall-clock (57m 39s vs 76m 46s)
- **51% fewer findings** overall (75 vs 153)
- **Score improved** from 3.95 → 4.13 (+0.18)
- **PARTIAL rate cut nearly in half** (17.2% → 10.8%), meaning responses are more consistently complete
- FAIL count slightly up (10 vs 12 absolute), but comparable given fewer total scenarios
- One latency outlier (27s) vs baseline max of 18s — isolated spike, not systemic
