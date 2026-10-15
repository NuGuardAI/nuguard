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

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint

_CONFIG_NAME_RE = re.compile(r"(?i)^application(?:-[\w.-]+)?\.(?:properties|ya?ml)$")
_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)*"
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
            rf"^quarkus\.langchain4j\.(?P<provider>{_NAME})\.(?:[\w-]+\.)?"
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
_PROP_LINE_RE = re.compile(r"^\s*(?P<key>[^\s#!=:][^\s=:]*)\s*[=:]\s*(?P<value>.*?)\s*$")
_PROFILE_RE = re.compile(r"^%[\w-]+\.")


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


def _flatten(node: Any, prefix: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
        yield prefix, str(node)


def _yaml_line(content: str, key: str, raw: str) -> int:
    leaf = _normalize_key(key.rsplit(".", 1)[-1])
    lines = content.splitlines()
    for number, line in enumerate(lines, start=1):
        if raw in line and _normalize_key(line.split(":", 1)[0]) == leaf:
            return number
    return next((n for n, line in enumerate(lines, start=1) if raw in line), 1)


def _entries(content: str, file_path: str) -> Iterator[tuple[int, str, str]]:
    if file_path.lower().endswith(".properties"):
        yield from _properties_entries(content)
        return
    try:
        documents = list(yaml.safe_load_all(content))
    except yaml.YAMLError:
        return
    for document in documents:
        for key, raw in _flatten(document):
            yield _yaml_line(content, key, raw), key, raw


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
