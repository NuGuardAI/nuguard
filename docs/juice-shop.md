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
| 2 | Broken Anti-Automation | `NGA-026` (missing rate limiting, static) + `build_rate_limit_probe` (`api_attacks.py`) | PARTIAL | No — no rate-limit-probe scenario appears in the 185-scenario run; the dynamic check didn't fire |
| 3 | Broken Authentication | `NGA-006` (missing auth on endpoint) + `build_auth_bypass`, session-fixation spec `S06` (`api_schema_attacks.py`) | PARTIAL | No — no weak-password, session-fixation, or JWT-specific finding surfaced |
| 4 | Cross-Site Scripting (XSS) | `build_output_xss` (`output_handling.py`) — LLM-output-echo XSS only | PARTIAL | No finding; only 3 "Improper Output Handling" scenario instances ran, and the check is scoped to markdown/HTML the *chat agent* echoes, not server-rendered HTML pages |
| 5 | Cryptographic Issues | `NGA-005` (unencrypted PII datastore, encryption-at-rest posture) + new `generic-security.yaml` rules `nuguard-js-weak-hash-for-password`, `nuguard-js-hardcoded-secret` | PARTIAL | **YES on the new rules** — `nuguard-js-weak-hash-for-password` fires on `lib/insecurity.ts:41` (MD5 for password hashing, a real Juice Shop weakness) and `scripts/package.mjs:121`; `nuguard-js-hardcoded-secret` fires on the hardcoded JWT signing secret in `lib/insecurity.ts:54`. Still no dedicated JWT-alg-confusion (`alg: none`) check — see backlog #8 |
| 6 | Improper Input Validation | New `generic-security.yaml` rule `nuguard-js-path-traversal` | PARTIAL | **YES** — fires on real code: `routes/videoHandler.ts:82`, `routes/vulnCodeFixes.ts:81`, `routes/vulnCodeSnippet.ts:90`, `rsn/rsnUtil.ts:66` and `:155` (5 hits total) |
| 7 | Injection (SQL/NoSQL/command) | `build_sql_injection` (`tool_abuse.py`, LLM-tool-mediated) + new **`generic-security.yaml`** semgrep ruleset (`nuguard-js-sql-injection`, `nuguard-js-command-injection`, `nuguard-js-insecure-eval`) for direct JS/TS source patterns | **YES** | "SQL Injection via Agent Chat — Sequelize" (redteam) ran 7/7 turns, no finding — plausible true negative, Juice Shop's chatbot doesn't proxy raw SQL. But the new semgrep ruleset found **real hits on Juice Shop's actual vulnerable code**: SQLi in `routes/login.ts:34` and `routes/search.ts:23` (the classic Juice Shop login-bypass and search-injection challenges), plus 4 more in `data/static/codefixes/*.ts`; `nuguard-js-insecure-eval` in `routes/captcha.ts:22` and `routes/userProfile.ts:65`; `nuguard-js-command-injection` in `scripts/package.mjs:20` |
| 8 | Insecure Deserialization | — | **NO** | N/A |
| 9 | Miscellaneous | — | N/A | N/A (not a distinct vuln class) |
| 10 | Security Misconfiguration | `trivy_scanner.py`'s built-in `misconfig` scanner + `checkov_scanner.py` (IaC) + new static rules **`NGA-027`** (missing security headers), **`NGA-028`** (permissive CORS), **`NGA-029`** (verbose error leak) in `nuguard/analysis/plugins/nga_rules.py`, fed by new `SecurityHeaderDetail`/`CorsPolicyDetail`/`debug_error_leak` extraction in `fastapi_adapter.py`/`flask_adapter.py` | **YES** | **YES, real findings on both IaC and app layers.** After installing semgrep+checkov and re-running: Trivy's misconfig scanner found 9 findings per `.tf` file (`AWS-0054`, `AWS-0104`, etc.); checkov itself now runs (`✅ ok`, 141 findings, `CKV_AWS_150`/`CKV_AWS_91`/etc. on `aws_lb.juice_shop`). `NGA-027` fired for real: 136 API endpoints flagged for no confirmed CSP/X-Frame-Options/HSTS. `NGA-028`/`NGA-029` stayed silent — Juice Shop's Express/Node endpoints aren't yet instrumented for CORS/debug-mode extraction (only FastAPI/Flask are), a known follow-up (see backlog #4) |
| 11 | Security through Obscurity | — | **NO** | N/A |
| 12 | Sensitive Data Exposure | `NGA-001`/`NGA-003`/`NGA-005`/`NGA-025` (static: PII to external LLM, secrets, unencrypted datastore, credentials in prompts) + `build_open_data_exposure` (`api_attacks.py`) | **YES** | **YES** — same `/API/Users` Mass Assignment finding: response body contained email, password hash, role, IP without auth |
| 13 | Unvalidated Redirects | — | **NO** | N/A |
| 14 | Vulnerable Components | `osv_client.py` + `grype_client.py` + `trivy_scanner.py` (CVE/SCA against SBOM components) | **YES** | **YES** — 2154 findings total (55 CRITICAL, 552 HIGH), largely from these three scanners |
| 15 | XML External Entities (XXE) | — | **NO** | N/A |
| 16 | Observability Failures | `NGA-009` (AI application has no audit logging enabled) | PARTIAL | Finding present in static report, but it's a generic "is there any AI-app audit logging" check, not Juice-Shop-specific log-injection/insufficient-logging coverage |

*(Not one of the named 16, but worth flagging: **CSRF** has no dedicated
check anywhere in NuGuard — neither static nor dynamic — despite being a
classic OWASP class that overlaps with Juice Shop's Broken Access
Control / Misconfiguration challenges.)*

**Summary**: 5 of 16 categories YES, 6 PARTIAL, 4 NO, 1 N/A. Of the 11
categories with any capability at all (YES + PARTIAL), **five** now
produce confirmed real findings: Broken Access Control / Sensitive Data
Exposure (the `/API/Users` Mass Assignment finding), Security
Misconfiguration (checkov's 141 IaC findings, Trivy's `.tf` misconfig
findings, and the new `NGA-027` static rule), Injection (new semgrep
hits on Juice Shop's actual login/search SQLi and `eval()` misuse), and
Cryptographic Issues + Improper Input Validation (new semgrep hits on
weak password hashing, a hardcoded JWT secret, and real path-traversal
bugs). Every other capable category ran its scenarios and came back
clean, for reasons detailed below.

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
3. **Add a generic direct-HTTP injection prober** in `api_attacks.py`,
   reusing the `target_path`-substitution pattern `build_idor` already
   uses: fuzz path/query/body parameters with SQLi/NoSQLi payloads and
   check for DB-error signatures or response-time/behavioral deltas.
   Closes the Injection gap for apps with no LLM-mediated DB path.
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
5. **Add path-traversal and open-redirect direct-HTTP probes** — small,
   cheap additions using the same parametrized-path pattern as #3.
6. **Broaden XSS coverage** beyond `build_output_xss`'s LLM-echo scope to
   also probe server-rendered HTML surfaces (reflected/stored params in
   non-chat responses) — needed for non-AI or hybrid apps like Juice Shop.
7. **Reconcile the API_ATTACK coverage stat.** The run summary reports
   "3% (3/92)" coverage for API Attack while the detailed scenario table
   shows far more API-attack rows actually executed with results — this
   looks like the catalog generator's stable-ID spec count being reported
   instead of the (larger) legacy SBOM-driven generator's actual output.
   Investigate and fix so the summary number is trustworthy. Also
   investigate why no rate-limit-probe scenario appears to have run at all.
8. **Add a JWT/crypto-implementation check** — a new NGA rule plus a
   dynamic JWT-tampering probe (alg=none, weak/guessable HMAC secret) —
   currently only datastore-encryption-at-rest (`NGA-005`) is checked.
