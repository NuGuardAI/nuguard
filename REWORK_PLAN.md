# REWORK_PLAN.md

## Affected Files
- `nuguard/cli/commands/redteam.py`: CLI entry path for redteam execution, config binding, and orchestrator construction.
- `nuguard/redteam/executor/orchestrator.py`: Main control plane for scenario generation, endpoint auto-discovery, execution, and finding synthesis.
- `nuguard/redteam/executor/executor.py`: Step-level request execution path (chat vs direct HTTP), response processing, success signal logic, and policy/canary evaluation.
- `nuguard/redteam/target/client.py`: HTTP transport contract for POST chat payloads and direct endpoint invocation, including current error/circuit behavior.
- `nuguard/redteam/scenarios/generator.py`: API attack scenario generation from SBOM endpoint metadata.
- `nuguard/redteam/scenarios/api_attacks.py`: Current static direct-HTTP probes (auth bypass, mass assignment, IDOR).
- `nuguard/sbom/models.py`: Contract surface for endpoint shape metadata already available without LLM (`request_body_schema`, `chat_payload_key`, `response_text_key`, `path_params`).
- `nuguard/sbom/extractor/framework_adapters/fastapi.py`: Non-LLM extraction of request/response field shape for FastAPI.
- `nuguard/sbom/extractor/framework_adapters/flask.py`: Non-LLM extraction of likely prompt key for Flask handlers.
- `nuguard/sbom/extractor/core.py`: Materialization of endpoint shape metadata into SBOM nodes consumed by redteam.
- `nuguard/config.py`: Public config keys for target endpoint and auth header.
- `nuguard.yaml.example`: User-facing config contract documentation for redteam endpoint/auth/timeout.
- `nuguard/redteam/tests/test_target_client.py`: Existing tests for HTTP error handling and circuit breaker semantics.

## Data Flow Impact
- Current verified flow for prompt POST is implemented:
  1. CLI resolves endpoint config in `nuguard/cli/commands/redteam.py`.
  2. Orchestrator can auto-discover chat path/payload key from SBOM metadata in `nuguard/redteam/executor/orchestrator.py`.
  3. Prompt payload is posted in `nuguard/redteam/target/client.py`.
  4. Response text/tool calls are parsed and consumed by executor logic in `nuguard/redteam/executor/executor.py`.

- Verified gap: auth header is parsed but not propagated to request execution.
- `auth_header` is parsed into `extra_headers` in `nuguard/cli/commands/redteam.py`, but never passed to `nuguard/redteam/target/client.py`. This impacts protected API endpoints and can manifest as 401/403/404 patterns.

- Current API-shape handling without LLM is partially implemented:
- FastAPI and Flask adapters extract endpoint shape hints into SBOM (`nuguard/sbom/extractor/framework_adapters/fastapi.py`, `nuguard/sbom/extractor/framework_adapters/flask.py`).
- Runtime sender currently supports one chat request shape (`{chat_payload_key: str|list}`) and fixed response-key fallbacks in `nuguard/redteam/target/client.py`.
- There is no generalized runtime schema-driven serializer/deserializer using `request_body_schema`/`response_text_key` yet.

- Error behavior and completion semantics:
- Transport already handles 4xx/5xx/network and circuit-breaks repeated 5xx/network in `nuguard/redteam/target/client.py` with tests in `nuguard/redteam/tests/test_target_client.py`.
- Executor currently sets chain status to completed unconditionally after loop in `nuguard/redteam/executor/executor.py`, and orchestrator logs scan complete based on findings count in `nuguard/redteam/executor/orchestrator.py`. This is the architectural reason scans can appear “successful with no findings” even when responses were predominantly gateway/server errors.

