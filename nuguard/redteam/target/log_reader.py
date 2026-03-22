"""AppLogReader — tails app log files (or an in-memory buffer) during a redteam scan.

Provides per-scenario log context by reading new lines written since the last
mark() call.  Two implementations:

- FileLogReader  — tails one or more log files on disk
- BufferLogReader — wraps an in-memory list (e.g. AppRunner._stderr_lines)
"""
from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)

_MAX_LINES = 50  # max log lines to include per scenario


class FileLogReader:
    """Reads new lines appended to log files since the last mark() call."""

    def __init__(self, log_paths: list[Path]) -> None:
        self._paths = [p for p in log_paths if p.exists()]
        # Track byte position per file
        self._positions: dict[Path, int] = {}
        for p in self._paths:
            try:
                self._positions[p] = p.stat().st_size
            except OSError:
                self._positions[p] = 0

    def mark(self) -> None:
        """Record current file sizes — new lines after this call will be returned by read_new()."""
        for p in self._paths:
            try:
                self._positions[p] = p.stat().st_size
            except OSError:
                pass

    def read_new(self) -> list[str]:
        """Return lines written since the last mark(), across all watched files."""
        lines: list[str] = []
        for p in self._paths:
            pos = self._positions.get(p, 0)
            try:
                with p.open("rb") as f:
                    f.seek(pos)
                    raw = f.read()
                new_lines = raw.decode(errors="replace").splitlines()
                lines.extend(new_lines)
                self._positions[p] = pos + len(raw)
            except OSError:
                pass
        return lines[-_MAX_LINES:]

    @classmethod
    def from_sbom_summary(cls, summary: object, source_dir: Path) -> "FileLogReader | None":
        """Build a FileLogReader from SBOM summary log_paths resolved against source_dir."""
        log_path_strs: list[str] = getattr(summary, "log_paths", None) or []
        if not log_path_strs:
            return None
        resolved = []
        for rel in log_path_strs:
            p = source_dir / rel
            # accept glob-ish paths too — only use if file exists
            if p.exists():
                resolved.append(p)
        if not resolved:
            _log.debug("[log_reader] No log files found under %s for paths %s", source_dir, log_path_strs)
            return None
        _log.info("[log_reader] Watching %d log file(s): %s", len(resolved), resolved)
        return cls(resolved)


class BufferLogReader:
    """Reads new lines from an in-memory list (e.g. AppRunner._stderr_lines)."""

    def __init__(self, buffer: list[str]) -> None:
        self._buffer = buffer
        self._pos = len(buffer)

    def mark(self) -> None:
        """Record current buffer length."""
        self._pos = len(self._buffer)

    def read_new(self) -> list[str]:
        """Return lines appended since the last mark()."""
        new = self._buffer[self._pos:]
        self._pos = len(self._buffer)
        return new[-_MAX_LINES:]
