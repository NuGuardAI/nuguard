# Plan: Personal Agent Protocol (Poppy) support in NuGuard

Status: draft plan for review. No code changes yet.
Source for the task: `puppy-research.md` (the file name is a typo; the protocol is "Poppy").

## 1. What Poppy is (as published)

- **Personal Agent Protocol (Poppy) 1.0**, announced by Sierra on 2026-11-13. It is an open standard developed with Meta, and it defines how a customer's *personal agent* talks to a *Company* (business agents, websites and APIs).
- The spec site (personalagentprotocol.org) calls the spec "0.4 with the fixes implementers needed" and the kit "a draft". Treat details as moving.
- Built on existing standards: MCP (Streamable HTTP), OpenAPI 3.0/3.1, UCP (commerce), OAuth 2.x (RFC 8414, 9728, 9126, 7523, 8628, 8707, 9449, 7009, 9207), RFC 9421 Web Bot Auth.
- **Discovery:** `https://{domain}/.well-known/poppy.json`. It is only valid if the final URL path is exactly that path, there are no HTTP downgrades, and `organization.domain` equals the serving host.
  - Fields: `protocol_version`, `supported_versions`, `organization`, `auth` (`issuer`, `scopes`, `scope_hints`), `agent.protocols[]` (`poppy` or `ucp`), `apis[]` (`openapi` or `mcp`), `web` (`browser_session_endpoint`, `unscoped`), `tasks.endpoint`, `extensions`.
  - Related docs: `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`, `/.well-known/ucp`.
- **Identity:** agent `client_id` is an HTTPS URL to a CIMD-style metadata doc (`jwks_uri`, `redirect_uris`, `private_key_jwt`). Sessions use a `Poppy-Session` JWT plus a short-lived delegation JWT.
- **Approval:** the `operations` extension (v3) returns `approval_required` (403 for APIs, `isError` for MCP) and expects a `Poppy-Approval` header. Per the spec, signed user approvals are only "on the roadmap" (8.1), so today approval rests on the Personal Agent's own statement. This is the biggest security gap to test.
- **Other surfaces:** `tasks.endpoint` (excluded tasks, `skill_index` with sha256 digests, feedback endpoint); handoff to a human (`/handoff`); extensions `browser-control`, `disclosures`, `links`, `mediated-sign-in` (transitional), `operations`, `ucp`.
- Spec section 10 security considerations: company content is data not instructions; secrets out of model context; lookalike domains pass domain checks; browser-join CSRF; consent phishing.

Not yet read: the browser-control, disclosures, links and mediated-sign-in extension pages, the roadmap, and the kit's validator source. Read these before implementation.

## 2. Where NuGuard stands today (from a code map; items marked "verify" were not read in detail)

| Area | Today | Gap for Poppy |
|---|---|---|
| SBOM | Adapters in `nuguard/sbom/adapters/` registered in `registry.py`. MCP has Python and Go adapters, and no TypeScript one. Types in `sbom/types.py`. Two schema JSONs must stay in sync. | No Poppy detection. No `poppy.json` parsing. |
| Analysis | `AnalysisPlugin` classes in `nuguard/analysis/plugins/`. NGA-xxx rules in `nga_rules.py`. | No Poppy rules. |
| Policy | `parse_policy` produces `CognitivePolicy` (allowed/restricted topics, restricted actions, HITL triggers, tool conditions, data classification, rate limits). | No agent-to-business sections (scopes, approvals). |
| Behavior | Static `BA-00x` rules in `behavior/alignment.py`. Dynamic happy-path runner, planner and judge. | No Poppy flows. |
| Redteam | `GoalType` has MCP_* values. `mcp_attacks.py`, `multi_agent.py` and a poison server exist. Docs say MCP is "scan only". | No Poppy-specific families. |
| Web/HTTP | `nuguard/pentest/` runs nuclei-style YAML templates (27 bundled: headers, CORS, open redirect, env exposure). | TLS and authenticity checks: none found (verify `transport.py`, `diagnostics.py`). |

## 3. Plan by research aspect

### 3.1 Security measures against common web vulnerabilities
Reuse `nuguard/pentest/` templates. Add a Poppy template pack run against the target's Poppy surfaces:
- `.well-known/poppy.json`, `oauth-protected-resource`, `oauth-authorization-server`: security headers, CORS (these are public docs, so wildcard CORS is fine, but credentialed CORS is not), cache headers, no redirect off-host, no open redirect on `redirect_uris` handling.
- OAuth endpoints: PKCE S256 enforced, exact `redirect_uri` match, `state` and `iss` returned, `prompt=consent`, refresh rotation, revocation works, no scope broader than requested, `invalid_scope` for unknown scopes.
- Rate limiting returns 429 with `Retry-After`.
- Session errors use the specified codes (400 for session errors, 401/403 for token and scope errors).

