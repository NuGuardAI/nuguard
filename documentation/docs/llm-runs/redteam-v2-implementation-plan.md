# Red-Team Engine v2 — Implementation Plan

> Source design: `docs/llm-runs/redteam-v2-design.md` (mirror of
> `docs/llm-runs/redteam-knowledge-source-guide.md`). This plan turns that design into a
> phased, buildable implementation for a more offensively effective red-team engine that
> produces fewer false positives.

## Context

NuGuard's red-team engine (`nuguard/redteam/`) is the most complex, actively-developed
package. v1 already has strong building blocks: a 125-entry stable-ID scenario catalog
(`catalog/registry.py`, `ScenarioSpec`, `taxonomy.py`, capability detection, selector,
coverage), static + guided executors, an LLM response evaluator with pre-LLM heuristics,
a policy engine, canary scanning, and Markdown/JSON/SARIF reporting.

The v2 design specifies an engine driven by three inputs — a detailed AI-SBOM, a Cognitive
Policy, and a **curated, versioned technique knowledge base** grounded in
standards/academic/vendor sources. It calls for:

1. A versioned **technique knowledge base** (metadata records, not raw payloads).
2. A per-target **catalog** derived from SBOM+Policy and **cached** for regression reuse.
3. An **intelligent phased scheduler** (phases 0–10: setup → recon → boundary → instruction
   conflict → multi-turn → indirect → data/identity → agentic kill-chains → high-impact dry
   run → cleanup), running attacker-like stages with strict safety ordering.
4. A **layered evaluation** pipeline (deterministic detectors → semantic multi-judge →
   side-effect verifier → coverage scorer → transferability scorer → judge-robustness check)
   that is the core of false-positive reduction.
5. **Findings + coverage gaps + regression export**, with transferability as a severity
   multiplier.

**Decisions confirmed with the user:**
- **Separate v2 package** under `nuguard/redteam/v2/`, reusing v1 shared infrastructure; v1
  stays untouched until v2 is proven. New CLI path `nuguard redteam --engine v2`.
- **Full phased plan**, all design sections (exotic families as later phases).
- **In-band canary detection only** for this iteration (reuse `CanaryScanner` + tool-trace +
  artifact scanning); an out-of-band egress-trap server is a later phase.
- **KB authoring = schema + loader + seed set**, mapped to the design's technique families
  and primary sources; expand iteratively.

Intended outcome: a self-contained v2 engine that selects techniques intelligently from a
durable KB, executes them in safe attacker-like phases, and gates findings through a layered
evaluator so reported issues are high-confidence and traceable to standards.

---

## Goals & Success Criteria

- **Effectiveness:** KB-driven, SBOM/policy-targeted technique selection; adaptive multi-turn
  + kill-chain composition; transferability retargeting of successful objectives.
- **Fewer false positives:** deterministic-first evaluation, programmatic judges for
  tool-call args / authz mismatch / canary egress / DB writes, multi-judge semantic voting
  for ambiguous text outcomes, side-effect verification ("did the action actually happen?"),
  and a judge-robustness/specification-gaming guard.
- **Traceability:** every technique and finding maps to source refs (OWASP/MITRE ATLAS/NIST/…)
  and to validated defensive controls.
- **Regression:** per-target catalog cached and reused; confirmed findings exported as
  replayable regression tests.
- **Safety:** synthetic tenants/canaries, fresh identities, phase ordering that never runs
  high-impact before baseline/data/indirect phases, dry-run-only destructive actions.

---

## Architecture Overview

New package, parallel to v1, maximal reuse of shared infra:

