"""Chat models configured in Spring Boot / Quarkus application config files.

The LangChain4j, Quarkus LangChain4j and Spring AI starters build the chat model from
``application.properties`` / ``application.yml``, so the Java source never contains the model
name and :class:`JavaAIAdapter` cannot see it. This module reads the known keys instead.
"""

from __future__ import annotations

import re
from collections.abc import Generator, Iterable, Iterator
from contextlib import closing
from pathlib import PurePosixPath
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, Node, SequenceNode
from yaml.resolver import BaseResolver

from nuguard.common.logging import get_logger

from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint

_CONFIG_NAME_RE = re.compile(r"(?i)^application(?:-[\w.-]+)?\.(?:properties|ya?ml)$")
_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_QUARKUS_CLIENT = r'(?:"[^"]+"|[\w-]+)'
_KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "langchain4j",
        re.compile(
            rf"^langchain4j\.(?P<provider>{_NAME})\.(?:streaming-)?chat-model"
            r"\.(?:model-name|model-id|deployment-name)$"
        ),
    ),
    (
        "quarkus-langchain4j",
        re.compile(
            rf"^quarkus\.langchain4j\.(?P<provider>{_NAME})\."
            rf"(?:{_QUARKUS_CLIENT}\.)?"
            r"(?:streaming-)?chat-model\.(?:model-name|model-id|deployment-name)$"
        ),
    ),
    (
        "spring-ai",
        re.compile(
            rf"^spring\.ai\.(?P<provider>{_NAME}(?:\.{_NAME})*?)\.chat\.options"
            r"\.(?:model|deployment-name)$"
        ),
    ),
)
_FRAMEWORK_FAMILY = {
    "langchain4j": {"langchain4j", "quarkus-langchain4j"},
    "quarkus-langchain4j": {"langchain4j", "quarkus-langchain4j"},
    "spring-ai": {"spring-ai"},
}
_PROVIDER_ALIASES = {
    "openai": "openai",
    "azureopenai": "azure-openai",
    "anthropic": "anthropic",
    "bedrock": "aws-bedrock",
    "bedrockconverse": "aws-bedrock",
    "googleaigemini": "google",
    "vertexaigemini": "google",
    "mistralai": "mistral",
    "ollama": "ollama",
}
_PLACEHOLDER_RE = re.compile(r"^\$\{[^:}]+:(?P<default>[^}]*)\}$")
_MODEL_NAME_RE = re.compile(r"^\w[\w.:/@+-]{1,119}$")
_PROP_SPACE = " \t\f"
_PROP_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}

_PROFILE_RE = re.compile(r"^%[\w-]+(?:,[\w-]+)*\.")


_log = get_logger(__name__)
_YAML_MAX_VISITS = 50_000
_YAML_MAX_DEPTH = 128


class _YamlWorkLimit(yaml.YAMLError):
    """The configuration file exceeded its YAML extraction work budget."""


class _YamlBudget:
    """One budget shared by all documents and all alias references in a file."""

    def __init__(self, *, reject_cycles: bool = False) -> None:
        self.remaining = _YAML_MAX_VISITS
        self.reject_cycles = reject_cycles

    def visit(self, depth: int) -> None:
        if depth > _YAML_MAX_DEPTH:
            raise _YamlWorkLimit("YAML depth limit exceeded")
        if self.remaining <= 0:
            raise _YamlWorkLimit("YAML work limit exceeded")
        self.remaining -= 1


def _check_yaml_work(
    node: Node,
    budget: _YamlBudget,
    ancestors: frozenset[int] = frozenset(),
) -> None:
    """Check alias expansion before constructors can expand YAML merge keys."""
    budget.visit(len(ancestors))
    if id(node) in ancestors:
        if budget.reject_cycles:
            raise _YamlWorkLimit("YAML recursive alias")
        return
    next_ancestors = ancestors | {id(node)}
    if isinstance(node, MappingNode):
        for key, value in node.value:
            _check_yaml_work(key, budget, next_ancestors)
            _check_yaml_work(value, budget, next_ancestors)
    elif isinstance(node, SequenceNode):
        for value in node.value:
            _check_yaml_work(value, budget, next_ancestors)


class _MarkedMapping(dict[Any, Any]):
    """Mapping that records the source line of each effective key."""

    def __init__(self) -> None:
        super().__init__()
        self.key_lines: dict[Any, int] = {}


class _MarkedSafeLoader(yaml.SafeLoader):
    """SafeLoader variant that preserves mapping-key source lines."""


