# Discovery Validation Gaps — Fix Plan

**Run analysed**: `agentic-test-20260517T231009.log`  
**Date**: 2026-05-17  
**Status**: 6 gaps identified from live run evidence

---

## Evidence Summary

The run showed the following in the redteam pre-scan discovery block:

```
INFO  pre-scan discovery: name='' ids=['AA1234', 'BB5678', 'DL-401', 'UA-892'] turns=1
```

The behavior run produced **no** `behavior pre-scan discovery:` log line.

Despite IDs being discovered, behavior scenario messages contained fictional names and codes throughout:

| Scenario | Fictional data sent |
|---|---|
| `cancel_booking_refund_check` turn 1 | `ticket 6Q9X2Z`, `Miguel Alvarez` |
| `update_seat_after_map_review` turn 1 | `booking HJ7K2P`, `Priya Nair` |
| `check_delay_on_connection_flight` | `ABC123`, `Jordan Lee` |
| `reservation_lookup_for_seat_change` | `Maria Lopez`, `Alex Chen`, `Maya Patel` |
| Various redteam payloads | `Priya Shah`, `ZR7K91`, `Jordan Lee` |

Real discovered IDs (`AA1234`, `BB5678`, `DL-401`, `UA-892`) appeared only in **agent responses**, never in sent payloads.

---

## Gap 1 — Name extraction fails for the discovery response

**Root cause**: `extract_customer_name()` in `id_extractor.py` requires a label prefix (`Name:`, `Passenger:`, `Customer:`, `Account Holder:`). The agent returns booking summaries without these labels:

```
"AA1234 (JFK→LAX) and BB5678 (LAX→ORD), both cancelled."
```

`name=''` is the result — the profile is half-empty, so `{golden_name}` substitutes to empty string.

**Fix — `nuguard/redteam/executor/id_extractor.py`**:

Extend `_NAME_PATTERN` to also match:
1. Possessive patterns: `"[Name]'s booking"` → `r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)'s\s+(?:booking|reservation|account|flight)"`
2. Contextual patterns: `"booking for [Name]"`, `"reserved for [Name]"`, `"registered to [Name]"`, `"logged in as [Name]"`
3. Greeting patterns from the agent: `"Hello [Name],"`, `"Hi [Name],"`, `"Dear [Name],"`

Also add a **second discovery turn** with an explicit name request:

```python
_AIRLINE_MESSAGES = [
    "What upcoming flights and bookings do you have for my account? "
    "Please include all booking references, passenger names, ...",
    "Can you confirm the passenger name on my account and list my active bookings?",  # explicit name ask
    ...
]
```

---

## Gap 2 — Behavior pre-scan discovery never runs

**Root cause**: In `behavior/runner.py`, scenario generation (via LLM) happens in `_generate_scenarios()` which is called at the start of `run()`, **before** `_build_client()`. The discovery block was inserted after `_build_client()`, so:

1. Scenario messages are LLM-generated with fictional names before discovery runs.
2. There is no mechanism to substitute tokens into pre-generated behavior scenario messages.

Evidence: no `behavior pre-scan discovery:` line in the log.

**Fix — Two-part**:

**Part A — Move `_build_client()` before scenario generation** so the sequence becomes:
```
_build_client() → discovery → _generate_scenarios(profile=profile) → run_scenarios
```

This requires passing `profile` into `_generate_scenarios()` so the LLM that generates behavior scenario messages can be told to use the real IDs.

**Part B — Add token substitution to behavior message sending**  
In `_run_scenario()`, before `client.send(message, ...)`, apply token substitution using `self._pre_scan_profile`:

```python
def _substitute_behavior_tokens(message: str, profile: "DiscoveredProfile | None") -> str:
    if not profile or not message:
        return message
    primary_id = profile.ids[0] if profile.ids else ""
    message = message.replace("{golden_id}", primary_id)
    message = message.replace("{golden_name}", profile.customer_name or "the authenticated user")
    # ... etc
    return message
```

Also add `{golden_id}` tokens to behavior scenario message templates (see Gap 4).

---

## Gap 3 — `{golden_name}` substitutes to empty string when name is missing

**Root cause**: When `customer_name == ""`, `_substitute_golden_tokens()` replaces `{golden_name}` with `""`, producing malformed payloads like `"Please cancel booking for on flight BA205"`.

**Fix — `nuguard/redteam/executor/executor.py`**:

In `_substitute_golden_tokens()`, replace the fallback for `golden_name`:

```python
# current:
payload = payload.replace("{golden_name}", session.golden_name or "")

# fixed:
_fallback_name = session.golden_name or (
    self._pre_scan_profile.customer_name if self._pre_scan_profile else ""
) or "the authenticated user"
payload = payload.replace("{golden_name}", _fallback_name)
```

This ensures payloads like `"Cancel booking for the authenticated user on flight AA1234"` rather than `"Cancel booking for  on flight AA1234"`.

---

## Gap 4 — Behavior scenario LLM prompts do not request `{golden_id}` tokens

