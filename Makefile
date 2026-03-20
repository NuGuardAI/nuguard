.PHONY: dev test lint fmt

dev:
	uv sync --dev

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check nuguard/
	uv run mypy nuguard/

fmt:
	uv run ruff format nuguard/ tests/
