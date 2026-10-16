"""Scan-time discovery of LangChain4j AI service interfaces for the Java Semgrep rules.

Semgrep (OSS) analyses one file at a time, so a controller that calls an ``@AiService``
interface declared in another file cannot be recognised by a static rule. The scanner
therefore finds the interface names up front and adds typed sink patterns to a copy of the
bundled Java ruleset.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml

_SKIP_DIRS = frozenset({".git", ".gradle", "build", "node_modules", "out", "target"})
_MAX_BYTES = 1_000_000
_ARGS = r"(?:\((?:[^()]|\([^()]*\))*\))?"
_AI_SERVICE_RE = re.compile(
    r"@(?:[\w.]+\.)?(?:AiService|RegisterAiService)\b"
    + _ARGS
    + r"(?:\s*@[\w.]+"
    + _ARGS
    + r")*"
    + r"\s*(?:(?:public|protected|private|abstract|static)\s+)*interface\s+(?P<name>[A-Za-z_]\w*)"
)


def _java_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".java"):
                yield Path(dirpath) / name


def find_ai_service_interfaces(paths: Iterable[str | Path]) -> list[str]:
    """Names of interfaces annotated ``@AiService`` / ``@RegisterAiService`` under *paths*."""
    names: set[str] = set()
    for root in paths:
        for file in _java_files(Path(root)):
            try:
                if file.stat().st_size > _MAX_BYTES:
                    continue
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "AiService" not in text:
                continue
            names.update(match.group("name") for match in _AI_SERVICE_RE.finditer(text))
    return sorted(names)


def augment_java_rules(rules: dict[str, Any], interfaces: list[str]) -> dict[str, Any]:
    """Return a copy of the Java ruleset with typed AI-service sinks added.

    Adds ``(<Interface> $SVC).$M(...)`` to the prompt-injection sinks and
    ``return (<Interface> $SVC).$M(...);`` to the controller-response alternatives.
    """
    out = copy.deepcopy(rules)
    for rule in out.get("rules", []):
        if rule.get("id") == "java-ai-prompt-injection":
            sinks = rule["pattern-sinks"][0]["pattern-either"]
            sinks.extend({"pattern": f"({name} $SVC).$M(...)"} for name in interfaces)
        elif rule.get("id") == "java-ai-unvalidated-controller-response":
            alternatives = rule["patterns"][1]["pattern-either"]
            alternatives.extend(
                {"pattern": f"return ({name} $SVC).$M(...);"} for name in interfaces
            )
    return out


def write_augmented_java_rules(rules_file: Path, interfaces: list[str], directory: Path) -> Path:
    """Write the augmented copy of *rules_file* into *directory* (same file name)."""
    rules = yaml.safe_load(rules_file.read_text(encoding="utf-8"))
    target = directory / rules_file.name
    target.write_text(
        yaml.safe_dump(augment_java_rules(rules, interfaces), sort_keys=False), encoding="utf-8"
    )
    return target
