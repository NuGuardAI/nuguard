"""Load and save compiled PolicyControl lists from/to JSON."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from nuguard.models.policy import PolicyControl

_log = logging.getLogger(__name__)


def save_controls(controls: list[PolicyControl], path: Path) -> None:
    """Write *controls* to *path* as a JSON file.

    Args:
        controls: Compiled policy controls.
        path:     Destination file path (typically ``cognitive_policy.json``).
    """
    path.write_text(
        json.dumps(
            [c.model_dump() for c in controls],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _log.debug("Saved %d policy controls to %s", len(controls), path)


def load_controls(path: Path) -> list[PolicyControl]:
    """Load PolicyControl list from a compiled JSON file.

    Args:
        path: Path to a ``cognitive_policy.json`` produced by
              ``nuguard policy compile``.

    Returns:
        List of PolicyControl instances.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError:        If the file contains invalid JSON or malformed controls.
    """
    if not path.exists():
        raise FileNotFoundError(f"Policy controls file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON array in {path}, got {type(raw).__name__}")

    controls: list[PolicyControl] = []
    errors: list[str] = []
    for i, item in enumerate(raw):
        try:
            controls.append(PolicyControl(**item))
        except Exception as exc:
            errors.append(f"item {i}: {exc}")

    if errors:
        _log.warning("Skipped %d malformed control(s) in %s: %s", len(errors), path, errors)

    _log.debug("Loaded %d policy controls from %s", len(controls), path)
    return controls


def compiled_path_for(policy_md_path: Path) -> Path:
    """Return the conventional compiled JSON path for a Markdown policy file.

    ``cognitive_policy.md`` → ``cognitive_policy.json``
    """
    return policy_md_path.with_suffix(".json")
