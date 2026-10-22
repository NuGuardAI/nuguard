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
_SPRING_BOOT_GROUP = "org.springframework.boot"
_SPRING_BOOT_PARENTS = {"spring-boot-starter-parent", "spring-boot-dependencies"}
# Safety limits for untrusted interpolation, independent of Maven build settings.
_MAX_PROPERTY_VALUE_CHARS = 16_384
_MAX_PROPERTY_DEPTH = 64
_MAX_PROPERTY_WORK = 1_000_000
_MAX_PROPERTY_REFERENCES = 50_000
_UNRESOLVED_PROPERTY = "${nuguard.unresolved}"
_MavenIdentity = tuple[str, str, str, str]


class _PropertyExpansionLimit(ValueError):
    """An interpolation is cyclic or would exceed its resource budget."""


class _PropertyBudget:
    def __init__(self) -> None:
        self.characters = _MAX_PROPERTY_WORK
        self.references = _MAX_PROPERTY_REFERENCES

    def consume(self, characters: int, references: int = 0) -> None:
        if characters > self.characters or references > self.references:
            raise _PropertyExpansionLimit
        self.characters -= characters
        self.references -= references


class _PropertyValues(dict[str, str]):
    """Raw properties sharing a work budget across one manifest, never pre-expanded."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        super().__init__(values or {})
        self.budget = _PropertyBudget()


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
    """Resolve acyclic references without allocating unbounded intermediate strings.

    An unsafe expression remains symbolic; returning an empty string could cause
    callers to substitute an unrelated managed/parent version. Literal fields
    remain usable even after this manifest's interpolation budget is exhausted.
    """
    if len(value) > _MAX_PROPERTY_VALUE_CHARS:
        return _UNRESOLVED_PROPERTY
    original = value.strip()
    budget = properties.budget if isinstance(properties, _PropertyValues) else _PropertyBudget()
    cache: dict[str, str] = {}

    def expand(text: str, active: frozenset[str]) -> str:
        if len(text) > _MAX_PROPERTY_VALUE_CHARS or len(active) > _MAX_PROPERTY_DEPTH:
            raise _PropertyExpansionLimit
        if not _PLACEHOLDER_RE.search(text):
            return text
        budget.consume(len(text))
        parts: list[str] = []
        length = 0
        end = 0

        def append(piece: str) -> None:
            nonlocal length
            if len(piece) > _MAX_PROPERTY_VALUE_CHARS - length:
                raise _PropertyExpansionLimit
            budget.consume(len(piece))
            parts.append(piece)
            length += len(piece)

        for match in _PLACEHOLDER_RE.finditer(text):
            budget.consume(0, 1)
            append(text[end:match.start()])
            key = match.group(1) or match.group(2)
            raw = properties.get(key, match.group(0))
            if raw == match.group(0):
                # Unknown and deliberately symbolic profile properties stay intact.
                replacement = raw
            elif key in active:
                raise _PropertyExpansionLimit
            elif key in cache:
                replacement = cache[key]
            else:
                replacement = expand(raw, active | {key})
                cache[key] = replacement
            append(replacement)
            end = match.end()
        append(text[end:])
        return "".join(parts)

    try:
        return expand(original, frozenset()).strip()
    except _PropertyExpansionLimit:
        return original


def _concrete_version(value: str) -> str | None:
    version = value.strip()
    if not version or "${" in version or "$" in version:
        return None
    if version in {"LATEST", "RELEASE", "latest.release", "latest.integration"}:
        return None
    # Gradle prefix selectors share this helper; build metadata such as 1.0+build
    # is still a literal version, while 1.+ and 1.0+ require repository resolution.
    if version.endswith("+"):
        return None
    if version.startswith("[") or version.startswith("(") or "," in version:
        return None
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+\-]*", version):
        return version
    return None


def _version_spec(value: str) -> str:
    concrete = _concrete_version(value)
    return f"=={concrete}" if concrete else value.strip()


def _purl(
    group: str,
    artifact: str,
    version: str,
    *,
    dependency_type: str = "jar",
    classifier: str = "",
) -> str:
    namespace = quote(group.strip(), safe=".")
    name = quote(artifact.strip(), safe="._-")
    concrete = _concrete_version(version)
    base = f"pkg:maven/{namespace}/{name}"
    if concrete:
        base += f"@{quote(concrete, safe='._+-')}"
    qualifiers = {}
    if classifier:
        qualifiers["classifier"] = classifier
    if dependency_type != "jar":
        qualifiers["type"] = dependency_type
    if qualifiers:
        base += "?" + "&".join(
            f"{key}={quote(value, safe='._-')}" for key, value in sorted(qualifiers.items())
        )
    return base


def _maven_purl_identity(purl: str) -> str:
    """Strip only the version; Maven coordinates and variant qualifiers are case-sensitive."""
    coordinate, separator, qualifiers = purl.partition("?")
    return coordinate.split("@", 1)[0] + separator + qualifiers


def _record(
    group: str,
    artifact: str,
    version: str,
    dependency_group: str,
    source_file: str,
    *,
    dependency_type: str = "jar",
    classifier: str = "",
) -> dict[str, str] | None:
    group = group.strip()
    artifact = artifact.strip()
    if not group or not artifact:
        return None
    return {
        "name": f"{group}:{artifact}",
        "version_spec": _version_spec(version),
        "purl": _purl(
            group, artifact, version, dependency_type=dependency_type, classifier=classifier,
        ),
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


def _maven_profiles(project: ET.Element) -> list[ET.Element]:
    profiles = _child(project, "profiles")
    return _children(profiles, "profile") if profiles is not None else []


def _maven_identity(dependency: ET.Element, properties: dict[str, str]) -> _MavenIdentity:
    """Maven management matches declared type/classifier, not artifact extension."""
    return (
        _resolve(_text(dependency, "groupId"), properties),
        _resolve(_text(dependency, "artifactId"), properties),
        _resolve(_text(dependency, "type") or "jar", properties),
        _resolve(_text(dependency, "classifier"), properties),
    )


def _maven_declared_version(
    dependency: ET.Element,
    properties: dict[str, str],
    managed_versions: dict[_MavenIdentity, str],
    boot_version: str,
) -> str:
    """Apply the same limited offline lookup to every competing declaration."""
    version = _resolve(_text(dependency, "version"), properties)
    if version:
        return version
    identity = _maven_identity(dependency, properties)
    # The Boot shortcut only knows the ordinary, unclassified JAR. Variants need
    # an explicit or exact local management entry, not an assumed Boot version.
    return managed_versions.get(identity, "") or (
        boot_version if identity[0] == _SPRING_BOOT_GROUP and identity[2:] == ("jar", "") else ""
    )


def _profile_version_uncertain(
    project: ET.Element,
    dependency: ET.Element,
    properties: dict[str, str],
    managed_versions: dict[_MavenIdentity, str],
    boot_version: str,
) -> bool:
    """Do not select a build-profile-dependent version without activation context.

    This offline scanner cannot know -P, settings.xml or environment activation.
    Even activeByDefault profiles can be disabled. Direct profile declarations
    can override direct versions; profile management only affects unversioned
    declarations. A profile BOM import is opaque offline, so it only counts when it could
    be the BOM behind this dependency's version; a concrete local entry outranks it.
    """
    identity = _maven_identity(dependency, properties)
    group, _artifact, dependency_type, classifier = identity
    if any(_PLACEHOLDER_RE.search(part) for part in identity):
        return True
    explicit = bool(_text(dependency, "version"))
    current_version = _concrete_version(
        _maven_declared_version(dependency, properties, managed_versions, boot_version)
    )

    def may_equal(value: str, expected: str) -> bool:
        resolved = _resolve(value, properties)
        return resolved == expected or bool(_PLACEHOLDER_RE.search(resolved))

    def may_match(candidate: ET.Element) -> bool:
        return all(
            actual == expected or bool(_PLACEHOLDER_RE.search(actual))
            for actual, expected in zip(_maven_identity(candidate, properties), identity)
        )

    def import_may_supply(candidate: ET.Element) -> bool:
        # BOM contents are unreadable offline. A profile import matters only if it could be
        # the BOM behind this dependency's version: the dependency itself, or the Spring
        # Boot BOM for a Spring Boot dependency (the only BOM the fallback consults).
        bom_group = _text(candidate, "groupId")
        bom_artifact = _text(candidate, "artifactId")
        if may_match(candidate):
            return True
        return (
            group == _SPRING_BOOT_GROUP
            and (dependency_type, classifier) == ("jar", "")
            and may_equal(bom_group, _SPRING_BOOT_GROUP)
            and may_equal(bom_artifact, "spring-boot-dependencies")
        )

    if not explicit:
        local_managed, _ = _maven_dependency_elements(project)
        for candidate in local_managed:
            candidate_identity = _maven_identity(candidate, properties)
            if (
                any(_PLACEHOLDER_RE.search(part) for part in candidate_identity)
                and may_match(candidate)
            ):
                return True
    for profile in _maven_profiles(project):
        managed, declared = _maven_dependency_elements(profile)
        for candidate in declared + ([] if explicit else managed):
            if candidate is dependency:
                continue
            if candidate in managed and (
                may_equal(_text(candidate, "type"), "pom")
                and may_equal(_text(candidate, "scope"), "import")
            ):
                if import_may_supply(candidate) and not any(
                    _maven_identity(entry, properties) == identity
                    and _concrete_version(_resolve(_text(entry, "version"), properties))
                    and not (
                        may_equal(_text(entry, "type"), "pom")
                        and may_equal(_text(entry, "scope"), "import")
                    )
                    for entry in local_managed
                ):
                    return True
                continue
            if not may_match(candidate):
                continue
            if candidate in managed:
                if _text(candidate, "version"):
                    return True
                continue
            # Profile declarations can obtain their version implicitly too.
            # An unresolved competitor cannot establish agreement; equal known
            # versions are safe even when their explicit/implicit forms differ.
            candidate_version = _concrete_version(
                _maven_declared_version(candidate, properties, managed_versions, boot_version)
            )
            if (
                _maven_identity(candidate, properties) != identity
                or candidate_version is None
                or candidate_version != current_version
            ):
                return True
    return False


def _maven_properties(project: ET.Element) -> dict[str, str]:
    parent = _child(project, "parent")
    group_id = _text(project, "groupId") or _text(parent, "groupId")
    artifact_id = _text(project, "artifactId")
    version = _text(project, "version") or _text(parent, "version")
    properties = _PropertyValues({
        "project.groupId": group_id,
        "pom.groupId": group_id,
        "project.artifactId": artifact_id,
        "pom.artifactId": artifact_id,
        "project.version": version,
        "pom.version": version,
    })
    properties_element = _child(project, "properties")
    if properties_element is not None:
        for child in properties_element:
            if isinstance(child.tag, str):
                properties[_local_name(child.tag)] = (child.text or "").strip()
    # Leave profile-controlled properties symbolic before resolving aliases.
    # Otherwise a nested reference can hide a profile override behind a literal.
    for profile in _maven_profiles(project):
        profile_properties = _child(profile, "properties")
        if profile_properties is not None:
            for entry in profile_properties:
                if isinstance(entry.tag, str):
                    name = _local_name(entry.tag)
                    properties[name] = "${" + name + "}"
    # Resolve lazily from the raw graph: eager in-place expansion can amplify
    # a cycle again when the dependency subsequently resolves its version.
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


def _spring_boot_version(
    project: ET.Element,
    properties: dict[str, str],
    imported_bom_versions: dict[tuple[str, str], str],
) -> str:
    """Return the limited offline Boot fallback, not an effective Maven model.

    Inherited management precedes imported BOM entries for overlapping artifacts.
    Explicit dependency and local managed versions are handled by the caller.
    A recognized parent with an unresolved version must not fall through to a BOM.
    """
    parent = _child(project, "parent")
    if parent is not None:
        group = _resolve(_text(parent, "groupId"), properties)
        artifact = _resolve(_text(parent, "artifactId"), properties)
        if _PLACEHOLDER_RE.search(group) or _PLACEHOLDER_RE.search(artifact):
            return ""
        if group == _SPRING_BOOT_GROUP and artifact in _SPRING_BOOT_PARENTS:
            return _resolve(_text(parent, "version"), properties)
    return imported_bom_versions.get((_SPRING_BOOT_GROUP, "spring-boot-dependencies"), "")


def _collapse_maven_declarations(records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Resolve same-POM duplicates by consensus, never by declaration order.

    Conflicting or partly unresolved versions yield one unversioned record.
    Independent manifests are not competing Maven profiles and stay separate
    here. Existing cross-manifest and Gradle selection remains unchanged.
    """
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for record in records:
        grouped.setdefault(
            (record["source_file"], _maven_purl_identity(record["purl"])), []
        ).append(record)
    result: list[dict[str, str]] = []
    for key in sorted(grouped):
        candidates = grouped[key]
        # Keep scope selection stable too, preferring runtime over narrower scopes.
        record = dict(min(candidates, key=lambda r: (r["group"] != "runtime", r["group"])))
        versions = {(r["purl"], r["version_spec"]) for r in candidates}
        if len(versions) > 1:
            record["purl"] = _maven_purl_identity(record["purl"])
            record["version_spec"] = ""
        result.append(record)
    return result


