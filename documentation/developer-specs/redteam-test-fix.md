# Fix plan — unresolved path params in behavior/redteam chat endpoints

**Status: IMPLEMENTED.** `path_param_sources` enrichment (§1), bootstrap
orchestration (§3), and the shared-helper extraction it required (see
below) are done, tested, and verified against a live local Studyield
deployment (direct curl chain: create-conversation → extract bare `id` →
substitute into member path → real handler reached, only blocked by a
missing AI API key in the sandbox, unrelated to this fix). One correction
to the plan below: the "reuse, don't reimplement" claims for the
id-extraction and minimal-payload heuristics didn't hold as written — both
needed extracting into a new shared module, `nuguard/common/response_extraction.py`
(`extract_response_id`, `build_minimal_payload`), since neither was
reusable from `nuguard.common` without either a layering violation
(`common` importing `redteam.scenarios`) or duplication. `client.py` and
`api_attacks.py` were refactored to use the shared versions.

**Follow-up found during verification, not fixed here:** the NestJS
adapter doesn't detect `app.setGlobalPrefix()` (Studyield's backend
prepends `/api/v1` to every route via `main.ts`), so SBOM-extracted
endpoint paths lack that prefix. This doesn't affect `path_param_sources`
matching itself (both the member and collection paths are consistently
un-prefixed, so the relative match still succeeds), but it does mean
`nuguard redteam`/`behavior` can't auto-discover a working endpoint against
Studyield without an explicit `target.endpoint` override — a separate,
pre-existing SBOM-extraction gap, out of scope for this fix.

## Problem

Studyield's real chat endpoint is a two-step REST resource pattern:

```
POST /chat/conversations          → { "id": "c_abc123", ... }   (create conversation)
POST /chat/conversations/:id/messages   { "content": "..." }    (send a message)
```

