# Behavior Run 1 — Performance Summary

**App:** Pinnacle Bank AI  
**Config:** `nuguard-sbom-azure.yaml`  
**LLM:** `azure/gpt-4.1-mini`  
**Date:** 2026-04-15  

| Metric | Baseline (Run 1) |
|---|---|
| Start | 17:28:10 |
| End | 18:44:56 |
| **Total wall-clock** | **76m 46s** |
| Scenarios | 44 |
| **Judge turns** | **331** |
| — PASS | 262 |
| — PARTIAL | 57 |
| — FAIL | 12 |
| Score avg | 3.95 / 5.0 |
| **Latency avg** | **6,602 ms** |
| Latency p95 | 12,629 ms |
| Latency max | 18,641 ms |
| **Total findings** | **153** |
| — intent_misalignment | 73 |
| — capability_gap | 56 |
| — policy_violation | 23 |
| — data_leak | 1 |
| Outcome | critical_findings |
