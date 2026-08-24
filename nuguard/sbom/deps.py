"""Dependency scanner: reads package manifests and emits ``PackageDep`` records.

Supported manifest formats
--------------------------
Python:
- ``pyproject.toml``  — PEP 621, Poetry, Hatch, uv
- ``requirements*.txt`` — pip freeze / hand-written
- ``setup.cfg``        — legacy ``install_requires``

JavaScript / TypeScript:
- ``package.json``    — dependencies, devDependencies, peerDependencies

C# / .NET:
- ``*.csproj`` — NuGet ``PackageReference`` entries
- ``packages.config`` — legacy NuGet package declarations
- ``Directory.Packages.props`` — central package management

The scanner is intentionally shallow: it reads *declared* dependencies, not the
full transitive closure.  For a complete lock-file SBOM combine this with
``pip-audit`` / ``cyclonedx-python`` (Python) or ``cyclonedx-npm`` (JS).
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
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
    """A single declared package dependency."""

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


class LifecycleScript(BaseModel):
    """A package lifecycle or build hook script extracted from manifests."""

    model_config = ConfigDict(frozen=True)

    name: str  # script name, e.g. "postinstall", "preinstall", "build-backend"
    body: str  # script body, truncated to 2000 chars
    source_file: str  # relative path to package.json / pyproject.toml / setup.py
    ecosystem: str  # "npm" | "python"
    phase: str  # "install-hook" | "build-hook" | "publish-hook"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[><=!~\[;\s]")
_COMMENT_RE = re.compile(r"#.*$")
_DIGIT_START = re.compile(r"\d")
_NUGET_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_MANIFEST_SKIP_DIRS = {
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


def _concrete_nuget_version(spec: str) -> str | None:
    """Return the exact version represented by a concrete NuGet specifier."""
    version = spec.strip()

    if version.startswith("=="):
        version = version[2:].strip()
    elif version.startswith("[") and version.endswith("]") and "," not in version:
        version = version[1:-1].strip()

    return version if _NUGET_VERSION_RE.fullmatch(version) else None


def _nuget_version_spec(raw_version: str) -> str:
    """Normalize concrete NuGet versions and preserve unresolved expressions."""
    version = raw_version.strip()
    concrete_version = _concrete_nuget_version(version)

    return f"=={concrete_version}" if concrete_version else version


def _to_nuget_purl(name: str, spec: str) -> str:
    """Build a ``pkg:nuget/`` PURL when *spec* contains a concrete version."""
    package_name = name.strip()
    concrete_version = _concrete_nuget_version(spec)

    if concrete_version:
        return f"pkg:nuget/{package_name}@{concrete_version}"

    return f"pkg:nuget/{package_name}"


def _xml_local_name(tag: str) -> str:
    """Return an XML tag or attribute name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def _xml_attr(element: ET.Element, name: str) -> str:
    """Read an XML attribute by local name, including namespaced attributes."""
    direct = element.attrib.get(name)
    if direct is not None:
        return direct.strip()
    for key, value in element.attrib.items():
        if _xml_local_name(key) == name:
            return value.strip()
    return ""


def _xml_child_text(element: ET.Element, name: str) -> str:
    """Read the text of a direct XML child by local name."""
    for child in element:
        if isinstance(child.tag, str) and _xml_local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _parse_xml(path: Path) -> ET.Element | None:
    """Parse *path*, returning ``None`` for unreadable or malformed XML."""
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None


def _poetry_spec(ver: object) -> str:
    if isinstance(ver, str) and _DIGIT_START.match(ver):
        return f"=={ver}"
    if isinstance(ver, str):
        return ver
    if isinstance(ver, dict):
        return str(ver.get("version", ""))
    return ""


def _root_first_sorted(root: Path, paths: list[Path], root_name: str) -> list[Path]:
    root_path = root / root_name
    ordered = sorted(set(paths))
    if root_path in ordered:
        ordered.remove(root_path)
        ordered.insert(0, root_path)
    return ordered


