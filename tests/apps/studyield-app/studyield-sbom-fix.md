# NuGuard SBOM Extractor — Remediation Plan (Studyield ground-truth benchmark)

Findings are from comparing `studyield.sbom.json` (nuguard-generated, 47
nodes/23 edges) against `studyield.ground-truth.sbom.json` (hand-curated, 22
nodes/15 edges) for `studyield/studyield` — a NestJS (TypeScript) backend, the
first Node.js/TypeScript web-framework target benchmarked so far (Phlox and
ChapterApps are both Python/FastAPI). The gaps below are largely new,
distinct root causes on the TypeScript side of the extractor, plus two
generic edge-resolution bugs that reproduce independent of language.

## 1. No HTTP web-framework adapter exists for TypeScript at all (highest priority, biggest gap)

**Root cause:** [nuguard/sbom/adapters/typescript/](../../../nuguard/sbom/adapters/typescript/)
contains only AI-framework adapters (`langgraph.py`, `openai_agents.py`,
`bedrock_agents.py`, `google_adk.py`, `azure_ai_agents.py`,
`claude_agent_sdk.py`, `agno.py`) plus generic `llm_clients.py`/
`datastores.py`/`prompts.py`. **There is no equivalent of Python's
[fastapi_adapter.py](../../../nuguard/sbom/adapters/python/fastapi_adapter.py)
or [flask_adapter.py](../../../nuguard/sbom/adapters/python/flask_adapter.py)
for any Node.js web framework** (Express, NestJS, Fastify, Koa). Result:
**zero of Studyield's 120+ real API endpoints were found** — `API_ENDPOINT`
count is 0 in the enriched output vs. 5 representative ones in the ground
truth (`/api/v1/auth/login`, `/api/v1/code-sandbox/execute`,
`/api/v1/webhooks/stripe`, etc.). This is not specific to Studyield or
NestJS — any Express/NestJS/Fastify backend hits the same zero.

**Plan:**
- Add `nuguard/sbom/adapters/typescript/nestjs_adapter.py` (or a broader
  `express_family_adapter.py` covering Express/NestJS/Fastify/Koa, since they
  share enough decorator/call-site shape to reuse one AST walk), modeled
  directly on `fastapi_adapter.py`'s structure:
  - NestJS: `@Controller('prefix')` class decorator + `@Get()/@Post()/@Put()/
    @Delete()/@Patch()` method decorators → compose `prefix + method-path`
    into `metadata["endpoint"]`, exactly mirroring the router-prefix
    composition fix already implemented for FastAPI (`app.include_router(...,
    prefix=...)`) — Nest's `@Controller(prefix)` is structurally the same
    problem one level simpler (no cross-file router composition needed,
    prefix and routes are on the same class).
  - Recognize `@Public()` (or equivalently-named custom decorators found via
    `@UseGuards`/guard-absence) as the `auth_required: false` signal, the same
    way `_AUTH_STRICT_CLASSES` gates FastAPI's `Depends(...)` detection.
  - Express (if included): `app.get('/path', handler)` / `router.post(...)`
    call-expression patterns, plus `app.use('/prefix', subRouter)` composition
    for prefix tracking.
  - Reuse the existing generic `api_endpoint_generic` RegexAdapter in
    [registry.py](../../../nuguard/sbom/adapters/registry.py) as a fallback
    only — it already runs across TS files today but its `_ROUTE_PATTERNS`
    evidently don't match Nest's decorator syntax; the new AST-based adapter
    should take priority when Nest/Express imports are present.
  - Add a regression test fixture mirroring Studyield's
    `@Controller('code-sandbox') @UseGuards(JwtAuthGuard); @Post('execute')`
    shape, asserting the composed `/code-sandbox/execute` endpoint and
    `auth_required: true`.

## 2. Hand-rolled (non-framework) multi-agent orchestration is invisible

**Root cause:** Studyield's `ProblemSolverService` sequentially invokes five
plain TypeScript classes (`AnalysisAgent`, `SolverAgent`, `VerifierAgent`,
`HintAgent`, `AlternativeMethodAgent`), each extending an abstract `BaseAgent`
that calls `AiService.completeJson()`. None of the existing TS agent adapters
(`langgraph.py`, `openai_agents.py`, etc.) match this because it isn't built
on any agent framework — it's application code. Result: **zero `AGENT` nodes**
found vs. 1 in ground truth. This is the same class of gap already
identified for Python in the Phlox/ChapterApps plans (non-framework
tool/agent detection), now confirmed on the TypeScript side too — the fix
should be implemented generically enough to cover both languages.

