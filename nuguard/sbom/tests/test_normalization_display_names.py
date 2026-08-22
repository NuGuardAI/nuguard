"""Unit tests for normalize_display_name's CONTAINER_IMAGE handling.

Regression: "nginx:alpine" was being title-cased down to just "Alpine" —
the single-colon-word namespace-prefix stripping (intended for values like
"framework:openai_agents") collided with Docker "repo:tag" refs whose tag
happens to be a bare lowercase word.
"""

from __future__ import annotations

from nuguard.sbom.normalization import normalize_display_name
from nuguard.sbom.types import ComponentType


def test_nginx_alpine_keeps_repo_prefix() -> None:
    result = normalize_display_name("nginx:alpine", ComponentType.CONTAINER_IMAGE)
    assert "nginx" in result.lower()
    assert "alpine" in result.lower()


def test_node_20_alpine_still_correct() -> None:
    """Regression guard: this case already worked (digit blocks the strip)."""
    result = normalize_display_name("node:20-alpine", ComponentType.CONTAINER_IMAGE)
    assert result == "Node:20 Alpine"


def test_namespace_prefix_still_stripped_for_non_container_types() -> None:
    """The original stripping behavior (e.g. "framework:openai_agents") must
    be unaffected for component types other than CONTAINER_IMAGE."""
    result = normalize_display_name("framework:openai_agents", ComponentType.FRAMEWORK)
    assert result == "OpenAI Agents"
