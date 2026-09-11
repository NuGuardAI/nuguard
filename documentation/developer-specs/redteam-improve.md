# Redteam effectiveness audit — findings from agentic-test-20260812T223217.log

## Context

The user asked for a redteamer's assessment of `tests/apps/pinnacle-bank-app/reports/agentic-test-20260812T223217.log`
(a live scan against the Pinnacle Bank agentic app on Azure): what's missing from NuGuard's
redteam technique catalog, and how execution/planning/escalation could be made more effective.
This log turned out to run on the **v2 engine** (`nuguard/redteam/v2/`, self-labeled "scaffold" in
its own output) — a different codebase from the v1 judge/severity work done in the prior session
(`nuguard/redteam/` proper). All findings below are grounded in this specific log plus direct
code investigation (file:line cited), not speculation.

**Bottom line up front:** the scenario *catalog* is broad and mostly not the problem — resource
exhaustion, tool-arg injection, multi-agent trust abuse, output handling (XSS/SSRF/SQLi-via-output),
and MCP toxic flow all already exist in v1 and are wired into v2. The real effectiveness gap is a
**reactive-intelligence gap**: the pipeline fires a static, pre-planned catalog once and grades each
shot in isolation. It doesn't (a) reliably get its own best evidence into the final report, (b) chain
a disclosure into a follow-up exploit attempt, or (c) learn within a run what already failed against
this specific target. One concrete consequence, confirmed in this run: **the single most severe,
unambiguous finding in the transcript — the agent, jailbroken via a "Developer Override Mode"
skeleton-key + WARNING-prefix trick, printing its full internal tool/function catalog annotated
with "No ownership checks are enforced" / "No explicit authorization checks are enforced" on
account-listing and account-status-update tools (log lines 867-906, repeated at 1019-1027) — never
made it into the final report.** The report's only finding is a generic PII-pattern match on "email"
from an unrelated memory-poisoning scenario. A tool-schema leak that hands an attacker a
verified map of unauthenticated admin actions is arguably more actionable than that, and it was
silently dropped.

---

## Findings (grounded in this log + code)

### 1. [CORRECTNESS, v2] The objective-level judge never sees the per-step judge's verdict — this is why the tool-schema leak was dropped

- Per-step judge (`nuguard/redteam/executor/executor.py:1045`) marked this scenario
  `succeeded=True` twice, with clear evidence ("acknowledged 'Developer Override Mode' and listed
  sensitive system functions ... without authorization checks" — log line ~762-765).
- The objective-level judge that actually builds `Finding`s ("rt2-judge",
  `nuguard/redteam/v2/evaluation/semantic_judge.py`) is fed by `EvaluationInput.from_outcome()`
  (`nuguard/redteam/v2/evaluation/verdict.py:54-101`), which **rebuilds evidence from raw signals
  only** (response text, tool_calls, canary_hits, policy_violations, egress_hits) and **never reads
  `outcome.succeeded`/`outcome.critical`**. `llm_eval_confidence` is written into the struct but
  **never read anywhere downstream** — dead data.
- With the per-step verdict discarded, confirmation depends entirely on either (a) the deterministic
  layer (`nuguard/redteam/v2/evaluation/deterministic.py:15-28,50-104`) — canary/PII/secret regexes
  only, **no detector class exists for "tool/function schema disclosure"** — or (b) a *fresh*,
  independent 3-judge LLM quorum vote (`pipeline.py:47-59`) re-reading the concatenated transcript
  of the whole objective, **truncated to 4000 chars** (`semantic_judge.py`, via `inp.response_text[:4000]`).
  On a multi-step chain, the actual leak can be pushed past that truncation boundary, and even in
  view, it's a fully independent probabilistic re-judgment with no guaranteed agreement with the
  original evaluator.
- **This is a structural gap, not a config issue.** Fix requires forwarding the per-step
  `succeeded`/evidence into the objective-level evaluation (a trusted-verdict fast path,
  analogous to the evidence-bundle `resolve_deterministic()` pattern already built for v1's judge
  last session) and/or adding a deterministic detector class for tool/function-schema disclosure.

### 2. [EFFICIENCY, v2] A boolean "critical seen" latch wholesale-skips 4 entire attack categories, not a budget

- `PhasedScheduler.run` (`nuguard/redteam/v2/scheduler/scheduler.py:117-215`) sets `critical_seen=True`
  the moment any objective confirms critical (here: the memory-poisoning PII hit), then skips
  **every** objective in `HIGH_IMPACT_PHASES = {AGENTIC_KILL_CHAIN(8), HIGH_IMPACT_DRY_RUN(9)}`
  (`scheduler/phases.py:29-31`) with **zero HTTP calls** — logged as `skipped_early_stop`.
- In this run that latch fired during phase 6 and erased all 12 objectives in phases 8-9:
  **Output Handling, Supply Chain, Tool Misuse Arg Injection, Resource Exhaustion** — all four are
  real, wired builders (not stubs — see finding 6), simply never invoked.
- This inverts what a human redteamer would do: finding one critical issue is a reason to dig
  *harder* into adjacent categories (what else does this authorization gap touch?), not a reason to
  stop testing unrelated ones. It also **silently degrades the report's honesty**: the coverage
  table shows these as plain "Not Tested / 0%" with no indication that testing was deliberately
  aborted because of an early critical hit elsewhere, which could easily be misread by a reviewer as
  "these categories weren't prioritized" rather than "we found something so severe we stopped
  looking at everything else."

### 3. [EFFICIENCY, v2/shared] The retry/circuit-breaker path fully serializes the whole scan, and burned ~25-27 of the ~36 minute run

- `redteam_max_concurrent_requests` defaults to **1** ("fully serialised", `nuguard/config.py:1144-1151`)
  and this scan's config (`nuguard-azure.yaml`) never overrides it — even though it sets
  `concurrency: 3` for the *scheduler*. The scheduler's concurrency setting is cosmetic if the
  underlying `TargetAppClient`'s single shared semaphore (`client.py:157-166`, wraps the whole
  retry loop at `client.py:345-363`) is left at 1: every HTTP call in the entire scan funnels through
  one lock.
