"""Checkpoint/resume support shared by the redteam and behavior pipelines.

Lets a long-running scan persist its progress incrementally and, on a
timeout/crash, hand back a *partial* result instead of losing everything
(see issue #508). Mirrors :class:`nuguard.redteam.llm_engine.prompt_cache.PromptCache`'s
on-disk conventions — a sha256-keyed JSON file living in the same
``prompt_cache_dir`` — so a checkpoint is just another file in that
directory, not a new storage concept.

Typical flow for a pipeline (redteam/behavior):

1. Construct a :class:`RunCheckpoint` for ``prompt_cache_dir`` whenever one
   is configured.
2. On ``--resume <path>``, call :meth:`RunCheckpoint.load` with that path,
   validate the fingerprint via :func:`fingerprint`, and pre-seed the run
   with the checkpoint's ``completed_signatures``/prior results.
3. As scenarios complete, call :meth:`RunCheckpoint.save` with the
   accumulated state so a crash mid-run still leaves a usable file on disk.
4. On success, call :meth:`RunCheckpoint.delete` — a completed run has no
   further use for its checkpoint.
5. On failure after >=1 scenario completed, raise :class:`PartialRunError`
   with the partial result attached so the caller (CLI, streaming API) can
   choose to persist/report it instead of only logging a stack trace.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from nuguard.common.logging import get_logger

_log = get_logger(__name__)

CHECKPOINT_VERSION = 1

RunKind = Literal["redteam", "behavior"]


class CheckpointMismatchError(Exception):
    """Raised when a ``--resume`` checkpoint's sbom/policy fingerprint doesn't match.

    A resume is refused rather than silently proceeding, since the
    ``completed_signatures`` recorded in the checkpoint may not correspond to
    the same attack surface as the current run's inputs.
    """


class PartialRunError(Exception):
    """Wraps an exception that aborted a run after >=1 scenario had completed.

    Carries the partial result payload and the checkpoint file path so a
    caller can persist/report a partial result instead of losing all
    progress. Always raised as ``raise PartialRunError(...) from cause`` so
    the original traceback is preserved.
    """

    def __init__(
        self,
        cause: BaseException,
        *,
        partial_payload: dict[str, Any],
        checkpoint_path: "Path | None",
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.partial_payload = partial_payload
        self.checkpoint_path = checkpoint_path
        # Set by the caller (run_redteam / behavior equivalent) once it has
        # assembled a JSON-safe partial result object from partial_payload.
        self.partial_result: Any = None


def _stable_json(obj: Any) -> str:
    """Stable, whitespace-free JSON serialisation suitable for hashing."""
    if obj is None:
        return ""
    try:
        if hasattr(obj, "model_dump_json"):
            raw = json.loads(obj.model_dump_json())
        else:
            raw = obj
        return json.dumps(raw, sort_keys=True, separators=(",", ":"))
    except Exception as exc:
        _log.warning("run_checkpoint: failed to serialize object for fingerprint: %s", exc)
        return str(obj)


def fingerprint(sbom: Any, policy: Any | None) -> str:
    """sha256 of stable sbom+policy serialisation, truncated to 16 hex chars.

    Identical scheme to ``PromptCache.cache_key``/``BehaviorPromptCache.cache_key``
    so a checkpoint's fingerprint is directly comparable to those caches' keys.
    """
    combined = _stable_json(sbom) + _stable_json(policy)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def validate_fingerprint(checkpoint: dict[str, Any], *, sbom: Any, policy: Any | None) -> None:
    """Raise :class:`CheckpointMismatchError` when *checkpoint* doesn't match the current inputs."""
    expected = fingerprint(sbom, policy)
    actual = checkpoint.get("cache_key")
    if actual != expected:
        raise CheckpointMismatchError(
            f"Checkpoint fingerprint {actual!r} does not match the current "
            f"sbom+policy ({expected!r}) — refusing to resume from a checkpoint "
            "that may not correspond to the same attack surface. Re-run without "
            "--resume, or resume from a checkpoint generated for this exact "
            "sbom/policy pair."
        )


