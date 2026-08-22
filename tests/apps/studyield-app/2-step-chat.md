# NuGuard — Two-Step (Resource-Bootstrap) Chat Endpoint Support

## Context

The NestJS/TypeScript extraction work (`studyield-sbom-fix.md` item #1, now
implemented — see `nuguard/sbom/adapters/typescript/nestjs_adapter.py`) fixed
Studyield's `API_ENDPOINT` discovery gap: NuGuard now correctly finds
`POST /chat/conversations/:id/messages` as the real chat route, with
`chat_payload_key='content'`.

That's necessary but not sufficient to actually redteam Studyield. The route
requires a **path parameter** (`:id`, a conversation ID) that doesn't exist
until a separate prerequisite call creates it:

```
POST /chat/conversations          → { "id": "c_abc123", ... }   (create conversation)
POST /chat/conversations/:id/messages   { "content": "..." }    (send a message)
```

Sent literally, `:id` in the URL always 404s — `endpoint_probe.py`'s
`_HAS_PATH_PARAM_RE` (added this round) now recognizes and penalizes/skips
such routes so they don't get *mis-ranked* as safe, but nothing resolves
them. Studyield has no path-param-free chat route, so today's behavior is:
either NuGuard picks a different, non-chat endpoint that happens to be
parameter-free (wrong target), or it has no usable candidate at all
(`aborted_endpoint_unreachable`).

This is not Studyield-specific. Any REST/RPC backend that models
conversations as create-then-post-to-subresource (Rails/NestJS/Express REST
conventions, most non-single-turn chat APIs) hits the same wall.

## Existing infrastructure this can build on

NuGuard already solves a structurally similar problem — **body-level**
identity/session fields that must be resolved before the first request —
via `nuguard/common/session_resolver.py`:

- `_get_sbom_context_fields()` reads `metadata.context_payload_fields` (or
  infers identity fields from `request_body_schema`) off the matching
  `API_ENDPOINT` node.
- `apply_sbom_context_hints()` resolves `"identity"` fields from the login
  response or username, and `"session"` fields by generating a fresh UUID
  client-side — then relies on `TargetAppClient`'s existing
  `_session_context` forwarding (`client.py:717-722`: scans each JSON
  response for `session_id`/`conversation_id`/`thread_id`/`chat_id` and
  merges into subsequent request **bodies**) to keep it correlated turn to
  turn.

**The gap**: that whole mechanism assumes the ID is a body field the client
can synthesize or extract from a chat response. A path-param conversation ID
is neither — it must come from a *different, non-chat endpoint's response*,
and it must be substituted into the **URL**, not the body, before the first
chat turn is ever sent.

## Plan

### 1. SBOM: mark path-param placeholders explicitly (small, mechanical)

`chat_path` currently carries the raw path string (`/chat/conversations/:id/messages`)
with no structured record of which segments are placeholders. Add
`metadata.path_params: list[str]` (e.g. `["id"]`) to `NodeMetadata`, populated
by whichever adapter already knows the path template:

- `nestjs_adapter.py`: parse `:paramName` segments out of the composed path
  when building `metadata["endpoint"]`.
- `fastapi_adapter.py`/`aspnet_core.py`: parse `{paramName}` segments (Flask
  uses `<paramName>` — same idea).

This is additive metadata only; existing consumers that ignore it see no
behavior change. Update `nuguard/sbom/schemas/aibom.schema.json` to match
(per CLAUDE.md, `AiSbomDocument.model_json_schema()` must stay in sync —
`test_committed_schema_matches_models` enforces this).

### 2. SBOM: identify the prerequisite ("resource-creation") endpoint

For a chat endpoint with `path_params`, find the sibling endpoint that
creates the resource the param identifies. Heuristic, generic across
frameworks (implemented once in `nuguard/common/session_resolver.py`
alongside `_get_sbom_context_fields`, not duplicated per adapter):

1. Strip the path down to the segment immediately before the first path
   param: `/chat/conversations/:id/messages` → collection path
   `/chat/conversations`.
2. Look for a `POST` `API_ENDPOINT` node at exactly that collection path in
   the same SBOM (same controller/file is a strong same-resource signal but
   not required — REST collection/member pairing is a path convention, not a
   file-locality one).
3. If found, record it as `metadata.path_param_source = {"id": "/chat/conversations"}`
   on the chat endpoint (or a parallel structure) — again additive metadata,
   computed at SBOM-generation time so redteam/behavior don't have to
   re-derive it live.
4. If no exact collection-path match exists, this stays unresolved and
   falls through to today's behavior (endpoint excluded/penalized, note
   surfaced) — no regression, just no bootstrap.

This mirrors the REST resource convention `POST /things` → `{id}` →
`.../things/:id/...`, which covers the common case (Studyield, and most
NestJS/Express/Rails-style APIs) without needing response-schema inference
across every possible sibling endpoint.

### 3. Client: path-template substitution

