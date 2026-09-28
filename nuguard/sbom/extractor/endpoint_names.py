"""Short, unambiguous display names for detected API endpoints."""

from __future__ import annotations

import re
from collections import defaultdict

from nuguard.sbom.models import Node
from nuguard.sbom.normalization import normalize_display_name
from nuguard.sbom.types import ComponentType


def _route_context(route: str) -> list[str]:
    parts = [part for part in route.split("/") if part]
    while parts and (parts[0].lower() in {"api", "rest"} or re.fullmatch(r"v\d+", parts[0], re.I)):
        parts.pop(0)
    context = [
        normalize_display_name(part, ComponentType.API_ENDPOINT)
        for part in parts
        if not part.startswith(":") and not (part.startswith("{") and part.endswith("}"))
    ]
    if parts and (
        parts[-1].startswith(":") or (parts[-1].startswith("{") and parts[-1].endswith("}"))
    ):
        context.append("By Id")
    return context


def disambiguate_endpoint_names(nodes: list[Node]) -> None:
    """Add the shortest useful route context only to repeated endpoint names.

    The HTTP route stays in metadata and canonical identity. Single, clear
    handler names retain their existing display value for compatibility.
    """
    by_name: dict[str, list[Node]] = defaultdict(list)
    for node in nodes:
        if node.component_type == ComponentType.API_ENDPOINT:
            by_name[node.name.casefold()].append(node)

    occupied = {group[0].name.casefold() for group in by_name.values() if len(group) == 1}
    for group in (by_name[name] for name in sorted(by_name) if len(by_name[name]) > 1):
        context = {node.id: _route_context(node.metadata.endpoint or "") for node in group}
        for node in sorted(
            group,
            key=lambda item: (
                item.metadata.endpoint or "",
                item.metadata.method or "",
                str(item.id),
            ),
        ):
            base = node.name
            parts = context[node.id]
            for width in range(1, len(parts) + 1):
                candidate = f"{base} {' '.join(parts[:width])}"
                if candidate.casefold() in occupied:
                    continue
                node.name = candidate
                occupied.add(candidate.casefold())
                break
            else:
                # Identical routes can still arise from separate source files.
                # A short numeric suffix keeps the UI label usable when route
                # context cannot distinguish those nodes.
                number = 1
                candidate = f"{base} #{number}"
                while candidate.casefold() in occupied:
                    number += 1
                    candidate = f"{base} #{number}"
                node.name = candidate
                occupied.add(candidate.casefold())
