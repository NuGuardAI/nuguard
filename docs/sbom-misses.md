# SBOM extraction misses — studyield-app

Comparison of `tests/apps/studyield-app/studyield.sbom.enriched.json` (generated)
against `tests/apps/studyield-app/studyield.ground-truth.sbom.json` (hand-curated).

**Status (2026-08-21): items 2–5 and 7 fixed; item 1 was already fixed by an
earlier commit before this doc was written; item 3 (TOOL) and part of item 7
(deployment keyword consolidation, Ci/GITHUB_WORKFLOW dedup) are deferred —
see notes in each section below.**

## Summary

| Type | GT | Generated (original) | Verdict |
|---|---|---|---|
| API_ENDPOINT | 5 | ~~0~~ 207 | Already fixed by commit `dc583b41` prior to this doc |
| AUTH | 3 | 1 → 3 | **Fixed**: split by mechanism (jwt/oauth2/apikey) |
| TOOL | 3 | 1 (false positive) | **Deferred** — needs a new hand-rolled-service heuristic |
| PROMPT | 0 (not modeled) | 20 → 14 | **Fixed** confirmed false positives; some remain (real prompts) |
| DATASTORE | 5 | 7 | **Fixed**: SES/S3 misclassification + numeric-suffix naming noise |
| CONTAINER_IMAGE | 2 | 2 | **Fixed**: nginx:alpine display name |
| DEPLOYMENT | 1 | 6 → 3 | **Partially fixed**: EXPOSE-port nodes + Render FP gone; keyword-per-node fragmentation and Ci/GITHUB_WORKFLOW dup deferred (see §7) |
| AGENT | 1 | 1 | OK (different name style) |
| MODEL | 1 | 4 (3 real) | GT models one gateway concept vs granular model names |
| GITHUB_WORKFLOW | 1 | 1 | Match |
| FRAMEWORK / PRIVILEGE | 0 | 3 / 2 | Extra categories GT doesn't model (not necessarily wrong) |

## 1. API_ENDPOINT — RESOLVED (was already fixed before this doc)