class RunCheckpoint:
    """File-backed checkpoint for a redteam/behavior run, enabling ``--resume``.

    One JSON file per ``(run_kind, cache_key)`` in *output_dir*, following
    ``PromptCache``'s naming/write conventions. Reads are best-effort (log +
    return ``None`` on any I/O/parse error) since a missing/corrupt
    checkpoint should never crash the run trying to use it; writes use a
    temp-file-then-rename for atomicity and are also best-effort — a
    checkpoint write failure must never crash the run it's protecting.
    """

    def __init__(self, output_dir: Path, run_kind: RunKind) -> None:
        self._dir = output_dir
        self._run_kind = run_kind

    def path_for(self, cache_key: str) -> Path:
        return self._dir / f"{self._run_kind}-checkpoint-{cache_key}.json"

    def load(self, path: Path) -> dict[str, Any] | None:
        """Load a checkpoint payload from an explicit path (the ``--resume <path>`` value)."""
        if not path.exists():
            _log.warning("run_checkpoint: checkpoint file not found: %s", path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            _log.warning("run_checkpoint: failed to load checkpoint %s: %s", path, exc)
            return None
        if data.get("run_kind") != self._run_kind:
            _log.warning(
                "run_checkpoint: checkpoint %s is for run_kind=%r, expected %r — ignoring",
                path, data.get("run_kind"), self._run_kind,
            )
            return None
        return data

    def save(self, path: Path, payload: dict[str, Any]) -> Path:
        """Write *payload* to *path* atomically (temp file + rename). Best-effort."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            to_write = dict(payload)
            to_write.setdefault("checkpoint_version", CHECKPOINT_VERSION)
            to_write.setdefault("run_kind", self._run_kind)
            now = datetime.now(UTC).isoformat()
            to_write["updated_at"] = now
            to_write.setdefault("created_at", now)
            fd, tmp_name = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(to_write, fh, indent=2, default=str)
                Path(tmp_name).replace(path)
            finally:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except OSError:
                    pass
        except Exception as exc:
            _log.warning("run_checkpoint: failed to save checkpoint %s: %s", path, exc)
        return path

    def delete(self, path: Path) -> None:
        """Best-effort deletion of a checkpoint file (called after a fully successful run)."""
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            _log.warning("run_checkpoint: failed to delete checkpoint %s: %s", path, exc)


def redteam_scenario_signature(
    goal_type: str,
    scenario_type: str,
    title: str,
    catalog_id: str | None = None,
) -> str:
    """Deterministic identity for an ``AttackScenario``/``ScenarioRecord`` that survives UUID regeneration.

    Generalizes the ``goal_type|scenario_type|title`` slug already used by
    ``LLMPromptGenerator.enrich_all`` (nuguard/redteam/llm_engine/prompt_generator.py)
    to match scenarios across a re-run of ``ScenarioGenerator.generate()``.
    Catalog-driven scenarios prefer their stable ``catalog_id`` over ``title``
    since a catalog template's title can repeat across many target nodes.
    """
    if catalog_id:
        return f"{goal_type}|{scenario_type}|catalog:{catalog_id}"
    return f"{goal_type}|{scenario_type}|{title}"


def attack_scenario_signature(scenario: Any) -> str:
    """:func:`redteam_scenario_signature` for a pre-run ``AttackScenario``."""
    goal_type = getattr(scenario.goal_type, "value", scenario.goal_type)
    scenario_type = getattr(scenario.scenario_type, "value", scenario.scenario_type)
    return redteam_scenario_signature(
        goal_type=str(goal_type),
        scenario_type=str(scenario_type),
        title=scenario.title,
        catalog_id=getattr(scenario, "catalog_id", None) or None,
    )


def scenario_record_signature(record: Any) -> str:
    """:func:`redteam_scenario_signature` for a post-run ``ScenarioRecord``."""
    return redteam_scenario_signature(
        goal_type=record.goal_type,
        scenario_type=record.scenario_type,
        title=record.title,
        catalog_id=getattr(record, "catalog_id", None) or None,
    )


def behavior_scenario_signature(scenario_type: str, name: str) -> str:
    """Deterministic identity for a ``BehaviorScenario``/``ScenarioResult`` that survives UUID regeneration.

    Deliberately just ``scenario_type|name`` (no target-component folding):
    ``BehaviorScenario.target_component`` and ``ScenarioResult`` have no
    directly corresponding field, so including it would make the pre-run and
    post-run signatures diverge and never match (see the identical
    simplification for :func:`redteam_scenario_signature`, which drops
    ``affected`` for the same reason).
    """
    return f"{scenario_type}|{name}"


def behavior_scenario_obj_signature(scenario: Any) -> str:
    """:func:`behavior_scenario_signature` for a pre-run ``BehaviorScenario``."""
    scenario_type = getattr(scenario.scenario_type, "value", scenario.scenario_type)
    return behavior_scenario_signature(scenario_type=str(scenario_type), name=scenario.name)


def behavior_result_signature(result: Any) -> str:
    """:func:`behavior_scenario_signature` for a post-run ``ScenarioResult``."""
    return behavior_scenario_signature(scenario_type=result.scenario_type, name=result.scenario_name)