### 3.2 Authenticity and compromise validation of Poppy sites
New module `nuguard/poppy/validate.py` (checks, no exploitation). Each check is traceable to a spec section:
- Fetch rules: HTTPS only, HTTPS-only redirects, exact path, `organization.domain` equals the host, issuer's `protected_resources` lists the org URL.
- Same-host rule for `supported_versions`, `jwks_uri` and `redirect_uris`.
- TLS and certificate sanity (expiry, chain, hostname), HSTS.
- Lookalike-domain heuristic (spec 10.3: the domain is the identity), and drift detection: hash `poppy.json`, scopes and `scope_hints` and alert on change between runs (a compromised site is likely to widen scopes or swap endpoints).
- `skill_index` digest verification (sha256 matches the downloaded skill) and flagging of skill scripts.
- Consistency between declared and actual surfaces: MCP `resource` equals server URL, OpenAPI operations versus `scope_hints.changes`.
- Optional: check Web Bot Auth signature support.

### 3.3 Behavior-based redteam (opt-in, authorized targets only)
Add `GoalType` values and a scenario module `redteam/scenarios/poppy_attacks.py`, wired through the catalog (`registry.py`, `taxonomy.py`, `capability.py`):
- **Approval bypass:** replay, stale `revision`, expired operation, mismatched action, `approver=agent` accepted when the user was required, repeated performance (at-most-once violation).
- **Consent and scope:** scope escalation on refresh, `poppy:browser-sign-in` granted silently, `web.unscoped` without full-access disclosure.
- **Session:** missing, forged or replayed `Poppy-Session`, wrong `aud`, `htu` or `htm`, cross-user `sid`, delegation lifetime over 24 h.
- **Injection via Company content:** business-agent messages and `excluded_tasks.alternative.instructions` that try to act as user consent (spec 9.4, 10.1). Reuse the MCP poison server pattern.
- **Handoff:** `/handoff` abuse, a human-impersonation `author.type`.
- **Device/browser join:** CSRF on browser join, consent phishing via device code.
Safety: gate behind explicit scope config, no production target by default (the vendor kit validator itself starts real Sessions).

### 3.4 Happy-path behavior analysis (no offensive testing)
Extend `behavior/` with Poppy customer-journey scenarios:
- Discover, sign in (device or direct), read scoped data, make a reversible change, make an irreversible change that must trigger `approval_required`, approve, confirm the effect happened exactly once, revoke.
- Judge for anomalies: operation summary and terms match the actual effect, approval prompt shown before irreversible actions, error codes match the spec, latency and refusal baselines, `scope_hints` match behavior.
- Static BA rules: for example BA-0xx "write-capable Poppy operation with `changes: none`" (scope hint lies), and "irreversible tool with no operations extension".

### 3.5 AIBOM updates
- New component representation: a Poppy endpoint (`poppy.json` parsed as an artifact), plus its agent endpoint, MCP and OpenAPI APIs, OAuth issuer, scopes and extensions.
- Decide between new `ComponentType` values (for example `POPPY_ENDPOINT`) and metadata on existing `API_ENDPOINT`/`MCP_SERVER`/`AUTH` nodes. Recommended: reuse existing types with `framework=poppy` and metadata, so no schema churn, plus one new node type only if a graph needs it.
- Edges: Poppy endpoint `EXPOSES` MCP server / OpenAPI endpoint / agent; `PROTECTS` from the OAuth issuer; scopes carried as attributes. Risk tags (hyphenated): `poppy-unscoped-web`, `poppy-no-approval-extension`, `poppy-irreversible-ungated`.
- Adapters: a JSON adapter for committed `poppy.json`; Python/TypeScript/Go adapters detecting Poppy SDK/route handlers (`/.well-known/poppy.json`, `Poppy-Session` header handling); Shopify theme/app detection if relevant. Add the missing TypeScript MCP adapter.
- Per CLAUDE.md: update both schema JSONs, `documentation/docs/sbom-schema.md`, `supported-technologies.md`, and keep `test_committed_schema_matches_models` green. Follow the Pydantic interface skill for any public model change.

### 3.6 Cognitive Policy
Add optional, backward-compatible sections to `CognitivePolicy` and `parse_policy`:
- Accepted and restricted topics and actions (existing fields).
- **New:** `poppy.allowed_scopes`, `poppy.forbidden_scopes`, `poppy.approval_required_for` (operations or actions that must carry `Poppy-Approval`), `poppy.max_delegation_lifetime`, `poppy.allowed_extensions`, `poppy.handoff_rules`, `poppy.unscoped_web_allowed: bool`.
- HITL: map `hitl_triggers` to Poppy `scope_hints.changes: irreversible` and to the operations flow.
- Compile into `PolicyControl`s with test prompts so redteam and behavior can check violations. Update `documentation/cognitive_policy.md` and the example JSON.

