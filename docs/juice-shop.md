# OWASP Juice Shop — Vulnerability-Category Coverage & Improvement Plan

This document maps every vulnerability category from the [OWASP Juice
Shop companion guide](https://pwning.owasp-juice.shop/companion-guide/snapshot/part1/categories.html)
against what NuGuard is *capable* of detecting (static analysis +
red-team), cross-checks that against what was actually flagged in the
existing test-app run artifacts, and lays out a prioritized plan to close
the gaps.

Sources:
- Category list: OWASP Juice Shop companion guide, "Vulnerability
  Categories" (16 categories).
- Static findings: `tests/apps/owasp-juice-shop/reports/juice-shop-analysis.md`
  (2154 findings from a scan of the Juice Shop AI-SBOM, after installing
  semgrep and checkov and re-running — see backlog item #2).
- Red-team findings: `tests/apps/owasp-juice-shop/reports/juice-shop-redteam.md`
  (185 scenarios against the live app's `/rest/chat` endpoint and
  discovered REST API).
- NuGuard capability: `nuguard/analysis/plugins/` (static detectors) and
  `nuguard/redteam/scenarios/` (dynamic scenario builders).

## Coverage matrix

| # | Juice Shop Category | NuGuard Capability | Coverage | Detected in existing run? |
|---|---|---|---|---|
| 1 | Broken Access Control | `NGA-021` (IDOR-prone endpoint, static) + `build_idor`/`build_auth_bypass`/`build_mass_assignment`/`build_auth_scope_bypass` (BFLA/RBAC) in `nuguard/redteam/scenarios/api_attacks.py` | **YES** | **YES** — HIGH finding: Mass Assignment on `/API/Users` leaked a JWT + password hash with no auth check |
| 2 | Broken Anti-Automation | `NGA-026` (missing rate limiting, static) + `build_rate_limit_probe` (`api_attacks.py`) | PARTIAL | Previously: No — the dynamic gate only fired on endpoints `meta.rate_limited=True`, the opposite of NGA-026's population, so it never ran on Juice Shop (see backlog #7, now fixed: the probe fires on the same unconfirmed population NGA-026 flags). **Not yet re-validated against a live run.** |
| 3 | Broken Authentication | `NGA-006` (missing auth on endpoint) + `build_auth_bypass`, session-fixation spec `S06` (`api_schema_attacks.py`) + new **`NGA-030`** (JWT verify call with no pinned `algorithms:` allow-list, static) + new **`build_jwt_tampering_probe`** (`api_attacks.py`, direct-HTTP, `ScenarioType.JWT_TAMPERING`) — forges an `alg: none` token and HS256 tokens signed with common weak/default secrets, sent as the `Authorization: Bearer` header in place of real credentials | PARTIAL | No — no weak-password, session-fixation, or JWT-specific finding surfaced. `NGA-030`/`build_jwt_tampering_probe` are new this session (unit-tested, both live in `lib/insecurity.ts`'s JWT sign/verify pair) — **not yet validated against a live Juice Shop run**, see backlog #8 |
| 4 | Cross-Site Scripting (XSS) | `build_output_xss` (`output_handling.py`, LLM-output-echo only) + new **`build_reflected_xss_probe`** (`api_attacks.py`, direct-HTTP, `ScenarioType.REFLECTED_XSS`) — fuzzes any endpoint's path/body parameters with a `<script>` payload and checks for an unescaped verbatim echo in the response, closing the server-rendered-HTML gap `build_output_xss` never covered | PARTIAL | No finding on `build_output_xss` (chat-scoped, as before). `build_reflected_xss_probe` is unit-tested but **not yet validated against a live Juice Shop run** — see backlog #6 |
| 5 | Cryptographic Issues | `NGA-005` (unencrypted PII datastore, encryption-at-rest posture) + new `generic-security.yaml` rules `nuguard-js-weak-hash-for-password`, `nuguard-js-hardcoded-secret` | PARTIAL | **YES on the new rules** — `nuguard-js-weak-hash-for-password` fires on `lib/insecurity.ts:41` (MD5 for password hashing, a real Juice Shop weakness) and `scripts/package.mjs:121`; `nuguard-js-hardcoded-secret` fires on the hardcoded JWT signing secret in `lib/insecurity.ts:54`. JWT-alg-confusion (`alg: none`) is now covered by `NGA-030`/`build_jwt_tampering_probe` — see row 3 and backlog #8 |
| 6 | Improper Input Validation | New `generic-security.yaml` rule `nuguard-js-path-traversal` + new **`build_path_traversal_probe`** (`api_attacks.py`, direct-HTTP, `ScenarioType.PATH_TRAVERSAL`) — fuzzes file/path-like path/body parameters with `../` payloads and checks for `/etc/passwd`/`win.ini` content in the response | **YES** | **YES on the static rule** — fires on real code: `routes/videoHandler.ts:82`, `routes/vulnCodeFixes.ts:81`, `routes/vulnCodeSnippet.ts:90`, `rsn/rsnUtil.ts:66` and `:155` (5 hits total). `build_path_traversal_probe` is unit-tested but **not yet validated against a live Juice Shop run** — see backlog #5 |
| 7 | Injection (SQL/NoSQL/command) | `build_sql_injection` (`tool_abuse.py`, LLM-tool-mediated) + new **`build_injection_probe`** (`nuguard/redteam/scenarios/api_attacks.py`, direct-HTTP, `GoalType.API_ATTACK`/`ScenarioType.SQL_INJECTION`) + new **`generic-security.yaml`** semgrep ruleset (`nuguard-js-sql-injection`, `nuguard-js-command-injection`, `nuguard-js-insecure-eval`) for direct JS/TS source patterns | **YES** | "SQL Injection via Agent Chat — Sequelize" (redteam) ran 7/7 turns, no finding — plausible true negative, Juice Shop's chatbot doesn't proxy raw SQL. But the new semgrep ruleset found **real hits on Juice Shop's actual vulnerable code**: SQLi in `routes/login.ts:34` and `routes/search.ts:23` (the classic Juice Shop login-bypass and search-injection challenges), plus 4 more in `data/static/codefixes/*.ts`; `nuguard-js-insecure-eval` in `routes/captcha.ts:22` and `routes/userProfile.ts:65`; `nuguard-js-command-injection` in `scripts/package.mjs:20`. `build_injection_probe` fuzzes any endpoint's path/body parameters with SQLi/NoSQLi payloads and checks for a DB-error signature, following `build_idor`'s multi-step fallback pattern (code landed, unit-tested; **not yet validated against a live Juice Shop run** — see backlog #3) |
| 8 | Insecure Deserialization | — | **NO** | N/A |
| 9 | Miscellaneous | — | N/A | N/A (not a distinct vuln class) |
| 10 | Security Misconfiguration | `trivy_scanner.py`'s built-in `misconfig` scanner + `checkov_scanner.py` (IaC) + new static rules **`NGA-027`** (missing security headers), **`NGA-028`** (permissive CORS), **`NGA-029`** (verbose error leak) in `nuguard/analysis/plugins/nga_rules.py`, fed by new `SecurityHeaderDetail`/`CorsPolicyDetail`/`debug_error_leak` extraction in `fastapi_adapter.py`/`flask_adapter.py` | **YES** | **YES, real findings on both IaC and app layers.** After installing semgrep+checkov and re-running: Trivy's misconfig scanner found 9 findings per `.tf` file (`AWS-0054`, `AWS-0104`, etc.); checkov itself now runs (`✅ ok`, 141 findings, `CKV_AWS_150`/`CKV_AWS_91`/etc. on `aws_lb.juice_shop`). `NGA-027` fired for real: 136 API endpoints flagged for no confirmed CSP/X-Frame-Options/HSTS. `NGA-028`/`NGA-029` stayed silent — Juice Shop's Express/Node endpoints aren't yet instrumented for CORS/debug-mode extraction (only FastAPI/Flask are), a known follow-up (see backlog #4) |
| 11 | Security through Obscurity | — | **NO** | N/A |
| 12 | Sensitive Data Exposure | `NGA-001`/`NGA-003`/`NGA-005`/`NGA-025` (static: PII to external LLM, secrets, unencrypted datastore, credentials in prompts) + `build_open_data_exposure` (`api_attacks.py`) | **YES** | **YES** — same `/API/Users` Mass Assignment finding: response body contained email, password hash, role, IP without auth |
| 13 | Unvalidated Redirects | New **`build_open_redirect_probe`** (`api_attacks.py`, direct-HTTP, `ScenarioType.OPEN_REDIRECT`) — fuzzes redirect-target-like path/body parameters (`url`, `redirect`, `next`, `return_to`, etc.) with an attacker-controlled `.invalid`-TLD marker URL; a DNS-failure attempt to reach it is evidence of a followed redirect | PARTIAL | **NO static coverage.** `build_open_redirect_probe` is unit-tested but **not yet validated against a live Juice Shop run** — see backlog #5 |
| 14 | Vulnerable Components | `osv_client.py` + `grype_client.py` + `trivy_scanner.py` (CVE/SCA against SBOM components) | **YES** | **YES** — 2154 findings total (55 CRITICAL, 552 HIGH), largely from these three scanners |
| 15 | XML External Entities (XXE) | — | **NO** | N/A |
| 16 | Observability Failures | `NGA-009` (AI application has no audit logging enabled) | PARTIAL | Finding present in static report, but it's a generic "is there any AI-app audit logging" check, not Juice-Shop-specific log-injection/insufficient-logging coverage |

*(Not one of the named 16, but worth flagging: **CSRF** has no dedicated
check anywhere in NuGuard — neither static nor dynamic — despite being a
classic OWASP class that overlaps with Juice Shop's Broken Access
Control / Misconfiguration challenges.)*

**Summary**: 5 of 16 categories YES, 7 PARTIAL, 3 NO, 1 N/A. Of the 12
categories with any capability at all (YES + PARTIAL), **five** now
produce confirmed real findings: Broken Access Control / Sensitive Data
Exposure (the `/API/Users` Mass Assignment finding), Security
Misconfiguration (checkov's 141 IaC findings, Trivy's `.tf` misconfig
findings, and the new `NGA-027` static rule), Injection (new semgrep
hits on Juice Shop's actual login/search SQLi and `eval()` misuse), and
Cryptographic Issues + Improper Input Validation (new semgrep hits on
weak password hashing, a hardcoded JWT secret, and real path-traversal
bugs). Unvalidated Redirects moved from NO to PARTIAL with the new
`build_open_redirect_probe`, though — like the other new redteam
probers added this session (SQLi/NoSQLi, path traversal, reflected
XSS, and now JWT tampering) — it has not yet produced a confirmed real
finding against a live Juice Shop run. Every other capable category ran
its scenarios and came back clean, for reasons detailed below. Two
report/coverage bugs were also found and fixed this session (backlog
#7): the Attack Coverage summary was miscounting real, fully-executed
scenario misses (`chain_status="aborted"` from `on_failure="abort"`'s
last-fallback-step-by-design pattern) as "not tested", deflating
per-goal-type coverage percentages; and `build_rate_limit_probe`'s
generator gate was inverted, only ever firing on endpoints already
confirmed rate-limited rather than the unconfirmed population NGA-026
flags — explaining why it never appeared in the 185-scenario run.

## Why coverage is thinner than the capability list suggests

**1. Tool-dependency and rule-coverage gaps (now closed).**
`semgrep_scanner.py` (Injection, XSS-adjacent, SSRF, hardcoded secrets)
and `checkov_scanner.py` (IaC coverage) were both silently skipped in the
original run because `semgrep`/`checkov` weren't installed —
`juice-shop-analysis.md`'s Tool Coverage table showed this plainly
(`⏭️ skipped`), easy to miss in a 2013-finding report. Both were installed
(via `pipx`, kept isolated from the project's own locked venv). checkov
now contributes 141 real IaC findings (`CKV_AWS_150`, `CKV_AWS_91`, etc.).
Semgrep initially ran cleanly but still contributed 0 findings against
Juice Shop, because its only bundled ruleset (`ai-security.yaml`) targets
AI/LLM-specific code patterns (SQLi-via-LLM, hardcoded API keys) with no
match in Juice Shop's non-AI Express/Node codebase — installing the tool
closed the tool-dependency gap but not the rule-coverage one. A second
bundled ruleset, **`generic-security.yaml`** (10 JS/TS rules: SQL
injection, command injection, path traversal, XSS, insecure eval,
insecure deserialization, weak password hashing, hardcoded secrets, open
redirect), now runs alongside `ai-security.yaml` on every scan and finds
**17 real findings** on Juice Shop's actual source, including hits on its
own intentionally-vulnerable challenge code (SQLi in `routes/login.ts`
and `routes/search.ts`, a hardcoded JWT secret in `lib/insecurity.ts`).

**2. Scope mismatch between attack path and target shape.** NuGuard's
`tool_abuse.py` (SQLi, SSRF) and `output_handling.py` (XSS) builders are
correctly designed for NuGuard's core use case — an LLM agent that calls
tools which touch a backend — but they only ever engage that chat-mediated
path. Juice Shop is a classic REST/HTML web app with a bolt-on chatbot;
most of its actual challenge catalog (XSS in product reviews, SQLi in the
login form, JWT tampering, path traversal, open redirects) lives entirely
outside the chat surface these builders probe. `api_attacks.py`'s
IDOR/mass-assignment/auth-bypass builders *do* hit raw HTTP directly,
which is exactly why they're the one family that found something real.

**3. The one real overlap had a real bug, now fixed but unvalidated.**
`build_idor` only ever substituted a hardcoded `99999` sentinel ID. On
Juice Shop's small sequential-integer primary keys (e.g. basket IDs),
that ID never exists, so every IDOR probe got a clean 404/403 regardless
of whether real ownership checks existed — a systematic false-negative.
This was root-caused and fixed in commit `f2169fd2` (adds a low-ID
`"1"` fallback probe), but the fix has not yet been validated against a
fresh run of Juice Shop.

## Improvement plan

Ordered roughly by effort-to-impact ratio; none of these are implemented
in this pass.

1. **Validate the IDOR fix.** Run a fresh red-team scan against Juice
   Shop and confirm `/Rest/Basket/:Id` (and other small-sequential-ID
   endpoints) now surface a real finding instead of a clean miss.
2. ~~**Install semgrep and checkov; extend semgrep beyond AI-security
   patterns**~~ — **done.** Both tools installed via `pipx`. checkov now
   contributes 141 real IaC findings. Added a second bundled ruleset,
   `nuguard/analysis/plugins/semgrep_rules/generic-security.yaml` (10 JS/TS
   rules covering Injection, path traversal, XSS, insecure eval, insecure
   deserialization, weak crypto, hardcoded secrets, open redirect),
   wired into `SemgrepScannerPlugin` alongside `ai-security.yaml` so every
   scan now covers both AI-specific and generic non-AI code. Validated
   against Juice Shop's real source: 17 findings, including its own
   intentional SQLi/hardcoded-secret/path-traversal challenges. **Open
   follow-up**: extend `generic-security.yaml` to more languages
   (Python/Go currently only have AI-specific rules) if non-AI Python/Go
   targets need the same generic coverage.
3. ~~**Add a generic direct-HTTP injection prober**~~ — **done (v1),
   validation pending.** Added `build_injection_probe` to
   `nuguard/redteam/scenarios/api_attacks.py`, following `build_idor`'s
   `target_path`-substitution / multi-step-fallback pattern: fuzzes path
   parameters and request-body fields with SQLi payloads
   (`' OR '1'='1`, `'; DROP TABLE x;--`) and NoSQLi operator payloads
   (`{"$ne": null}`, `{"$gt": ""}`), checking each response for one of 12
   DB driver/ORM error signatures (MySQL, Postgres, SQLite, SQL Server,
   Sequelize, MongoDB) via the existing `StepResult` OR-keyword matcher —
   no executor changes needed. Wired into `ScenarioGenerator`'s
   `_api_attack_scenarios` for any endpoint with a path or body parameter.
   Reuses `ScenarioType.SQL_INJECTION` (already scored 8.5 in
   `pre_scorer.py`), so it plugs into existing severity/compliance mapping
   with no other changes. Unit-tested (structure, fallback chaining,
   generator wiring, `StepResult` keyword matching) — `uv run pytest
   nuguard/redteam/ -q` passes (343 tests). **Not yet run against a live
   Juice Shop instance** — closes the gap in principle but real
   findings/misses against `routes/login.ts`/`routes/search.ts` are
   unconfirmed; do that validation in a follow-up redteam run before
   calling the Injection row fully "confirmed real finding" in the
   coverage matrix. **Deliberately deferred, not built**: (a)
   response-time/behavioral-delta (blind SQLi) detection — needs new
   timing plumbing in `TargetAppClient.invoke_endpoint`/`StepResult` that
   doesn't exist today; (b) query-param injection — no `query_params`
   SBOM metadata field exists yet, so only path/body params are probed.
4. ~~**Add a security-misconfiguration prober**~~ — **done, as a static
   check.** Added `NGA-027` (missing security headers), `NGA-028`
   (permissive CORS), `NGA-029` (verbose error leak) to
   `nuguard/analysis/plugins/nga_rules.py`, backed by new
   `SecurityHeaderDetail`/`CorsPolicyDetail`/`debug_error_leak` metadata
   (`nuguard/sbom/models.py`) extracted in `fastapi_adapter.py` and
   `flask_adapter.py`. Confirmed against Juice Shop's real SBOM: `NGA-027`
   fires (136 endpoints, no confirmed CSP/X-Frame-Options/HSTS).
   `NGA-028`/`NGA-029` correctly stay silent for Juice Shop today — its
   Express/Node endpoints aren't yet instrumented for CORS/debug-mode
   extraction (only FastAPI/Flask are). **Follow-up**: add the same
   detection to an Express/NestJS adapter so `NGA-028`/`NGA-029` can fire
   on Juice Shop itself, not just Python targets.
5. ~~**Add path-traversal and open-redirect direct-HTTP probes**~~ —
   **done, validation pending.** Added `build_path_traversal_probe`
   (`ScenarioType.PATH_TRAVERSAL`) and `build_open_redirect_probe`
   (`ScenarioType.OPEN_REDIRECT`) to `api_attacks.py`, both following
   `build_idor`'s parametrized-path fallback-chain pattern and gated on
   name-hinted path/body parameters (`file`/`filename`/`path`/... for
   traversal; `url`/`redirect`/`next`/`return_to`/... for redirect).
   Path traversal checks the response for `/etc/passwd`/`win.ini` content
   signatures. Open redirect uses an attacker-controlled marker URL on the
   RFC 2606 reserved `.invalid` TLD — since `TargetAppClient` follows
   redirects by default, a DNS-failure attempt to reach that marker
   (surfaced via a small addition to `invoke_endpoint`'s exception handler
   that now includes the unreachable request URL in `[REQUEST_ERROR: ...]`)
   is proof the server issued a redirect there. Both wired into
   `ScenarioGenerator._api_attack_scenarios` and unit-tested (structure,
   name-filtering, fallback chaining, generator wiring, and the client's
   new failed-URL surfacing). **Not yet validated against a live Juice
   Shop run.**
6. ~~**Broaden XSS coverage**~~ — **done, validation pending.** Added
   `build_reflected_xss_probe` (`ScenarioType.REFLECTED_XSS`) to
   `api_attacks.py`, complementing `build_output_xss`'s chat-scoped check:
   fuzzes any endpoint's path/body parameters (broad, unlike
   traversal/redirect — reflection isn't limited to suggestively-named
   fields) with a `<script>` payload carrying a random per-scenario
   marker, and checks for an exact unescaped echo in the response.
   Unit-tested; **not yet validated against a live Juice Shop run.**
   **Known limitation**: `TargetAppClient` doesn't expose response
   `Content-Type`, so a value reflected verbatim in a JSON API body (not
   itself exploitable unless a downstream page later renders it as HTML)
   can't currently be distinguished from a directly-HTML-rendered
   reflection — `use_llm_eval=True` is relied on as a secondary filter.
7. ~~**Reconcile the API_ATTACK coverage stat.**~~ — **done.** The
   catalog-count theory was wrong: `_attack_coverage_summary`
   (`nuguard/redteam/report.py`) already counted real
   `scenario_records`, not catalog spec entries. The actual bug was in
   what counted as "not tested": `on_failure="abort"` on a chain's last
   fallback step (`build_idor`, `build_injection_probe`,
   `build_path_traversal_probe`, etc. all use this by design — see
   backlog #3/#5) means a clean miss on every candidate ends the chain
   with a bare `chain_status="aborted"` — a real, fully-executed
   negative result, not a skip. The old `_NOT_TESTED` set counted that
   bare `"aborted"` as not-tested while (due to Python set membership on
   the literal string) *excluding* reason-suffixed circuit-breaker
   aborts (`"aborted:consecutive_request_failures"`) — backwards from
   what it should count. Fixed to treat only genuine non-executions
   (`skipped`, `similar_miss`, `target_unreachable`, `timeout`, and any
   `"aborted:<reason>"`) as not-tested; a bare `"aborted"` now counts as
   completed. Also fixed `build_rate_limit_probe`'s generator gate
   (`nuguard/redteam/scenarios/generator.py`), which required
   `meta.rate_limited is True` — backwards, since that only probes
   endpoints already believed to be rate-limited. On frameworks with no
   rate-limit adapter instrumentation (plain Express/Node, Juice Shop's
   case), `rate_limited` is never set at all, so the probe never fired
   in the 185-scenario run. Now fires on the population NGA-026 flags
   (`rate_limited is not True`), so a real 429 can empirically overturn
   the static finding and a clean burst confirms it. Both fixes are
   unit-tested (`test_report.py`, `test_api_attacks.py`);
   **the corrected numbers have not yet been re-validated against a
   live Juice Shop run.**
8. ~~**Add a JWT/crypto-implementation check**~~ — **done, validation
   pending.** Added `NGA-030` (`nuguard/analysis/plugins/nga_rules.py`)
   — flags any JWT `AUTH` node whose verification call site doesn't
   confirmably pin an `algorithms:` allow-list, pessimistic-by-default
   like `NGA-027`. Backed by a new `AuthDetail.jwt_algorithm_restricted`
   field extracted in `NestJSAuthTSAdapter`
   (`nuguard/sbom/adapters/typescript/auth_detector.py`): scans the same
   file as the JWT-signing call site for a `jwt.verify(...)` call and
   checks whether its options include `algorithms:`. Added
   `build_jwt_tampering_probe` (`api_attacks.py`,
   `ScenarioType.JWT_TAMPERING`) — sends the request with real
   credentials stripped and a forged `Authorization: Bearer` header in
   their place: one `alg: none` unsigned token, then HS256 tokens signed
   with 3 common default secrets (`"secret"`, `"changeit"`,
   `"your-256-bit-secret"`). A 2xx response to any forged token means
   the server accepted a token it never verified. Wired into the
   generator on the same population as `build_auth_bypass`
   (`auth_required=True`, or unknown and not a public-looking path).
   Required two small supporting fixes: `TargetAppClient.invoke_endpoint`
   (`nuguard/redteam/target/client.py`) was stripping `extra_headers` of
   the same name as a tracked auth header (e.g. a forged `Authorization`)
   right along with the real one when `strip_auth=True` — reordered so
   stripping happens before `extra_headers` is applied, not after; and
   the executor's direct-HTTP step path
   (`nuguard/redteam/executor/executor.py`) never forwarded
   `step.extra_headers` to `invoke_endpoint()` at all, so no builder
   could have used per-step custom headers regardless. Unit-tested
   (forging helpers, builder structure, generator wiring, the
   client/executor fixes); **not yet validated against a live Juice
   Shop run.** **Deliberately deferred, not built**: RS256→HS256
   algorithm-confusion (needs the server's real public key, which the
   SBOM doesn't capture) and brute-forcing beyond the fixed 3-secret
   dictionary.
