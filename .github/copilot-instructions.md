# GitHub Copilot Instructions

This file configures GitHub Copilot for the NuGuard repository. Refer to [CLAUDE.md](../CLAUDE.md) and [README.md](../README.md) for full project context.

## Project Overview

NuGuard is an open-source AI application security CLI. It generates AI-focused SBOMs from source code, runs static security analysis, validates cognitive policy documents, and red-teams live AI applications with adversarial scenario testing.

Pipeline: `sbom generate` → `analyze` → `behavior` → `redteam` → `report`

## Development Commands

```bash
uv sync --dev          # install dependencies
uv run pytest tests/ -v  # run all tests
uv run ruff check nuguard/  # lint
uv run mypy nuguard/        # type check
uv run nuguard --help       # run CLI
```

Makefile shortcuts: `make dev`, `make test`, `make lint`, `make fmt`

## Package Layout

```
nuguard/
├── sbom/       # AI-SBOM generation
├── behavior/   # Static and dynamic behavioral testing
├── redteam/    # Dynamic adversarial testing (agents, executor, scenarios, policy_engine, risk_engine)
├── graph/      # Attack graph builder (SBOM → enriched graph)
├── analysis/   # Static SBOM analysis — detector plugins
├── policy/     # Cognitive Policy parsing and violation checking
├── models/     # Shared Pydantic models (AttackGraph, ExploitChain, Scan, Finding, Policy)
├── db/         # SQLite (default) or Postgres (async SQLAlchemy)
├── output/     # SARIF / JSON / Markdown report generators
├── cli/        # Typer app — main.py wires sub-commands from commands/
├── config.py   # nuguard.yaml loader with ${ENV_VAR} interpolation
└── common/     # errors.py, logging.py, llm_client.py, http.py
```

## Coding Style

- Follow PEP 8 and Black formatting
- Type-hint all functions and methods
- Use `logging` for debug/info/warning — no bare `print` statements
- Write modular, single-responsibility functions and classes
- Add docstrings for complex logic and public APIs
- Write unit tests for all new code; aim for high coverage
- Use `uv run` to execute commands inside the virtual environment
- Use the `tmp/` folder for scratch scripts instead of streaming commands in the terminal
- For CLI commands, provide helpful error messages and proper exit codes
- For LLM calls, handle rate limits and errors gracefully with fallback behavior

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

## CLI Surface

Implemented today: `nuguard sbom`, `nuguard analyze`, `nuguard scan`, `nuguard policy`, `nuguard redteam`, `nuguard behavior`

Stubbed / not yet implemented: `nuguard seed`, `nuguard report`, `nuguard findings`, `nuguard replay`