- Combined with the transient-error handling (`client.py:365-477`): a retriable transport error
  triggers a 60s sleep *while holding that global semaphore* ("waiting 60s ... semaphore held; no
  other chains will send during this window"), then on retry, if the response still looks off,
  it's unconditionally reclassified as **"content-filter block (backend is healthy)"** rather than
  a genuine transient/rate-limit failure — and the step's evaluation is skipped entirely
  ("App transient error detected — skipping eval").
- Confirmed 25 occurrences of this pair in the log, ~61-73s apart, ≈25-27 minutes of the ~36-minute
  total run spent purely in this blocking sleep. This is a real, independent bottleneck from
  finding 2 (they coincided in this run but are different bugs) — and the "must be a content filter,
  not a real error" heuristic risks silently misclassifying genuine target instability/rate-limiting
  as an intentional guardrail, discarding real signal in the process.

### 4. [REPORTING BUG, v2] Every scenario in the markdown report shows "Turns: 0", masking manual review

- `_build_scenario_records()` (`nuguard/redteam/v2/orchestrator.py:678-717`) hardcodes
  `"steps": []` for every scenario (line 710) even though the real per-turn transcript exists on
  `ObjectiveOutcome.step_results` and is used for console printing
  (`nuguard/redteam/v2/execution/runner.py:428-477`). The Scenario Coverage table's turn counts are
  fine (fed separately from `step_count`), but the per-scenario "Scenario Details" transcript
  section (`nuguard/output/validation_report.py:106-153,243-264`) always renders 0 turns.
- This matters directly for finding 1: a human reviewer trying to manually catch near-misses that
  the automated judge dropped has no transcript to look at in the delivered report — they'd have to
  go back to the raw run log, which most users won't have/keep.

### 5. [MISSING CAPABILITY, v1+v2] Nothing chains a tool-schema disclosure into an actual exploitation attempt

- Confirmed absent in both engines. The only "confirm → escalate" path that exists is
  `codegen-esc` (`nuguard/redteam/executor/orchestrator.py:1436-1484`), gated purely on detecting a
  fenced code block in the response (`codegen_escalation.py:53`) — unrelated to a tool-schema
  disclosure event. `chain_assembler.py` is a static topological sort, not a reactive planner.
  BOLA/BFLA builders (`authorization.py`) are pre-planned static probes, not triggered by what a
  prior scenario revealed.
- Concretely: this run's agent admitted, in plain text, that `list_all_accounts` and
  `update_account_status` have no ownership/authorization checks. A real redteamer's next move is
  obvious — actually ask the (jailbroken) agent to invoke `list_all_accounts` or `get_account` on a
  different customer's ID and see if it complies, turning a disclosure into a proven IDOR. NuGuard
  has no mechanism to generate and fire that follow-up automatically.

### 6. [NOT A BUG] Resource Exhaustion / Supply Chain / Tool Misuse Arg Injection / Output Handling are real, wired builders

Confirmed each has a genuine v1 builder wired into v2's catalog (`business_logic.py:build_resource_exhaustion`,
`supply_chain_attacks.py:build_artifact_integrity_probe`, `api_schema_attacks.py:build_param_confusion_injection`,
`output_handling.py:build_output_xss`) — they were skipped by finding 2's early-stop gate, not because
they're unimplemented. **Multimodal Injection is a genuine stub** though: its KB record has no
`builder_key` at all (explicit comment: "Strategy-only for now"), so `ObjectiveRunner` short-circuits
with zero HTTP calls regardless of budget (`runner.py:161-167`) — yet the report shows it as "100%
coverage," which overstates what was actually tested.