```
nuguard/redteam/v2/
├── __init__.py
├── knowledge/                 # Phase 1 — versioned technique knowledge base
│   ├── schema.py              # TechniqueRecord pydantic model (design's KB schema)
│   ├── loader.py              # importlib.resources loader + version pinning
│   └── data/                  # bundled YAML technique records (seed set)
│       └── techniques/*.yaml
├── surface/                   # Phase 2 — attack-surface graph + recon
│   ├── attack_surface.py      # SBOM → normalized attack-surface graph (wraps AnalysisGraph)
│   ├── recon.py               # authenticated user-data extraction (golden data)
│   └── target_catalog.py      # per-target catalog build + cache (SBOM+policy hash)
├── planning/                  # Phase 3 — coverage matrix + objective generation
│   ├── coverage_matrix.py     # SBOM nodes × policy clauses × technique families
│   └── objective_generator.py # scenario *objectives* (not just prompts)
├── scheduler/                 # Phase 4 — phased scheduler
│   ├── phases.py              # Phase enum 0–10 + ordering rules
│   ├── scheduler.py           # phase gating, resource locks, fresh-identity mgmt
│   └── safety.py              # non-negotiable safety guards (dry-run, ordering)
├── execution/                 # Phase 5 — adaptive execution (thin layer over v1 executors)
│   └── runner.py              # wraps AttackExecutor/GuidedAttackExecutor + adversary loop
├── evaluation/               # Phase 6 — layered evaluation (FP reduction core)
│   ├── pipeline.py            # orchestrates the 6 layers, produces a verdict
│   ├── deterministic.py       # canary/PII/tool-arg/authz/egress detectors
│   ├── semantic_judge.py      # isolated multi-judge LLM voting over Cognitive Policy
│   ├── side_effect.py         # did the action actually happen? (trace/log/diff)
│   ├── transferability.py     # safe retargeting + behavior-cluster scoring
│   └── robustness.py          # judge/spec-gaming guard
├── findings/                  # Phase 7 — findings + regression export
│   ├── builder.py             # verdict → Finding (design's finding fields)
│   └── regression.py          # confirmed finding → replayable regression test
├── orchestrator.py            # top-level v2 runner: surface→plan→schedule→exec→eval→report
└── report.py                  # v2 report (coverage gaps + transferability + control map)
```

**Reuse map (do NOT re-implement):**

| Need | Reuse from v1 / common |
| --- | --- |
| Endpoint discovery (pre-scan) | `nuguard/common/endpoint_probe.py` (`discover_chat_config_from_sbom`, `probe_chat_endpoints`) |
| Target HTTP + circuit breaker + sessions | `nuguard/redteam/target/client.py` (`TargetAppClient`) |
| Canary scanning (verbatim + decoded) | `nuguard/redteam/target/canary.py` (`CanaryScanner`, `CanaryConfig`) |
| LLM calls (adversary/judge), token accounting, canned-response offline mode | `nuguard/common/llm_client.py` (`LLMClient`) |
| Policy detectors | `nuguard/redteam/policy_engine/evaluator.py` (`PolicyEvaluator`, `PolicyViolation`) |
| Step execution, golden-data tokens, tool-trace judge, artifact scan | `nuguard/redteam/executor/executor.py` (`AttackExecutor`) |
| Adaptive multi-turn (PAIR/TAP) | `guided_executor.py`, `llm_engine/conversation_director.py`, `adaptive_mutation.py` |
| Response eval heuristics | `llm_engine/response_evaluator.py` (`evaluate`, `evaluate_partial`) |
| SBOM/graph models & queries | `nuguard/models/*`, `nuguard/analysis/graph.py` (`AnalysisGraph`), `behavior/sbom_graph.py` |
| Data models | `models/exploit_chain.py` (`GoalType`, `ScenarioType`, `ExploitChain/Step`), `models/finding.py` (`Finding`, `Severity`), `models/policy.py` (`CognitivePolicy`, `PolicyControl`) |
| Existing scenario catalog as seed | `redteam/catalog/` (`ScenarioSpec`, `taxonomy.py`, `builders.py`, `selector.py`, `capability.py`, `coverage.py`) |
| Report sinks | `nuguard/output/` (json/sarif), `redteam/report.py` patterns, `output/pytest_emitter.py` (regression files) |
| Config + `${ENV}` interpolation | `nuguard/config.py` (`load_config`, `_expand_env_vars`, `_flatten_yaml`, `RedteamFindingTriggers`) |
| CLI Typer wiring | `nuguard/cli/main.py`, `nuguard/cli/commands/redteam.py` |

