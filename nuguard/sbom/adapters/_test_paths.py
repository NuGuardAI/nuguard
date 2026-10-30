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

import os
from pathlib import Path, PurePosixPath

TEST_PATH_PARTS: frozenset[str] = frozenset({"tests", "test", "__tests__", "spec", "specs", "e2e"})


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
    """Classify a file by its first conventional Maven/Gradle source set.

    Once inside ``src/main``, later ``src/test`` segments are package/resource
    names, not a new source root. Module prefixes before the source set remain
    supported. The final path component is a filename, not a directory.
    """
    parts = PurePosixPath(file_path.replace("\\", "/")).parts
    for parent, source_set in zip(parts, parts[1:-1]):
        if parent != "src":
            continue
        if source_set == "main":
            return False
        if source_set in JVM_TEST_SOURCE_DIRS:
            return True
    return False


_JVM_CONTENT_DIRS = frozenset({"java", "resources", "kotlin", "scala", "groovy"})
_JVM_PROJECT_MARKERS = (
    ".git",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
)


def jvm_source_path_prefix(root: Path) -> PurePosixPath:
    """Recover source-set context for a scan rooted inside conventional sources.

    Inspect only the selected root's ancestors, stopping at a build/VCS boundary.
    A recognized context is ``src``, ``src/<source-set>``, or a subtree of its
    standard language/resource directory. An arbitrary checkout under a directory
    named ``src/test`` does not inherit test status. Prefer the outer source set
    so production package names cannot masquerade as test roots.

    This prefix is used only for classification; evidence and exclusion patterns
    remain relative to the caller's scan root. Do not resolve symlinks here.
    """
    absolute_root = Path(os.path.abspath(root))
    source_root: Path | None = None
    for ancestor in (absolute_root, *absolute_root.parents):
        try:
            if any((ancestor / name).exists() for name in _JVM_PROJECT_MARKERS):
                break
        except OSError:
            # Inaccessible ancestors cannot establish trustworthy context.
            break
        if ancestor.name != "src":
            continue
        tail = absolute_root.relative_to(ancestor).parts
        if not tail or (
            (tail[0] == "main" or tail[0] in JVM_TEST_SOURCE_DIRS)
            and (len(tail) == 1 or tail[1] in _JVM_CONTENT_DIRS)
        ):
            source_root = ancestor
    if source_root is None:
        return PurePosixPath()
    return PurePosixPath(*absolute_root.relative_to(source_root.parent).parts)
