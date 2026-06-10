"""Load and merge threat-intelligence YAML feeds from the threat_intel package."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger("threat_intel.loader")

_FEEDS_DIR = Path(__file__).parent


def load_feeds(
    feed_paths: list[Path] | None = None,
    feed_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Load and merge all YAML threat-intel feeds.

    Parameters
    ----------
    feed_paths:
        Additional YAML paths to load beyond the built-in feeds.
    feed_ids:
        If provided, only load built-in feeds whose ``feed_id`` is in this list.
        Pass ``["builtin:*"]`` (or leave ``None``) to load all built-in feeds.

    Returns
    -------
    dict with merged keys:
      known_malicious_packages: {"npm": [...], "pypi": [...]}
      suspicious_lifecycle_patterns: [str, ...]
      suspicious_file_patterns: [{"pattern": ..., "context": ..., "severity": ...}, ...]
    """
    try:
        import yaml as _yaml  # type: ignore[import-untyped]
    except ImportError:
        _log.warning("PyYAML not available; threat-intel feeds will not be loaded")
        return _empty_result()

    merged: dict[str, Any] = _empty_result()

    # Built-in feeds
    all_ids_wanted = feed_ids is None or "builtin:*" in feed_ids
    for feed_file in sorted(_FEEDS_DIR.glob("*.yaml")):
        try:
            data: dict[str, Any] = _yaml.safe_load(feed_file.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            _log.warning("Failed to load threat-intel feed %s: %s", feed_file, exc)
            continue
        feed_id = data.get("feed_id", "")
        if not all_ids_wanted and feed_ids and feed_id not in feed_ids:
            continue
        _merge(merged, data)
        _log.debug("Loaded threat-intel feed: %s", feed_id or feed_file.name)

    # Extra paths supplied by caller
    for path in feed_paths or []:
        try:
            data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            _log.warning("Failed to load threat-intel feed %s: %s", path, exc)
            continue
        _merge(merged, data)

    return merged


def _empty_result() -> dict[str, Any]:
    return {
        "known_malicious_packages": {"npm": [], "pypi": []},
        "suspicious_lifecycle_patterns": [],
        "suspicious_file_patterns": [],
    }


def _merge(merged: dict[str, Any], feed: dict[str, Any]) -> None:
    """Merge one feed dict into the accumulated result."""
    packages = feed.get("known_malicious_packages") or {}
    for ecosystem, pkg_list in packages.items():
        if isinstance(pkg_list, list):
            dest = merged["known_malicious_packages"].setdefault(ecosystem, [])
            dest.extend(pkg_list)

    for pattern in feed.get("suspicious_lifecycle_patterns") or []:
        if isinstance(pattern, str) and pattern not in merged["suspicious_lifecycle_patterns"]:
            merged["suspicious_lifecycle_patterns"].append(pattern)

    for fp in feed.get("suspicious_file_patterns") or []:
        if isinstance(fp, dict):
            merged["suspicious_file_patterns"].append(fp)
