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

## Implemented Behavior (Current)

Runtime enrichment is now integrated directly into redteam execution with default
auto behavior and no CLI options.

### Decision Rule (Auto)
- Redteam scores baseline SBOM confidence before scenario generation.
- If confidence is high, baseline SBOM is used as-is.
- If confidence is low, enrichment runs and the enriched SBOM is used downstream.

### Confidence Inputs
- Presence of AGENT nodes.
- API endpoint coverage (count and metadata completeness).
- Tool coverage and tool-to-datastore path evidence.
- Datastore sensitivity metadata (classified_fields and/or pii_fields/phi_fields).

### Enrichment Steps
1. Clone baseline SBOM in memory (never mutate original object).
2. Apply safe static enrichment:
	- infer missing AGENT node when graph strongly implies one,
	- fill API endpoint/method metadata gaps,
	- add API nodes discovered in summary.api_endpoints,
	- derive flat pii_fields from classified_fields where available,
	- add missing AGENT->MODEL/TOOL and minimal TOOL->DATASTORE edges when justified.
3. Optionally probe target URL with a bounded request budget:
	- small GET/POST checks for discovered API paths,
	- update auth_required/rate_limited/response_text_key metadata from observed behavior,
	- add endpoint nodes only when runtime evidence exists.
4. Re-score confidence after enrichment.
5. Use enriched SBOM for scenario generation/execution.
6. Write a derived SBOM artifact (`*.enriched.json`) next to the input SBOM when enrichment changed the graph.

### Failure Behavior
- Enrichment is best-effort; if enrichment/probing fails, redteam continues with baseline SBOM.
- Any failure is logged as warning and does not block scan execution.

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
