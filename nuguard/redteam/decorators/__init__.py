"""Composable payload decorators (redteam-proposal.md W6).

A :class:`~.base.PayloadDecorator` is a deterministic ``str -> str``
transform (encoding, framing, or structure) applied to an already-built
payload on failure, before falling back to free-form LLM paraphrase. See
:mod:`.base` for the interface and :mod:`.registry` for weighted selection.

This phase ships :mod:`.encodings` only; :mod:`.framing` and
:mod:`.structure` are empty stubs populated in later phases (W3 pretext
library, C05/C08 structure lift).
"""
from __future__ import annotations

from .base import DecoratorCategory, PayloadDecorator, WeightedDecorator
from .registry import DECORATOR_REGISTRY, select_decorator

__all__ = [
    "DECORATOR_REGISTRY",
    "DecoratorCategory",
    "PayloadDecorator",
    "WeightedDecorator",
    "select_decorator",
]
