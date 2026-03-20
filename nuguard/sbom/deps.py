"""Dependency scanner: reads package manifests and emits ``PackageDep`` records.

Supported manifest formats
--------------------------
Python:
- ``pyproject.toml``  — PEP 621, Poetry, Hatch, uv
- ``requirements*.txt`` — pip freeze / hand-written
- ``setup.cfg``        — legacy ``install_requires``

JavaScript / TypeScript:
- ``package.json``    — dependencies, devDependencies, peerDependencies

The scanner is intentionally shallow: it reads *declared* dependencies, not the
full transitive closure.  For a complete lock-file SBOM combine this with
``pip-audit`` / ``cyclonedx-python`` (Python) or ``cyclonedx-npm`` (JS).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef,import-not-found]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class PackageDep(BaseModel):
    """A single declared package dependency (Python or JavaScript)."""

    model_config = ConfigDict(frozen=True)

    name: str  # normalised name: PEP 503 for Python, original for JS
    version_spec: str  # raw specifier string, e.g. ">=2.7,<3", "^18.0.0", or ""
    purl: str  # pkg:pypi/{name}@{ver}, pkg:npm/{name}@{ver}, etc.
    group: str  # "runtime" | "dev" | "optional:{name}" | "optional:peer"
    source_file: str  # relative path to the manifest where it was found

    @property
    def version(self) -> str | None:
        """Return a single pinned version when the spec is ``==X.Y.Z``."""
        m = re.match(r"==\s*([\w.\-+]+)", self.version_spec)
        return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[><=!~\[;\s]")
_COMMENT_RE = re.compile(r"#.*$")
_DIGIT_START = re.compile(r"\d")


def _normalise(name: str) -> str:
    """PEP 503 normalisation: lowercase, collapse separators to hyphens."""
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def _to_purl(name: str, spec: str) -> str:
    m = re.match(r"==\s*([\w.\-+]+)", spec.strip())
    ver = m.group(1) if m else None
    norm = _normalise(name)
    return f"pkg:pypi/{norm}@{ver}" if ver else f"pkg:pypi/{norm}"


def _parse_req_line(line: str, source: str, group: str) -> PackageDep | None:
    """Parse a single pip-style requirement line into a ``PackageDep``."""
    line = _COMMENT_RE.sub("", line).strip()
    if not line or line.startswith(("-r ", "-c ", "--", "#", "http://", "https://")):
        return None

    m = _SPLIT_RE.search(line)
    if m:
        raw_name = line[: m.start()].strip()
        spec = line[m.start() :].split(";")[0].strip()
    else:
        raw_name = line.strip()
        spec = ""

    if not raw_name or raw_name.startswith("-"):
        return None

    return PackageDep(
        name=_normalise(raw_name),
        version_spec=spec,
        purl=_to_purl(raw_name, spec),
        group=group,
        source_file=source,
    )


def _to_npm_purl(name: str, spec: str) -> str:
    """Build a ``pkg:npm/`` PURL for a JS/TS package.

    Scoped packages (``@scope/pkg``) are encoded with ``%40``:
    ``@langchain/core@0.3.0`` → ``pkg:npm/%40langchain/core@0.3.0``

    The version is only embedded in the PURL when *spec* resolves to a clean
    semver string, i.e. when stripping a single leading ``^`` or ``~`` leaves
    an ``X.Y.Z`` (with optional pre-release/build suffix).
    """
    encoded = ("%40" + name[1:]) if name.startswith("@") else name
    clean = re.sub(r"^[~^]", "", spec.strip())
    if re.match(r"^\d+(\.\d+){1,2}([-+][\w.\-]+)?$", clean):
        return f"pkg:npm/{encoded}@{clean}"
    return f"pkg:npm/{encoded}"


def _poetry_spec(ver: object) -> str:
    if isinstance(ver, str) and _DIGIT_START.match(ver):
        return f"=={ver}"
    if isinstance(ver, str):
        return ver
    if isinstance(ver, dict):
        return str(ver.get("version", ""))
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DependencyScanner:
    """Scan a project root directory and collect declared Python dependencies.

    Usage::

        scanner = DependencyScanner()
        deps = scanner.scan(Path("."))
        for dep in deps:
            print(dep.purl)
    """

    def scan(self, root: Path) -> list[PackageDep]:
        """Return deduplicated deps from all manifests under *root*.

        Priority (Python): ``pyproject.toml`` > ``requirements*.txt`` > ``setup.cfg``.
        JS deps from ``package.json`` are included under separate PURL keys so
        Python and JS packages with the same name never collide.

        Dedup key is the PURL without version (``pkg:pypi/requests``,
        ``pkg:npm/debug``) so ecosystem is always part of the key.
        """
        seen: dict[str, PackageDep] = {}
        for dep in [
            *self._scan_pyproject(root),
            *self._scan_requirements(root),
            *self._scan_setup_cfg(root),
            *self._scan_package_json(root),
        ]:
            # Strip version from PURL for dedup key so pkg:pypi/foo and
            # pkg:npm/foo are treated as distinct entries.
            key = dep.purl.split("@")[0] if "@" in dep.purl else dep.purl
            seen.setdefault(key, dep)
        return list(seen.values())

    # ------------------------------------------------------------------
    # Manifest parsers
    # ------------------------------------------------------------------

    def _scan_pyproject(self, root: Path) -> list[PackageDep]:
        """Parse ``pyproject.toml`` files found under *root*.

        Scans the root-level file first; then recursively finds any
        ``pyproject.toml`` files in sub-packages (skipping common
        virtual-environment / build directories).
        """
        _SKIP_DIRS = {
            ".venv",
            "venv",
            ".env",
            "env",
            "node_modules",
            ".git",
            "__pycache__",
            "site-packages",
            "dist",
            "build",
            ".tox",
        }
        if tomllib is None:
            return []  # type: ignore[unreachable]

        candidate_paths: list[Path] = []
        seen_abs: set[Path] = set()

        def _add(p: Path) -> None:
            if p in seen_abs or not p.exists():
                return
            rel_parts = p.relative_to(root).parts
            if any(part in _SKIP_DIRS for part in rel_parts[:-1]):
                return
            seen_abs.add(p)
            candidate_paths.append(p)

        # Root first (highest priority for dedup in scan())
        _add(root / "pyproject.toml")
        for p in sorted(root.rglob("pyproject.toml")):
            _add(p)

        deps: list[PackageDep] = []
        for path in candidate_paths:
            src = str(path.relative_to(root))
            try:
                data: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            project = data.get("project") if isinstance(data.get("project"), dict) else {}
            tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}

            # ── PEP 621 / setuptools / hatch ──────────────────────────────
            assert isinstance(project, dict)
            for spec in project.get("dependencies", []):
                if isinstance(spec, str):
                    dep = _parse_req_line(spec, src, "runtime")
                    if dep:
                        deps.append(dep)

            for grp, specs in project.get("optional-dependencies", {}).items():
                if isinstance(specs, list):
                    for spec in specs:
                        if isinstance(spec, str):
                            dep = _parse_req_line(spec, src, f"optional:{grp}")
                            if dep:
                                deps.append(dep)

            # ── Poetry ────────────────────────────────────────────────────
            assert isinstance(tool, dict)
            poetry = tool.get("poetry", {})
            if isinstance(poetry, dict):
                for pkg, ver in poetry.get("dependencies", {}).items():
                    if _normalise(pkg) == "python":
                        continue
                    spec = _poetry_spec(ver)
                    norm = _normalise(pkg)
                    deps.append(
                        PackageDep(
                            name=norm,
                            version_spec=spec,
                            purl=_to_purl(pkg, spec),
                            group="runtime",
                            source_file=src,
                        )
                    )
                for pkg, ver in poetry.get("dev-dependencies", {}).items():
                    spec = _poetry_spec(ver)
                    norm = _normalise(pkg)
                    deps.append(
                        PackageDep(
                            name=norm,
                            version_spec=spec,
                            purl=_to_purl(pkg, spec),
                            group="dev",
                            source_file=src,
                        )
                    )
                for grp, grp_data in poetry.get("group", {}).items():
                    if isinstance(grp_data, dict):
                        for pkg, ver in grp_data.get("dependencies", {}).items():
                            spec = _poetry_spec(ver)
                            norm = _normalise(pkg)
                            deps.append(
                                PackageDep(
                                    name=norm,
                                    version_spec=spec,
                                    purl=_to_purl(pkg, spec),
                                    group="dev"
                                    if grp in {"dev", "test", "lint"}
                                    else f"optional:{grp}",
                                    source_file=src,
                                )
                            )

            # ── uv dev-dependencies ───────────────────────────────────────
            uv = tool.get("uv", {})
            if isinstance(uv, dict):
                for spec in uv.get("dev-dependencies", []):
                    if isinstance(spec, str):
                        dep = _parse_req_line(spec, src, "dev")
                        if dep:
                            deps.append(dep)

        return deps

    def _scan_requirements(self, root: Path) -> list[PackageDep]:
        """Return deps from all requirements files found anywhere under *root*.

        Recursively globs for ``requirements*.txt`` (e.g. ``requirements.txt``,
        ``requirements-dev.txt``, ``python-backend/requirements.txt``) and
        ``requirements/*.txt`` (e.g. ``requirements/base.txt``).  Common
        virtual-environment and cache directories are skipped.
        """
        _SKIP_DIRS = {
            ".venv",
            "venv",
            ".env",
            "env",
            "node_modules",
            ".git",
            "__pycache__",
            "site-packages",
            "dist",
            "build",
            ".tox",
        }

        # Collect candidate paths (deduplicated, stable sort).
        seen_abs: set[Path] = set()
        candidate_paths: list[Path] = []

        def _add(p: Path) -> None:
            if p in seen_abs:
                return
            rel_parts = p.relative_to(root).parts
            if any(part in _SKIP_DIRS for part in rel_parts):
                return
            seen_abs.add(p)
            candidate_paths.append(p)

        # Pattern 1: requirements*.txt anywhere in tree
        for p in sorted(root.rglob("requirements*.txt")):
            _add(p)

        # Pattern 2: requirements/<name>.txt anywhere in tree (base.txt, prod.txt …)
        for p in sorted(root.rglob("requirements/*.txt")):
            _add(p)

        deps: list[PackageDep] = []
        for req_path in candidate_paths:
            relpath = str(req_path.relative_to(root))
            path_lower = relpath.lower()
            if any(kw in path_lower for kw in ("dev", "test", "ci", "lint")):
                group = "dev"
            else:
                group = "runtime"
            try:
                for line in req_path.read_text(encoding="utf-8").splitlines():
                    dep = _parse_req_line(line, relpath, group)
                    if dep:
                        deps.append(dep)
            except OSError:
                pass
        return deps

    def _scan_setup_cfg(self, root: Path) -> list[PackageDep]:
        path = root / "setup.cfg"
        if not path.exists():
            return []
        deps: list[PackageDep] = []
        in_section = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "install_requires" or stripped == "install_requires =":
                in_section = True
                continue
            if in_section:
                if stripped.startswith("[") or (stripped and not line[0].isspace()):
                    in_section = False
                    continue
                dep = _parse_req_line(stripped, "setup.cfg", "runtime")
                if dep:
                    deps.append(dep)
        return deps

    def _scan_package_json(self, root: Path) -> list[PackageDep]:
        """Parse ``package.json`` files and return npm deps with versions.

        Reads the standard dependency sections:

        - ``dependencies``     → group ``"runtime"``
        - ``devDependencies``  → group ``"dev"``
        - ``peerDependencies`` → group ``"optional:peer"``

        Recursively finds ``package.json`` files under *root*, skipping
        ``node_modules`` and other common non-project directories.

        Version strings like ``"^18.0.0"`` and ``"~1.2.3"`` are stored
        verbatim in ``version_spec``; a cleaned semver is embedded in the
        PURL when it resolves to ``X.Y.Z`` form.  Workspace references
        (``"workspace:*"``), file links (``"file:.."``) and git URLs are
        skipped as they carry no useful version info for an SBOM.
        """
        _SKIP_DIRS = {
            "node_modules",
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "dist",
            "build",
            ".tox",
        }
        _SKIP_PREFIXES = ("workspace:", "file:", "git+", "git://", "github:", "link:", "portal:")
        _GROUP_MAP = {
            "dependencies": "runtime",
            "devDependencies": "dev",
            "peerDependencies": "optional:peer",
        }

        seen_abs: set[Path] = set()
        candidate_paths: list[Path] = []

        def _add(p: Path) -> None:
            if p in seen_abs or not p.exists():
                return
            rel_parts = p.relative_to(root).parts
            if any(part in _SKIP_DIRS for part in rel_parts[:-1]):
                return
            seen_abs.add(p)
            candidate_paths.append(p)

        _add(root / "package.json")
        for p in sorted(root.rglob("package.json")):
            _add(p)

        deps: list[PackageDep] = []
        for path in candidate_paths:
            src = str(path.relative_to(root))
            try:
                data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for key, group in _GROUP_MAP.items():
                section = data.get(key)
                if not isinstance(section, dict):
                    continue
                for name, raw_ver in section.items():
                    if not isinstance(name, str) or not name.strip():
                        continue
                    spec = str(raw_ver).strip() if isinstance(raw_ver, str) else ""
                    if any(spec.startswith(p) for p in _SKIP_PREFIXES):
                        continue
                    deps.append(
                        PackageDep(
                            name=name,
                            version_spec=spec,
                            purl=_to_npm_purl(name, spec),
                            group=group,
                            source_file=src,
                        )
                    )
        return deps
