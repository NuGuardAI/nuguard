"""Hash-based prompt cache for LLM-generated attack payloads.

The cache key is sha256(stable_sbom_json + stable_policy_json)[:16].
"Stable" means sorted keys so whitespace-only changes don't bust the cache.
Cache files live in OUTPUT_DIR as redteam-prompts-{cache_key}.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)



def _stable_sbom_str(sbom: AiSbomDocument) -> str:
    """Return a stable JSON string of the SBOM (sorted keys, no whitespace)."""
    try:
        raw = json.loads(sbom.model_dump_json())
        return json.dumps(raw, sort_keys=True, separators=(",", ":"))
    except Exception:
        return ""


def _stable_policy_str(policy: object | None) -> str:
    """Return a stable string representation of the policy."""
    if policy is None:
        return ""
    try:
        if hasattr(policy, "model_dump_json"):
            raw = json.loads(policy.model_dump_json())
            return json.dumps(raw, sort_keys=True, separators=(",", ":"))
        return str(policy)
    except Exception:
        return str(policy)


class PromptCache:
    """File-backed cache for LLM-generated attack payloads."""

    def __init__(self, output_dir: Path) -> None:
        self._dir = output_dir

    def cache_key(self, sbom: AiSbomDocument, policy: object | None) -> str:
        """sha256 of stable SBOM + policy serialisation, truncated to 16 hex chars."""
        combined = _stable_sbom_str(sbom) + _stable_policy_str(policy)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def path_for(self, cache_key: str) -> Path:
        return self._dir / f"redteam-prompts-{cache_key}.json"

    def load(self, cache_key: str) -> dict | None:
        """Return parsed cache dict if the file exists and is valid, else None."""
        path = self.path_for(cache_key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("cache_key") != cache_key:
                _log.debug("Cache key mismatch in %s — ignoring", path)
                return None
            _log.info("Prompt cache hit: %s", path)
            return data
        except Exception as exc:
            _log.warning("Failed to load prompt cache %s: %s", path, exc)
            return None

    def save(self, cache_key: str, scenarios: dict) -> Path:
        """Write scenarios dict to the cache file and return the path."""
        path = self.path_for(cache_key)
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_key": cache_key,
            "generated_at": datetime.now(UTC).isoformat(),
            "scenarios": scenarios,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _log.info("Prompt cache saved to %s", path)
        return path

    def get_payloads(self, cache_key: str, scenario_id: str) -> list[str] | None:
        """Return cached payloads for one scenario, or None if not cached."""
        data = self.load(cache_key)
        if data is None:
            return None
        return data.get("scenarios", {}).get(scenario_id, {}).get("payloads")