**Plan:**
- Add a generic "orchestrator class" heuristic (mirrors the existing
  `auth_generic` regex-tier pattern used when no framework-native AUTH class
  matches): flag an `abstract class` (TS) / base class whose subclasses are
  all instantiated and invoked in sequence from one constructor/method in
  another class, where each call ultimately reaches an LLM-client call
  (`AiService`/`llm_clients.py`-recognized client) within a small number of
  hops. Emit one `AGENT` node for the orchestrating service (canonical name
  from the class doing the sequencing, e.g. `ProblemSolverService`), with
  `metadata.extras.detected_by_tiers` marking it a heuristic, lower-confidence
  detection (0.5–0.6) distinguishable from framework-native `AGENT` nodes —
  same confidence-tiering approach already used for the generic GUARDRAIL
  heuristic in the Phlox plan.
- This heuristic is deliberately framework-agnostic (walks the same AST/call-
  graph data both `langgraph.py` and `llm_clients.py` already build) so it
  should be added once in a shared location (e.g.
  `nuguard/sbom/adapters/generic_agent.py`) and wired into both the Python and
  TypeScript adapter registries, rather than duplicated per language.

## 3. Hand-rolled `fetch()`-based external tool calls are missed (and the one TOOL hit is a false positive)

**Root cause:** Studyield's code-sandbox, web-search, and RAG-retrieval
"tools" are plain service classes that call `fetch(externalUrl, {...})`
directly — no LangChain `@tool`, no OpenAI function-calling schema, no MCP.
The single `TOOL` node the extractor did find (`"Generic"` at
`exam-clone.service.ts:898`) is a **false positive**: that line is a spaced-
repetition SQL review-queue method with no external call or tool semantics at
all — it only matched a weak/generic tool-detection regex by coincidence.
Result: 1 `TOOL` node (and the wrong one) vs. 3 in ground truth.

**Plan:**
- Extend `tool_search` / add a new `RegexAdapter`/AST rule in
  [registry.py](../../../nuguard/sbom/adapters/registry.py) (or a TS-specific
  companion) that recognizes a **class method that both (a) is called from a
  controller endpoint behind an auth guard and (b) issues an outbound
  `fetch()`/`axios`/`http.request()` call to a URL built from a config/env
  value** (not a hardcoded first-party API route) as a candidate `TOOL` node
  — this is the same "generic external-call tool" shape flagged as missing in
  the Phlox plan's item #3, just via `fetch()` instead of a LangChain
  factory/OpenAI schema. High-privilege signal (`high_privilege: true`)
  should be set when the called code path accepts free-form user input
  forwarded into the request body (as with the code-sandbox's `code`
  parameter) — reuse the existing `PrivilegeScope.CODE_EXECUTION` heuristic
  already defined in [nuguard/sbom/types.py](../../../nuguard/sbom/types.py)
  for this, rather than inventing a new signal.
- Tighten (or add a same-file semantic check to) whatever regex produced the
  `exam-clone.service.ts:898` false positive so a bare SQL query method
  doesn't qualify as a `TOOL` — require actual outbound-call syntax
  (`fetch(`, `axios.`, `http.request(`, an SDK client method call) in the
  matched span, not just a nearby keyword.

## 4. OpenRouter gateway misclassified as FRAMEWORK, model-name constants misclassified as MODEL

**Root cause:**
[nuguard/sbom/adapters/typescript/llm_clients.py](../../../nuguard/sbom/adapters/typescript/llm_clients.py)'s
provider table already documents supporting the `new OpenAI({ baseURL: "..."
})` proxy pattern for Groq/Gemini/Ollama-style OpenAI-compatible gateways, but
its `openrouter` entry only matches an `OpenRouter`/`OpenRouterClient` class
name — not the baseURL-proxy pattern Studyield actually uses
(`new OpenAI({ baseURL: 'https://openrouter.ai/api/v1' })`). Result: the real
gateway instantiation (`ai.service.ts:80`) surfaces as a `FRAMEWORK` node
named `"Openrouter"` instead of the `MODEL` node ground truth expects, **and
is duplicated 3 times** (`"Openrouter"`, `"LLM Clients Ts"`, `"Datastore
Ts"` — likely three different adapter passes over the same file each
emitting their own node for the same client). Separately, the constant
`DEFAULT_MODEL = 'openai/gpt-4o-mini'` string became its own `MODEL` node
correctly, but a **local variable name literally called `model`**
(`const model = options.model || this.getModel('text')` at line 146) also
became a `MODEL` node — a clear false positive from name-based node creation
with no check that the "name" is an actual model identifier string literal
rather than a variable reference.