`TargetAppClient` (`nuguard/redteam/target/client.py`) currently treats
`_chat_path` as a literal string sent straight to `self._client.post(chat_path, ...)`
(`client.py:592`, `:841` for streaming). Add:

- `_chat_path_template: str` (the raw `:id`/`{id}`-bearing path) and
  `_path_param_values: dict[str, str]`, set via a new
  `set_path_param(name, value)` method.
- In `send()`/`send_stream()`, before issuing the request, substitute
  `:name`/`{name}` tokens in `_chat_path_template` using
  `_path_param_values` to produce the concrete `_chat_path` for that call.
  Missing values (bootstrap not yet run, or failed) should surface as a
  clear `[CONFIG_ERROR: unresolved path param 'id']`-style response rather
  than silently sending the literal placeholder — this keeps failures
  legible in scenario records instead of looking like a generic 404.

### 4. Bootstrap: one prerequisite call before the first turn

Extend `nuguard/common/endpoint_preflight.py`'s
`validate_and_rotate_chat_endpoint` (already the shared pre-scenario
validation hook for both `behavior` and `redteam`) with a bootstrap step
that runs *after* an endpoint is confirmed reachable, *before* returning
`ok=True`, whenever the resolved chat endpoint has an unresolved
`path_param_source`:

1. POST to the source collection endpoint. Body: start with `{}`; if that
   4xx's with a validation error, fall back to a minimal payload built from
   the source endpoint's own `request_body_schema` using the same
   "string-typed field → placeholder value" heuristic FastAPI/Flask/NestJS
   adapters already use for chat detection (e.g. `title: "NuGuard Test
   Conversation"` for Studyield's `CreateConversationDto`). This is
   best-effort; a persistent failure here should degrade to "endpoint
   unreachable" with a specific note (*"chat endpoint requires a
   conversation ID from POST /chat/conversations, which itself failed:
   ..."*) rather than a generic timeout.
2. Extract the created resource's ID from the JSON response. Reuse (don't
   reimplement) the key-name heuristic already in
   `client.py:720` (`session_id`, `conversation_id`, `thread_id`, `chat_id`)
   plus a plain `"id"` fallback, since NestJS conventionally returns bare
   `id`.
3. Call `client.set_path_param(param_name, resolved_id)` (new method, §3).
4. Re-run the existing test-request check against the now-substituted path
   before declaring success.

Auth: the bootstrap POST must go through the same authenticated client
(`validate_and_rotate_chat_endpoint` already receives a ready-to-use
`client` with auth headers set) — no new auth plumbing needed.

### 5. Multiple / nested path params (defer, don't design away)

`/orgs/:orgId/projects/:projectId/chat` (nested resource ownership) needs a
*chain* of bootstrap calls, each resolving one param and likely needing the
previous one substituted into its own path. §3/§4's `dict[str,str]` param
store and template substitution already generalize to N params; the only
new piece would be ordering the bootstrap chain (resolve outermost resource
first). Not needed for Studyield (single `:id`) — call out as a follow-up
once a real multi-param target is hit, rather than speculatively building
chain-ordering logic now.

## Non-goals for the first pass

- **Response-schema-based prerequisite inference** (matching the param name
  semantically to a response field across arbitrary endpoint pairs, not just
  the collection-path convention in §2) — high false-positive risk, defer
  until the path-convention heuristic proves insufficient on a real target.
- **Cleanup** (deleting/closing the bootstrapped resource after the scan) —
  out of scope; test/scan accounts are expected to accumulate scaffolding
  data the same way they already do for chat conversations created during
  normal scenario turns.
- **GraphQL / RPC-style single-endpoint APIs** that pass a resource ID as a
  mutation argument rather than a URL segment — different shape entirely,
  not covered by this plan.

## Suggested order of work

1. **§3** (client-side path-template substitution) — foundational, needed
   before anything else can plug in; cheap, mechanical, easy to unit test in
   isolation with `set_path_param` called directly (no SBOM/bootstrap
   dependency).
2. **§1** (`path_params` metadata) — small, mirrors existing metadata
   patterns (`context_payload_fields`), one adapter at a time
   (NestJS first, since it's the confirmed real case).
3. **§2** (prerequisite-endpoint identification) — the actual new heuristic;
   validate against Studyield's real SBOM before generalizing.
4. **§4** (bootstrap orchestration in `endpoint_preflight.py`) — wires 1-3
   together; this is where it becomes end-to-end testable against Studyield.
5. **§5** (multi-param chains) — only if/when a concrete target needs it.

Each step should ship with unit tests colocated with the module it touches
(`nuguard/redteam/tests/` for client changes, `nuguard/sbom/tests/` for
metadata, `nuguard/common/tests/` for session_resolver/preflight), plus a
final end-to-end check re-running redteam against the Studyield fixture
(`tests/apps/studyield-app/`) and confirming turns against
`/chat/conversations/:id/messages` actually execute instead of 404ing.
