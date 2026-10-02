"""Framing (pretext) decorators — stub (redteam-proposal.md W3 + W6).

Populated once the pretext-template library (W3, Phase 4) lands; each
``PretextTemplate`` will implement the same ``decorate(payload) -> payload``
shape as :class:`~nuguard.redteam.decorators.base.PayloadDecorator` so it
can register here. Empty for now — this module exists so the registry's
import and the executor's decorator-category plumbing are in place before
Phase 4, not to be extended ad hoc later.
"""
from __future__ import annotations

from .base import PayloadDecorator

FRAMING_DECORATORS: tuple[PayloadDecorator, ...] = ()
