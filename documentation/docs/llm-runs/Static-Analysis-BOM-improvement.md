# Static Analysis SBOM Evidence Improvement

## Summary

This plan improves NuGuard's AI-SBOM so static analysis has the evidence needed
for all 18 NGA rules and the MITRE ATLAS native checks.

The current implementation already models many useful typed fields, but several
static rules still depend on older `summary` or `metadata.extras` fields, and
some adapters do not preserve enough source-backed evidence for the analyzer to
make precise findings.

## Gaps Identified

- **Finding evidence is too thin:** NGA and ATLAS findings do not consistently
  include source path, line, snippet, confidence, or the supporting SBOM node.
- **GitHub Actions evidence is dropped:** NGA-010, NGA-011, and NGA-014 expect
  raw or structured workflow evidence, but `GitHubActionsAdapter` emits workflow
  metadata without preserving unsafe expression details.
- **Kubernetes NetworkPolicy is not captured:** NGA-013 checks
  `has_network_policy`, but the SBOM model and K8s adapter path do not currently
  populate that field for workload nodes.
- **Typed metadata is not consumed consistently:** rules often inspect
  `metadata.extras` or summary keys while newer typed fields such as
  `auth_required`, `has_resource_limits`, `instrumentation`,
  `encryption_detail`, and `data_handling` may hold the better evidence.
- **Model supply-chain evidence is incomplete:** NGA-008 and ATLAS-NC-001 need
  model source URL, digest/checksum/signature/provenance, and integrity hash
  evidence. Model adapters mostly capture provider/model-card details today.
- **Datastore write-path evidence is imprecise:** ATLAS-NC-002 describes a
  writable datastore path, but `RelationshipHint` cannot carry `access_type`,
  and the native check does not distinguish read from write reachability.
- **Auth and guardrail graph edges are too broad:** fallback edges such as
  `AUTH -> all API_ENDPOINT` can hide missing route-specific auth. Guardrail
  coverage has the same risk for model/agent paths.
- **IAM isolation is under-modeled:** NGA-007 and NGA-018 need scoped identity
  bindings. Current fallback edges broadly attach IAM to deployments and do not
  express per-agent or per-datastore isolation.
- **Audit and HITL evidence is weak:** NGA-009 does not fully consume the newer
  instrumentation summary, and NGA-012 relies on string matching rather than
  typed approval-gate metadata.
- **Test coverage is uneven:** tests cover only part of the NGA suite and do not
  exercise enough generated-SBOM fixture paths for the evidence required by all
  rules.

## Key Changes

### Rule evidence contract

- Add a common static-analysis evidence shape:
  `[{path, line, snippet, kind, confidence, component_id, component_name}]`.
- Update NGA and ATLAS findings to include this evidence whenever a finding is
  based on a node, edge, or summary-level scanner result.
- Add shared helpers in `nuguard/analysis/plugins/nga_rules.py` and
  `nuguard/analysis/plugins/atlas_annotator.py` to pull evidence from affected
  SBOM nodes and workflow/security summary entries.

### SBOM schema and models

- Extend `NodeMetadata` with:
  - `has_network_policy: bool | None`
  - `source_url: str | None`
  - `integrity_hash: str | None`
  - `checksum: str | None`
  - `hitl_required: bool | None`
  - `approval_gates: list[str] | None`
  - `external_domains: list[str] | None`
  - `outbound_methods: list[str] | None`
- Extend `ScanSummary` with:
  - `workflow_security_findings: list[dict]`
- Extend `RelationshipHint` with:
  - `access_type: str | None`
- Preserve `RelationshipHint.access_type` into `Edge.access_type`.
- Update `docs/sbom-schema.md` and the generated JSON schema to document the
  new fields.

### Adapter and extractor improvements

- **GitHub Actions adapter**
  - Detect unsafe `pull_request_target` interpolation.
  - Detect unsafe `$GITHUB_ENV` writes from untrusted expressions.
  - Detect `ACTIONS_RUNNER_DEBUG`.
  - Store structured finding records with path, line, snippet, rule signal, and
    confidence in `summary.workflow_security_findings`.

- **Kubernetes adapter**
  - Parse multi-document YAML.
  - Emit `NetworkPolicy` evidence or associate it with matching workload labels.
  - Set `deployment.metadata.has_network_policy` for covered workloads.

- **Model adapters**
  - Capture model artifact `source_url` for Hugging Face, local model paths,
    registry URLs, and explicit download/load calls.
  - Capture `integrity_hash`, `checksum`, digest, signature, or provenance
    fields when detectable from code/config.
  - Map legacy model provenance values into typed metadata while keeping
    compatibility in `extras`.

- **Datastore and graph extraction**
  - Infer `Edge.access_type` as `read`, `write`, or `readwrite` for common
    datastore operations.
  - Preserve access type from framework adapters through relationship hints.
  - Avoid treating read-only datastore paths as ATLAS-NC-002 write risks.

- **Auth, guardrail, and IAM graph precision**
  - Prefer explicit route/component relationships over broad fallback edges.
  - Mark fallback edges with lower-confidence evidence where possible.
  - Preserve route-specific `AUTH/GUARDRAIL -> API_ENDPOINT` protection.
  - Add scoped IAM binding metadata where adapters can infer service account,
    role, namespace, resource, or datastore relationship.

## Analyzer Updates

- Make every NGA rule read typed metadata first, then `metadata.extras`, then
  summary-level fallback fields.
- Update NGA-002 to be path-aware for guardrail coverage.
- Update NGA-006 to use `auth_required`, `no_auth_required`, and explicit
  `PROTECTS` evidence.
- Update NGA-009 to consume `summary.instrumentation` and node instrumentation.
- Update NGA-010, NGA-011, and NGA-014 to consume
  `summary.workflow_security_findings`.
- Update NGA-012 to consume typed `hitl_required` and `approval_gates`.
- Update NGA-015 to treat `has_resource_limits is False` as the canonical
  signal.
- Update ATLAS-NC-001 to consume typed model integrity/provenance fields.
- Update ATLAS-NC-002 to require `write` or `readwrite` datastore reachability.
- Update ATLAS-NC-003 to prefer explicit model-serving/deployment/auth paths and
  downgrade fallback-only evidence.
- Keep backward compatibility with existing SBOMs by preserving legacy reads
  from `extras` and old summary keys.

## Test Plan

- Add unit tests for NGA-001 through NGA-018 with typed metadata and legacy
  `extras` inputs.
- Add ATLAS native tests for NC-001 through NC-004 covering positive, negative,
  and evidence-output cases.
- Add generated-SBOM fixture tests for:
  - unsafe GitHub Actions workflow patterns,
  - K8s deployment with and without matching NetworkPolicy,
  - external model with and without integrity metadata,
  - write-capable datastore path with and without guardrail,
  - endpoint with route-specific auth and unauthenticated endpoint.
- Run:
  - `pytest nuguard/analysis/tests/test_nga_rules.py`
  - `pytest nuguard/analysis/tests/test_atlas_annotator.py`
  - `pytest nuguard/sbom/tests`
- Run one CLI smoke test:
  - generate an SBOM for a fixture app,
  - run `nuguard analyze --nga --format json`,
  - verify findings include structured evidence.

## Assumptions

- Do not store secret values or full dotenv values in the SBOM.
- Store secret-related evidence as key names, source snippets, and structured
  scanner findings only.
- Keep MITRE ATLAS analysis offline by default.
- Treat the embedded `_atlas_data.py` dataset as versioned data and document a
  maintenance process for refreshing it when MITRE ATLAS changes.
- Preserve compatibility for existing SBOM consumers by adding fields without
  removing existing ones.
