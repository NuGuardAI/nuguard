# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync --dev

# Run all tests
uv run pytest tests/ -v

# Lint and type check
uv run ruff check nuguard/
uv run mypy nuguard/

# Run the CLI
uv run nuguard --help
uv run nuguard sbom generate --from-repo https://github.com/org/repo --ref main
```

Or use the Makefile shortcuts: `make dev`, `make test`, `make lint`, `make fmt`.

## Architecture

NuGuard is an AI application security package. The four capabilities are:

1. **sbom** — Generate an AI-SBOM (AI Bill of Materials) by statically scanning Python/TypeScript source
2. **analyze** — Static analysis of an AI-SBOM to detect security issues
3. **policy** — Parse and validate a Cognitive Policy document against a scan
4. **behavior** — Static and dynamic behavioral testing against a live AI application endpoint
5. **redteam** — Dynamic adversarial testing against a live AI application endpoint

The intended pipeline is: `sbom generate` → `analyze` → `behavior` → `redteam` → `report`.

### Package layout

```
nuguard/
├── sbom/           # AI-SBOM generation — the most complete package (absorbed from Xelo)
├── behavior/       # Static and dynamic behavioral testing
├── redteam/        # Dynamic red-team — agents, executor, scenarios, policy_engine, risk_engine
├── graph/          # Attack graph builder (SBOM → enriched graph)
├── analysis/       # Static SBOM analysis — detector plugins
├── policy/         # Cognitive Policy parsing and violation checking
├── models/         # Shared Pydantic models (AttackGraph, ExploitChain, Scan, Finding, Policy)
├── db/             # SQLite (default) or Postgres (async SQLAlchemy)
├── output/         # SARIF / JSON / Markdown report generators
├── cli/            # Typer app — main.py wires 8 sub-commands from commands/
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

### Coding style
- Follow PEP8 and Black formatting
- use the tmp folder for scratch code and scripts instead of streaming the command in the terminal and to resue
- Being a security tool, prioritize secure coding practices (e.g. validate all inputs, handle exceptions, avoid shelling out when possible)
- Type hint all functions and methods
- Use `logging` for debug/info/warning messages; avoid print statements
- Write modular, single-responsibility functions and classes
- Use docstrings to explain complex logic and public APIs
- Write unit tests for all new code and aim for high coverage
- Use `uv run` to execute commands in the virtual environment
- For CLI commands, provide helpful messages and error handling
- For LLM calls, handle rate limits and errors gracefully; provide fallback behavior if the LLM is unavailable
- When in doubt, prioritize code readability and maintainability over cleverness
- Use GitHub Issues and Pull Requests to track work and code changes; provide clear descriptions and context in PRs
- Regularly update this CLAUDE.md file as the codebase evolves to keep guidance accurate and relevant

