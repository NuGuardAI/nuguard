# Runtime SBOM Enrichment for Redteam (High-Level)

## Purpose
Improve redteam scenario quality by enriching a static SBOM with runtime observations from a live app, while keeping the original SBOM unchanged.

## Example Application
- stateset-icommerce

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
