"""Cross-run disk-backed cache for BehaviorJudge turn verdicts.

Cache key: ``sha256(scenario_type | request | response)[:20]``
Cache file: ``<cache_dir>/behavior-judge-{sbom_key}.json``

The cache is scoped to the SBOM key so verdicts are automatically invalidated
when the application (and therefore its SBOM) changes.  Within a single SBOM
version the key is content-addressed — identical (request, response,
scenario_type) tuples always return an identical cached verdict.

Usage::

    jcache = JudgeCache(cache_dir="/path/to/output", sbom_key="abc123")
    key = jcache.cache_key(request, response, scenario_type)
    verdict = jcache.get(key)
    if verdict is None:
        verdict = await judge.judge_turn(...)
        jcache.put(key, verdict)
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.behavior.judge import TurnVerdict

_log = logging.getLogger(__name__)


class JudgeCache:
    """Disk-backed cache for ``TurnVerdict`` objects.

    Args:
        cache_dir: Directory in which the cache file is stored.  Pass an empty
            string or ``None`` to disable caching (all operations become no-ops).
        sbom_key: 16-char hex key derived from the SBOM (same key as
            ``BehaviorPromptCache``).  Used to scope the cache file so that
            changing the application automatically invalidates all cached verdicts.
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

    def cache_key(self, request: str, response: str, scenario_type: str) -> str:
        """Stable content-addressed key for a single turn evaluation."""
        raw = f"{scenario_type}|{request}|{response}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    # ------------------------------------------------------------------
    # Disk I/O
    # ------------------------------------------------------------------

    def _path(self) -> pathlib.Path:
        return self._dir / f"behavior-judge-{self._sbom_key}.json"

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

    def get(self, key: str) -> "TurnVerdict | None":
        """Return a cached ``TurnVerdict`` or *None* on miss / disabled."""
        if not self._enabled:
            return None
        raw = self._store.get(key)
        if raw is None:
            return None
        try:
            from nuguard.behavior.judge import TurnVerdict
            return TurnVerdict(**raw)
        except Exception as exc:
            _log.debug("JudgeCache: failed to deserialise entry key=%s (%s)", key, exc)
            return None

    def put(self, key: str, verdict: "TurnVerdict") -> None:
        """Store a ``TurnVerdict`` and mark the cache as dirty."""
        if not self._enabled:
            return
        try:
            self._store[key] = verdict.to_dict()
            self._dirty = True
        except Exception as exc:
            _log.debug("JudgeCache: failed to store entry key=%s (%s)", key, exc)