## Breaking Changes
- Medium risk: introducing schema-driven payload/response handling can change request body/response parsing defaults for existing users relying on implicit `{message: "..."} -> response/content/message`.
- Medium risk: propagating auth header to all requests may alter behavior of previously unauthenticated runs and reveal latent target-side auth issues.
- High risk if not gated: changing completion semantics (treating high transport error rate as failed/inconclusive) can affect CI exit expectations and benchmark baselines.
- Low risk: adding richer HTTP error classification (`timeout`, `upstream_5xx`, `not_found`, `auth`) as additional report fields if current output fields remain backward-compatible.
- Contract surfaces to preserve:
- CLI flags and config keys in `nuguard/config.py` and `nuguard.yaml.example`.
- SBOM endpoint metadata schema in `nuguard/sbom/models.py`.
- Existing client error string conventions (`[HTTP NNN]`, `[REQUEST_ERROR: ...]`) used in executor logic at `nuguard/redteam/executor/executor.py`.

## Migration Strategy
1. Phase 1: Verification hardening and observability
   - Add explicit per-scenario transport health counters in orchestrator records (`http_2xx`, `http_4xx`, `http_5xx`, request_errors, timeout_errors).
   - Add a scan-level outcome state (`passed`, `no_findings`, `inconclusive_target_errors`, `aborted_target_unavailable`) without removing current findings output.
   - Gate with config flag for initial rollout (`redteam.strict_outcome=false` default).

2. Phase 2: Auth and endpoint contract propagation
   - Wire `redteam_auth_header` through orchestrator into client default headers for both chat and direct endpoint invoke.
   - Add optional per-scenario/per-step header overrides only as additive metadata; keep global header behavior stable.
   - Backward compatibility: if no header configured, behavior unchanged.

3. Phase 3: Non-LLM API shape adaptation (new feature)
   - Introduce deterministic shape resolver module that prioritizes:
     1. SBOM endpoint metadata (`chat_payload_key`, `chat_payload_list`, `request_body_schema`, `response_text_key`).
     2. Framework-derived defaults (FastAPI/Flask adapters).
     3. Safe fallback templates (`message`, `query`, `prompt`, then first string-like field).
   - Add serializer strategy per endpoint:
     1. Build request body from schema map with deterministic fillers for non-prompt required fields.
     2. Inject attack prompt into resolved prompt field.
     3. Validate outbound shape before send and record chosen strategy in trace.
   - Add response extractor strategy per endpoint:
     1. Prefer `response_text_key`.
     2. Fallback to known keys and message arrays.
     3. Preserve raw response for evidence when extraction fails.
   - Rollback path: feature flag (`redteam.dynamic_api_shape=false` by default), enabling targeted canary rollout.

4. Phase 4: Completion semantics fix (your stated lower priority)
   - Define failure thresholds (for example: `>=80%` requests in scenario are 5xx/request_errors -> scenario `failed_transport`).
   - Prevent reporting overall “success/no findings” when all scenarios are transport-failed; return `inconclusive`.
   - Keep findings logic unchanged; only outcome classification and reporting semantics change.

5. Testing sequence
   - Extend `nuguard/redteam/tests/test_target_client.py` for 404/504 classification and header propagation.
   - Add orchestrator outcome-state tests around all-504/all-404 runs and mixed runs.
   - Add deterministic schema-adaptation tests with FastAPI/Flask fixture SBOM nodes and malformed shape fallbacks.
   - Add compatibility tests to ensure legacy `{message: str}` payloads still work when dynamic shaping is off.

## Risk Assessment
- Primary architectural risk: coupling between SBOM extraction fidelity and runtime request shaping. Mitigation: keep strict fallback chain and feature-flagged rollout.
- Primary behavioral risk: changing scan “success” semantics can impact CI pipelines and user trust if rolled out abruptly. Mitigation: phased rollout with dual reporting (`legacy_outcome` + `strict_outcome`) during transition.
- Security risk: missing auth-header propagation can produce false negatives on protected endpoints and mislead risk posture. Mitigation: prioritize Phase 2 early.
- Reliability risk: current circuit breaker protects against hammering, but not against false clean outcomes under persistent 5xx. Mitigation: Phase 1+4 outcome model.
- Scope risk: language/framework coverage for non-LLM shape extraction is currently Python-heavy (FastAPI/Flask). Mitigation: design shape resolver to consume SBOM abstract metadata first, then incrementally improve adapters by framework.

Implementation Handoff: Implement first the auth/header propagation plus outcome-state instrumentation, protect with tests for all-504/all-404/all-401 and mixed response runs plus backward-compatible payload parsing, then have the default coding agent take over next.
