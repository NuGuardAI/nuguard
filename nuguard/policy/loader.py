"""Load and save compiled PolicyControl lists from/to JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger
from nuguard.models.policy import PolicyControl

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.models.policy import CognitivePolicy

_log = get_logger(__name__)


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


async def ensure_policy_controls(
    policy_path: Path,
    use_llm: bool = False,
    llm_client: "LLMClient | None" = None,
) -> "tuple[CognitivePolicy, list[PolicyControl]]":
    """Return a parsed CognitivePolicy and compiled PolicyControl list.

    Loads controls from the companion ``.json`` file when it already exists
    (fast, deterministic).  If the file is absent, compiles controls from the
    Markdown source, persists the result to ``<policy>.json``, then returns it.

    This is the canonical entry-point for both ``behavior`` and ``redteam``
    commands; it replaces the previous pattern of calling ``parse_policy`` +
    ``compile_controls`` / ``load_controls`` separately.

    Args:
        policy_path: Path to the Cognitive Policy Markdown file (``.md``).
        use_llm:     Pass ``True`` to allow LLM-assisted prompt generation when
                     the JSON needs to be (re-)built.  Ignored when the JSON
                     already exists.
        llm_client:  LLMClient instance used when *use_llm* is ``True``.

    Returns:
        ``(cognitive_policy, controls)`` — both are always non-None.
    """
    from nuguard.policy.compiler import compile_controls  # noqa: PLC0415
    from nuguard.policy.parser import parse_policy  # noqa: PLC0415

    text = policy_path.read_text(encoding="utf-8")
    cognitive_policy = parse_policy(text)

    compiled = compiled_path_for(policy_path)
    if compiled.exists():
        _log.info("Loading compiled policy controls from %s", compiled)
        controls = load_controls(compiled)
    else:
        _log.info(
            "Compiled policy controls not found at %s — building from %s",
            compiled,
            policy_path.name,
        )
        controls = await compile_controls(text, use_llm=use_llm, llm_client=llm_client)
        save_controls(controls, compiled)
        _log.info("Built and saved %d policy controls to %s", len(controls), compiled)

    return cognitive_policy, controls
