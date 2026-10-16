"""Shared "is this file under a test directory" path helper.

Before this module, the only such check in the codebase was the binary
``RegexAdapter.skip_path_parts`` gate (``base.py``) — usable only by legacy
regex adapters, not by ``FrameworkAdapter``/``TSFrameworkAdapter``
subclasses, which have to inspect ``file_path`` themselves. Factored out so
every adapter — regardless of base class — can share one directory-name
convention instead of re-declaring its own frozenset
(docs/sbom-accuracy-plan.md #3).
"""
from __future__ import annotations

from pathlib import PurePosixPath

TEST_PATH_PARTS: frozenset[str] = frozenset(
    {"tests", "test", "__tests__", "spec", "specs", "e2e"}
)


def looks_like_test_path(file_path: str) -> bool:
    """True when any path component of *file_path* names a test directory,
    or the filename itself is a ``*.test.ts``/``*.spec.ts``-style test file."""
    path = PurePosixPath(file_path.replace("\\", "/"))
    if set(path.parts) & TEST_PATH_PARTS:
        return True
    stem = path.stem
    return stem.endswith(".test") or stem.endswith(".spec")


JVM_TEST_SOURCE_DIRS: frozenset[str] = frozenset({"test", "testFixtures", "integrationTest"})


def is_jvm_test_source_path(file_path: str) -> bool:
    """True when *file_path* is under a Maven/Gradle test source root (``src/test``, ...).

    Stricter than :func:`looks_like_test_path`: it needs the ``src/<test-dir>`` pair, so a
    production package that happens to be named ``test`` is not flagged.
    """
    parts = PurePosixPath(file_path.replace("\\", "/")).parts
    return any(a == "src" and b in JVM_TEST_SOURCE_DIRS for a, b in zip(parts, parts[1:-1]))
