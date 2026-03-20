"""Standard CycloneDX SBOM generator.

Following the reference architecture (Section B — Standard SBOM Generator),
this module generates a dependency-level CycloneDX 1.6 BOM that is later
merged with the AI-BOM extraction.

Resolution order
----------------
1. **cyclonedx-py CLI** (``cyclonedx-bom`` package, v4.x) — highest fidelity:
   transitive deps, license SPDX IDs, component hashes, CPE.
   Invoked as a subprocess so it runs in the same directory as the project
   and picks up the correct lock files.

2. **DependencyScanner fallback** — declared (shallow) deps only; always
   available because it uses stdlib + tomllib. Produces PURLs but no
   license or hash data.

The output is always a CycloneDX 1.6 BOM ``dict`` compatible with
``AiBomMerger.merge()``.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from .deps import DependencyScanner
from .serializer import AiSbomSerializer
from .models import AiSbomDocument

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"(\d+\.\d+[\w.\-]*)")


def _cdx_py_available() -> bool:
    """Return True if the ``cyclonedx-py`` CLI is on PATH."""
    try:
        r = subprocess.run(
            ["cyclonedx-py", "--version"],
            capture_output=True,
            timeout=10,
        )
        available = r.returncode == 0
        if available:
            version = r.stdout.decode(errors="replace").strip()
            _log.debug("cyclonedx-py found: %s", version)
        else:
            _log.debug("cyclonedx-py --version returned non-zero: %d", r.returncode)
        return available
    except FileNotFoundError:
        _log.debug("cyclonedx-py not found on PATH")
        return False
    except subprocess.TimeoutExpired:
        _log.warning("cyclonedx-py --version timed out")
        return False


def _run_cdx(args: list[str], cwd: Path) -> dict[str, Any] | None:
    """Run ``cyclonedx-py`` and return the parsed JSON BOM or *None* on failure."""
    cmd = ["cyclonedx-py", *args, "--of", "JSON", "--sv", "1.6", "-o", "-"]
    _log.debug("running: %s (cwd=%s)", " ".join(cmd), cwd)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=120,
        )
        if r.returncode == 0 and r.stdout.strip():
            try:
                bom: dict[str, Any] = json.loads(r.stdout)
                n = len(bom.get("components", []))
                _log.info("cyclonedx-py produced %d components via: %s", n, " ".join(args))
                return bom
            except json.JSONDecodeError as exc:
                _log.warning("cyclonedx-py output was not valid JSON: %s", exc)
                return None
        else:
            stderr = r.stderr.strip() if r.stderr else ""
            _log.warning(
                "cyclonedx-py exited %d for args %s%s",
                r.returncode,
                args,
                f": {stderr}" if stderr else "",
            )
            return None
    except FileNotFoundError:
        _log.error("cyclonedx-py disappeared from PATH mid-run")
        return None
    except subprocess.TimeoutExpired:
        _log.warning("cyclonedx-py timed out after 120 s for args: %s", args)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class CycloneDxGenerator:
    """Generate a standard CycloneDX BOM for a project directory.

    Usage::

        gen = CycloneDxGenerator()
        bom = gen.generate(Path("/path/to/project"))
        # bom is a CycloneDX 1.6 dict ready for AiBomMerger.merge()
    """

    def generate(self, root: Path) -> tuple[dict[str, Any], str]:
        """Return ``(bom_dict, method)`` where *method* describes which
        generator was used (``"cyclonedx-py/requirements"``,
        ``"cyclonedx-py/poetry"``, ``"dep-scanner"``, etc.).
        """
        if _cdx_py_available():
            bom, method = self._try_cdx_cli(root)
            if bom:
                return bom, method
            _log.warning(
                "cyclonedx-py is installed but no supported lock/requirements file "
                "found under %s — falling back to dep-scanner",
                root,
            )
        else:
            _log.warning(
                "cyclonedx-py not available (install with: pip install xelo[cdx]); "
                "using shallow dep-scanner fallback"
            )

        _log.info("running dep-scanner fallback for %s", root)
        return self._dep_scanner_fallback(root), "dep-scanner"

    # ------------------------------------------------------------------
    # cyclonedx-py CLI strategies
    # ------------------------------------------------------------------

    def _try_cdx_cli(self, root: Path) -> tuple[dict[str, Any] | None, str]:
        """Try the most appropriate cyclonedx-py subcommand for the project."""
        # Poetry — lock file present gives transitive deps
        if (root / "poetry.lock").exists():
            _log.info("poetry.lock detected — trying cyclonedx-py poetry")
            bom = _run_cdx(["poetry"], root)
            if bom:
                return bom, "cyclonedx-py/poetry"

        # Pipenv
        if (root / "Pipfile.lock").exists():
            _log.info("Pipfile.lock detected — trying cyclonedx-py pipenv")
            bom = _run_cdx(["pipenv"], root)
            if bom:
                return bom, "cyclonedx-py/pipenv"

        # requirements.txt variants (most common)
        # Pass the filename relative to root so it resolves correctly from cwd=root.
        for req_file in ("requirements.txt", "requirements/base.txt", "requirements/prod.txt"):
            if (root / req_file).exists():
                _log.info("%s detected — trying cyclonedx-py requirements", req_file)
                bom = _run_cdx(["requirements", req_file], root)
                if bom:
                    return bom, f"cyclonedx-py/requirements:{req_file}"
                _log.warning("cyclonedx-py requirements failed for %s", req_file)

        # pyproject.toml PEP 621 — cyclonedx-py can use --pyproject but still
        # needs a requirements file; we skip if none exists (dep-scanner covers it)
        return None, ""

    # ------------------------------------------------------------------
    # Fallback: DependencyScanner → minimal CycloneDX BOM
    # ------------------------------------------------------------------

    def _dep_scanner_fallback(self, root: Path) -> dict[str, Any]:
        """Build a minimal CycloneDX BOM from declared manifests only."""
        deps = DependencyScanner().scan(root)
        _log.info("dep-scanner found %d declared dependencies", len(deps))
        # Reuse serializer logic: pass empty doc + deps
        empty_doc = AiSbomDocument(target=root.name)
        bom = AiSbomSerializer.to_cyclonedx(empty_doc, deps=deps)
        # Remove empty AI-specific fields so the BOM reads as pure standard
        bom["components"] = [
            c for c in bom["components"] if c.get("purl", "").startswith("pkg:pypi/")
        ]
        bom["dependencies"] = []
        bom.setdefault("metadata", {})["properties"] = [
            {"name": "cdx:generator", "value": "xelo-dep-scanner"},
            {
                "name": "cdx:note",
                "value": "Shallow manifest scan only — install cyclonedx-bom for full SBOM",
            },
        ]
        return bom
