# Runtime SBOM Enrichment for Redteam (High-Level)

## Purpose
Improve redteam scenario quality by enriching a static SBOM with runtime observations from a live app, while keeping the original SBOM unchanged.

## Example Application
- stateset-icommerce

## Baseline SBOM vs Corrected SBOM (stateset-icommerce)

| Area | Baseline SBOM | Corrected SBOM | Delta |
| --- | --- | --- | --- |
| Total nodes | 66 | 69 | +3 |
| Total edges | 67 | 72 | +5 |
| Added nodes | 0 | 3 | +3 |
| Updated nodes | 0 | 4 | +4 |
| Removed nodes | 0 | 0 | 0 |
| Added edges | 0 | 5 | +5 |
| Removed edges | 0 | 0 | 0 |

### Node-Level Changes

| Change Type | Component Type | Component Name |
| --- | --- | --- |
| Added | AGENT | StateSet Commerce Assistant |
| Added | API_ENDPOINT | Orders API |
| Added | TOOL | admin-order-write-tool |
| Updated | API_ENDPOINT | Health API |
| Updated | API_ENDPOINT | Webchat Message API |
| Updated | TOOL | mcp-web-fetch-tool |
| Updated | DATASTORE | postgres |

### Summary
- The corrected SBOM adds runtime-realistic structure (agent, endpoint, and tool) that was not represented in the baseline artifact.
- Existing components were refined with better metadata and relationship context, especially around API/tool/datastore flows.
- No components were removed, so the corrected artifact is additive and corrective rather than destructive.
- Net effect: better scenario grounding for redteam (especially data-exfiltration and chained access paths) while preserving traceability to the baseline SBOM.

### Evidence Source
- Baseline artifact: `output/stateset-icommerce.sbom.json`
- Corrected artifact: `output/stateset-icommerce.sbom.corrected.json`
- Comparison basis: node/edge structural diff (counts plus added/updated/removed entities)

## What Enrichment Adds
- Confirmed runtime endpoints and methods actually reachable in the app.
- Effective chat interface details (path, payload shape, required headers).
- Observed integration links (agent -> tool -> API -> datastore) not fully visible from static scan.
- Runtime behavior signals relevant to testing (auth rejection patterns, rate limiting, response schema hints).
- Additional context used by scenario generation (for example, stronger confidence in data-exfiltration and privilege-chain candidates).

## Proposed Workflow
1. Generate baseline SBOM from source (static artifact).
2. Run runtime enrichment against the live target.
3. Produce a second artifact (enriched SBOM) plus a machine-readable diff.
4. Execute redteam with enriched SBOM.
5. Keep both artifacts for traceability and review.

## Why This Helps
- Increases relevant scenario generation and execution coverage.
- Reduces false negatives caused by incomplete static metadata.
- Aligns tests with actual running behavior, not only inferred structure.

## Guardrails
- Do not mutate baseline SBOM; enrichment creates a derived artifact.
- Tag enriched fields with evidence/provenance (runtime source and timestamp).
- Keep enrichment bounded and non-destructive.
- Allow opt-in mode in CI/CD (static-only vs static+runtime).

## Suggested Decision
Adopt runtime enrichment as an optional redteam pre-step, review impact on coverage and stability, then decide whether to make it default in selected environments.
