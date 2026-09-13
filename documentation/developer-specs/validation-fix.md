# Validation accuracy & efficiency: endpoint liveness, browser-based discovery, enriched-SBOM caching, capability dedup

## Context

Behavior and redteam scans currently re-discover the same live-target facts
every run — which chat endpoint actually works, whether other REST endpoints
in the SBOM are even reachable, what sub-agents/tools/golden test data the
target exposes — because none of that gets cached across runs, and most of it
isn't even checked per-endpoint today (only the single primary chat endpoint
gets a liveness ping). This wastes network calls/time and, more importantly,
lets dead/misresolved endpoints silently read as "safeguard held" (the same
root cause as Gap A in the section below, generalized to *every* endpoint, not
just the ones a static filter can catch). Separately, the SBOM's static
extractor can miss endpoints entirely, and capability-name discovery still
produces avoidable near-duplicates that a cheap LLM pass could collapse.

Goal: make behavior/redteam testing more **accurate** (mark endpoints
operational/non-operational from a live ping so dead ones can't masquerade as
"defended"; discover endpoints the static extractor missed via a bounded
browser crawl) and more **efficient** (persist all of this — liveness, golden
data, discovered capabilities — into the enriched SBOM so a redteam run after
a behavior run, or vice versa, doesn't repeat live network round-trips),
following the exact caching pattern already proven for `discovered_profile`
(commit `9b9fa056`). Maximize reuse of existing primitives; extend, don't
duplicate, the Gap A1/A2 endpoint-liveness-filtering mechanism already
implemented in this codebase.

This plan is additive to (does not replace) the already-implemented
Gaps A–G below (endpoint structural filtering, refusal-aware evidence, finding
dedup, 4xx misclassification, remediation mapping, SSE capability parsing,
exfiltration severity) — those stay as historical record; this new work is the
next stage on top of them.

## Existing pattern to replicate (verified in code)

`nuguard/behavior/runner.py:2024-2056` + `nuguard/common/auto_sbom_enricher.py`:
- **Model field**: `AiSbomDocument.discovered_profile: dict[str, Any] | None`
  (`nuguard/sbom/models.py`).
- **Writer**: `persist_discovery_profile_sbom(sbom, sbom_path)` in
  `auto_sbom_enricher.py`, writes to `<name>.sbom.enriched.json` via
  `_enriched_output_path()` / `_write_enriched(sbom, out_path, cache_key=None)`
  — `cache_key=None` deliberately, so this live-target-derived write survives
  generic cache-key staleness checks.
- **Populate-guard**: `_persist_discovery_profile_sbom()` no-ops when
  `profile.is_empty` or sbom/path missing — never overwrite good cached data
  with an empty result.
- **Read/skip-guard**: `_cached_discovery_profile()` returns `None` on
  missing/unparseable/empty data; `discover()` uses the cached profile
  directly and prints `"Pre-scan discovery (from enriched SBOM): ..."`,
  skipping the live HTTP round-trip entirely, when present.

Every new cache in this plan (endpoint liveness, golden data) replicates this
same 4-part shape.

## Relevant existing primitives (do not reinvent)

- `nuguard/common/endpoint_probe.py` — `probe_chat_endpoints()`,
  `discover_chat_candidates_from_sbom()`: chat-like classification (2xx-JSON
  or non-404/405 4xx), health/login/metrics path exclusion.
- `nuguard/common/endpoint_preflight.py` — `validate_and_rotate_chat_endpoint()`,
  `PreflightOutcome`, `_TEST_MESSAGE="Hello"`, `_ROTATION_TRIGGER_PREFIXES =
  ("[HTTP 405]", "[HTTP 404]", "[HTTP 400]", "[HTTP 422]")` — the closest
  existing "liveness ping" analog, currently applied only to the one primary
  chat endpoint, called independently (duplicated) from both
  `behavior/runner.py:2403` and `redteam/executor/orchestrator.py:1745`.
- `nuguard/redteam/scenarios/generator.py` `_api_attack_scenarios()` /
  `_looks_like_rest_path()` and `nuguard/redteam/executor/orchestrator.py`
  `_maybe_mark_endpoint_not_found()` — already-implemented Gap A1/A2 mechanism
  that skips/reclassifies *structurally* bad endpoints (bind-address/SSE/MCP
  strings, or all-404 execution). This plan extends the same skip mechanism to
  also cover "confirmed dead via live ping," not just "structurally invalid."
- `nuguard/common/discovery.py` — `DiscoveredProfile`, `run_discovery`,
  capability discovery (`sbom_capability_gaps`, `run_capability_discovery`,
  `apply_capability_discovery`) — regex/heuristic only today, shared by both
  behavior and redteam.
- `nuguard/common/browser_login/session.py` — `BrowserLoginSession`,
  `_sniff_chat_request()` (network-request sniffing for exactly one chat POST
  during login capture). This is the machinery Phase 2 extends from "sniff one
  request" to "sniff many during a bounded, operator-declared crawl" — reuses
  the same Playwright/Chromium dependency already in the codebase, no new
  browser-automation library.
- `nuguard/sbom/core/gap_fill/dedup.py` — `_normalize_endpoint()`,
  `DedupContext` — the merge/dedup primitive for adding newly-discovered
  endpoints as SBOM nodes without duplicating existing ones.
- `nuguard/redteam/target/session.py` `AttackSession.golden_data`/`golden_ids`
  and `nuguard/redteam/executor/golden_data_filter.py` — verbatim golden-data
  capture and its token-overlap false-positive suppression, currently
  run-scoped only (never persisted).
- `nuguard/config.py` `BehaviorConfig.golden_data` (:653),
  `NuGuardConfig.redteam_golden_data` (:1517), `probe_llm`/`capability_discovery`
  booleans (:665-680) — the established style for new optional config knobs.

## Phase 1 — Per-endpoint liveness/reachability marking

**New fields** on `NodeMetadata` (`nuguard/sbom/models.py`, next to the
existing API_ENDPOINT block ~line 529-563):
```python
operational: bool | None = Field(default=None, description=(
    "True when a live authenticated ping to this API endpoint returned a "
    "reachable response (including a 401/403 auth-correctly-enforced "
    "response); False when it hit a rotation-trigger 4xx (404/405/400/422) "
    "or a network-level failure; None when never probed."))
liveness_checked_at: str | None = Field(default=None, description="ISO8601 timestamp of the last liveness probe.")
liveness_notes: list[str] = Field(default_factory=list, description="Notes from the last liveness probe (status codes, timeouts, rotation).")
```
New public fields on `AiSbomDocument`'s node metadata → triggers the
[pydantic-interface skill](.github/skills/pydantic-interface/SKILL.md):
regenerate `tests/contracts/public_api.schema.json`, re-run
`test_public_api_schema_contract.py`.

**New module** `nuguard/common/endpoint_liveness.py`:
- `async def check_endpoint_liveness(sbom, client, auth_headers, *, per_endpoint_timeout, max_concurrent) -> LivenessReport` —
  bounded by `asyncio.Semaphore(max_concurrent)` (config-controlled, default
  5); serialize (no concurrency) against any node with `rate_limited=True`.
  `LivenessReport` is a small summary model (`checked`, `operational`,
  `non_operational`, `skipped`, `notes`) for logging.
- Reuse `_ROTATION_TRIGGER_PREFIXES`/classification logic from
  `endpoint_preflight.py` (import, don't duplicate) for chat-like endpoints;
  for other REST endpoints send a minimal request using the endpoint's
  declared `method` (reuse `build_minimal_payload` from
  `nuguard/common/response_extraction.py`, already imported by
  `endpoint_preflight.py`).
- Nodes already filtered by `_looks_like_rest_path` as structurally invalid
  are skipped (not probed) and marked `operational=None` with a note — `None`
  because we never actually tested reachability, `False` would wrongly imply
  "pinged and dead."
- **401/403 → `operational=True`** (endpoint exists, correctly gated) — reuse
  the existing 401-vs-404 distinction pattern from Gap A2's
  `_maybe_mark_endpoint_not_found`/404-detection helper in `orchestrator.py`,
  don't reimplement.
- Network exception (DNS/connection refused) → `operational=False`, logged at
  `_log.error` (real outage/misconfiguration); timeout → `operational=False`,
  logged at `_log.warning` (routine, not necessarily a bug).

**Shared call site** (avoid duplicating the ping across both packages): add
`ensure_endpoint_liveness(sbom, client, auth_headers, config)` as the single
entry point, called from both `behavior/runner.py` (right after its existing
`validate_and_rotate_chat_endpoint` block, ~line 2403+) and
`redteam/executor/orchestrator.py` (its equivalent preflight call, ~line
1745+). This function checks Phase 3's cache first (see below) so only
whichever package runs first actually pays the live-probe cost.

**Consumption by scenario generators** (extends existing Gap A1/A2, same
skip-note convention):
- `generator.py` `_api_attack_scenarios()`: after the existing structural
  `_looks_like_rest_path` check, additionally skip direct-HTTP scenario
  synthesis when `meta.operational is False`. `operational is None` does
  **not** skip (preserves current behavior for never-probed endpoints).
- `behavior/scenarios.py` `_endpoint_coverage_scenarios` (~1795-1886): same
  `operational is False` skip, with an equivalent skip-note surfaced in the
  run summary.
- Extract the (structural-filter OR confirmed-dead) skip decision into one
  shared helper — `nuguard/common/endpoint_scenario_gate.py::should_skip_direct_http_scenario(meta) -> tuple[bool, str | None]`
  — imported by both `generator.py` and `scenarios.py`, so this one decision
  never has to be kept in sync by hand across the two files again.

**Tests**: `nuguard/common/tests/test_endpoint_liveness.py`
(`test_operational_true_on_2xx`, `test_operational_false_on_404`,
`test_operational_true_on_401_not_false`,
`test_bind_address_endpoint_skipped_not_marked_dead`,
`test_rate_limited_endpoint_probed_serially`,
`test_timeout_marks_non_operational_with_note`);
`nuguard/common/tests/test_endpoint_scenario_gate.py`
(`test_confirmed_dead_endpoint_skipped`,
`test_structurally_invalid_endpoint_skipped`,
`test_operational_unknown_not_skipped`); extend
`nuguard/redteam/tests/test_api_endpoint_liveness_filter.py` and add
`nuguard/behavior/tests/test_scenarios_endpoint_coverage.py` for the generator
call sites.

## Phase 2 — Browser/Playwright-based endpoint discovery (v1, config-gated)

**As implemented**: `crawl_and_sniff()`/`merge_sniffed_endpoints_into_sbom()`
landed as a standalone, tested capability plus config fields
(`behavior.browser_discover_endpoints`/`nav_targets`,
`redteam.browser_discover_endpoints`/`nav_targets`). Automatic bootstrap
wiring into `behavior/runner.py`/`orchestrator.py` was deliberately deferred:
`browser_login/session.py`'s own docstring states it "is only imported by
`nuguard/cli/commands/target_browser.py`, never by any hot path" — wiring
this crawl into the automatic bootstrap would mean constructing a full
separate browser-login flow (its own auth resolution, cookie/session
lifecycle) inside those runners, a materially larger and riskier change than
Phase 1/3's single-call-site additions. Until that wiring lands, invoke the
crawl explicitly (e.g. from a future `nuguard target discover-browser`
extension, or a short CLI/script wrapper) and merge its result into an
enriched SBOM before running `behavior`/`redteam`.

**Scope**: a bounded, operator-declared crawl — not autonomous link-following
(too open-ended for a security tool; could trigger destructive actions like a
delete-account button). v1 visits only caller-supplied `nav_targets` paths
plus optionally re-runs the existing chat interaction, passively sniffing all
XHR/fetch requests during that bounded sequence.

**New module** `nuguard/common/browser_login/endpoint_sniffer.py` (sibling to
`session.py`, reuses its `BrowserLoginSession` rather than subclassing):
- `SniffedRequest` (Pydantic): `{method, url, path, status_code,
  request_body_keys, response_snippet, same_origin}` — key names only, not
  full bodies (avoid capturing secrets/PII into the SBOM).
- `async def crawl_and_sniff(session, *, nav_targets, interact_chat, budget_s) -> list[SniffedRequest]`
  — mirrors `_sniff_chat_request`'s `_on_request` listener but without its
  early-exit-on-first-match, capped at a max request count (e.g. 50). Reuses
  `_same_origin`/`_ASSET_EXT_RE` filtering as-is. `nav_targets` are plain
  `page.goto(url)` calls to config-declared relative paths — never
  auto-discovered link-following.

**New config** (`nuguard/config.py`, mirrors `probe_llm` style):
```python
browser_discover_endpoints: bool = Field(default=False, description=(
    "After browser login, crawl caller-declared nav_targets and sniff network "
    "requests via Playwright to find REST endpoints the static SBOM extractor "
    "missed, merging them into the SBOM as new API_ENDPOINT nodes. Off by "
    "default — requires Playwright/Chromium and browser login already configured."))
browser_discovery_nav_targets: list[str] = Field(default_factory=list)
```

**Merge into SBOM**: `merge_sniffed_endpoints_into_sbom(sbom, sniffed) -> int`
using `nuguard/sbom/core/gap_fill/dedup.py`'s `DedupContext`/
`_normalize_endpoint()` directly (same primitive `gap_fill/rounds.py` already
uses) — no need to route through the full LLM gap-fill round. New nodes get
`Evidence(kind="browser_sniff", confidence=0.6)`, matching the existing
`dynamic_probe` confidence-0.5 convention used by `apply_capability_discovery`.

**Error handling**: Chromium/Playwright missing → same `BrowserLoginError`
path `session.py` already handles, caught by the caller, logged at
`_log.warning`, run continues without this optional step. Unreachable
nav-target → skip that one target, `_log.info`, continue with the rest.
Non-JSON/unparseable sniffed body → skip that request at `_log.debug` (matches
`_sniff_chat_request`'s existing `except (ValueError, TypeError): return`).

**Integration**: every newly-merged node starts with `operational=None`; run
Phase 1's `ensure_endpoint_liveness` again after this merge (same idempotent,
cache-aware function — no new liveness code needed, just correct ordering).

**Tests**: `nuguard/common/browser_login/tests/test_endpoint_sniffer.py`
(`test_crawl_captures_multiple_requests_not_just_first`,
`test_asset_urls_filtered`, `test_cross_origin_filtered`,
`test_response_body_not_captured_full_only_keys`,
`test_max_capture_count_bounds_collection`); extend
`nuguard/sbom/tests/` gap-fill dedup tests with
`test_sniffed_endpoint_deduped_against_existing_node`,
`test_new_sniffed_endpoint_creates_node_with_dynamic_evidence`.

## Phase 3 — Enriched-SBOM caching (liveness + golden data)

**3a. Liveness** — no new top-level dict field needed: `operational`/
`liveness_checked_at`/`liveness_notes` already live on `NodeMetadata`, part of
`sbom.nodes`, so they're captured by any existing enriched-SBOM write. Add:
- `persist_liveness_sbom(sbom, sbom_path)` in `auto_sbom_enricher.py`, same
  `_enriched_output_path()`/`_write_enriched(sbom, out_path, cache_key=None)`
  shape as `persist_discovery_profile_sbom` (same reason: live-target-derived
  data must survive generic cache-key staleness checks).
- Populate-guard: only write when at least one node's `operational` changed
  from `None` to a definite value, or the cache is past TTL — never write a
  no-op pass.
- Read/skip-guard: `_cached_liveness_is_fresh(sbom, ttl_seconds) -> bool` in
  `endpoint_liveness.py`; `ensure_endpoint_liveness` skips the live probe for
  any node whose `liveness_checked_at` is within
  `redteam.liveness_cache_ttl_seconds`/`behavior.liveness_cache_ttl_seconds`
  (new config, default 3600), logging `"Endpoint liveness (from enriched
  SBOM, checked {ts}): {path} -> {operational}"` — mirrors the
  `discovered_profile` cache-hit log line.

**3b. Golden data** — **implemented (revised from the original sketch
below).** Investigating the actual executor code (`nuguard/redteam/executor/executor.py`,
`_golden_data_cache` population) showed `DiscoveredProfile.raw_response` is
already documented and used as "concatenation of all discovery turn responses
(for golden-data cache seeding)" — it's already the verbatim text
`golden_data_filter.py` needs, not a lossy normalized form. So no second field
was needed; the real gap was simpler and more consistent with "maximum code
reuse": **redteam never read or wrote `sbom.discovered_profile` at all** —
`orchestrator.py` always ran a live DISCOVER conversation unconditionally
(unless `skip_discovery`), and never persisted its result, even though
`behavior/runner.py` has had this exact read/write cache for `discovered_profile`
since commit `9b9fa056`. Implemented:
- New shared `nuguard.common.discovery.cached_discovery_profile(sbom)` —
  extracted from `behavior/runner.py`'s existing private
  `_cached_discovery_profile()` into a module-level function both packages
  can call (parse-guard + `is_empty` check, same as the original).
- `orchestrator.py`'s pre-scan discovery block now calls
  `cached_discovery_profile(self._sbom)` before opening the discovery client
  connection; on a hit it skips the `run_discovery(...)` call entirely
  (capability discovery, a separate concern, still runs if it has its own
  gaps to fill) and logs `"pre-scan discovery (from enriched SBOM): name=... ids=..."`.
  On a miss, it runs live discovery as before, then — new — persists a
  non-empty result via `persist_discovery_profile_sbom` so a later run (either
  package) gets the cache hit.
- Original sketch (superseded by the above, kept here for history): add a
  distinct `AiSbomDocument.discovered_golden_data` field, a parallel
  `persist_golden_data_sbom`, and a priority order of cached-SBOM > live-DISCOVER >
  config fallback. Not built — `discovered_profile` already covers this
  without a second field or a second persistence path.

**Tests**: `nuguard/common/tests/test_endpoint_liveness_cache.py`
(`test_fresh_cache_skips_live_probe`, `test_stale_cache_triggers_reprobe`,
`test_empty_liveness_result_does_not_overwrite_cached_data`,
`test_fresh_probe_result_persisted_to_enriched_sbom`,
`test_ttl_none_preserves_original_always_probe_behavior`);
`nuguard/common/tests/test_discovery_profile_persistence.py` extended with
`cached_discovery_profile()` unit tests (`returns_none_when_sbom_is_none`,
`_when_field_unset`, `_when_empty`, `_on_unparseable_data`,
`returns_profile_when_non_empty`).

## Phase 4 — LLM-assisted semantic dedup for capability/tool discovery

Targets a still-open gap distinct from the already-fixed SSE/JSON-fragment
bug (commit `faa56ce9`): plain `.lower()` set-membership dedup in
`apply_capability_discovery` (`discovery.py:880-984`) misses near-duplicates
like `"send_email"` vs `"SendEmailTool"` vs `"email_sender"`.

- `async def llm_dedup_capability_names(candidates, existing, llm) -> dict[str, str]`
  (new `nuguard/common/capability_dedup_llm.py`, kept separate so
  `discovery.py` doesn't need an `llm_client` import for callers who don't
  want it) — maps a candidate to an existing canonical name when the LLM
  judges them equivalent; unmapped candidates are treated as genuinely new
  (unchanged behavior).
- Called as an **additive second pass** inside `apply_capability_discovery`,
  only on names that survive the existing regex dedup (minimizes token cost).
  Gated by new `behavior.llm_capability_dedup`/`redteam.llm_capability_dedup: bool = False`
  config, and by an explicit check that a real LLM is configured (not just
  `LLMClient`'s canned-response fallback per `llm_client.py:194-196`) — skip
  silently, don't call, when no LLM is configured.
- Any LLM failure/timeout/malformed response → fall back to the pre-LLM
  regex-only result unchanged (`_log.warning` on failure, `_log.debug` when
  skipped because disabled/unconfigured) — additive-only, never weakens
  existing detection.

**Tests**: `nuguard/common/tests/test_capability_dedup_llm.py`
(`test_near_duplicate_names_deduped_via_llm`,
`test_llm_failure_falls_back_to_heuristic_result_unchanged`,
`test_disabled_by_default_no_llm_call_made`,
`test_canned_llm_client_response_does_not_cause_false_dedup`).

**Implemented as designed**, with one structural consequence: since the LLM
call is async, `apply_capability_discovery()` itself had to become
`async def` (it was previously synchronous). All three call sites
(`orchestrator.py`, `behavior/runner.py` x2) were already inside `async`
functions calling it right after an `await run_capability_discovery(...)`, so
adding `await` there was a one-line change each; `llm` defaults to `None`
everywhere, so non-LLM callers are unaffected. New `behavior.llm_capability_dedup`
/ `redteam.llm_capability_dedup` config (off by default), threaded through the
CLI's nested wrapper functions in `nuguard/cli/commands/redteam.py` and
`RedteamRunRequest` in `nuguard/redteam/public_api.py`. One pre-existing test
file outside the `nuguard/` tree, `tests/common/test_capability_discovery.py`,
called `apply_capability_discovery()` synchronously and had to be updated to
`async def`/`await` — a real (now-fixed) regression this change would
otherwise have introduced.

## Phase 5 — Behavior/redteam scenario-generation convergence: recommendation

**Do not** force a shared scenario-generator base class between
`behavior/scenarios.py` (`BehaviorScenarioType`, customer-centric,
avoid-refusal-triggering phrasing) and `redteam/scenarios/generator.py`
(`ScenarioType`, adversarial phrasing against `CognitivePolicy`). The intents
are different enough that a shared base would either leak adversarial helpers
into behavior's builder or produce an abstraction whose only real shared
surface is "both read a policy and iterate SBOM nodes" — already shared via
`discovery.py`/`models/policy.py`. Forcing more would be premature
abstraction. The one piece of duplication actually worth fixing —
Phase 1's endpoint-skip decision — is already addressed by the shared
`endpoint_scenario_gate.py` helper above; no further convergence is proposed
in this plan. (Recorded as a deliberate "no" so it isn't re-litigated without
new information.)

## Recommended follow-ups (not required by this plan)

1. A single `BootstrapSummary` surfaced once per run (endpoints
   checked/operational/skipped, cache hit/miss counts, browser-discovery
   new-node count, LLM-dedup collapse count) so operators can see how much of
   a run was live-verified vs. cached vs. assumed — makes the efficiency gain
   from this plan visible, not just implicit.
2. Once `operational` is a first-class field, `nuguard analyze`'s static risk
   scoring could down-weight findings tied to confirmed-dead endpoints.
3. If NuGuard ever tracks deployment/version fingerprints, use that (not just
   a time TTL) to invalidate the liveness cache on redeploy.

## Phase ordering

1. **Phase 1** first (defines the fields everything else depends on) —
   include the Phase 5 `endpoint_scenario_gate.py` extraction in this same
   change, since Phase 1 is already touching both generator call sites.
2. **Phase 3a** (liveness caching) immediately after Phase 1, same PR/series —
   don't ship the new field without its cache half, or the "don't repeat live
   pings across runs" goal isn't actually met yet.
3. **Phase 2** next (depends on Phase 1 existing so new nodes get
   liveness-checked through the same path).
4. **Phase 3b** (golden data) can land in parallel with Phase 2 — independent
   code path (redteam executor/orchestrator only).
5. **Phase 4** (LLM dedup) is fully independent — any time, lowest priority
   relative to the liveness/caching work.

## Verification

After each phase:
```
uv run ruff check nuguard/
uv run mypy nuguard/
uv run pytest tests/ nuguard/ -q
```
Contract regeneration (Phase 1 and Phase 3 add public `AiSbomDocument`/
`NodeMetadata` fields):
```
uv run pytest tests/contracts/test_public_api_schema_contract.py -q
# regenerate tests/contracts/public_api.schema.json per the pydantic-interface skill
```

End-to-end, against at least two `tests/apps/*` targets:
```
# Behavior first — should populate operational/liveness_checked_at + discovered_profile.
uv run nuguard behavior --config tests/apps/pinnacle-bank-app/nuguard-azure.yaml --format markdown -o tests/apps/pinnacle-bank-app/reports/pinnacle-bank-behavior.md

# Inspect the enriched SBOM for the new liveness fields.
python -c "import json; d=json.load(open('tests/apps/pinnacle-bank-app/pinnacle-bank.sbom.enriched.json')); print([n['metadata'].get('operational') for n in d['nodes'] if n.get('component_type')=='API_ENDPOINT'])"

# Redteam second, same target — should read cached liveness/golden-data from
# the enriched SBOM (log lines "Endpoint liveness (from enriched SBOM, ...)"),
# not re-probe.
uv run nuguard redteam --config tests/apps/pinnacle-bank-app/nuguard-azure.yaml --format markdown -o tests/apps/pinnacle-bank-app/reports/pinnacle-bank-redteam.md

# Confirm: (a) no duplicate live pings for endpoints the behavior run already
# checked; (b) confirmed-dead endpoints show as skipped/"Not Reached" rather
# than a false-negative pass; (c) golden-data-based suppression still fires.

# Second app for cross-target regressions:
uv run nuguard behavior --config tests/apps/shop-chat-agent/nuguard.yaml --format markdown -o tests/apps/shop-chat-agent/reports/shop-chat-behavior.md
uv run nuguard redteam --config tests/apps/shop-chat-agent/nuguard.yaml --format markdown -o tests/apps/shop-chat-agent/reports/shop-chat-redteam.md

# Phase 2 opt-in check (only if Chromium available): set
# browser_discover_endpoints: true for one app, re-run behavior, confirm new
# API_ENDPOINT nodes appear with Evidence(kind="browser_sniff"), and that a
# subsequent run without the flag does not re-crawl (cache discipline holds).
```

## Critical files

`nuguard/sbom/models.py` · `nuguard/common/endpoint_liveness.py` (new) ·
`nuguard/common/endpoint_scenario_gate.py` (new) ·
`nuguard/common/endpoint_preflight.py` · `nuguard/common/auto_sbom_enricher.py` ·
`nuguard/behavior/runner.py` · `nuguard/redteam/executor/orchestrator.py` ·
`nuguard/redteam/scenarios/generator.py` · `nuguard/behavior/scenarios.py` ·
`nuguard/common/browser_login/session.py` ·
`nuguard/common/browser_login/endpoint_sniffer.py` (new) ·
`nuguard/common/discovery.py` · `nuguard/common/capability_dedup_llm.py` (new) ·
`nuguard/sbom/core/gap_fill/dedup.py` · `nuguard/config.py`.

Per the user's request, once approved and implemented this plan's content
(context, phases, verification) should also be written to `./docs/validation-fix.md`
as the durable design record.