def _collect_manifest_candidates(root: Path) -> dict[str, list[Path]]:
    """Walk project tree once and bucket manifest paths by type."""
    pyproject_paths: list[Path] = []
    requirements_paths: list[Path] = []
    setup_cfg_paths: list[Path] = []
    package_json_paths: list[Path] = []
    csproj_paths: list[Path] = []
    packages_config_paths: list[Path] = []
    directory_packages_props_paths: list[Path] = []
    go_mod_paths: list[Path] = []

    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _MANIFEST_SKIP_DIRS]
        current = Path(current_root)
        in_requirements_dir = current.name == "requirements"

        for filename in filenames:
            path = current / filename
            if filename == "pyproject.toml":
                pyproject_paths.append(path)
            elif filename == "setup.cfg":
                setup_cfg_paths.append(path)
            elif filename == "package.json":
                package_json_paths.append(path)
            elif filename.endswith(".csproj"):
                csproj_paths.append(path)
            elif filename == "packages.config":
                packages_config_paths.append(path)
            elif filename == "Directory.Packages.props":
                directory_packages_props_paths.append(path)
            elif filename in ("go.mod", "go.sum"):
                go_mod_paths.append(path)
            elif filename.endswith(".txt") and (
                filename.startswith("requirements") or in_requirements_dir
            ):
                requirements_paths.append(path)

    return {
        "pyproject": _root_first_sorted(root, pyproject_paths, "pyproject.toml"),
        "requirements": sorted(set(requirements_paths)),
        "setup_cfg": _root_first_sorted(root, setup_cfg_paths, "setup.cfg"),
        "package_json": _root_first_sorted(root, package_json_paths, "package.json"),
        "csproj": sorted(set(csproj_paths)),
        "packages_config": _root_first_sorted(root, packages_config_paths, "packages.config"),
        "directory_packages_props": _root_first_sorted(
            root, directory_packages_props_paths, "Directory.Packages.props"
        ),
        "go_mod": _root_first_sorted(root, go_mod_paths, "go.mod"),
    }


