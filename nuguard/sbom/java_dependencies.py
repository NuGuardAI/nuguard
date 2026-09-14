"""Declared Java dependency discovery for Maven and Gradle projects.

This module does not execute Maven or Gradle.  It parses common declarative
forms from ``pom.xml``, ``build.gradle``, ``build.gradle.kts``, and Gradle
version catalogs and returns dictionaries accepted by ``PackageDep``.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 fallback is exercised by project CI
    try:
        import tomli as tomllib  # type: ignore[no-redef,import-not-found]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_SKIP_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".mvn",
    "build",
    "dist",
    "node_modules",
    "out",
    "target",
    "venv",
    ".venv",
}
_GRADLE_CONFIG_GROUPS = {
    "implementation": "runtime",
    "api": "runtime",
    "compile": "runtime",
    "runtimeonly": "runtime",
    "annotationprocessor": "dev",
    "compileonly": "dev",
    "testimplementation": "dev",
    "testruntimeonly": "dev",
    "testcompileonly": "dev",
    "developmentonly": "dev",
}
_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][\w.]*)")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [
        child for child in element if isinstance(child.tag, str) and _local_name(child.tag) == name
    ]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    values = _children(element, name)
    return values[0] if values else None


def _text(element: ET.Element | None, name: str | None = None) -> str:
    if element is None:
        return ""
    target = _child(element, name) if name else element
    return (target.text or "").strip() if target is not None else ""


def _resolve(value: str, properties: dict[str, str]) -> str:
    previous = value.strip()
    for _ in range(8):
        current = _PLACEHOLDER_RE.sub(
            lambda match: properties.get(match.group(1) or match.group(2), match.group(0)),
            previous,
        )
        if current == previous:
            break
        previous = current
    return previous.strip()


def _concrete_version(value: str) -> str | None:
    version = value.strip()
    if not version or "${" in version or "$" in version:
        return None
    if version.startswith("[") or version.startswith("(") or "," in version:
        return None
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+\-]*", version):
        return version
    return None


def _version_spec(value: str) -> str:
    concrete = _concrete_version(value)
    return f"=={concrete}" if concrete else value.strip()


def _purl(group: str, artifact: str, version: str) -> str:
    namespace = quote(group.strip(), safe=".")
    name = quote(artifact.strip(), safe="._-")
    concrete = _concrete_version(version)
    base = f"pkg:maven/{namespace}/{name}"
    return f"{base}@{quote(concrete, safe='._+-')}" if concrete else base


def _record(
    group: str,
    artifact: str,
    version: str,
    dependency_group: str,
    source_file: str,
) -> dict[str, str] | None:
    group = group.strip()
    artifact = artifact.strip()
    if not group or not artifact:
        return None
    return {
        "name": f"{group}:{artifact}",
        "version_spec": _version_spec(version),
        "purl": _purl(group, artifact, version),
        "group": dependency_group,
        "source_file": source_file,
    }


def _manifest_paths(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    poms: list[Path] = []
    gradle_builds: list[Path] = []
    catalogs: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        current = Path(current_root)
        for filename in filenames:
            path = current / filename
            if filename == "pom.xml":
                poms.append(path)
            elif filename in {"build.gradle", "build.gradle.kts"}:
                gradle_builds.append(path)
            elif filename == "libs.versions.toml" and "gradle" in path.parts:
                catalogs.append(path)
    return sorted(poms), sorted(gradle_builds), sorted(catalogs)


def _maven_properties(project: ET.Element) -> dict[str, str]:
    parent = _child(project, "parent")
    group_id = _text(project, "groupId") or _text(parent, "groupId")
    artifact_id = _text(project, "artifactId")
    version = _text(project, "version") or _text(parent, "version")
    properties = {
        "project.groupId": group_id,
        "pom.groupId": group_id,
        "project.artifactId": artifact_id,
        "pom.artifactId": artifact_id,
        "project.version": version,
        "pom.version": version,
    }
    properties_element = _child(project, "properties")
    if properties_element is not None:
        for child in properties_element:
            if isinstance(child.tag, str):
                properties[_local_name(child.tag)] = (child.text or "").strip()
    for key, value in list(properties.items()):
        properties[key] = _resolve(value, properties)
    return properties


def _maven_dependency_elements(project: ET.Element) -> tuple[list[ET.Element], list[ET.Element]]:
    managed: list[ET.Element] = []
    declared: list[ET.Element] = []
    management = _child(project, "dependencyManagement")
    if management is not None:
        dependencies = _child(management, "dependencies")
        if dependencies is not None:
            managed.extend(_children(dependencies, "dependency"))
    direct = _child(project, "dependencies")
    if direct is not None:
        declared.extend(_children(direct, "dependency"))
    profiles = _child(project, "profiles")
    if profiles is not None:
        for profile in _children(profiles, "profile"):
            profile_dependencies = _child(profile, "dependencies")
            if profile_dependencies is not None:
                declared.extend(_children(profile_dependencies, "dependency"))
    return managed, declared


def _maven_scope(scope: str, optional: str) -> str:
    if optional.casefold() == "true":
        return "optional:maven"
    if scope.casefold() in {"test", "provided", "system"}:
        return "dev"
    if scope.casefold() == "runtime":
        return "runtime"
    return "runtime"


def _scan_pom(root: Path, path: Path) -> list[dict[str, str]]:
    try:
        project = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    properties = _maven_properties(project)
    managed, declared = _maven_dependency_elements(project)
    managed_versions: dict[tuple[str, str], str] = {}
    for dependency in managed:
        group = _resolve(_text(dependency, "groupId"), properties)
        artifact = _resolve(_text(dependency, "artifactId"), properties)
        version = _resolve(_text(dependency, "version"), properties)
        if group and artifact and version:
            managed_versions[(group, artifact)] = version
    source = str(path.relative_to(root))
    result: list[dict[str, str]] = []
    for dependency in declared:
        group = _resolve(_text(dependency, "groupId"), properties)
        artifact = _resolve(_text(dependency, "artifactId"), properties)
        version = _resolve(_text(dependency, "version"), properties)
        if not version:
            version = managed_versions.get((group, artifact), "")
        scope = _resolve(_text(dependency, "scope"), properties)
        optional = _resolve(_text(dependency, "optional"), properties)
        item = _record(group, artifact, version, _maven_scope(scope, optional), source)
        if item is not None:
            result.append(item)
    return result


def _gradle_variables(path: Path, root: Path, content: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    candidates = [root / "gradle.properties", path.parent / "gradle.properties"]
    for properties_path in candidates:
        if not properties_path.is_file():
            continue
        try:
            lines = properties_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "!")) or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            variables[key.strip()] = value.strip()
    assignment_re = re.compile(
        r"(?m)^\s*(?:(?:def|val|var|String)\s+)?"
        r"(?P<name>[A-Za-z_][\w.]*)\s*=\s*[\"'](?P<value>[^\"']+)[\"']"
    )
    for match in assignment_re.finditer(content):
        variables[match.group("name")] = match.group("value")
    return variables


def _gradle_group(configuration: str) -> str:
    lowered = configuration.casefold()
    if lowered in _GRADLE_CONFIG_GROUPS:
        return _GRADLE_CONFIG_GROUPS[lowered]
    if lowered.startswith("test") or "test" in lowered:
        return "dev"
    if lowered.endswith("implementation") or lowered.endswith("api"):
        return "runtime"
    return "optional:gradle"


def _scan_gradle_build(root: Path, path: Path) -> list[dict[str, str]]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    variables = _gradle_variables(path, root, content)
    source = str(path.relative_to(root))
    result: list[dict[str, str]] = []
    string_notation = re.compile(
        r"(?m)^\s*(?P<configuration>[A-Za-z_][\w]*)\s*"
        r"(?:\(\s*)?(?:(?:platform|enforcedPlatform)\s*\(\s*)?"
        r"(?P<quote>[\"'])(?P<coordinate>[^\"']+)(?P=quote)"
    )
    for match in string_notation.finditer(content):
        configuration = match.group("configuration")
        if configuration.casefold() not in _GRADLE_CONFIG_GROUPS and not any(
            token in configuration.casefold()
            for token in ("implementation", "runtime", "compile", "api", "test")
        ):
            continue
        coordinate = _resolve(match.group("coordinate"), variables)
        if coordinate.startswith(("project(", "files(", "fileTree(")):
            continue
        parts = coordinate.split(":")
        if len(parts) < 2:
            continue
        group, artifact = parts[0], parts[1]
        version = ":".join(parts[2:]) if len(parts) > 2 else ""
        item = _record(group, artifact, version, _gradle_group(configuration), source)
        if item is not None:
            result.append(item)
    map_notation = re.compile(
        r"(?m)^\s*(?P<configuration>[A-Za-z_][\w]*)\s*(?:\(\s*)?"
        r"group\s*[:=]\s*[\"'](?P<group>[^\"']+)[\"']\s*,\s*"
        r"(?:name|module)\s*[:=]\s*[\"'](?P<artifact>[^\"']+)[\"']"
        r"(?:\s*,\s*version\s*[:=]\s*[\"'](?P<version>[^\"']+)[\"'])?"
    )
    for match in map_notation.finditer(content):
        item = _record(
            _resolve(match.group("group"), variables),
            _resolve(match.group("artifact"), variables),
            _resolve(match.group("version") or "", variables),
            _gradle_group(match.group("configuration")),
            source,
        )
        if item is not None:
            result.append(item)
    return result


def _catalog_version(value: Any, versions: dict[str, Any]) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    direct = value.get("version")
    if isinstance(direct, str):
        return direct
    if isinstance(direct, dict):
        ref = direct.get("ref")
        if isinstance(ref, str):
            return str(versions.get(ref, ""))
    ref = value.get("version.ref")
    return str(versions.get(ref, "")) if isinstance(ref, str) else ""


def _catalog_table(value: object) -> dict[str, Any]:
    """Return a string-keyed TOML table, or an empty table for invalid shapes."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _scan_version_catalog(root: Path, path: Path) -> list[dict[str, str]]:
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    versions = _catalog_table(data.get("versions"))
    libraries = _catalog_table(data.get("libraries"))
    source = str(path.relative_to(root))
    result: list[dict[str, str]] = []
    for value in libraries.values():
        group = artifact = version = ""
        if isinstance(value, str):
            parts = value.split(":")
            if len(parts) >= 2:
                group, artifact = parts[:2]
                version = ":".join(parts[2:]) if len(parts) > 2 else ""
        elif isinstance(value, dict):
            module = value.get("module")
            if isinstance(module, str) and ":" in module:
                group, artifact = module.split(":", 1)
            else:
                group = str(value.get("group") or "")
                artifact = str(value.get("name") or "")
            version = _catalog_version(value, versions)
        item = _record(group, artifact, version, "runtime", source)
        if item is not None:
            result.append(item)
    return result


def scan_java_dependencies(root: Path) -> list[dict[str, str]]:
    """Return deduplicated Maven-PURL dependency dictionaries under *root*."""
    poms, gradle_builds, catalogs = _manifest_paths(root)
    records: list[dict[str, str]] = []
    for path in poms:
        records.extend(_scan_pom(root, path))
    for path in gradle_builds:
        records.extend(_scan_gradle_build(root, path))
    for path in catalogs:
        records.extend(_scan_version_catalog(root, path))
    seen: dict[str, dict[str, str]] = {}
    for record in records:
        key = record["purl"].split("@", 1)[0].casefold()
        seen.setdefault(key, record)
    return list(seen.values())