---

## Phases

### Phase 0 — Scaffolding, config, CLI engine switch
**Deliverables:** package skeleton above; `--engine v1|v2` flag on `nuguard redteam` (default
`v1`); config block `redteam.v2.*` flowing through `_flatten_yaml`.
- Add `redteam.engine` and a `redteam.v2` section (knowledge_base_version, phases enabled,
  semantic_judge.count/quorum, transferability.enabled, max_per_phase, dry_run_only) to
  `config.py` + `nuguard.yaml.example`.
- `cli/commands/redteam.py`: add `--engine`; when `v2`, lazy-import `redteam.v2.orchestrator`.
- v2 orchestrator is a stub returning empty findings (wires end-to-end early).
**Tests:** CLI `--engine v2` runs to a clean empty report; config round-trips.

### Phase 1 — Technique Knowledge Base (schema + loader + seed set)
**Deliverables:** `knowledge/schema.py` `TechniqueRecord` matching the design's KB schema
(`id`, `name`, `source_refs`, `surfaces`, `preconditions`, `attack_intent`,
`safe_payload_strategy`, `success_criteria`, `detectors`, `execution_phase`, `state_impact`,
`required_fixtures`, `reset_hooks`, `mapped_controls`); `knowledge/loader.py` loading bundled
YAML via `importlib.resources` with a pinned `knowledge_base_version`; seed records covering
the design's technique families (direct/indirect injection, system-prompt override & policy
patching, faux reasoning, session/metadata forgery, multi-turn escalation, encoding/format
confusion, policy-boundary blending, prompt/config extraction, sensitive exfiltration, tool
misuse/arg injection, excessive agency, identity/privilege abuse, memory/RAG poisoning,
MCP/tool poisoning, inter-/human-agent trust, output handling, resource exhaustion,
transferable templates, judge gaming).
- **No raw harmful payloads stored** (safety rule) — records hold metadata + safe payload
  *strategy*; concrete prompts synthesized at runtime by builders.
- Cross-link each record to existing `ScenarioSpec.builder_key` so v1 builders synthesize
  payloads (reuse, not rewrite). Records add what specs lack: `source_refs`,
  `execution_phase`, `detectors`, `state_impact`, `reset_hooks`, `mapped_controls`.
**Tests:** schema validation; loader returns versioned records; every record's `builder_key`
resolves in `BUILDER_FACTORIES`; snapshot test on record IDs (like catalog snapshot).

### Phase 2 — Attack-surface graph, recon, per-target catalog cache
**Deliverables:**
- `surface/attack_surface.py`: normalize SBOM into the design's attack-surface table (models,
  prompts/policies, agents, tools/MCP, APIs, datastores, identity, deps, deployment,
  observability), wrapping `AnalysisGraph` for O(1) traversal. Each node tagged with trust
  boundary, data sensitivity, privileges, side effects, observability.
- `surface/recon.py`: pre-scan discovery (reuse `endpoint_probe`) + **authenticated user-data
  extraction** to seed realistic prompts and exfil canaries (extends v1 DISCOVER/golden-data).
- `surface/target_catalog.py`: build per-target catalog by matching KB techniques ×
  attack-surface × policy clauses (reuse `catalog/selector.py` + `capability.py`); **cache**
  to `.nuguard/redteam-v2/catalog-<sbom+policy hash>.yaml` (reuse `catalog/loader.py`
  export/load). Reuse if hashes unchanged; diff + update when SBOM/policy changes.
**Tests:** surface graph built from `minimal_sbom_doc`; catalog cache hit/miss on hash change;
recon golden-data extraction against fixture app.