`:id` (or `{id}`/`<id>` depending on framework) doesn't exist until the
prerequisite `POST /chat/conversations` call runs. Today nothing resolves it
automatically, so every behavior/redteam scenario against Studyield sends the
literal placeholder, `TargetAppClient` correctly refuses to send it
(`_substitute_path_params` in [nuguard/redteam/target/client.py](../nuguard/redteam/target/client.py#L55)),
and every turn gets a canned `[CONFIG_ERROR: unresolved path param 'id']`
response instead of a real one — see
[tests/apps/studyield-app/reports/studyield-test-20260821T013020.log](../tests/apps/studyield-app/reports/studyield-test-20260821T013020.log)
(e.g. lines 5705, 5729+). The judge correctly scores these as total failures,
but the failure is a test-harness gap, not a finding about the app.

This is not Studyield-specific — any REST/RPC backend that models
sub-resources as create-then-post-to-member (Rails/NestJS/Express/FastAPI
conventions) hits the same wall for any dynamically-filled path segment
(`:id`, `:userId`, `:conversationId`, etc.), not just literally `id`.

## What already exists (no changes needed)

- Path-param **extraction** into SBOM metadata:
  [nuguard/sbom/enricher.py](../nuguard/sbom/enricher.py) `_extract_path_params()`
  → `NodeMetadata.path_params: list[str]`
  ([nuguard/sbom/models.py](../nuguard/sbom/models.py#L495)).
- Path-template **substitution** and the `[CONFIG_ERROR]` guard in
  [nuguard/redteam/target/client.py](../nuguard/redteam/target/client.py)
  (`_substitute_path_params`, `_send_impl`, `send_stream`).
- A manual binding API, `TargetAppClient.set_path_param(name, value)`
  ([client.py:392](../nuguard/redteam/target/client.py#L392)), that nothing
  currently calls automatically.

## What's missing

1. **No identification of which endpoint resolves which path param**, for
   *any* param name, not just `id`.
2. **No automatic bootstrap call** before scenarios run — the shared
   pre-scenario hook, `validate_and_rotate_chat_endpoint()` in
   [nuguard/common/endpoint_preflight.py](../nuguard/common/endpoint_preflight.py)
   (called by both [behavior/runner.py](../nuguard/behavior/runner.py#L1979)
   and [redteam/executor/orchestrator.py](../nuguard/redteam/executor/orchestrator.py#L1280)),
   rotates to a working *path* but never resolves a `:param` in it.

## Plan

### 1. New SBOM schema field: `path_param_sources`

Add to `NodeMetadata` in [nuguard/sbom/models.py](../nuguard/sbom/models.py)
(next to `path_params`):

```python
path_param_sources: dict[str, str] | None = Field(
    default=None,
    description=(
        "Maps each entry in path_params to the API_ENDPOINT path that "
        "creates the identified resource, e.g. "
        "{'id': '/chat/conversations'} for a chat endpoint at "
        "'/chat/conversations/:id/messages'"
    ),
)
```

- Update `nuguard/sbom/schemas/aibom.schema.json` to match (per project
  convention, `test_committed_schema_matches_models` enforces this).
- Populate it generically in
  [nuguard/sbom/enricher.py](../nuguard/sbom/enricher.py), for **every**
  path param name (not just `id`), immediately after `path_params` is set:
  - For each param, walk the path segments preceding the `:param`/`{param}`
    token and take everything up to (not including) that token as the
    candidate collection path (e.g. `/orgs/:orgId/projects/:projectId/chat`
    → `orgId` candidate is `/orgs`, `projectId` candidate is
    `/orgs/:orgId/projects`).
  - Look for a `POST` `API_ENDPOINT` node in the same SBOM whose path
    matches the candidate exactly (with earlier params in the candidate
    itself substituted by name, not value, since this is a static SBOM-time
    match, not a runtime one).
  - If found, record `{param_name: source_path}` in `path_param_sources`. If
    not found, leave that param absent from the dict (falls through to
    today's "unresolved" behavior for that param only — no regression).
- This generalizes to N params per path (ordered chain), not just a single
  `:id` — the multi-param case is resolved at SBOM-enrichment time by simply
  running the same per-param lookup for each param in `path_params`, so no
  special-casing is needed for nested resources.

### 2. Generalize path-param **substitution and storage** for multiple params

Already generic (`_path_param_values: dict[str, str]` +
`_substitute_path_params` in `client.py` handle any number of named params).
No changes needed here beyond what bootstrap orchestration (§3) will call.

### 3. Bootstrap orchestration in `validate_and_rotate_chat_endpoint`

Extend [nuguard/common/endpoint_preflight.py](../nuguard/common/endpoint_preflight.py):

- After an endpoint is confirmed reachable (existing 400/404/405 rotation
  logic unchanged), check the resolved chat endpoint's
  `path_param_sources` metadata.
- For each param that has a source (process in path order so an outer
  resource id is available before an inner one that might depend on it):
  1. POST to the source endpoint. Body: start with `{}`; on a 4xx
     validation error, fall back to a minimal payload built from the source
     endpoint's `request_body_schema` using the existing
     "string-typed field → placeholder value" heuristic already used
     elsewhere for chat-endpoint payload construction (reuse, don't
     reimplement).
  2. Extract the created id from the JSON response using the existing key
     heuristic already in `client.py` (`session_id`/`conversation_id`/
     `thread_id`/`chat_id`) plus a bare `"id"` fallback.
  3. Call `client.set_path_param(param_name, resolved_id)`.
  4. If the POST fails or no id can be extracted: **fall back silently** —
     log a debug/info note, leave that param unbound, and continue exactly
     as today (the endpoint will keep 400/404ing per-request via the
     existing `[CONFIG_ERROR]` guard, or preflight rotation will have
     already moved on to a different candidate endpoint). Do not abort the
     run or surface this as a hard `ok=False` failure — per-scenario
     failures downstream already report this clearly enough via existing
     `[CONFIG_ERROR]`/deviation reporting.
- Re-run the existing test-request check against the now-substituted path
  before declaring `ok=True`, same as current rotation behavior.

### 4. Regenerate the Studyield SBOM

Since this depends on new enrichment metadata, regenerate
`tests/apps/studyield-app/studyield.sbom.json` /
`studyield.sbom.enriched.json` after §1 lands, and confirm
`path_param_sources` shows `{"id": "/chat/conversations"}` on the chat
endpoint node.

### 5. Tests

- [nuguard/sbom/tests/test_enricher.py](../nuguard/sbom/tests/test_enricher.py):
  single-param resolution, multi-param/nested resolution, no-match case
  (param stays absent from `path_param_sources`), adapter-provided
  `path_param_sources` left untouched (mirrors existing `path_params` tests).
- Schema sync test (`test_committed_schema_matches_models`) — must pass
  after the schema file update.
- [nuguard/common/tests/test_endpoint_preflight.py](../nuguard/common/tests/test_endpoint_preflight.py):
  successful bootstrap (single param), multi-param ordered bootstrap,
  bootstrap POST failure → silent fallback (`ok` unaffected, no exception),
  no `path_param_sources` present → no behavior change (regression guard).
- End-to-end: re-run `tests/apps/studyield-app/studyield-test.sh` and
  confirm turns against `/chat/conversations/:id/messages` get real
  responses instead of `[CONFIG_ERROR]`.

## Non-goals (explicitly out of scope for this pass)

- Response-schema-based prerequisite inference beyond the collection/member
  path-convention heuristic (matching by path shape only, not by semantic
  field matching across arbitrary endpoint pairs).
- Cleanup of resources created during bootstrap (consistent with existing
  behavior for conversations created during normal scenario turns).
- GraphQL/RPC-style APIs that pass a resource id as a mutation argument
  rather than a URL path segment.

## Suggested order of work

1. §1 — schema field + enrichment logic + tests (mechanical, testable in
   isolation without touching preflight).
2. §3 — bootstrap orchestration in `endpoint_preflight.py` + tests.
3. §4 — regenerate Studyield SBOM.
4. §5 end-to-end verification against the Studyield fixture.
