# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync --dev

# Run all tests
uv run pytest tests/ -v

# Run a single test file or test by name
uv run pytest tests/redteam/test_finding_triggers.py -v
uv run pytest tests/ -k "test_llm_eval_high_confidence" -v

# Lint and type check
uv run ruff check nuguard/
uv run mypy nuguard/

# Run the CLI
uv run nuguard --help
uv run nuguard sbom generate --from-repo https://github.com/org/repo --ref main
```

Makefile shortcuts: `make dev`, `make test`, `make lint`, `make fmt`.

Use `tmp/` for scratch scripts and one-off experiments rather than streaming commands in the terminal.

## Architecture

NuGuard is an AI application security package with five capabilities:

1. **sbom** — Generate an AI-SBOM (AI Bill of Materials) by statically scanning Python/TypeScript source
2. **analyze** — Static analysis of an AI-SBOM to detect security issues
3. **policy** — Parse and validate a Cognitive Policy document against a scan
4. **behavior** — Static and dynamic behavioral testing against a live AI application endpoint
5. **redteam** — Dynamic adversarial testing against a live AI application endpoint

The intended pipeline is: `sbom generate` → `analyze` → `behavior` → `redteam` → `report`.

### Package layout

```
nuguard/
├── sbom/           # AI-SBOM generation — the most complete package
├── behavior/       # Static and dynamic behavioral testing
├── redteam/        # Dynamic red-team — see detailed breakdown below
├── graph/          # Attack graph builder (SBOM → enriched graph)
├── analysis/       # Static SBOM analysis — detector plugins
├── policy/         # Cognitive Policy parsing and violation checking
├── remediation/    # Shared remediation-artefact synthesis (behavior, redteam, analysis)
├── models/         # Shared Pydantic models (AttackGraph, ExploitChain, Scan, Finding, Policy)
├── db/             # SQLite (default) or Postgres (async SQLAlchemy)
├── output/         # SARIF / JSON / Markdown report generators
├── cli/            # Typer app — main.py wires sub-commands from commands/
├── config.py       # nuguard.yaml loader with ${ENV_VAR} interpolation
└── common/         # errors.py, logging.py, llm_client.py, http.py
```

### SBOM package (nuguard.sbom)

The bundled JSON Schema is at `nuguard/sbom/schemas/aibom.schema.json` and must stay in sync with `AiSbomDocument.model_json_schema()` — `test_committed_schema_matches_models` enforces this.

### CLI wiring

`nuguard/cli/main.py` registers all sub-commands via `app.add_typer(...)`. Each command module in `nuguard/cli/commands/` imports from the relevant package lazily (inside the command function) to keep startup fast.

### LLM enrichment

LLM calls are optional everywhere. Pass `--llm` to `nuguard sbom generate` or set `llm: true` in config. The client wraps LiteLLM; default model is `gemini/gemini-2.0-flash`. API key via `LITELLM_API_KEY`.

### Configuration

`nuguard.yaml` (or `--config` flag) is the primary config file. See `nuguard.yaml.example` for all options. Environment variables are interpolated with `${VAR}` syntax. CLI flags override config file values.

### Naming conventions

- SBOM node/edge type values: `SCREAMING_SNAKE_CASE` (e.g. `AGENT`, `CALLS`, `ACCESSES`)
- Risk attribute tags: hyphenated lowercase (e.g. `SQL-injectable`, `no-auth-required`)
- Pydantic fields and file names: `snake_case`
- `ACCESSES` edges carry `access_type: read | write | readwrite`

---

## Redteam package internals

The redteam package is the most complex and actively developed. Understanding its data flow is critical.

```
nuguard/redteam/
├── scenarios/          # Scenario generation: reads SBOM → emits AttackScenario list
│   ├── generator.py    # ScenarioGenerator — entry point, wires all builders
│   ├── scenario_types.py  # AttackScenario dataclass
│   └── *.py            # One file per attack family (data_exfiltration, prompt_injection, etc.)
├── executor/
│   ├── orchestrator.py # RedteamOrchestrator — top-level async runner
│   ├── executor.py     # AttackExecutor — runs ExploitChain step by step
│   ├── guided_executor.py  # GuidedAttackExecutor — multi-turn adaptive conversations
│   └── chain_assembler.py  # Assembles LLM-enriched payloads into ExploitStep sequences
├── llm_engine/
│   ├── response_evaluator.py  # LLMResponseEvaluator — judges whether a step succeeded
│   ├── adaptive_mutation.py   # AdaptiveMutationStrategy — rewrites payloads between turns
│   └── conversation_director.py  # Steers guided conversations toward goal
├── policy_engine/
│   └── evaluator.py    # PolicyEvaluator — checks responses against CognitivePolicy
├── risk_engine/
│   └── severity_scorer.py, compliance_mapper.py, risk_scorer.py  # Severity + OWASP/ATLAS refs
│       # Remediation-artefact synthesis lives in nuguard/remediation/ (shared with
│       # behavior and analysis) — see RemediationSynthesizer, resolve_remediation_llm_client.
├── target/
│   ├── client.py       # TargetAppClient — HTTP client with circuit breaker
│   └── canary.py       # CanaryScanner — watches for canary value leaks in responses
└── report.py           # Builds the Markdown/JSON redteam report from scenario records
```

### Data flow

1. `ScenarioGenerator.generate()` reads the SBOM and emits a sorted list of `AttackScenario` objects. Each scenario has either a `chain: ExploitChain` (static steps) or a `guided_conversation: GuidedConversation` (adaptive).
2. `RedteamOrchestrator.run()` deduplicates scenarios, then dispatches them concurrently via `asyncio.Semaphore` (default concurrency=5).
3. Static chains run through `AttackExecutor.run()` — each `ExploitStep` is executed in order with optional LLM eval (`use_llm_eval=True`) and adaptive mutation on failure.
4. Guided conversations run through `GuidedAttackExecutor` — a `ConversationDirector` steers turn-by-turn toward the goal, scoring progress 0–1 each turn.
5. `_build_findings()` converts step results into `Finding` objects using a 4-tier trigger hierarchy: **canary_hits → policy_violations → critical_success_hits → any_inject_success**. The `RedteamFindingTriggers` config controls which tiers are active.

### Finding triggers and LLM eval

`ExploitStep.on_failure` controls abort semantics:
- `"abort"` — step failure aborts the chain; success always qualifies as a critical hit
- `"skip"` / `"mutate"` — non-critical steps; only qualify as findings when `use_llm_eval=True` and `llm_eval_confidence in ("high", "medium")`

`LLMResponseEvaluator.evaluate()` returns `{succeeded, confidence, evidence, severity_signal}`. Pre-LLM heuristics short-circuit the call for known patterns (unexpected code generation → PROMPT_DRIVEN_THREAT; fictional-framing + PII fields → DATA_EXFILTRATION).

### Circuit breaker and scenario timeout

`TargetUnavailableError` from `TargetAppClient` trips the circuit breaker only after **3 consecutive** failures (threshold in `_run_scenarios`). A single transient error marks that scenario `"aborted"` but does not stop the run.

`scenario_timeout` (config: `redteam.scenario_timeout`, default 300 s) wraps each scenario execution in `asyncio.wait_for()`. Timed-out scenarios record `chain_status="timeout"`.

### GoalType taxonomy

`GoalType` (in `nuguard/models/exploit_chain.py`) maps to attack families. Key ones:
- `PROMPT_DRIVEN_THREAT` — prompt injection, system prompt extraction, guardrail bypass
- `DATA_EXFILTRATION` — PII/PHI extraction, cross-tenant, covert encoding
- `PRIVILEGE_ESCALATION` — HITL bypass, privilege chain
- `API_ATTACK` — auth bypass, IDOR, mass assignment
- `MCP_TOXIC_FLOW` — poisoned MCP server content flowing to write-capable tools
- `POLICY_VIOLATION` — topic boundary, restricted actions

### Coding style
- Follow PEP8 and Black formatting
- Type hint all functions and methods
- Use `logging` for debug/info/warning messages; avoid print statements
- For LLM calls, handle rate limits and errors gracefully; provide fallback behavior if the LLM is unavailable
- Ensure consistent coding style across the codebase; run `uv run ruff check nuguard/` before committing
- Maximum code reuse and modularity; avoid monolithic functions or classes
- Write comprehensive tests for all new functionality; aim for high coverage in critical areas like scenario generation and execution
- Document all public functions and classes with docstrings; include examples where helpful
- Use `nuguard.yaml` for any configurable parameters; avoid hardcoding values in the code
- When in doubt, prioritize clarity and maintainability over cleverness or brevity