This section originally reported 207 → 0 API_ENDPOINT nodes vanishing in the
full pipeline. That was traced to a pre-existing generic/framework endpoint
dedup bug, already fixed by commit `dc583b41` ("Fix SBOM endpoint-dedup and
PROMPT-verification regressions") in an earlier session — this doc was
written from a stale artifact that predated that fix. Both
`studyield.sbom.json` (no-LLM) and `studyield.sbom.enriched.json`
(LLM-enabled) on disk already contain 207 API_ENDPOINT nodes. No further
action needed here.

## 2. AUTH — 3 mechanisms collapsed into 1

Ground truth models `jwt-primary-auth`, `google-oauth`, `apple-sign-in-jwks`
separately. The generated SBOM has one `JWT Auth` node whose 47 evidence hits
mix JWT, OAuth, and API-key matches (all tagged `auth_generic`).

Root cause: the generic auth regex adapter uses a single canonical name
(`auth_generic`) regardless of which specific mechanism matched, so distinct
auth types dedupe into one node instead of being split by mechanism (jwt vs
oauth vs apiKey).

## 3. TOOL — real tools undetected, only a false positive survives

GT expects `code-sandbox-execution-tool`, `web-search-tool`,
`knowledge-base-rag-retrieval-tool` (all hand-rolled NestJS services, not
built on a recognized agent-framework Tool class). None were detected. The
one `TOOL` node present ("Generic") comes from a stray regex match on the
token `rq` inside `exam-clone.service.ts` — unrelated noise.

Cause: TS tool-detection adapters likely only recognize framework-specific
Tool constructs (e.g., LangChain/OpenAI function-calling schemas) and have no
heuristic for hand-rolled service classes that act as agent tools.

**Deferred**: this needs a new detection heuristic (not a bug fix to
existing logic), which carries meaningfully higher false-positive risk than
the other items here. Scoped as follow-up work, not fixed in this pass.

## 4. PROMPT — 20 nodes, mostly false positives — FIXED (partially)

Ground truth doesn't model prompts as separate top-level nodes for this app
at all. The generator's `nuguard/sbom/adapters/typescript/prompts.py`
`_is_likely_prompt()` heuristic is too permissive:

- `prompt_ctx_words = {"prompt", "instruction", "system", "template",
  "message", "persona"}` — including `"message"` means *any* string literal
  whose enclosing variable/function name contains "message" is auto-flagged,
  regardless of content.
- Role-marker matching (`"user:"`, `"assistant:"`, `"system:"`, `"context:"`)
  fires on generic log lines and UI labels, not just real prompt templates.
- Confirmed false positives: `Can Activate` (a NestJS `canActivate()` guard
  method), `Handle Connection`/`Handle Disconnect` (WebSocket gateway
  lifecycle methods), `Message Bubble` (a React chat-UI component), `Origin`,
  `Tring` — none of these are prompt templates; they inherited their name
  from `_prompt_name()`'s fallback to the enclosing function/variable name.
- Real prompts (`System Prompt`, `Analysis Prompt`, `Generation Prompt`,
  `Stem Prompt`) are correctly detected with high confidence (0.84–1.0),
  while the false positives cluster at low confidence (0.44–0.66) — so a
  confidence-threshold filter would fix most of this without touching
  detection logic.

**Fix applied**: removed `"message"` from `prompt_ctx_words`, and tightened
role-marker matching to require the marker at the start of a line rather
than anywhere as a substring. Confirmed false positives `Can Activate`,
`Handle Connection`, `Handle Disconnect`, and `Message Bubble` are gone
(20 → 14 PROMPT nodes). `Origin` and `Tring` remain, but turned out on
inspection to be *real* prompt template literals (`const prompt = \`...\``)
with an unrelated `_prompt_name()` display-naming quirk — not false
positives — left out of scope for this pass.

## 5. DATASTORE — one misclassification + noisy fallback names — FIXED

**Misclassification:** `Aws S3` node is evidenced from
`backend/src/modules/email/ses.service.ts`, which imports
`@aws-sdk/client-ses` (AWS **SES**, email — not S3). Root cause found in
`nuguard/sbom/adapters/typescript/datastores.py`:
`_OBJECT_STORAGE_PACKAGES = {"aws-sdk": "aws-s3", ...}` combined with
substring matching `if pattern in mod`. Since `"aws-sdk"` is a substring of
`"@aws-sdk/client-ses"`, any `@aws-sdk/client-*` import (SES, SNS, SQS,
DynamoDB, etc.) gets misclassified as S3 object storage.

**Noisy names:** `Pool`, `Postgresql 13`, `Qdrant 77`, `Redis 13` come from
the naming fallback
`name = url_details.get("database") or self._assignment_name(...) or
f"{provider}_{inst.line_start}"` — when neither a URL nor a resolvable
variable-assignment name is found, it falls back to `provider_lineNumber`,
producing meaningless numeric suffixes instead of a clean canonical name like
ground truth's `postgres`/`redis`/`qdrant`.

**Fix applied**: import matching now requires an exact package-name match or
a `/`/`-` boundary (not raw substring), so `@aws-sdk/client-ses` no longer
matches the `aws-sdk` S3 pattern. The numeric-suffix naming fallback now
uses the bare provider name instead of `provider_lineNumber`, so repeated
unresolvable instances in one file consolidate to one node — confirmed via
unit test and a no-LLM re-extraction (no more `Postgresql 13`/`Qdrant 77`
style noise). Note: instances with a real, distinct resolvable variable name
across *different* files (e.g. `backend/scripts/migrate.js`'s
`const pool = new Pool(...)`) still produce their own node — that's
legitimate per-name resolution, not the fallback-noise bug, and
cross-file/cross-script consolidation into one node per provider was out of
scope for this pass.

## 6. CONTAINER_IMAGE — display name loses the repo prefix — FIXED

`nginx:alpine` → displayed as just `Alpine` (canonical_name is correctly
`container_image_nginx_alpine`, so only the *display* formatting drops the
`nginx` segment). `node:20-alpine` → `Node:20 Alpine` is fine. This is a pure
display-name derivation bug for the nginx image specifically.

**Fix applied**: `normalize_display_name()`'s colon-prefix-stripping (meant
for namespace prefixes like `framework:openai_agents`) is now skipped for
`CONTAINER_IMAGE`, since a Docker `repo:tag` ref isn't a namespace prefix.
`nginx:alpine` now displays as `Nginx:Alpine`; `node:20-alpine` unaffected
(regression-tested).

