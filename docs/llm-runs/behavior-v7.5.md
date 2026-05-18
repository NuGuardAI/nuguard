# Behavior v7.5 — Single-focus turns, topic continuity, SBOM coverage

## Context

The behavior module run against Fintech-App (log: `agentic-test-20260424T065141.log`) revealed three systematic problems that reduce test quality and produce noisy, confusing scenarios:

1. **Compound messages** — `_adapt_message()` concatenated a reactive probe + the original scripted message into one turn (`f"{probe}\n\n{original}"`), so the agent received two competing requests. All 12 scenarios showed this pattern.
2. **Topic drift** — Probes fired regardless of scenario type (COMPONENT_COVERAGE and AGENT_COVERAGE scenarios suddenly pivoted to "what PII do you hold?"). Coverage turns also went through `_adapt_message`, further contaminating focused tool-testing flows. Hook/error probes had no "already fired" guard so they repeated every turn.
3. **SBOM coverage collapse** — Fintech App has 80+ tools and no AGENT nodes. `_tool_coverage_scenarios()` dumped all standalone tools into a single `__standalone__` bucket, then capped chains at 4 tools — producing only a handful of scenarios that missed most of the SBOM.

Subsidiary issues: the inter-turn confirmation reply was a static generic string; `_TURN_SUFFIX` ("list all agents and tools") was appended to ACTION turns mid-chain, not just the final turn.

---

## Changes implemented

### Change 1 — `_adapt_message()`: probe deferral + scenario-type filter

**File:** `nuguard/behavior/runner.py`

**New signature (4-tuple return):**
```python
def _adapt_message(
    message: str,
    context: "TurnContext | None",
    scenario: BehaviorScenario,
    pii_probed: bool = False,
    hook_probed: bool = False,
) -> tuple[str, bool, bool, str | None]:
    # returns (next_message, pii_probed, hook_probed, deferred_original)
```

**Logic:**
1. Early return for `AGENT_COVERAGE` and `COMPONENT_COVERAGE` — no probes on tool/agent-testing flows.
2. PII branch: return `(probe, True, hook_probed, message)` — probe fires now, original deferred to next turn.
3. Error/hook branches: same deferral pattern with `hook_probed=True`.
4. Passthrough: return `(message, pii_probed, hook_probed, None)`.

### Change 2 — `_run_scenario()`: handle 4-tuple + bypass coverage turn adaptation

**File:** `nuguard/behavior/runner.py`

- Added `hook_probed: bool = False` state variable alongside existing `pii_probed`.
- Scripted message call site unpacks 4-tuple and inserts deferred to `pending_messages[0]`.
- Coverage turn call site bypasses `_adapt_message()` entirely — sends clean focused questions.

### Change 3 — Inter-turn confirmation: use `generate_contextual_reply()`

**File:** `nuguard/behavior/runner.py`

Replaced static `_conf_reply(conf_type)` with `await generate_contextual_reply()` from `nuguard/common/credentials.py`. When an LLM is available, generates a specific contextual reply (e.g., "Yes, please transfer $500 from checking to savings."). Falls back to a type-specific hardcoded string when no LLM.

### Change 4 — Standalone tool sharding in `_tool_coverage_scenarios()`

**File:** `nuguard/behavior/scenarios.py`

Added `_is_standalone_group()` helper to match all `__standalone__*` group keys. Replaced single `tool_groups["__standalone__"] = standalone_tools` with tier-based sharding in chunks of ≤5:

```
tier_buckets = {"INFO": [...], "DECISION": [...], "ACTION": [...]}
keys: __standalone__INFO_0, __standalone__INFO_1, __standalone__ACTION_0, ...
```

Effect for Fintech App: 80 tools → 16 focused groups (8 INFO + 4 DECISION + 4 ACTION), each ≤5 turns, covering all 80 tools.

### Change 5 — `_TURN_SUFFIX` on final turn only

**File:** `nuguard/behavior/scenarios.py` `_deterministic_tool_chain()`

`_TURN_SUFFIX` ("list all agents and tools involved") now appended only to the last message in the chain, not to every ACTION mid-chain turn.

---

## Tests added

**`test_runner.py`:**
- `test_adapt_message_pii_probe_defers_original` — probe fires, original deferred, no compound message
- `test_adapt_message_skips_probe_for_component_coverage` — COMPONENT_COVERAGE bypasses all probes
- `test_adapt_message_skips_probe_for_agent_coverage` — AGENT_COVERAGE bypasses all probes
- `test_adapt_message_hook_probed_only_once` — hook fires once, suppressed on second call
- `test_adapt_message_no_context_passthrough` — None context returns message unchanged

**`test_scenarios.py`:**
- `test_deterministic_tool_chain_suffix_only_on_last_turn` — suffix not in first message, appended to last
- `test_deterministic_tool_chain_single_turn_has_suffix` — single-tool chain still carries suffix
- `test_is_standalone_group_exact` / `_tier_prefix` / `_false_for_real_agent` — helper correctness
- `test_standalone_tool_sharding` — 12 tools shard into ≥3 groups, each ≤5 tools

---

## Verification

```bash
uv run ruff check nuguard/behavior/   # 0 errors
uv run mypy nuguard/behavior/          # Success: no issues found in 28 source files
uv run pytest nuguard/behavior/tests/ -v  # 188 passed
```