### 3.7 Static analysis of Poppy implementations
- Rules in `nga_rules.py` (or a new plugin) plus Semgrep rules for common mistakes:
  - Session JWT verification missing `aud`, `htu`, `htm`, `iat` or `ath` checks; accepts `alg: none` or any `alg` other than ES256/PS384.
  - Client assertion `exp` over 5 minutes, missing `jti`, or `aud` not equal to the issuer.
  - Non-exact `redirect_uri` matching, missing PKCE S256, missing `state`/`iss`.
  - Refresh tokens not rotated, no revocation endpoint, scope grant wider than requested.
  - Operations: performing an operation without checking revision/expiry/already-performed; no at-most-once guard.
  - Secrets (tokens, private keys, device codes) logged or placed in LLM context (ties to the existing PII-to-LLM rules).
  - Company text or tool output forwarded to the model as instructions.
  - `poppy.json` served with a mismatched `organization.domain`.

## 4. Delivery phases (revised after decisions)

Priority order from the user: AIBOM, then static rules, then validation. Everything folds into existing commands (no `nuguard poppy` group).

1. **Phase 1 - AIBOM (`sbom generate`):** `poppy.json` parser/models (in `nuguard/sbom/`), a JSON adapter for committed `poppy.json`, language adapters (Python, TypeScript, Go) detecting Poppy route handlers and `Poppy-Session` / `Poppy-Approval` handling, plus the missing TypeScript MCP adapter. Reuse existing node types with `framework=poppy` metadata and risk tags. Schema/doc sync. Fixtures taken from `authlete/poppy-company` (TypeScript, Stays module).
2. **Phase 2 - Static rules (`analyze`, `behavior` static BA rules):** NGA/BA/Semgrep rules from 3.7 and the scope-hint-versus-behavior check from 3.4.
3. **Phase 3 - Validation (conformance/authenticity from 3.2):** fold into `analyze` as a live-URL mode, or into the `pentest` template pack, to be decided at the start of the phase. Runs read-only (GET survey) against the sandbox targets below.
4. **Later:** Policy sections (3.6), happy-path behavior (3.4), redteam families (3.3). Personal-agent (consumer) side comes after the site side.

Each phase ships independently with tests (`uv run pytest`, `ruff`, `mypy`, `deptry`). Config keys go in `nuguard.yaml` and `nuguard.yaml.example`.

## Sandbox / reference targets found on GitHub

All are unreviewed third-party code; clone into an empty directory and do not run anything from them without reading it first.

| Repo | What | Use |
|---|---|---|
| `authlete/poppy-company` (Apache-2.0, TypeScript, pushed 2026-11-11) | Minimal reference Company backed by an Authlete auth server; README says discovery runs with no env vars set, and its dev server routes by `Host` header. | Best first fixture: run locally for phase 3 and use its source for phase 1/2 adapters. Sign-in flows need Authlete credentials, so start with discovery only. |
| `personal-agent-protocol/personal-agent-protocol` (Apache-2.0) | Official site source, includes `kit/` with the validator. The README says it documents the protocol and does not implement the APIs. | Reference for conformance checks. Use the validator against our local sandbox only; the kit says it starts real Sessions. |
| `timothyohare/personal-agent-protocol` | Demo Company ("Tipping Co") and personal agent. README says "planning, no code yet" at the time of review. | Revisit later. Not usable now. |
| `ohartwig/poppy`, `ohartwig/sylius-poppy`, `ohartwig/typo3-poppy` | Server core and PHP (Sylius, TYPO3) read-only mirrors. | Candidate fixtures for PHP detection later. |
| `kidkuddy/poppy-cli` | CLI and Claude Code skill for testing Companies. | Comparison only. |

The authlete README also mentions a hosted `*.poppysandbox.dev` environment. I have not probed it. We should only scan it with the owner's permission, so local runs are the default.

## 5. Risks

- The spec is a draft; version-pin the checks to `protocol_version` and cite spec sections in each finding so churn is easy to track.
- Approvals are unsigned in 1.0, so approval-bypass findings may be "by design"; label severity accordingly.
- Dynamic tests perform real actions. They need explicit authorization, a sandbox target, and never replay writes (consistent with campaign-mode rules).
- Findings must never contain tokens, JWTs or keys; redact per the credential-redaction rules.

## 6. Decisions (from the user)

1. Targets: both Company side and personal-agent side; start with sites (Company side).
2. Sandbox: use a public GitHub reference implementation (see table above; `authlete/poppy-company` first).
3. AIBOM: reuse existing node types plus metadata; no schema bump for new node types.
4. Interface: fold into existing commands.
5. Priority: AIBOM first, then static rules, then validation.
6. Spec: pin to the current draft (1.0 as published).
7. Naming: Poppy.

## 7. Open items

- Where validation lives (an `analyze` live-URL mode versus the `pentest` template pack). Decide at the start of Phase 3.
- Whether to read the unread extension pages and the kit validator source before Phase 1; I recommend yes for `browser-control`, `disclosures` and `links`, since they may add discovery surfaces.
