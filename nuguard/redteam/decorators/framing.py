"""Framing (pretext) decorators (redteam-proposal.md W3 + W6).

The W3 pretext library (``nuguard.redteam.pretexts``) implements the same
``decorate(payload) -> payload`` shape as
:class:`~nuguard.redteam.decorators.base.PayloadDecorator`, so its
templates register directly into the W6 decorator pipeline here.
"""
from __future__ import annotations

from nuguard.redteam.pretexts import PRETEXT_DECORATORS

from .base import PayloadDecorator

FRAMING_DECORATORS: tuple[PayloadDecorator, ...] = PRETEXT_DECORATORS
