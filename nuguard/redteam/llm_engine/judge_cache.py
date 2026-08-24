"""Cross-run disk-backed cache for LLMResponseEvaluator verdicts.

Cache key: ``sha256(goal_type | payload | response | golden_data)[:20]``
Cache file: ``<cache_dir>/redteam-judge-{sbom_key}.json``

Mirrors :class:`nuguard.behavior.judge_cache.JudgeCache` — same shape, same
content-addressed-per-SBOM design — adapted for the redteam evaluator's plain
verdict ``dict`` (no dataclass wrapper needed, so ``get``/``put`` operate on
dicts directly).

Usage::

    jcache = JudgeCache(cache_dir="/path/to/output", sbom_key="abc123")
    key = jcache.cache_key(goal_type, payload, response, golden_data)
    verdict = jcache.get(key)
    if verdict is None:
        verdict = await evaluator.evaluate(...)
        jcache.put(key, verdict)
"""
from __future__ import annotations

import hashlib
import json
import pathlib

from nuguard.common.logging import get_logger

_log = get_logger(__name__)


class JudgeCache:
    """Disk-backed cache for redteam judge verdict dicts.

    Args:
        cache_dir: Directory in which the cache file is stored. Pass an empty
            string or ``None`` to disable caching (all operations become
            no-ops).
        sbom_key: Key derived from the SBOM (or another stable per-target
            identifier). Used to scope the cache file so that changing the
            application automatically invalidates all cached verdicts.
    """

    def __init__(
        self,
        cache_dir: str | pathlib.Path | None = None,
        sbom_key: str = "default",
    ) -> None:
        self._enabled = bool(cache_dir)
        self._dir = pathlib.Path(cache_dir) if cache_dir else pathlib.Path(".")
        self._sbom_key = sbom_key
        self._store: dict[str, dict] = {}
        self._dirty = False
        if self._enabled:
            self._load_from_disk()

    # ------------------------------------------------------------------
    # Key
    # ------------------------------------------------------------------

    def cache_key(self, goal_type: str, payload: str, response: str, golden_data: str = "") -> str:
        """Stable content-addressed key for a single judge evaluation."""
        raw = f"{goal_type}|{payload}|{response}|{golden_data}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------

    def _path(self) -> pathlib.Path:
        return self._dir / f"redteam-judge-{self._sbom_key}.json"

    def _load_from_disk(self) -> None:
        path = self._path()
        if not path.exists():
            return
        try:
            self._store = json.loads(path.read_text(encoding="utf-8"))
            _log.info(
                "JudgeCache: loaded %d cached verdicts from %s", len(self._store), path
            )
        except Exception as exc:
            _log.warning("JudgeCache: failed to load cache (%s) — starting empty", exc)
            self._store = {}

    def flush(self) -> None:
        """Write the in-memory store to disk if it has been modified."""
        if not self._enabled or not self._dirty:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path().write_text(json.dumps(self._store, indent=2), encoding="utf-8")
            self._dirty = False
            _log.debug("JudgeCache: flushed %d entries to %s", len(self._store), self._path())
        except Exception as exc:
            _log.warning("JudgeCache: failed to flush (%s)", exc)

    # ------------------------------------------------------------------
    # Get / put
    # ------------------------------------------------------------------

    def get(self, key: str) -> dict | None:
        """Return a cached verdict dict, or ``None`` on miss / disabled."""
        if not self._enabled:
            return None
        return self._store.get(key)

    def put(self, key: str, verdict: dict) -> None:
        """Store a verdict dict and mark the cache as dirty."""
        if not self._enabled:
            return
        try:
            self._store[key] = verdict
            self._dirty = True
        except Exception as exc:
            _log.debug("JudgeCache: failed to store entry key=%s (%s)", key, exc)