**Root cause**: The behavior scenario generator (`_generate_scenarios()`) in `runner.py` calls the LLM to produce test messages. Its prompt has no TOKEN USAGE section and no profile section, so the LLM invents `6Q9X2Z`, `HJ7K2P`, `ABC123`, etc.

**Fix — `nuguard/behavior/runner.py`**:

1. Add a `_BEHAVIOR_SCENARIO_TOKEN_USAGE` constant (mirror of `_SYSTEM_PROMPT`'s TOKEN USAGE section).
2. Pass `profile` into `_generate_scenarios()` and inject a profile section into the LLM prompt identical to `_build_profile_section()` used in the redteam prompt generator.
3. After Part A of Gap 2 is done, `_generate_scenarios(profile=profile)` can produce messages that contain `{golden_id}`, which are then substituted at send-time by the new `_substitute_behavior_tokens()` function.

---

## Gap 5 — Cross-scenario context contamination in behavior

**Root cause**: Behavior scenarios run concurrently against a **stateful** FastAPI endpoint. The agent maintains per-session conversation history via the auth token. When scenarios run concurrently (or sequentially in a shared session), the agent's in-memory context bleeds across scenarios.

Evidence from the log:
- Turn 5 of `cancel_booking_refund_check` references seat `8A` — data from `update_seat_after_map_review`.
- Turn 4 of `lost_baggage_claim_help` responds with cancellation instructions instead of baggage help.
- Agent responds to `check_delay_on_connection_flight` turn 5 with a cancellation request for `ABC123` (injected from a different scenario).

**Fix — `nuguard/behavior/runner.py`**:

Add `isolation_mode` support. For endpoints known to be stateful (session-based auth), each scenario should:
1. Re-authenticate (re-POST to `/login`) to get a fresh session token before the scenario starts, OR
2. Send an explicit `[RESET]` turn first if the target supports it, OR
3. Run behavior scenarios sequentially (concurrency=1) when session statefulness is detected.

A simpler immediate fix: add a config flag `behavior.isolate_scenarios: bool = true` (default `true`). When enabled, each scenario acquires its own auth session via `bootstrapper.refresh()`.

Add to `BehaviorRunner._run_one()`:
```python
if self._isolate_scenarios:
    isolated_client = await self._build_client()  # fresh auth token per scenario
    result = await self._run_scenario(scenario, isolated_client, policy_evaluator)
    await isolated_client.aclose()
else:
    result = await self._run_scenario(scenario, client, policy_evaluator)
```

---

## Gap 6 — Discovery log is not emitted for behavior run

**Root cause**: The behavior pre-scan discovery `try/except` block silences failures at DEBUG level and the `INFO` log line for success is in the inner `try`, meaning if discovery raises any exception, nothing is logged at INFO level.

Evidence: No behavior discovery INFO line in the log.

Also: the behavior `run()` method calls `_generate_scenarios()` before the discovery code, so even if discovery works, the scenarios are already generated. The profile only reaches `_generate_data_reactive_turns()` (a small subset of scenarios).

**Fix**:

1. Add an INFO-level log at the end of the try/except regardless of success:

```python
_log.info(
    "behavior pre-scan discovery: %s",
    f"name={_pre_scan_profile.customer_name!r} ids={_pre_scan_profile.ids} turns={_pre_scan_profile.turns_sent}"
    if not _pre_scan_profile.is_empty
    else "no profile extracted",
)
```

2. This gap is resolved entirely by Gap 2's Part A (move client build before scenario generation).

---

## Implementation Order

| Priority | Gap | File(s) | Complexity |
|---|---|---|---|
| P0 | Gap 1 — name extraction | `id_extractor.py`, `discovery.py` | Low |
| P0 | Gap 3 — `{golden_name}` fallback | `executor.py` | Low |
| P1 | Gap 2A — behavior discovery before gen | `runner.py` | Medium |
| P1 | Gap 2B — token sub in behavior messages | `runner.py` | Medium |
| P1 | Gap 4 — behavior LLM prompt tokens | `runner.py` | Medium |
| P2 | Gap 5 — scenario isolation | `runner.py`, `config.py` | Medium |
| P2 | Gap 6 — discovery logging | `runner.py` | Trivial |

Total estimated changes: ~5 files, ~120 lines net.

---

## Acceptance Criteria

After fixes, a re-run of the agentic test should show:

1. `behavior pre-scan discovery: name='Alice Johnson' ids=['AA1234', 'BB5678', ...] turns=1` in the log.
2. `pre-scan discovery: name='Alice Johnson' ids=[...] turns=1` in the redteam section (name no longer empty).
3. Behavior scenario turn 1 for `cancel_booking_refund_check` uses `AA1234` or `BB5678`, not `6Q9X2Z` or `ABC123`.
4. No fictional passenger names (`Priya Nair`, `Miguel Alvarez`, `Maya Patel`, etc.) in sent payloads.
5. When `customer_name` is empty, payloads use `"the authenticated user"` not empty string.
6. Each behavior scenario starts with a fresh auth context (no cross-scenario contamination).