def _construct_marked_mapping(
    loader: Any,
    node: Any,
) -> Iterator[_MarkedMapping]:
    mapping = _MarkedMapping()
    yield mapping

    # Preserve SafeLoader's normal YAML merge-key handling.
    loader.flatten_mapping(node)

    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        try:
            hash(key)
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from exc

        mapping[key] = loader.construct_object(value_node)
        mapping.key_lines[key] = key_node.start_mark.line + 1


_MarkedSafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_marked_mapping,
)


def is_java_app_config_name(name: str) -> bool:
    """True for ``application.properties``, ``application-dev.yml`` and similar names."""
    return bool(_CONFIG_NAME_RE.match(name))


def _normalize_key(key: str) -> str:
    key = _PROFILE_RE.sub("", key.strip())
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", key).lower().replace("_", "-")


def _resolve_value(raw: str) -> str | None:
    """Return the model name, using the default of ``${ENV:default}``; None if unresolvable."""
    value = raw.strip().strip("\"'").strip()
    placeholder = _PLACEHOLDER_RE.match(value)
    if placeholder:
        value = placeholder.group("default").strip()
    if "${" in value or "#{" in value or not _MODEL_NAME_RE.match(value):
        return None
    return value


def _split_property(logical: str) -> tuple[str, str] | None:
    """Split a logical properties line into raw key and value, honoring ``\\`` escapes."""
    size = len(logical)
    index = 0
    while index < size and logical[index] in _PROP_SPACE:
        index += 1
    start = index
    while index < size and logical[index] not in "=:" + _PROP_SPACE:
        index += 2 if logical[index] == "\\" else 1
    key = logical[start:index]
    while index < size and logical[index] in _PROP_SPACE:
        index += 1
    if index < size and logical[index] in "=:":
        index += 1
        while index < size and logical[index] in _PROP_SPACE:
            index += 1
    return (key, logical[index:]) if key else None


def _unescape_properties(text: str) -> str:
    """Decode Java properties escapes; raise ValueError on a malformed unicode escape."""
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if char != "\\":
            out.append(char)
        elif index < len(text):
            char = text[index]
            index += 1
            if char == "u":
                digits = text[index : index + 4]
                if len(digits) != 4 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                    raise ValueError("malformed unicode escape")
                out.append(chr(int(digits, 16)))
                index += 4
            else:
                out.append(_PROP_ESCAPES.get(char, char))
    return "".join(out)


def _properties_entries(content: str) -> Iterator[tuple[int, str, str]]:
    # Java natural lines end at CR, LF or CRLF, not at form-feed whitespace.
    lines = iter(enumerate(re.split(r"\r\n|\r|\n", content), start=1))
    for lineno, line in lines:
        if not line.strip(" \t\f") or line.lstrip(" \t\f").startswith(("#", "!")):
            continue
        fragments: list[str] = []
        while True:
            continued = (len(line) - len(line.rstrip("\\"))) % 2 == 1
            fragments.append(line[:-1] if continued else line)
            if not continued:
                break
            try:
                _, line = next(lines)
            except StopIteration:
                break
            line = line.lstrip(" \t\f")
        entry = _split_property("".join(fragments))
        if entry:
            yield lineno, entry[0], entry[1]


def _flatten(
    node: Any,
    prefix: str = "",
    line: int = 1,
    ancestors: frozenset[int] | None = None,
    budget: _YamlBudget | None = None,
) -> Iterator[tuple[int, str, str]]:
    if ancestors is None:
        ancestors = frozenset()
    if budget is None:
        budget = _YamlBudget()
    budget.visit(len(ancestors))

    if isinstance(node, dict):
        node_id = id(node)
        if node_id in ancestors:
            return
        next_ancestors = ancestors | {node_id}
        key_lines = node.key_lines if isinstance(node, _MarkedMapping) else {}
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten(value, path, key_lines.get(key, line), next_ancestors, budget)
    elif isinstance(node, list):
        node_id = id(node)
        if node_id in ancestors:
            return
        next_ancestors = ancestors | {node_id}
        for index, value in enumerate(node):
            path = f"{prefix}.{index}" if prefix else str(index)
            yield from _flatten(value, path, line, next_ancestors, budget)
    elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
        yield line, prefix, str(node)


def _load_yaml_documents(content: str, budget: _YamlBudget) -> Generator[Any, None, None]:
    loader = _MarkedSafeLoader(content)
    try:
        while loader.check_node():
            node = loader.get_node()
            if node is not None:
                _check_yaml_work(node, budget)
                yield loader.construct_document(node)
    finally:
        loader.dispose()