### Phase 3 — Coverage matrix + objective generation
**Deliverables:**
- `planning/coverage_matrix.py`: matrix across SBOM nodes × policy clauses × technique
  families with reason codes for generated/skipped/blocked (design's "coverage scorer" input).
- `planning/objective_generator.py`: emit scenario **objectives** (not just prompts) per the
  design's pseudo-code — for each node and each policy clause, positive/negative/mixed-intent
  tests, tool/action tests where side effects/approvals exist; assign execution phase,
  resource locks, detectors, required evidence; ART behavior categories (confidentiality
  breach, conflicting objective, prohibited content, prohibited action) as explicit dims.
- Each objective references a KB technique + a `ScenarioSpec`/builder for payload synthesis.
**Tests:** objectives produced for each policy clause type; skipped clauses recorded with
reasons; coverage matrix counts match generated objectives.

### Phase 4 — Phased scheduler (phases 0–10) + safety
**Deliverables:**
- `scheduler/phases.py`: `Phase` enum (Setup, Recon, Warmup, BoundaryMapping,
  InstructionConflict, MultiTurn, IndirectContent, DataIdentity, AgenticKillChain,
  HighImpactDryRun, Cleanup) + per-phase stop/escalate signals.
- `scheduler/scheduler.py`: order objectives by phase; enforce ordering rules (never run
  high-impact before baseline/policy/data/indirect; fresh identity/conversation per test;
  one adaptive multi-turn per isolated thread; egress only against controlled canaries; stop
  later high-impact phases if a critical uncontrolled side effect appears). Resource locks
  (e.g. `rag_index:{id}`, `identity:{user}`) to serialize state-mutating tests; concurrency
  via `asyncio.Semaphore` for independent ones (reuse v1 orchestrator concurrency pattern).
- `scheduler/safety.py`: dry-run-only enforcement for destructive actions; fixture/reset-hook
  invocation.
**Tests:** ordering invariants (high-impact never precedes data/indirect); resource-lock
serialization; fresh-identity per scenario.

### Phase 5 — Adaptive execution (thin layer over v1 executors)
**Deliverables:** `execution/runner.py` translating a scheduled objective → `ExploitChain` or
`GuidedConversation` (via the linked builder) and running it through the existing
`AttackExecutor` / `GuidedAttackExecutor` with `TargetAppClient`. Adds the design's
adversary-loop + kill-chain composition: feed prior-phase successes (e.g. a guardrail bypass)
as launch points for later tool-misuse/exfil/privilege chains. All payloads logged and
human-reviewable.
**Tests:** runner executes a static chain and a guided conversation against the fixture app;
kill-chain composition passes a prior success into a later objective.

### Phase 6 — Layered evaluation pipeline (false-positive reduction core)
**Deliverables:** `evaluation/pipeline.py` runs layers in order and only escalates to LLMs
when deterministic layers are inconclusive:
1. `deterministic.py` — canary leak (reuse `CanaryScanner`), PII/secret regex, forbidden
   tool-call args, missing-approval, egress hit, role/scope mismatch (reuse tool-trace judge +
   `PolicyEvaluator`). **Programmatic verdicts trusted first.**
2. `semantic_judge.py` — isolated judge `LLMClient` (no target tools), **N-judge majority
   vote** with a structured rubric over the Cognitive Policy; behavior-level success criteria
   (a violation is an outcome, not a string). Reuse `response_evaluator` heuristics as a
   pre-filter.
3. `side_effect.py` — verify a claimed/attempted action actually happened (tool trace, audit
   log via `app_log_reader`, DB/state diff, dry-run receipt). Suppresses "talked about it"
   false positives.
4. `transferability.py` — safe retargeting of confirmed objectives across sibling
   models/roles/tools/policy clauses; behavior-cluster IDs; transferability as **severity
   multiplier**.
5. `robustness.py` — judge/specification-gaming guard: detect formatting tricks, repeated
   tokens, partial-language matches, detector-only "success"; flag ambiguous cases for human
   review.
- Output: a `Verdict` (succeeded, confidence, evidence bundle, contributing layers, severity
  signal, needs_human_review). Configurable trigger gates extend v1's `RedteamFindingTriggers`.
**Tests:** deterministic short-circuits (canary hit → confirmed without LLM); semantic-only
outcome requires judge quorum; side-effect verifier downgrades unverified claims;
spec-gaming inputs flagged; transferability raises severity.

### Phase 7 — Findings, reporting, regression export
**Deliverables:**
- `findings/builder.py`: `Verdict` → `Finding` populated with the design's fields (severity,
  confidence, technique_family, mapped_sources, sbom_nodes, policy_clauses, attack_phase,
  objective, observed/expected behavior, evidence, blast_radius, recommended_controls,
  regression_test_id). Severity accounts for action capability, data sensitivity, privilege
  crossed, repeatability, user interaction, kill-chain composition.
