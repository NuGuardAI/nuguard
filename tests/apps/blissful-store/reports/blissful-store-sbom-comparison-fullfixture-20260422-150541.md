# Blissful Store Full-Fixture SBOM Semantic Comparison

Generated at: 2026-04-22 15:05:41

Compared files:
- tests/apps/blissful-store/reports/blissful-store-sbom-fullfixture-no-llm-20260422-145402.json
- tests/apps/blissful-store/reports/blissful-store-sbom-fullfixture-llm-azure-gpt-4.1-20260422-150109.json
- tests/apps/blissful-store/reports/blissful-store-sbom-fullfixture-llm-gemini-3.1-flash-lite-preview-20260422-150109.json
- tests/apps/blissful-store/reports/blissful-store-sbom-fullfixture-llm-claude-sonnet-4-5-20260422-150109.json

## High-Level Results

| Variant | Nodes | Edges | Node Hash (semantic) | Edge Hash (semantic) | Same as no-llm? |
|---|---:|---:|---|---|---|
| no-llm | 40 | 26 | `2dceba1464ca` | `ca65357d5598` | yes |
| azure/gpt-4.1 | 40 | 26 | `2dceba1464ca` | `ca65357d5598` | yes |
| gemini/gemini-3.1-flash-lite-preview | 40 | 26 | `2dceba1464ca` | `ca65357d5598` | yes |
| claude-sonnet-4-5 | 40 | 26 | `2dceba1464ca` | `ca65357d5598` | yes |

## Node Type Counts

| Variant | AGENT | API_ENDPOINT | AUTH | CONTAINER_IMAGE | DEPLOYMENT | FRAMEWORK | GUARDRAIL | MODEL | PRIVILEGE | PROMPT | TOOL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| no-llm | 3 | 2 | 1 | 1 | 2 | 1 | 2 | 7 | 3 | 5 | 13 |
| azure/gpt-4.1 | 3 | 2 | 1 | 1 | 2 | 1 | 2 | 7 | 3 | 5 | 13 |
| gemini/gemini-3.1-flash-lite-preview | 3 | 2 | 1 | 1 | 2 | 1 | 2 | 7 | 3 | 5 | 13 |
| claude-sonnet-4-5 | 3 | 2 | 1 | 1 | 2 | 1 | 2 | 7 | 3 | 5 | 13 |

## Edge Type Counts

| Variant | CALLS | DEPLOYS | USES |
|---|---|---|---|
| no-llm | 15 | 2 | 9 |
| azure/gpt-4.1 | 15 | 2 | 9 |
| gemini/gemini-3.1-flash-lite-preview | 15 | 2 | 9 |
| claude-sonnet-4-5 | 15 | 2 | 9 |

## Diff vs No-LLM Baseline (Semantic)

### azure/gpt-4.1
- Added nodes: 0
- Removed nodes: 0
- Added edges: 0
- Removed edges: 0

### gemini/gemini-3.1-flash-lite-preview
- Added nodes: 0
- Removed nodes: 0
- Added edges: 0
- Removed edges: 0

### claude-sonnet-4-5
- Added nodes: 0
- Removed nodes: 0
- Added edges: 0
- Removed edges: 0

## Conclusion

- All four full-fixture SBOMs are semantically identical.