def _scan_pom(root: Path, path: Path) -> list[dict[str, str]]:
    try:
        project = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    properties = _maven_properties(project)
    managed, declared = _maven_dependency_elements(project)
    managed_versions: dict[_MavenIdentity, str] = {}
    imported_bom_versions: dict[tuple[str, str], str] = {}
    for dependency in managed:
        identity = _maven_identity(dependency, properties)
        group, artifact, dependency_type, classifier = identity
        version = _resolve(_text(dependency, "version"), properties)
        if group and artifact and version:
            scope = _resolve(_text(dependency, "scope"), properties)
            if dependency_type == "pom" and scope == "import" and not classifier:
                imported_bom_versions[(group, artifact)] = version
            else:
                managed_versions[identity] = version
    boot_version = _spring_boot_version(project, properties, imported_bom_versions)
    source = str(path.relative_to(root))
    result: list[dict[str, str]] = []
    for dependency in declared:
        group, artifact, dependency_type, classifier = _maven_identity(dependency, properties)
        version = _maven_declared_version(dependency, properties, managed_versions, boot_version)
        if _concrete_version(version) and _profile_version_uncertain(
            project, dependency, properties, managed_versions, boot_version
        ):
            version = ""
        scope = _resolve(_text(dependency, "scope"), properties)
        optional = _resolve(_text(dependency, "optional"), properties)
        item = _record(
            group, artifact, version, _maven_scope(scope, optional), source,
            dependency_type=dependency_type, classifier=classifier,
        )
        if item is not None:
            result.append(item)
    return _collapse_maven_declarations(result)


def _gradle_variables(path: Path, root: Path, content: str) -> dict[str, str]:
    variables: dict[str, str] = _PropertyValues()
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
        key = _maven_purl_identity(record["purl"])
        seen.setdefault(key, record)
    return list(seen.values())
