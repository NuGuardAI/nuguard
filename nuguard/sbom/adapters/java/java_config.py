"""Chat models configured in Spring Boot / Quarkus application config files.

The LangChain4j, Quarkus LangChain4j and Spring AI starters build the chat model from
``application.properties`` / ``application.yml``, so the Java source never contains the model
name and :class:`JavaAIAdapter` cannot see it. This module reads the known keys instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import PurePosixPath
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

from ...normalization import canonicalize_text
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
_PROP_LINE_RE = re.compile(r"^\s*(?P<key>[^\s#!=:][^\s=:]*)(?:\s*[=:]\s*|\s+)(?P<value>.*?)\s*$")

_PROFILE_RE = re.compile(r"^%[\w-]+\.")


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


def _properties_entries(content: str) -> Iterator[tuple[int, str, str]]:
    for lineno, line in enumerate(content.splitlines(), start=1):
        if not line.strip() or line.lstrip()[0] in "#!":
            continue
        match = _PROP_LINE_RE.match(line)
        if match:
            yield lineno, match.group("key"), match.group("value")


def _flatten(
    node: Any,
    prefix: str = "",
    line: int = 1,
    ancestors: frozenset[int] | None = None,
) -> Iterator[tuple[int, str, str]]:
    if ancestors is None:
        ancestors = frozenset()

    if isinstance(node, dict):
        node_id = id(node)
        if node_id in ancestors:
            return

        next_ancestors = ancestors | {node_id}
        key_lines = node.key_lines if isinstance(node, _MarkedMapping) else {}

        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten(
                value,
                path,
                key_lines.get(key, line),
                next_ancestors,
            )

    elif isinstance(node, list):
        node_id = id(node)
        if node_id in ancestors:
            return

        next_ancestors = ancestors | {node_id}
        for index, value in enumerate(node):
            path = f"{prefix}.{index}" if prefix else str(index)
            yield from _flatten(value, path, line, next_ancestors)

    elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
        yield line, prefix, str(node)


def _load_yaml_documents(content: str) -> Iterator[Any]:
    loader = _MarkedSafeLoader(content)
    try:
        while loader.check_data():
            yield loader.get_data()
    finally:
        loader.dispose()


def _entries(content: str, file_path: str) -> Iterator[tuple[int, str, str]]:
    if file_path.lower().endswith(".properties"):
        yield from _properties_entries(content)
        return

    try:
        for document in _load_yaml_documents(content):
            yield from _flatten(document)
    except (yaml.YAMLError, RecursionError):
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
                    canonical_name=canonicalize_text(model.lower()),
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
        canonical = canonicalize_text(det.canonical_name)
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