**Plan:**
- Extend `llm_clients.py`'s baseURL-sniffing (already used for
  Groq/Gemini/Ollama per its own docstring) to also match
  `openrouter.ai/api/v1`, and when matched, classify the node as `MODEL`
  (not `FRAMEWORK`) with `metadata.extras.gateway = "openrouter"` — this
  keeps the single-gateway-many-models semantics correct rather than forcing
  the model list into a separate node type. Deduplicate on the constructed
  client's canonical call site (file:line of the `new OpenAI(...)` call) so
  it doesn't get reported once per adapter pass.
- Add a guard before emitting a `MODEL` node from a bare identifier match:
  the matched name must resolve to a string literal (constant or default
  parameter value), not an arbitrary local variable name — reuse the
  `CESScanner` constant-resolution machinery already visible in this scan's
  logs (`CESScanner: resolved 2 variable(s) in ai.service.ts:
  DEFAULT_MODEL=...`) to check that the "model name" actually is one of the
  resolved constants before creating a node, instead of pattern-matching on
  the bare token `model`.

## 5. Only primary JWT auth detected; Google OAuth2 / Apple Sign-In (JWKS) entirely missed

**Root cause:** the enriched output has exactly one `AUTH` node ("JWT Auth" —
a generic keyword hit at `auth.service.ts:8`, an import line, not the actual
token-signing call site). Studyield's `AuthService` also instantiates a
Google `OAuth2Client` (`google-auth-library`) and an Apple JWKS client
(`jwks-rsa`) for third-party sign-in, neither of which any current
Python/TypeScript AUTH detector recognizes — the FastAPI-side
`_AUTH_CLASSES` table (`OAuth2PasswordBearer`, `HTTPBearer`, etc., in
[fastapi_adapter.py](../../../nuguard/sbom/adapters/python/fastapi_adapter.py))
has no TypeScript equivalent, and neither list includes
`OAuth2Client`/`jwksClient` regardless of language.

**Plan:**
- Add a generic (language-agnostic where possible) OAuth/OIDC client-class
  table analogous to `_AUTH_CLASSES`, covering `google-auth-library`'s
  `OAuth2Client` (`.verifyIdToken()`), `jwks-rsa`'s `JwksClient`, and their
  common Python equivalents (`google.oauth2.id_token`, `python-jose`'s JWKS
  helpers) — one shared table consumed by both a new TS auth adapter and the
  existing Python one, rather than two independently-maintained lists.
- Prefer the actual verification call site (`verifyIdToken(...)`,
  `getSigningKey(...)`) as the AUTH node's evidence location over the import
  statement — the current "JWT Auth" node citing line 8 (an `import` line) is
  a symptom of falling back to a weak import-based heuristic instead of
  finding the real usage site; this should reuse whatever call-site-location
  logic already prefers instantiation-over-import evidence elsewhere (e.g.
  `_AUTH_STRICT_CLASSES` handling in the FastAPI adapter).

## 6. DATASTORE nodes prefer incidental references over the real client instantiation

