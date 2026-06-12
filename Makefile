.PHONY: dev test lint fmt precommit-install precommit-run precommit-advisory

dev:
	uv sync --dev

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check nuguard/
	uv run mypy nuguard/

fmt:
	uv run ruff format nuguard/ tests/

precommit-install:
	uv run pre-commit install

precommit-run:
	uv run pre-commit run --all-files

precommit-advisory:
	uv run pre-commit run ruff-full-advisory --hook-stage manual --all-files
	uv run pre-commit run mypy-advisory --hook-stage manual --all-files