def _to_go_purl(module: str, version: str) -> str:
    """Build a ``pkg:go/`` PURL for a Go module."""
    module_clean = module.strip()
    ver_clean = version.strip()
    if ver_clean:
        return f"pkg:go/{module_clean}@{ver_clean}"
    return f"pkg:go/{module_clean}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DependencyScanner:
    """Scan a project root directory and collect declared package dependencies.

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
        manifest_candidates = _collect_manifest_candidates(root)
        directory_package_versions = self._read_directory_package_versions(
            manifest_candidates["directory_packages_props"]
        )
        for dep in [
            *self._scan_pyproject(root, manifest_candidates["pyproject"]),
            *self._scan_requirements(root, manifest_candidates["requirements"]),
            *self._scan_setup_cfg(root, manifest_candidates["setup_cfg"]),
            *self._scan_package_json(root, manifest_candidates["package_json"]),
            *self._scan_csproj(
                root,
                manifest_candidates["csproj"],
                directory_package_versions,
            ),
            *self._scan_packages_config(root, manifest_candidates["packages_config"]),
            *self._scan_directory_packages_props(
                root,
                manifest_candidates["directory_packages_props"],
                directory_package_versions,
            ),
            *self._scan_go_mod(root, manifest_candidates["go_mod"]),
        ]:
            # Strip version from PURL for dedup key so pkg:pypi/foo and
            # pkg:npm/foo are treated as distinct entries.
            key = dep.purl.split("@")[0] if "@" in dep.purl else dep.purl
            if key.startswith("pkg:nuget/"):
                key = key.casefold()
            seen.setdefault(key, dep)
        return list(seen.values())

    # ------------------------------------------------------------------
    # Manifest parsers
    # ------------------------------------------------------------------

    def _scan_pyproject(
        self, root: Path, candidate_paths: list[Path] | None = None
    ) -> list[PackageDep]:
        """Parse ``pyproject.toml`` files found under *root*.

        Scans the root-level file first; then recursively finds any
        ``pyproject.toml`` files in sub-packages (skipping common
        virtual-environment / build directories).
        """
        if tomllib is None:
            return []  # type: ignore[unreachable]

        if candidate_paths is None:
            candidate_paths = _collect_manifest_candidates(root)["pyproject"]

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

    def _scan_requirements(
        self, root: Path, candidate_paths: list[Path] | None = None
    ) -> list[PackageDep]:
        """Return deps from all requirements files found anywhere under *root*.

        Recursively globs for ``requirements*.txt`` (e.g. ``requirements.txt``,
        ``requirements-dev.txt``, ``python-backend/requirements.txt``) and
        ``requirements/*.txt`` (e.g. ``requirements/base.txt``).  Common
        virtual-environment and cache directories are skipped.
        """
        if candidate_paths is None:
            candidate_paths = _collect_manifest_candidates(root)["requirements"]

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

    def _scan_setup_cfg(
        self, root: Path, candidate_paths: list[Path] | None = None
    ) -> list[PackageDep]:
        if candidate_paths is None:
            candidate_paths = _collect_manifest_candidates(root)["setup_cfg"]
        if not candidate_paths:
            return []

        deps: list[PackageDep] = []
        for path in candidate_paths:
            rel_path = str(path.relative_to(root))
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue

            in_section = False
            for line in lines:
                stripped = line.strip()
                if stripped == "install_requires" or stripped == "install_requires =":
                    in_section = True
                    continue
                if in_section:
                    if stripped.startswith("[") or (stripped and not line[0].isspace()):
                        in_section = False
                        continue
                    dep = _parse_req_line(stripped, rel_path, "runtime")
                    if dep:
                        deps.append(dep)
        return deps

    def _scan_package_json(
        self, root: Path, candidate_paths: list[Path] | None = None
    ) -> list[PackageDep]:
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
        _SKIP_PREFIXES = ("workspace:", "file:", "git+", "git://", "github:", "link:", "portal:")
        _GROUP_MAP = {
            "dependencies": "runtime",
            "devDependencies": "dev",
            "peerDependencies": "optional:peer",
        }

        if candidate_paths is None:
            candidate_paths = _collect_manifest_candidates(root)["package_json"]

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

    def _read_directory_package_versions(
        self,
        candidate_paths: list[Path],
    ) -> dict[Path, dict[str, tuple[str, str]]]:
        """Read central NuGet versions, keyed by props path and package ID."""
        versions_by_path: dict[Path, dict[str, tuple[str, str]]] = {}

        for path in candidate_paths:
            package_versions: dict[str, tuple[str, str]] = {}
            versions_by_path[path] = package_versions

            xml_root = _parse_xml(path)

            if xml_root is None:
                continue

            for element in xml_root.iter():
                if not isinstance(element.tag, str):
                    continue

                if _xml_local_name(element.tag) != "PackageVersion":
                    continue

                include_name = _xml_attr(
                    element,
                    "Include",
                )

                update_name = _xml_attr(
                    element,
                    "Update",
                )

                name = include_name or update_name

                raw_version = _xml_attr(
                    element,
                    "Version",
                ) or _xml_child_text(
                    element,
                    "Version",
                )

                if not name or not raw_version:
                    continue

                key = name.casefold()

                if update_name:
                    existing = package_versions.get(key)

                    # Update mutates an item already declared in this file.
                    # Imported props-chain evaluation remains out of scope.
                    if existing is not None:
                        package_versions[key] = (
                            existing[0],
                            raw_version,
                        )

                    continue

                package_versions.setdefault(
                    key,
                    (
                        name,
                        raw_version,
                    ),
                )

        return versions_by_path

    @staticmethod
    def _nearest_directory_package_versions(
        project_path: Path,
        versions_by_path: dict[Path, dict[str, tuple[str, str]]],
    ) -> dict[str, tuple[str, str]]:
        """Return the nearest ancestor ``Directory.Packages.props`` map."""
        matching_paths = [path for path in versions_by_path if path.parent in project_path.parents]
        if not matching_paths:
            return {}
        nearest = max(
            matching_paths,
            key=lambda path: len(path.parent.parts),
        )
        return versions_by_path[nearest]

    def _scan_csproj(
        self,
        root: Path,
        candidate_paths: list[Path] | None = None,
        directory_package_versions: (dict[Path, dict[str, tuple[str, str]]] | None) = None,
    ) -> list[PackageDep]:
        """Parse NuGet ``PackageReference`` entries from C# project files."""
        paths = candidate_paths
        versions_by_path = directory_package_versions

        if paths is None or versions_by_path is None:
            candidates = _collect_manifest_candidates(root)

            if paths is None:
                paths = candidates["csproj"]

            if versions_by_path is None:
                versions_by_path = self._read_directory_package_versions(
                    candidates["directory_packages_props"]
                )

        deps: list[PackageDep] = []

        for path in paths:
            xml_root = _parse_xml(path)

            if xml_root is None:
                continue

            src = str(path.relative_to(root))

            central_versions = self._nearest_directory_package_versions(
                path,
                versions_by_path,
            )

            references: dict[
                str,
                tuple[str, str],
            ] = {}

            for element in xml_root.iter():
                if not isinstance(element.tag, str):
                    continue

                if _xml_local_name(element.tag) != "PackageReference":
                    continue

                include_name = _xml_attr(
                    element,
                    "Include",
                )

                update_name = _xml_attr(
                    element,
                    "Update",
                )

                name = include_name or update_name

                if not name:
                    continue

                raw_version = _xml_attr(
                    element,
                    "Version",
                ) or _xml_child_text(
                    element,
                    "Version",
                )

                key = name.casefold()

                if update_name:
                    existing = references.get(key)

                    # Update mutates a reference already declared in this
                    # project. Imported reference evaluation is out of scope.
                    if existing is not None:
                        references[key] = (
                            existing[0],
                            raw_version or existing[1],
                        )

                    continue

                references.setdefault(
                    key,
                    (
                        name,
                        raw_version,
                    ),
                )

            for name, raw_version in references.values():
                if not raw_version:
                    central = central_versions.get(name.casefold())

                    raw_version = central[1] if central is not None else ""

                spec = _nuget_version_spec(raw_version)

                deps.append(
                    PackageDep(
                        name=name,
                        version_spec=spec,
                        purl=_to_nuget_purl(
                            name,
                            spec,
                        ),
                        group="runtime",
                        source_file=src,
                    )
                )

        return deps

    def _scan_packages_config(
        self,
        root: Path,
        candidate_paths: list[Path] | None = None,
    ) -> list[PackageDep]:
        """Parse legacy NuGet ``packages.config`` files."""
        if candidate_paths is None:
            candidate_paths = _collect_manifest_candidates(root)["packages_config"]

        deps: list[PackageDep] = []
        for path in candidate_paths:
            xml_root = _parse_xml(path)
            if xml_root is None:
                continue
            src = str(path.relative_to(root))
            for element in xml_root.iter():
                if not isinstance(element.tag, str):
                    continue
                if _xml_local_name(element.tag) != "package":
                    continue
                name = _xml_attr(element, "id")
                raw_version = _xml_attr(element, "version")
                if not name or not raw_version:
                    continue
                spec = _nuget_version_spec(raw_version)
                is_dev = _xml_attr(element, "developmentDependency").casefold() == "true"
                deps.append(
                    PackageDep(
                        name=name,
                        version_spec=spec,
                        purl=_to_nuget_purl(name, spec),
                        group="dev" if is_dev else "runtime",
                        source_file=src,
                    )
                )
        return deps

    def _scan_directory_packages_props(
        self,
        root: Path,
        candidate_paths: list[Path] | None = None,
        directory_package_versions: (dict[Path, dict[str, tuple[str, str]]] | None) = None,
    ) -> list[PackageDep]:
        """Emit dependencies declared by central package management files."""
        if candidate_paths is None:
            candidate_paths = _collect_manifest_candidates(root)["directory_packages_props"]
        if directory_package_versions is None:
            directory_package_versions = self._read_directory_package_versions(candidate_paths)

        deps: list[PackageDep] = []
        for path in candidate_paths:
            src = str(path.relative_to(root))
            package_versions = directory_package_versions.get(path, {})
            for name, raw_version in package_versions.values():
                spec = _nuget_version_spec(raw_version)
                deps.append(
                    PackageDep(
                        name=name,
                        version_spec=spec,
                        purl=_to_nuget_purl(name, spec),
                        group="runtime",
                        source_file=src,
                    )
                )
        return deps

    def _scan_go_mod(
        self, root: Path, candidate_paths: list[Path] | None = None
    ) -> list[PackageDep]:
        """Parse ``go.mod`` and ``go.sum`` files and return Go module dependencies.

        Parses both single-line require directives:
            require github.com/gin-gonic/gin v1.9.1
        And block require directives:
            require (
                github.com/google/uuid v1.3.0
            )
        Also parses ``go.sum`` for exact pinned module versions.
        """
        if candidate_paths is None:
            candidate_paths = _collect_manifest_candidates(root)["go_mod"]

        # Ensure go.sum paths are processed before go.mod so exact version pins win
        sorted_candidates = sorted(candidate_paths, key=lambda p: 0 if p.name == "go.sum" else 1)

        deps: list[PackageDep] = []
        for path in sorted_candidates:
            src = str(path.relative_to(root))
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue

            # Parse go.sum
            if path.name == "go.sum":
                for line in content.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        mod, ver = parts[0], parts[1].split("/go.mod")[0]
                        deps.append(
                            PackageDep(
                                name=mod,
                                version_spec=f"=={ver}",
                                purl=_to_go_purl(mod, ver),
                                group="runtime",
                                source_file=src,
                            )
                        )
                continue

            # Parse go.mod
            in_require_block = False
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("//"):
                    continue

                if line == "require (" or line.startswith("require ("):
                    in_require_block = True
                    continue

                if in_require_block:
                    if line == ")":
                        in_require_block = False
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        mod, ver = parts[0], parts[1]
                        group = "runtime" if "// indirect" not in line else "optional:indirect"
                        deps.append(
                            PackageDep(
                                name=mod,
                                version_spec=f"=={ver}",
                                purl=_to_go_purl(mod, ver),
                                group=group,
                                source_file=src,
                            )
                        )
                elif line.startswith("require "):
                    parts = line.split()
                    if len(parts) >= 3:
                        mod, ver = parts[1], parts[2]
                        group = "runtime" if "// indirect" not in line else "optional:indirect"
                        deps.append(
                            PackageDep(
                                name=mod,
                                version_spec=f"=={ver}",
                                purl=_to_go_purl(mod, ver),
                                group=group,
                                source_file=src,
                            )
                        )

        return deps

    # ------------------------------------------------------------------
    # Supply-chain extensions — no changes to existing scan() interface
    # ------------------------------------------------------------------

    def parse_lifecycle_scripts(self, root: Path) -> list[LifecycleScript]:
        """Return npm scripts and pyproject.toml/setup.py build hooks.

        All npm ``scripts`` entries are captured (not just install/publish
        hooks) — malicious patterns (pipe-to-shell, Bun download, credential
        access, eval/obfuscation — NGA-SC-012/013/014/016) are just as
        dangerous in a "build" or "postbuild" script as in "postinstall",
        and this keeps SBOM-time capture consistent with
        SupplyChainScanner._scan_lifecycle_from_files's raw-file fallback,
        which already scans every script regardless of phase. Only
        NGA-SC-011 (install hook makes a network request) is phase-gated,
        via the "install-hook" classification below.

        Python phases scanned: [build-system] build-backend,
        [tool.hatch.build.hooks.*], setup.py (presence only).
        """
        scripts: list[LifecycleScript] = []
        candidates = _collect_manifest_candidates(root)

        # ── npm scripts ──────────────────────────────────────────────
        _install_phases = {"preinstall", "install", "postinstall", "prepare"}
        _publish_phases = {"prepack", "postpack", "prepublish", "prepublishOnly", "publish"}
        for path in candidates["package_json"]:
            src = str(path.relative_to(root))
            try:
                data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            npm_scripts = data.get("scripts")
            if not isinstance(npm_scripts, dict):
                continue
            for phase, body in npm_scripts.items():
                if not isinstance(phase, str) or not isinstance(body, str):
                    continue
                phase_lower = phase.lower()
                if phase_lower in _install_phases:
                    hook_phase = "install-hook"
                elif phase_lower in _publish_phases:
                    hook_phase = "publish-hook"
                else:
                    hook_phase = "build-hook" if phase_lower == "build" else "other-script"
                scripts.append(
                    LifecycleScript(
                        name=phase,
                        body=body[:2000],
                        source_file=src,
                        ecosystem="npm",
                        phase=hook_phase,
                    )
                )

        # ── Python build hooks ────────────────────────────────────────
        scripts.extend(self.parse_python_build_hooks(root))
        return scripts

    def parse_python_build_hooks(self, root: Path) -> list[LifecycleScript]:
        """Extract Python build hooks from pyproject.toml and setup.py."""
        scripts: list[LifecycleScript] = []
        if tomllib is None:
            return scripts

        for path in _collect_manifest_candidates(root)["pyproject"]:
            src = str(path.relative_to(root))
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            build_sys = data.get("build-system", {})
            backend = build_sys.get("build-backend")
            if backend and isinstance(backend, str):
                scripts.append(
                    LifecycleScript(
                        name="build-backend",
                        body=backend[:2000],
                        source_file=src,
                        ecosystem="python",
                        phase="build-hook",
                    )
                )
            tool = data.get("tool", {})
            hatch = tool.get("hatch", {})
            for hook_name, hook_cfg in hatch.get("build", {}).get("hooks", {}).items():
                if isinstance(hook_cfg, dict):
                    body = json.dumps(hook_cfg)[:2000]
                    scripts.append(
                        LifecycleScript(
                            name=f"hatch.build.hooks.{hook_name}",
                            body=body,
                            source_file=src,
                            ecosystem="python",
                            phase="build-hook",
                        )
                    )

        # setup.py presence: flag for manual review
        for setup_py in root.rglob("setup.py"):
            if any(skip in setup_py.parts for skip in _MANIFEST_SKIP_DIRS):
                continue
            src = str(setup_py.relative_to(root))
            try:
                body = setup_py.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                continue
            scripts.append(
                LifecycleScript(
                    name="setup.py",
                    body=body,
                    source_file=src,
                    ecosystem="python",
                    phase="build-hook",
                )
            )
        return scripts

    def parse_lockfiles(self, root: Path) -> list[dict[str, str]]:
        """Return minimal integrity records from lockfiles.

        Reads: package-lock.json, pnpm-lock.yaml, uv.lock, poetry.lock.
        Returns dicts with keys: name, version, resolved_url, integrity_hash, ecosystem.
        """
        records: list[dict[str, str]] = []
        records.extend(self._parse_npm_lockfile(root))
        records.extend(self._parse_uv_lockfile(root))
        return records

    def _parse_npm_lockfile(self, root: Path) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        lock_path = root / "package-lock.json"
        if not lock_path.exists():
            return records
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return records
        packages = data.get("packages") or data.get("dependencies") or {}
        for pkg_key, pkg_data in packages.items():
            if not isinstance(pkg_data, dict):
                continue
            name = (
                pkg_key.removeprefix("node_modules/")
                if pkg_key.startswith("node_modules/")
                else pkg_key
            )
            if not name:
                continue
            records.append(
                {
                    "name": name,
                    "version": str(pkg_data.get("version", "")),
                    "resolved_url": str(pkg_data.get("resolved", "")),
                    "integrity_hash": str(pkg_data.get("integrity", "")),
                    "ecosystem": "npm",
                }
            )
        return records

    def _parse_uv_lockfile(self, root: Path) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        if tomllib is None:
            return records
        lock_path = root / "uv.lock"
        if not lock_path.exists():
            return records
        try:
            data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            return records
        for pkg in data.get("package", []):
            if not isinstance(pkg, dict):
                continue
            name = str(pkg.get("name", ""))
            version = str(pkg.get("version", ""))
            for src in (
                pkg.get("source", {}).values() if isinstance(pkg.get("source"), dict) else []
            ):
                records.append(
                    {
                        "name": name,
                        "version": version,
                        "resolved_url": str(src),
                        "integrity_hash": "",
                        "ecosystem": "python",
                    }
                )
                break
            else:
                if name:
                    records.append(
                        {
                            "name": name,
                            "version": version,
                            "resolved_url": "",
                            "integrity_hash": "",
                            "ecosystem": "python",
                        }
                    )
        return records
