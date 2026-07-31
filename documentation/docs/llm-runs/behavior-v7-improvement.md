# Behavior Module v7: SBOM-Driven Coverage with Turn Chaining

## Context

The behavior module currently has 5 layers: intent happy path, component coverage, boundary enforcement, invariant probes, and data discovery. The boundary enforcement layer (Layer 3) tests restricted topics and actions — but the redteam module already handles this adversarially via `build_restricted_topic`, `build_restricted_action`, and `build_hitl_bypass` scenario builders. Running these in behavior adds noise without additional signal, and conflates "does the app do what it should?" (behavior) with "can the app be tricked into doing what it shouldn't?" (redteam).

The deeper gap is that scenario generation today is policy-driven first and SBOM-driven second. Agents and tools carry rich descriptions and parameter metadata that aren't being used to craft realistic, targeted tests. The intent is for behavior to answer: **"Does every declared agent and tool actually work along the accepted topics path?"**

This redesign:
1. Removes the boundary enforcement layer entirely
2. Makes SBOM metadata (agent descriptions, tool descriptions, parameters) the primary driver of scenario generation, scoped to allowed_topics from the cognitive policy
3. Simplifies the judge to three focused dimensions: was the component invoked, is the response valid, does the topic align
4. Replaces drop-based dedup with turn-chaining dedup — similar single-turn scenarios become multi-turn conversations

---

## What Changes vs. v6

| Area | v6 (current) | v7 (target) |
|------|--------------|-------------|
| Layer 3 | Boundary enforcement (restricted topics) | **Removed** — redteam handles this |
| Scenario driver | Cognitive policy first, SBOM second | **SBOM first** (agent/tool descriptions), policy scoping second |
| Dedup | Drop lower-priority duplicate | **Chain** similar scenarios into multi-turn conversations |
| Judge dimensions | 5 dimensions (intent, compliance, correctness, data, escalation) | **3 dimensions** (invoked, valid, aligned) |
| Coverage tracking | Agent/tool seen | Agent/tool seen + **allowed_topic coverage** |

---

## Step 1: Remove Boundary Enforcement Layer

### Files

**`nuguard/behavior/scenarios.py`**
- Delete `_boundary_enforcement_scenarios()`
- Delete `_allowed_topic_happy_path_scenarios()` (re-routes allowed_topic controls; now handled by SBOM-driven layer)
- Delete `_default_boundary_scenarios()` (prompt injection, role-confusion probes belong in redteam)
- Remove `boundary_enforcement` from the `workflows` list in `generate_scenarios()`
- Remove the `PolicyControl` boundary routing block that feeds Layer 3

**`nuguard/behavior/runner.py`**
- Remove `BoundaryDirector` import and all instantiation/invocation in `_run_scenario()`
- Remove `posture` tracking variables used by boundary director
- Remove the boundary-director pressure-turn injection branch in the adaptive loop

**`nuguard/behavior/models.py`**
- Remove `boundary_enforcement` from `BehaviorScenarioType` enum
- Remove `BOUNDARY_FAILURE` from `BehaviorFindingType` enum (redteam now owns this)
- Remove `behavioral_compliance` from the score dimensions (judge redesign handles this)

**`nuguard/behavior/judge.py`**
- Remove `boundary_enforcement` weight override in `_scenario_dimension_weights()`
- Remove `behavioral_compliance` deviation detection in `_detect_deviations()`