**Root cause:** the enriched output has 7 `DATASTORE` nodes vs. 5 in ground
truth, with several citing the wrong file: `"Pool"` at
`backend/scripts/migrate.js:26` (a one-off migration-script `new Pool()`,
duplicating the real one in `database.service.ts:13`); `"Redis 13"` at
`backend/src/health.controller.ts:5` (an import re-export, not
`redis.service.ts`'s actual `new Redis(...)` instantiation); `"Aws S3"` at
`backend/src/modules/email/ses.service.ts:3` (this file sends email via SES
and only imports an unrelated `@aws-sdk` symbol — a misattribution; the real
R2/S3 client lives in `storage.service.ts`).

**Plan:**
- Add a same-file-instantiation preference to the TS `datastores.py`
  dedup/ranking pass: when multiple candidate locations exist for the same
  datastore technology (Postgres, Redis, S3-compatible), prefer the one where
  the actual client constructor call (`new Pool(`, `new Redis(`, `new
  S3Client(`) appears in a file whose class is injected via NestJS DI into
  multiple other modules (a strong "this is the shared service" signal
  already implicit in the `@Injectable()` decorator), over a lone
  script-local instantiation (`migrate.js`) or an unrelated same-package
  import (`ses.service.ts`'s AWS SDK import that isn't actually
  constructing an S3 client).
- This is the same underlying "prefer the real definition site over an
  incidental reference" problem already seen in the ChapterApps benchmark
  (Postgres/Mongo/Qdrant fragmentation across files) — worth fixing once in
  the shared dedup/ranking logic both adapters call into, rather than
  patching each language's adapter separately.

## 7. DEPLOYMENT is fragmented into keyword hits, all wired to every CONTAINER_IMAGE (generic edge-resolution bug)

**Root cause:** `deployment_generic` in
[registry.py](../../../nuguard/sbom/adapters/registry.py) intentionally emits
one node per matched deployment-related keyword with no `canonical_name`
grouping (by design, to keep distinct technologies distinguishable — see its
own comment). For Studyield this produces 7 near-meaningless nodes (`Compose`,
`Ci`, `Port 3010`, `Port 80`, `Docker`, `Nginx`, `Render`) from keyword hits in
`start.sh`, `ci.yml`, and both Dockerfiles' `EXPOSE`/`FROM` lines — instead of
the one `DEPLOYMENT` node ground truth expects for the actual
`docker-compose.yml` service topology. Separately, and more seriously, the
edge-resolution step in
[nuguard/sbom/extractor/core.py](../../../nuguard/sbom/extractor/core.py)
connects **every** `DEPLOYMENT` node to **every** `CONTAINER_IMAGE` node found
anywhere in the scan — all 7 keyword-derived pseudo-nodes each produce a
`DEPLOYS` edge to both container images, yielding 14 redundant edges that
carry no real information about which deployment actually builds/references
which image. `"Render"` is also a confirmed false positive: it matched
against `frontend/src/pages/dashboard/SolutionPage.tsx:156`, a React
`render()` call, colliding with the `deployment_generic` pattern's `render`
keyword (intended for Render.com, the PaaS).

**Plan:**
- In `core.py`'s edge-resolution, only wire a `DEPLOYMENT` node to a
  `CONTAINER_IMAGE` node when there's an actual file-level relationship
  between them (same IaC file referencing both, e.g. `docker-compose.yml`
  naming a `build:`/`image:` that resolves to a scanned `Dockerfile`), instead
  of an all-to-all cross join. This is a generic bug, not TS/Studyield-
  specific — any repo with 2+ keyword-derived `DEPLOYMENT` nodes and 2+
  `CONTAINER_IMAGE` nodes will currently get the same N×M redundant-edge
  explosion.
- Make the `render` keyword in `deployment_generic`'s PaaS pattern
  word-boundary-and-context aware (e.g. require it adjacent to `deploy`/
  `.com`/a CLI-style invocation `render `, not a bare identifier match) to
  avoid colliding with the extremely common `render()`/`render:` tokens in
  UI code — the same false-positive risk likely also affects the `compose`
  keyword (Jetpack Compose, Vue's Composition API) and `deployment`
  (Kubernetes `kind: Deployment` YAML field vs. the English word), worth a
  quick audit of the whole keyword list for this class of collision.
- Optionally, promote `docker-compose.yml`/`docker compose` detection to its
  own higher-priority, IaC-aware adapter (parsing the YAML's `services:` keys
  the same way `iac.py`'s `K8sAdapter` parses Kubernetes manifests) instead of
  relying on the generic keyword regex — this would let a single well-formed
  `DEPLOYMENT` node carry the real service topology (`postgres`, `redis`,
  `qdrant`, `clickhouse`, `backend`, `frontend`) as structured metadata,
  matching ground truth's single node with a descriptive service list.

## Suggested order of work

1. **#1** (NestJS/Express API_ENDPOINT adapter — zero-to-something, highest value, most generic: benefits every future Node.js target)
2. **#7** (DEPLOYMENT all-to-all edge bug — generic, cheap, fixes edge-count noise across every repo with multiple DEPLOYMENT/CONTAINER_IMAGE nodes, not just Studyield)
3. **#4** (OpenRouter MODEL/FRAMEWORK misclassification + `model`-variable false positive — isolated to `llm_clients.py`, moderate effort)
4. **#6** (DATASTORE instantiation-site preference — same fix shape as the ChapterApps plan's fragmentation fix, reuse that logic)
5. **#5** (Google OAuth2/Apple JWKS AUTH detection — moderate effort, security-relevant)
6. **#3** (generic `fetch()`-based TOOL heuristic — moderate-to-high effort)
7. **#2** (generic non-framework AGENT orchestrator heuristic — highest effort, most speculative; do last and validate carefully against false-positive risk on ordinary service classes that just happen to call each other)

Each item should ship with its own unit test under `nuguard/sbom/tests/`
(TypeScript-adapter tests already exist alongside the Python ones — mirror
that location) plus a final end-to-end check that re-runs
`nuguard sbom generate --config nuguard.yaml` against the Studyield fixture
(`tests/apps/studyield-app/`) and confirms the previously-missing/incorrect
ground-truth nodes are now found with correct type/location.
