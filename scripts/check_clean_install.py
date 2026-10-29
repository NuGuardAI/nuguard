"""Check that every nuguard module imports from an install with no optional extras.

Run it after installing the built wheel into a clean environment, from a directory
outside the source tree, so the installed package is the one being checked::

    python /path/to/repo/scripts/check_clean_install.py

Exits non-zero if any module fails to import for a reason other than a third-party
package that is provided only by an optional extra.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

# Top-level third-party module -> optional extra that provides it. A missing module
# from this map is expected in a no-extras install. Anything else is a packaging bug.
OPTIONAL_MODULE_EXTRAS: dict[str, str] = {
    "mcp": "mcp",
    "multipart": "mcp",
    "starlette": "mcp",
    "smithery": "smithery",
    "pyshacl": "spdx",
    "rdflib": "spdx",
    "playwright": "browser",
    "ruamel": "browser",
}


@dataclass(frozen=True)
class ImportFailure:
    """A module that failed to import for an unexpected reason."""

    module: str
    error: str


def discover_modules(package_root: Path) -> list[str]:
    """Return dotted names for every module under ``package_root``, skipping test code.

    Args:
        package_root: Directory of the ``nuguard`` package.

    Returns:
        Sorted dotted module names such as ``nuguard.cli.main``.
    """
    names: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        parts = path.relative_to(package_root.parent).with_suffix("").parts
        if "tests" in parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return names


def is_expected_optional_failure(exc: ModuleNotFoundError) -> bool:
    """Return True when the missing module belongs to a known optional extra."""
    missing_top_level = (exc.name or "").split(".")[0]
    return missing_top_level in OPTIONAL_MODULE_EXTRAS


def check_modules(module_names: list[str]) -> list[ImportFailure]:
    """Import each module and collect unexpected import failures.

    Args:
        module_names: Dotted module names to import.

    Returns:
        One ``ImportFailure`` per module that failed for an unexpected reason.
    """
    failures: list[ImportFailure] = []
    for name in module_names:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if not is_expected_optional_failure(exc):
                failures.append(ImportFailure(name, repr(exc)))
        except Exception as exc:
            # Any other import-time failure is a packaging or code bug and must be reported.
            failures.append(ImportFailure(name, repr(exc)))
    return failures


def main() -> int:
    """Run the clean-install import check against the importable ``nuguard`` package."""
    import nuguard

    package_root = Path(nuguard.__file__).resolve().parent
    module_names = discover_modules(package_root)
    print(f"Checking {len(module_names)} nuguard modules imported from {package_root}")

    failures = check_modules(module_names)
    for failure in failures:
        print(f"FAIL {failure.module}: {failure.error}")
    if failures:
        return 1

    print("OK: all modules import without optional extras")
    return 0


if __name__ == "__main__":
    sys.exit(main())