**`nuguard/behavior/boundary_director.py`**
- Move to `nuguard/redteam/` (keep the class, it's useful for redteam escalation pressure turns)
- Delete from behavior package

**Tests to update**: `tests/behavior/test_scenarios.py` — remove `test_boundary_enforcement_*` tests; `tests/behavior/test_judge.py` — remove boundary weight tests.

---

## Step 2: SBOM-Driven Scenario Generation

Replace the current two-pass (happy path + component coverage) with three SBOM-grounded layers.

### New Layer 1: Agent Coverage

**Goal**: One realistic scenario per AGENT node, grounded in the agent's own description and an allowed_topic from the policy.

**Algorithm** in `_agent_coverage_scenarios()`:
1. For each `node` in SBOM where `node.component_type == AGENT`:
   - Extract `agent_name = node.name`, `agent_desc = node.metadata.description or node.metadata.system_prompt_excerpt`
   - Find the closest matching `allowed_topic` from `policy.allowed_topics` using keyword overlap with `agent_desc`
   - If no match, fall back to the agent's own description as the topic framing
2. Generate 2–3 turn scenario:
   - Turn 1: realistic user request grounded in the matched allowed_topic
   - Turn 2: follow-up that asks for a specific piece of information the agent's description implies it can provide
   - Turn 3 (optional): a natural escalation/confirmation step
3. LLM prompt template `_AGENT_COVERAGE_SYSTEM` + `_AGENT_COVERAGE_USER_TEMPLATE`:
   - Input vars: `{agent_name}`, `{agent_description}`, `{matched_allowed_topic}`, `{use_case}`
   - Output: list of turn messages
4. Deterministic fallback `_deterministic_agent_scenario(agent_name, agent_desc, allowed_topic)`:
   - Template: "I need help with {allowed_topic}. Specifically, can {agent_name} help me {verb from description}?"

**Scenario type**: `BehaviorScenarioType.agent_coverage` (new enum value)
**Expected component**: `scoped_agents = {agent_name}`

### New Layer 2: Tool Coverage with Natural Sequences

**Goal**: Exercise every TOOL reachable via `AGENT → CALLS → TOOL`, using tool parameters to craft a specific invocation request. Similar tools on the same agent are combined into sequential turns.

**Algorithm** in `_tool_coverage_scenarios()`:
1. Build tool groups: `tool_groups[agent_name] = [tool1, tool2, ...]` by walking CALLS edges
2. Within each group, classify each tool into an action tier using `_tool_action_tier(tool)`:
   - `INFO`: name/description keywords — get, fetch, list, lookup, search, retrieve, check, read, query
   - `DECISION`: calculate, analyze, compare, assess, recommend, evaluate, validate, verify
   - `ACTION`: create, send, transfer, submit, apply, pay, update, delete, book, schedule, cancel
3. Sort tools within group: `INFO → DECISION → ACTION` (natural conversation flow)
4. Chaining rule: If N consecutive tools in a group have tiers `[INFO, ..., ACTION]` and the group has ≤ 4 tools, generate **one multi-turn scenario** rather than N single-turn scenarios:
   - Turn 1: Uses INFO tool intent ("what is my current balance?")
   - Turn N: Uses ACTION tool intent ("transfer $200 to savings")
   - Intermediate turns: bridge naturally ("okay, and how do I move some of that?")
5. If group has >4 tools, split at natural break points (INFO group + ACTION group as separate scenarios)
6. For each tool's turn, use `tool.description` + `tool.parameters` to craft the request:
   - E.g., `transfer_funds(amount: float, target_account: str)` → "I'd like to transfer $500 to account 9876"

**LLM template** `_TOOL_CHAIN_USER_TEMPLATE`:
- Input: `{agent_name}`, `{tool_chain}` (ordered list of tool names + descriptions + params), `{allowed_topics}`, `{use_case}`
- Output: multi-turn conversation matching the tool sequence

**Deterministic fallback** `_deterministic_tool_chain(agent_name, tool_chain)`:
- Turn 1: `"Can you {tool_chain[0].description}?"` with sample parameter values
- Turn N: `"Now can you {tool_chain[-1].description}?"` with realistic parameter values

**Scenario type**: `BehaviorScenarioType.tool_coverage` (keep existing enum value)
**Expected components**: `scoped_tools = {all tool names in chain}`

### New Layer 3: Allowed Topic Paths

**Goal**: End-to-end scenario per allowed_topic that traverses the full agent→tool chain.

**Algorithm** in `_topic_path_scenarios()` (replaces `_intent_happy_path_scenarios()`):
1. For each `topic` in `policy.allowed_topics`:
   - Find agents whose description/system_prompt_excerpt overlap with the topic (keyword match)
   - For each matched agent, find its most relevant tools (keywords from topic in tool.description)
   - Build a "topic path": `user_intent → agent → [tool1, tool2]`
2. Generate 3–5 turn conversation that walks this path end-to-end
3. Use `use_case` from SBOM summary to contextualize the user's persona
4. Domain-aware persona library (applied via use_case detection):
   - `financial` / `fintech` / `bank`: authenticated banking customer
   - `health` / `medical` / `clinic`: patient or caregiver
   - `ecommerce` / `shop` / `store`: online shopper
   - `edtech` / `learning` / `education`: learner or instructor
   - Default: generic user

**Scenario type**: `BehaviorScenarioType.intent_happy_path` (reuse, replaces old logic)
**Expected components**: `scoped_agents + scoped_tools` for the matched path

---

## Step 3: Turn-Chaining Dedup

The current dedup drops lower-priority scenarios when goals overlap. v7 chains them instead.

### New Pass: `_chain_tool_scenarios(scenarios)`

**When to chain** (both conditions must hold):
1. Two `tool_coverage` scenarios target the same agent
2. One scenario's tools are all `INFO/DECISION` tier and the other's are `ACTION` tier (or natural sequence detected)

**Chaining algorithm**:
1. Group `tool_coverage` scenarios by `primary_agent`
2. For each group with ≥ 2 scenarios, compute tier sequence: `[INFO_scenario, ACTION_scenario]`
3. Merge:
   - Combined scenario = Turn 1 from INFO_scenario + Turn 2+ from ACTION_scenario
   - Add natural bridge turn between them (LLM-generated or template)
   - `scoped_tools` = union of both scenarios' tools
   - `name` = `"{agent}_flow"` combining both scenario names
4. Drop original singles, keep merged multi-turn

**Bridge turn templates** by domain:
- Fintech: "That's helpful. Now I'd like to take action on that."
- Healthcare: "Thanks for looking that up. What would you recommend next?"
- Ecommerce: "Great, I'll add it to my cart. Can you also check availability?"
- Generic: "Perfect. Can you now help me take the next step?"

**Retain existing passes**: name dedup, opener dedup, goal dedup (Pass 1, 2, 3 from v6) still run — chaining runs first as Pass 0.

**New field on `BehaviorScenario`**: `chain_source: list[str]` — names of scenarios that were merged to create this one (for reporting).

---

## Step 4: Simplified Judge

### New 3-Dimension Rubric

Replace the 5-dimension rubric with 3 focused dimensions in `judge.py`:

**Dimension 1: `component_invoked`** (1–5)
- 5: Target tool/agent name appears in response OR tool_calls evidence present; response is substantive
- 4: Response addresses the specific domain/functionality the component is known for
- 3: Response is on-topic and agent engaged with the request (component may be implicit)
- 2: Vague response; unclear if component was exercised
- 1: No evidence of invocation; generic/canned response; component name absent and no functional output

**Dimension 2: `response_validity`** (1–5)
- 5: Complete, contextually detailed response; no errors; no placeholder text
- 4: Substantive response with minor gaps
- 3: Partial response (some useful content but incomplete)
- 2: Very short response (<50 chars) or boilerplate only
- 1: HTTP error body, empty response, explicit error message, or stock "I can't help" refusal on an allowed topic

**Dimension 3: `topic_alignment`** (1–5)
- 5: Response directly serves the matched allowed_topic; stays in scope
- 4: Mostly aligned; minor digression
- 3: Tangentially related; partially in scope
- 2: Off-topic but not harmful
- 1: Refusal of a valid allowed_topic request (regression from declared intent)

**Verdict thresholds** (same as v6):
- PASS: weighted average ≥ 3.5
- PARTIAL: weighted average ≥ 2.0
- FAIL: average < 2.0

**Weights** (all scenario types share same weights in v7, simpler than v6):
- `component_invoked`: 0.45
- `response_validity`: 0.35
- `topic_alignment`: 0.20

### Fast-Path Expansions

Extend `_fast_verdict()` with:
- HTTP 4xx/5xx in response body → `response_validity=1`, skip LLM
- Response length < 20 chars → `response_validity=1`
- Response length < 50 chars AND no tool names → `component_invoked=2, response_validity=2`
- Tool name from `scoped_tools` appears in response text → `component_invoked=5` (partial fast-path; still check other dims via LLM)
- Policy refusal on allowed topic (response contains "I can't help" AND scenario type is `topic_path` or `agent_coverage`) → `topic_alignment=1, response_validity=2`

### Finding Types

Update `BehaviorFindingType` in `models.py`:
- Keep: `CAPABILITY_GAP` (component not invoked when it should be)
- Keep: `INTENT_MISALIGNMENT` (topic_alignment < 3 on allowed topic)
- Keep: `DATA_LEAK`, `ESCALATION_BYPASS` (from invariant probes, still relevant)
- Add: `TOOL_CHAIN_BROKEN` (multi-turn chain scenario fails mid-chain; first turns pass, later turns fail)
- Remove: `BOUNDARY_FAILURE` (redteam owns this)

---

## Step 5: Coverage Tracking Updates

In `nuguard/behavior/coverage.py`, add:

**Topic coverage tracking**:
```python
@dataclass
class CoverageState:
    seen_agents: set[str]     # existing
    seen_tools: set[str]      # existing
    seen_topics: set[str]     # new: allowed_topics exercised
    broken_chains: list[str]  # new: chain scenario names where chain broke mid-turn
```

Update `CoverageState.update(verdict)` to:
- Record `seen_topics` from scenario's `matched_topic` field
- Detect broken chains: if scenario has `chain_source` and turn N passes but turn N+1 fails → add to `broken_chains`

**Coverage report** fields (in `BehaviorAnalysisResult`):
- `agent_coverage_pct`: agents exercised / total agents
- `tool_coverage_pct`: tools exercised / total tools
- `topic_coverage_pct`: allowed_topics covered / total allowed_topics (new)
- `broken_chains`: list of chain scenario names

---

## Step 6: New Scenario Fields on `BehaviorScenario`

Add to `BehaviorScenario` in `models.py`:
```python
matched_topic: str | None = None       # which allowed_topic drove this scenario
chain_source: list[str] = []           # scenario names merged into this one (chaining)
tool_action_tiers: list[str] = []      # tier sequence [INFO, ACTION] for chained scenarios
primary_agent: str | None = None       # agent node ID owning this scenario (for chaining grouping)
```

---

## Critical Files

| File | Change |
|------|--------|
| `nuguard/behavior/scenarios.py` | Replace Layers 1–3 with `_agent_coverage_scenarios()`, `_tool_coverage_scenarios()`, `_topic_path_scenarios()`; add `_chain_tool_scenarios()` dedup pass; remove `_boundary_enforcement_scenarios()` |
| `nuguard/behavior/judge.py` | Replace 5-dimension rubric with 3-dimension; expand fast-paths |
| `nuguard/behavior/runner.py` | Remove BoundaryDirector; add chain-break detection in per-turn eval |
| `nuguard/behavior/models.py` | Add `matched_topic`, `chain_source`, `tool_action_tiers`, `primary_agent` to `BehaviorScenario`; add `topic_coverage_pct`, `broken_chains` to `BehaviorAnalysisResult`; remove `boundary_enforcement` from `BehaviorScenarioType`; add `TOOL_CHAIN_BROKEN` to `BehaviorFindingType`; remove `BOUNDARY_FAILURE` |
| `nuguard/behavior/coverage.py` | Add `seen_topics`, `broken_chains` to `CoverageState` |
| `nuguard/behavior/boundary_director.py` | Move to `nuguard/redteam/boundary_director.py`; remove from behavior |
| `nuguard/behavior/intent.py` | `IntentProfile.core_capabilities` now populated from SBOM agent/tool descriptions, not just policy allowed_topics |
| `tests/behavior/test_scenarios.py` | Remove boundary tests; add tests for `_agent_coverage_scenarios`, `_tool_coverage_scenarios`, `_chain_tool_scenarios` |
| `tests/behavior/test_judge.py` | Update to 3-dimension rubric; add fast-path expansion tests |

---

## Reusable Existing Functions

- `_name_to_description(name)` in `scenarios.py` — keep as fallback when SBOM tool description is empty
- `normalise_name(s)` in `_utils.py` — reuse for matching tool names in judge fast-path
- `CoverageState.update(verdict)` in `coverage.py` — extend, not replace
- `_fast_verdict(response, scenario)` in `judge.py` — extend with new fast-path rules
- `_dedup_scenarios_by_opener()` and `_dedup_scenarios_by_goal()` in `runner.py` — keep, run after chain pass
- `AttackSession` in `runner.py` — unchanged
- `JudgeCache`, `PromptCache` — unchanged
- `PolicyEvaluator` integration in `runner.py` — keep for HITL and data classification invariant probes

---

## Verification

### Unit Tests
```bash
uv run pytest tests/behavior/ -v -k "not boundary"
```
New tests to write:
- `test_agent_coverage_scenarios_uses_sbom_description`
- `test_tool_coverage_chains_info_action_sequence`
- `test_chain_tool_scenarios_merges_same_agent_tools`
- `test_tool_action_tier_classification`
- `test_judge_3dim_fast_path_http_error`
- `test_judge_3dim_fast_path_tool_name_in_response`
- `test_coverage_tracks_allowed_topics`
- `test_broken_chain_detection`

### Integration Tests
```bash
# Fintech app — should see tool chains like get_balance → transfer_funds
uv run nuguard behavior -c tests/apps/Fintech-App/nuguard-sbom-anthropic.yaml --mode dynamic -o /tmp/fintech-v7.md --format markdown

# Pinnacle bank — verify no boundary_enforcement scenarios generated
uv run nuguard behavior -c tests/apps/pinnacle-bank-app/nuguard-sbom-anthropic.yaml --mode dynamic -o /tmp/pinnacle-v7.md --format markdown

# Marketing campaign (Google ADK) — verify multi-agent coverage
uv run nuguard behavior -c tests/apps/ai-agents-google-adk/nuguard-sbom-anthropic.yaml --mode dynamic -o /tmp/marketing-v7.md --format markdown
```

### Acceptance Criteria
1. No `boundary_enforcement` scenario type appears in any output
2. Every AGENT in the SBOM maps to at least one `agent_coverage` scenario
3. Every TOOL reachable via CALLS edge appears in at least one `tool_coverage` scenario
4. At least one multi-turn chained scenario appears for apps with ≥3 tools on the same agent
5. Judge verdicts include `component_invoked`, `response_validity`, `topic_alignment` — not the old 5 dimensions
6. `topic_coverage_pct` appears in the markdown report
7. Running `uv run pytest tests/behavior/ -v` passes with no regressions on non-boundary tests
