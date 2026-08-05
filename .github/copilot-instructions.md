# GitHub Copilot Instructions

This file configures GitHub Copilot for the NuGuard repository. Refer to [CLAUDE.md](../CLAUDE.md) and [README.md](../README.md) for full project context.

## Project Overview

NuGuard is an open-source AI application security CLI. It generates AI-focused SBOMs from source code, runs static security analysis, validates cognitive policy documents, and red-teams live AI applications with adversarial scenario testing.

Pipeline: `sbom generate` → `analyze` → `behavior` → `redteam` → `report`

## Development Commands

```bash
uv sync --dev                                        # install dependencies
uv run pytest tests/ -v                              # run all tests
uv run pytest tests/redteam/test_finding_triggers.py -v  # single test file
uv run pytest tests/ -k "test_llm_eval_high_confidence" -v  # test by name
uv run ruff check nuguard/                           # lint
uv run mypy nuguard/                                 # type check
uv run nuguard --help                                # run CLI
uv run nuguard sbom generate --from-repo https://github.com/org/repo --ref main
```

Makefile shortcuts: `make dev`, `make test`, `make lint`, `make fmt`

Use `tmp/` for scratch scripts and one-off experiments rather than streaming commands in the terminal.

## Package Layout

```
nuguard/
├── sbom/           # AI-SBOM generation — the most complete package
├── behavior/       # Static and dynamic behavioral testing
├── redteam/        # Dynamic red-team — see Redteam Internals below
├── graph/          # Attack graph builder (SBOM → enriched graph)
├── analysis/       # Static SBOM analysis — detector plugins
├── policy/         # Cognitive Policy parsing and violation checking
├── models/         # Shared Pydantic models (AttackGraph, ExploitChain, Scan, Finding, Policy)
├── db/             # SQLite (default); Postgres planned (async SQLAlchemy, not yet implemented)
├── output/         # SARIF / JSON / Markdown report generators
├── cli/            # Typer app — main.py wires sub-commands from commands/
├── config.py       # nuguard.yaml loader with ${ENV_VAR} interpolation
└── common/         # errors.py, logging.py, llm_client.py, http.py
```

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
- Use the `tmp/` folder for scratch scripts instead of streaming commands in the terminal
- For CLI commands, provide helpful error messages and proper exit codes


## Security Requirements (OWASP Top 10)

NuGuard is a security tool — hold all code to a high security bar:

- Validate all inputs at system boundaries
- Avoid shelling out when Python APIs exist
- Never hard-code secrets; read from environment variables
- Handle exceptions explicitly; do not swallow errors silently
- Sanitize any data written to output files or databases

## Naming Conventions

| Context | Convention |
|---|---|
| SBOM node/edge type values | `SCREAMING_SNAKE_CASE` (e.g. `AGENT`, `CALLS`, `ACCESSES`) |
| Risk attribute tags | hyphenated lowercase (e.g. `SQL-injectable`, `no-auth-required`) |
| Pydantic fields & file names | `snake_case` |
| `ACCESSES` edge attribute | `access_type: read \| write \| readwrite` |

## Key Architecture Notes

- **SBOM schema**: `nuguard/sbom/schemas/aibom.schema.json` must stay in sync with `AiSbomDocument.model_json_schema()`. The test `test_committed_schema_matches_models` enforces this.
- **CLI wiring**: `nuguard/cli/main.py` registers sub-commands via `app.add_typer(...)`. Command modules import from their package lazily (inside the function body) to keep startup fast.
- **LLM enrichment**: optional everywhere. Pass `--llm` or set `llm: true` in config. Default model: `gemini/gemini-2.0-flash`. API key: `LITELLM_API_KEY`.
- **Configuration precedence**: CLI flags > `nuguard.yaml` > environment variables > built-in defaults.
- **Shared SBOM enrichment**: both `nuguard behavior` and `nuguard redteam` call `enrich_sbom_for_run()` from `nuguard/cli/common.py` before scenario generation. Prefer this shared helper over duplicating enrichment logic.

## CLI Surface

Implemented: `nuguard sbom`, `nuguard analyze`, `nuguard scan`, `nuguard policy`, `nuguard redteam`, `nuguard behavior`

Stubbed / not yet implemented: `nuguard seed`, `nuguard report`, `nuguard findings`, `nuguard replay`

## Redteam Package Internals

The redteam package is the most complex and actively developed. Understanding its data flow is critical.

```
nuguard/redteam/
├── scenarios/          # Scenario generation: reads SBOM → emits AttackScenario list
│   ├── generator.py    # ScenarioGenerator — entry point, wires all builders
│   ├── scenario_types.py  # AttackScenario dataclass
│   ├── advanced_jailbreaks.py  # Many-Shot, Crescendo, Skeleton Key, Payload Splitting
│   ├── evasion.py      # Encoding evasion (ROT-13/leet/morse), multi-language bypass
│   ├── agentic_attacks.py  # Confused Deputy, Multi-Agent Trust, Memory Poisoning, Goal Hijacking
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
│   ├── remediation_generator.py  # Template-based remediation advice per GoalType
│   └── remediation_synthesizer.py  # LLM-based remediation narrative
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
- `AGENTIC_TRUST_ABUSE` — confused deputy, multi-agent trust boundary, memory poisoning, goal hijacking

### LLM client

`nuguard/common/llm_client.py` wraps LiteLLM. It applies **exponential backoff with jitter** (up to 4 retries, capped at 60 s) for `RateLimitError` and `ServiceUnavailableError`. Non-transient errors fail fast. Always use `LLMClient` — never call litellm directly.
