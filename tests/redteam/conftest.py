"""Pytest configuration for E2E redteam tests.

E2E tests are opt-in: they require real LLM API keys and take several minutes.
Set ``NUGUARD_REDTEAM_E2E=1`` in the environment to enable them (or populate
``tests/redteam/.env`` — this file is loaded automatically if present).

Usage::

    # With .env file pre-populated:
    uv run pytest tests/redteam/ -v -s

    # Or inline:
    NUGUARD_REDTEAM_E2E=1 OPENAI_API_KEY=sk-... uv run pytest tests/redteam/ -v -s

To run a single app::

    uv run pytest tests/redteam/ -v -s -k voicelive
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_ENV_FILE = Path(__file__).parent / ".env"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — no external dependency required."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Don't override values already set in the environment
        if key and key not in os.environ:
            os.environ[key] = value


# Load .env before any test collection so env vars are available early
if _ENV_FILE.exists():
    _load_dotenv(_ENV_FILE)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "redteam_e2e: mark test as an E2E redteam scan (requires NUGUARD_REDTEAM_E2E=1)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip redteam_e2e tests unless NUGUARD_REDTEAM_E2E=1 is set."""
    if os.getenv("NUGUARD_REDTEAM_E2E") == "1":
        return
    skip_marker = pytest.mark.skip(
        reason="E2E redteam tests are opt-in. Set NUGUARD_REDTEAM_E2E=1 to run (or populate tests/redteam/.env)."
    )
    for item in items:
        if item.get_closest_marker("redteam_e2e"):
            item.add_marker(skip_marker)
