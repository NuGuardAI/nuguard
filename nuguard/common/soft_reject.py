"""Shared contract for LLM-soft-rejected SBOM nodes.

Deterministic nodes rejected by LLM verification remain in the AI-SBOM for
recall, audit, and troubleshooting. Downstream consumers that treat nodes as
confirmed application components must use this module rather than duplicating
the metadata-key check.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TypeVar

SOFT_REJECT_FLAG = "llm_soft_rejected"

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class NodeCountPartition:
    """Effective and soft-rejected node counts by component type."""

    effective: dict[str, int]
    soft_rejected: dict[str, int]


def _member(
    value: object,
    name: str,
) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)

    return getattr(
        value,
        name,
        None,
    )


def _extras(
    node: object,
) -> Mapping[str, object]:
    metadata = _member(
        node,
        "metadata",
    )

    if metadata is None:
        return {}

    extras = _member(
        metadata,
        "extras",
    )

    if isinstance(extras, Mapping):
        return extras

    return {}


BULK_CATALOG_TRUNCATED_FLAG = "bulk_catalog_truncated"


def is_soft_rejected(
    node: object,
) -> bool:
    """Return whether verification rejected a retained deterministic node.

    Covers two independent flags with identical "keep for provenance, exclude
    from downstream counts/findings/scenarios" semantics: ``llm_soft_rejected``
    (LLM verification judged a deterministic node a likely false positive) and
    ``bulk_catalog_truncated`` (a node beyond the first few representative
    entries collapsed from a bulk data-catalog/fixture file — see
    ``nuguard.sbom.extractor.postprocess._collapse_bulk_catalog_files``).
    """
    extras = _extras(node)
    return extras.get(SOFT_REJECT_FLAG) is True or extras.get(BULK_CATALOG_TRUNCATED_FLAG) is True


def iter_effective_nodes(
    nodes: Iterable[T],
) -> Iterator[T]:
    """Yield nodes that downstream consumers may treat as confirmed."""
    for node in nodes:
        if not is_soft_rejected(node):
            yield node


def _component_type_name(
    node: object,
) -> str | None:
    component_type = _member(
        node,
        "component_type",
    )

    if component_type is None:
        return None

    enum_value = _member(
        component_type,
        "value",
    )
    raw_value = enum_value if enum_value is not None else component_type

    if not isinstance(raw_value, str):
        return None

    normalized = raw_value.strip()

    return normalized or None


def partition_node_counts(
    nodes: Iterable[object],
) -> NodeCountPartition:
    """Count effective and soft-rejected nodes without removing audit records."""
    effective: dict[str, int] = {}
    soft_rejected: dict[str, int] = {}

    for node in nodes:
        component_type = _component_type_name(node)

        if component_type is None:
            continue

        destination = soft_rejected if is_soft_rejected(node) else effective

        destination[component_type] = (
            destination.get(
                component_type,
                0,
            )
            + 1
        )

    return NodeCountPartition(
        effective=dict(sorted(effective.items())),
        soft_rejected=dict(sorted(soft_rejected.items())),
    )


__all__ = [
    "BULK_CATALOG_TRUNCATED_FLAG",
    "NodeCountPartition",
    "SOFT_REJECT_FLAG",
    "is_soft_rejected",
    "iter_effective_nodes",
    "partition_node_counts",
]
