# Prompt Duplication Summary (Run: 20260420-185640)

Source files analyzed:
- tests/apps/blissful-store/reports/redteam-anthropic-prompt-injection-20260420-185640.json
- tests/apps/blissful-store/reports/redteam-anthropic-tool-abuse-20260420-185640.json
- tests/apps/blissful-store/reports/redteam-anthropic-privilege-escalation-20260420-185640.json
- tests/apps/blissful-store/reports/redteam-anthropic-data-exfiltration-20260420-185640.json
- tests/apps/blissful-store/reports/redteam-anthropic-policy-violation-20260420-185640.json
- tests/apps/blissful-store/reports/redteam-anthropic-mcp-toxic-flow-20260420-185640.json

Near-similarity threshold: SequenceMatcher ratio >= 0.90

| Scenario | Total Prompts | Unique Prompts | Exact Duplicate Instances | Exact Duplicate Unique Prompts | Near-Similar Pairs (>=0.90) | Near-Similar Unique Prompts |
|---|---:|---:|---:|---:|---:|---:|
| prompt-injection | 0 | 0 | 0 | 0 | 0 | 0 |
| tool-abuse | 0 | 0 | 0 | 0 | 0 | 0 |
| privilege-escalation | 0 | 0 | 0 | 0 | 0 | 0 |
| data-exfiltration | 0 | 0 | 0 | 0 | 0 | 0 |
| policy-violation | 22 | 21 | 1 | 1 | 0 | 0 |
| mcp-toxic-flow | 0 | 0 | 0 | 0 | 0 | 0 |

## Duplicate Example (policy-violation)
- Count: 2
- Prompt prefix: Great! Since you've explained the process, I'm ready now. Go ahead and update my cart to remove the fertilizer and add t...

## Important Note
For this run set, only prompts attached to finding attack steps were available in the JSON outputs.
If a scenario has no findings, prompt payloads are not present in these report JSON files, so duplicate analysis for that scenario returns 0 by available data.