## 7. DEPLOYMENT — fragmented into keyword noise — PARTIALLY FIXED

Ground truth models one deployment concept
(`studyield-docker-compose-deployment`). Generated output has 6: `Ci`,
`Port 3010`, `Port 80`, `Docker`, `Nginx`, `Render`. Causes:

- `Port 3010`/`Port 80` come from Dockerfile `EXPOSE` lines being promoted to
  standalone deployment nodes instead of metadata on a parent deployment
  node.
- `Ci` duplicates the separately-modeled `GITHUB_WORKFLOW` node.
- `Docker`/`Nginx` are generic keyword hits that should consolidate into one
  Docker Compose deployment, not separate nodes.
- `Render` is a false positive — evidenced from
  `frontend/src/pages/dashboard/SolutionPage.tsx:156`, almost certainly a
  React `render` reference, misread as the Render.com hosting platform.

**Fixed**: EXPOSE ports are now attached as `exposed_ports` metadata on the
CONTAINER_IMAGE node instead of standalone `Port N` DEPLOYMENT nodes. The
`render` keyword pattern now requires an actual Render.com signal
(`render.com`, `render-deploy`, `render.yaml`) rather than excluding only
call-syntax — the original guard didn't catch the actual false positive,
which was a prose comment (`// ... and render it`), not a function call.
Confirmed via no-LLM re-extraction: DEPLOYMENT count on studyield-app went
from 6 → 3, with zero `Port *`/`Render` nodes remaining.

**Still deferred** (found to conflict with existing, intentional design —
flagged rather than changed without confirmation):
- `Docker`/`Nginx` staying as separate nodes rather than consolidating into
  one deployment concept is not a bug — `deployment_generic`'s
  `canonical_name=None` is explicitly documented
  (`nuguard/sbom/adapters/registry.py:430-434`) as intentional, so that
  distinct technologies stay visible instead of collapsing into one
  "generic" node. Consolidating it would reverse a deliberate design choice
  the ground truth comparison doesn't account for.
- `Ci` duplicating the GITHUB_WORKFLOW node: the DEPLOYMENT node
  `GitHubActionsAdapter` (`nuguard/sbom/adapters/iac.py`) emits for
  `.github/workflows/*.yml` carries security-findings metadata
  (NGA-010/011/014 checks, OIDC/permissions/cloud-provider detection) that
  the separate GITHUB_WORKFLOW node (`nuguard/sbom/adapters/dev_tools.py`)
  doesn't capture — existing tests
  (`nuguard/sbom/tests/test_iac_adapters_new.py::TestGitHubActionsAdapterFindings`)
  depend on this DEPLOYMENT node existing for workflow files. Suppressing it
  would silently drop that security metadata, not just dedupe a name.

## Follow-up items (need a decision, not yet scoped)

1. **TOOL detection** — add a heuristic for hand-rolled service-class "tools"
   (NestJS services invoked by agent code) since framework-specific
   Tool-class detection misses this app's architecture entirely.
2. **Deployment keyword consolidation vs. tech visibility** — decide whether
   `deployment_generic`'s one-node-per-keyword design should change for
   apps like this, or whether ground truth should model multiple deployment
   nodes instead of one.
3. **Ci/GITHUB_WORKFLOW dedup** — decide whether the DEPLOYMENT node's
   security-findings metadata should move onto the GITHUB_WORKFLOW node
   (then the DEPLOYMENT node can be safely dropped), or whether both should
   simply keep existing side by side.