def application_yaml_allows_scan(content: str, file_path: str) -> bool:
    """Preflight the whole file before any unbounded downstream YAML reader.

    Unlike the model-only walker, other adapters may not handle cycles. Reject
    cyclic, malformed or over-budget input for the entire file; safe YAML still
    follows the ordinary adapter pipeline. No values are included in diagnostics.
    """
    budget = _YamlBudget(reject_cycles=True)
    try:
        with closing(_load_yaml_documents(content, budget)) as documents:
            for document in documents:
                for _ in _flatten(document, budget=budget):
                    pass
    except _YamlWorkLimit as exc:
        _log.warning("java_config: skipping unsafe YAML file %s: %s", file_path, exc)
        return False
    except (yaml.YAMLError, RecursionError, ValueError, AttributeError):
        _log.warning("java_config: skipping invalid YAML file %s", file_path)
        return False
    return True


def _entries(content: str, file_path: str) -> Iterator[tuple[int, str, str]]:
    if file_path.lower().endswith(".properties"):
        # java.util.Properties keeps the last value of a repeated key.
        latest: dict[str, tuple[int, str, str]] = {}
        for line, raw_key, raw_value in _properties_entries(content):
            try:
                key, value = _unescape_properties(raw_key), _unescape_properties(raw_value)
            except ValueError:
                continue
            latest.pop(key, None)
            latest[key] = (line, key, value)
        yield from latest.values()
        return
    budget = _YamlBudget()
    try:
        with closing(_load_yaml_documents(content, budget)) as documents:
            for document in documents:
                yield from _flatten(document, budget=budget)
    except _YamlWorkLimit as exc:
        _log.warning("java_config: stopped YAML model extraction for %s: %s", file_path, exc)
    except (yaml.YAMLError, RecursionError, ValueError, AttributeError):
        return


def extract_java_config_models(content: str, file_path: str) -> list[ComponentDetection]:
    """Emit one MODEL detection per chat-model key found in an application config file."""
    detections: list[ComponentDetection] = []
    for line, raw_key, raw_value in _entries(content, file_path):
        key = _normalize_key(raw_key)
        for framework, pattern in _KEY_PATTERNS:
            match = pattern.match(key)
            if not match:
                continue
            model = _resolve_value(raw_value)
            if model is None:
                break
            provider = match.group("provider").lower()
            compact = re.sub(r"[^a-z0-9]", "", provider)
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    # Preserve the exact value in a namespace ordinary slug keys
                    # cannot enter. The merge stage must not normalize this key.
                    canonical_name=f"config_model:{model}",
                    display_name=model,
                    adapter_name="java_config",
                    priority=20,
                    confidence=0.9,
                    metadata={
                        "framework": framework,
                        "provider": _PROVIDER_ALIASES.get(compact, provider),
                        "language": "java",
                        "source": "java_config",
                        "config_key": key,
                    },
                    file_path=file_path,
                    line=line,
                    snippet=f"{raw_key}={raw_value}"[:160],
                    evidence_kind="regex",
                )
            )
            break
    return detections


def _config_scope(config_path: str) -> str:
    """Source root a config file applies to: ``<module>/src/main`` for ``src/main/resources``."""
    parts = PurePosixPath(config_path.replace("\\", "/")).parts[:-1]
    for index in range(len(parts) - 1, 1, -1):
        if parts[index] == "resources" and parts[index - 2] == "src":
            return "/".join(parts[:index])
    return "/".join(parts)


def link_config_models(
    node_map: dict[tuple[ComponentType, str], Any],
    detections: Iterable[ComponentDetection],
) -> int:
    """Add AGENT -USES-> MODEL hints from config-derived models to same-source-root agents.

    Only agents of the matching framework family whose evidence lies under the directory the
    config file applies to are linked. Returns the number of hints added.
    """
    agents = [acc for acc in node_map.values() if acc.component_type == ComponentType.AGENT]
    added = 0
    for det in detections:
        canonical = det.canonical_name
        if (ComponentType.MODEL, canonical) not in node_map:
            continue
        scope = _config_scope(det.file_path)
        family = _FRAMEWORK_FAMILY.get(str(det.metadata.get("framework")), set())
        for agent in agents:
            if agent.metadata.get("framework") not in family:
                continue
            paths = [
                ev.location.path.replace("\\", "/")
                for ev in agent.evidence
                if ev.location and ev.location.path
            ]
            if not any(not scope or path.startswith(scope + "/") for path in paths):
                continue
            if any(
                hint.source_canonical == agent.canonical_name
                and hint.target_canonical == canonical
                and hint.relationship_type == "USES"
                for hint in agent.relationships
            ):
                continue
            agent.relationships.append(
                RelationshipHint(
                    source_canonical=agent.canonical_name,
                    source_type=ComponentType.AGENT,
                    target_canonical=canonical,
                    target_type=ComponentType.MODEL,
                    relationship_type="USES",
                )
            )
            added += 1
    return added