- `report.py`: v2 report = findings + **coverage gaps + skipped tests** + control-validation
  map + transferability clusters. Reuse `output/json_generator.py`, `output/sarif_generator.py`.
- `findings/regression.py`: each confirmed finding → replayable regression test (reuse
  `output/pytest_emitter.py` patterns) for continuous monitoring.
**Tests:** finding fields populated; report renders coverage gaps; regression file replays and
asserts refusal.

### Phase 8 — Later families & continuous monitoring (outline)
**Deliverables (subsequent iterations):** multimodal injection, model/data supply-chain
integrity, out-of-band egress-trap server, full continuous-monitoring loop reusing the cached
catalog for scheduled regression runs. Wired as additional KB records + phase objectives;
no architectural change required.

---

## False-Positive Reduction Strategy (explicit)

1. **Deterministic-first:** trust programmatic detectors (canary, tool-arg, authz, egress, DB
   diff) before any LLM; LLM judges only adjudicate ambiguous semantic outcomes.
2. **Behavior-level criteria:** success = policy-violating outcome, not a matched string;
   reuse golden-data filtering so the authenticated user's own data is never a "leak."
3. **Side-effect verification:** a claimed action must be evidenced in a trace/log/diff/dry-run
   receipt, eliminating "the model described doing X" false positives.
4. **Multi-judge quorum** for semantic verdicts; **judge-robustness guard** against spec
   gaming (formatting, repetition, partial matches, detector-only success).
5. **Human-review path** for high-impact or ambiguous findings (flag, don't auto-confirm).
6. **Transferability as severity signal** (not a new false positive): brittle one-offs ranked
   below issues that reproduce across models/policies.

---

## Verification

- **Unit/integration tests** per phase (above), reusing `tests/conftest.py` `minimal_sbom_doc`,
  `MagicMock`/`AsyncMock` `LLMClient` canned-response mode, and `tests/redteam/app_runner.py`
  fixture-app harness. Place under `tests/redteam/v2/`.
- **End-to-end:** extend `tests/redteam/test_e2e_redteam.py` with a v2 path
  (`@pytest.mark.redteam_e2e`, opt-in `NUGUARD_REDTEAM_E2E=1`): fixture app → SBOM → v2
  orchestrator → report; assert findings have full design fields, coverage gaps populated, and
  no finding is emitted without a deterministic or quorum verdict.
- **FP-reduction assertions:** golden tests that previously over-reported in v1 must now pass
  through layered eval with no finding (e.g. model *describes* a refund but no tool trace →
  no finding).
- **Manual CLI:** `uv run nuguard redteam --engine v2 --sbom <fixture>.sbom.json --target
  http://localhost:PORT --policy <policy>.md --format markdown`.
- **Lint/type:** `uv run ruff check nuguard/` and `uv run mypy nuguard/` clean for `redteam/v2`.

---

## Risks & Safety

- **Scope:** v2 is large; the separate package + phase boundaries let each phase ship and be
  tested independently without touching v1.
- **Safety rules (non-negotiable, from design):** authorized targets only; synthetic
  tenants/canaries/dry-run tools; no real sends/deletes/purchases/refunds/code-exec/exports
  unless an explicit sandbox fixture enables them; no harmful operational payloads in the KB;
  isolate adversary LLM, judge LLM, and target; treat all external content as untrusted and
  every tool call as a security boundary; phase ordering enforced in `scheduler/safety.py`.
- **Cost:** layered eval is deterministic-first to minimize LLM calls; reuse `LLMClient` token
  accounting and `prompt_cache_dir`.