### 7. [WEAK ADAPTATION, v1] Adaptive mutation has no memory of what already failed against this target

- `AdaptiveMutationStrategy._pick_strategy()` (`adaptive_mutation.py:207-301`) is a pure function of
  `(failure_type, attempt) → strategy`, with **zero state** — no target ID, no run history. Every
  new chain restarts the same fixed rotation (skeleton-key → encoding evasion → many-shot →
  persona/authority) from scratch, regardless of what every earlier chain in the same run already
  learned about this target's defenses.
- Root cause of the observed exact-repeat payloads: `build_encoding_evasion()`/
  `build_multi_language_bypass()` (`evasion.py`) have a **hardcoded default** `attack_goal` string
  ("provide instructions for unauthorized access to systems"), and the catalog call site
  (`catalog/builders.py:672-676`) never passes a dynamic one — so every matching agent node gets the
  byte-identical ROT-13/leet/Morse payload. `SimilarityMissTracker` (default `miss_threshold=4`)
  eventually retires a cluster after ~4 misses within one batch (matches the observed repeat count),
  but a fresh batch (e.g. the escalation batch) gets its own tracker and can resend the same
  payloads again from zero.

---

## Candidate fixes (unprioritized — see questions below)

| # | Fix | Addresses | Engine |
|---|---|---|---|
| A | Forward per-step `succeeded`/evidence into `EvaluationInput`; add a trusted fast-path so a high-confidence per-step HIT doesn't depend purely on a fresh, truncated re-vote | Finding 1 (dropped finding) | v2 |
| B | Add a deterministic detector class for tool/function-schema disclosure (esp. "no auth check" admissions) | Finding 1 | v2 (and v1's evidence-bundle `signals.py` for symmetry) |
| C | Make the early-stop gate additive not exclusionary: on a critical hit, prioritize *related* phases for deeper follow-up instead of skipping unrelated ones; at minimum, render skipped-due-to-early-stop distinctly from "not prioritized" in the report | Finding 2 | v2 |
| D | Surface/fix the concurrency footgun: warn (or fail fast) when `concurrency:` is set >1 in scan config but `redteam_max_concurrent_requests` is left at the serializing default of 1; reconsider always-reclassify-as-content-filter retry heuristic | Finding 3 | v1 client, shared |
| E | Populate `"steps"` in `_build_scenario_records()` from real `ObjectiveOutcome.step_results` | Finding 4 | v2 |
| F | Reactive escalation: when a response discloses a tool/action lacking authorization checks, auto-generate a targeted follow-up scenario invoking that tool against a different identity/account to attempt a real IDOR | Finding 5 | v1+v2, biggest lift |
| G | Give adaptive mutation a per-run "target defense profile" (which strategies/payload families have already failed) shared across scenarios, not just within-batch similarity clustering | Finding 7 | v1 |
| H | Dynamic-ize the hardcoded `attack_goal` default in `evasion.py` builders so encoding-evasion payloads target this SBOM's actual restricted topics instead of a generic phrase repeated everywhere | Finding 7 | v1 |
| I | Either implement multimodal injection in v2 or have the report visibly flag it "not implemented" rather than blending into "100% coverage" | Finding 6 | v2 |

---

## Open questions before any implementation

(see AskUserQuestion — engine priority, whether to proceed to implementation now vs. keep this as
an analysis deliverable, and which fixes matter most.)
