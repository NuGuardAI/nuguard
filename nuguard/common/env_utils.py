"""Environment-variable helper utilities shared across nuguard packages.

These thin wrappers around ``os.getenv`` provide typed, validated reads of
environment variables with a default fallback and a warning log when the
value is present but malformed.
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)


def env_float(name: str, default: float) -> float:
    """Read *name* from the environment and return it as a float.

    Returns *default* when the variable is unset, empty, or non-numeric and
    logs a warning in the latter case.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("Invalid %s=%r; using default %.3f", name, raw, default)
        return default


def env_int(name: str, default: int) -> int:
    """Read *name* from the environment and return it as an int.

    Returns *default* when the variable is unset, empty, or non-numeric and
    logs a warning in the latter case.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default


def env_bool(name: str, default: bool) -> bool:
    """Read *name* from the environment and return it as a bool.

    Truthy values: ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-insensitive).
    Returns *default* when the variable is unset or empty.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_optional_float(name: str) -> float | None:
    """Read *name* from the environment as a float, returning ``None`` when unset."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        _log.warning("Invalid %s=%r; ignoring", name, raw)
        return None


def env_optional_int(name: str) -> int | None:
    """Read *name* from the environment as an int, returning ``None`` when unset."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        _log.warning("Invalid %s=%r; ignoring", name, raw)
        return None
