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
| 5 | Cryptographic Issues | `NGA-005` (unencrypted PII datastore) — encryption-at-rest posture only | PARTIAL | No — no crypto-implementation (weak JWT alg, guessable secret) check exists to fire |
| 6 | Improper Input Validation | — | **NO** | N/A — no generic input-validation/fuzz prober |
| 7 | Injection (SQL/NoSQL/command) | `build_sql_injection` (`tool_abuse.py`) + semgrep rule `nuguard-sql-injection-via-llm` — both LLM-tool-mediated only | PARTIAL | "SQL Injection via Agent Chat — Sequelize" ran 7/7 turns, no finding (plausible true negative — Juice Shop's chatbot doesn't proxy raw SQL). Semgrep now runs (`✅ ok`) but still contributes 0 findings — its rules target LLM-mediated SQLi patterns, not Juice Shop's plain Sequelize/JS code, so the Injection gap for generic (non-AI) apps remains real |
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

**Summary**: 4 of 16 categories YES, 6 PARTIAL, 5 NO, 1 N/A. Of the 10
categories with any capability at all (YES + PARTIAL), **two** now
produce confirmed real findings: Broken Access Control / Sensitive Data
Exposure (the `/API/Users` Mass Assignment finding), and Security
Misconfiguration (checkov's 141 IaC findings, Trivy's `.tf` misconfig
findings, and the new `NGA-027` static rule). Every other capable
category ran its scenarios and came back clean, for reasons detailed
below.

## Why coverage is thinner than the capability list suggests

**1. Tool-dependency gaps (now closed).** `semgrep_scanner.py` (Injection,
XSS-adjacent, SSRF, hardcoded secrets) and `checkov_scanner.py`
(IaC coverage) were both silently skipped in the original run because
`semgrep`/`checkov` weren't installed — `juice-shop-analysis.md`'s Tool
Coverage table showed this plainly (`⏭️ skipped`), easy to miss in a
2013-finding report. Both were installed (via `pipx`, kept isolated from
the project's own locked venv) and the analysis re-run: checkov now
contributes 141 real IaC findings (`CKV_AWS_150`, `CKV_AWS_91`, etc.).
Semgrep runs cleanly but contributes 0 findings against Juice Shop —
its bundled `ai-security.yaml` ruleset targets AI/LLM-specific code
patterns (SQLi-via-LLM, hardcoded API keys) that don't have a match in
Juice Shop's non-AI Express/Node codebase, so the tool-dependency gap is
closed but the *rule coverage* gap for generic (non-AI) JS/TS SAST
remains open.

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
2. ~~**Install semgrep and checkov**~~ — **done.** Both installed via
   `pipx` and wired into the CI/test-app pipeline's tool resolution.
   checkov now contributes 141 real IaC findings. Semgrep runs cleanly
   but its current ruleset (`ai-security.yaml`) is AI/LLM-pattern-specific
   and doesn't match Juice Shop's non-AI JS/TS code — extending semgrep
   with generic JS/TS SAST rules (not just AI-security ones) remains open
   if broader Injection/XSS source-level coverage is wanted.
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
