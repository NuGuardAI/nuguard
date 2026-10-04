"""The ``PayloadDecorator`` interface (redteam-proposal.md W6).

A decorator is a **deterministic** ``str -> str`` transform — no LLM call —
applied to an already-built payload after a refusal, before falling back to
free-form LLM paraphrase. This is the "evasion matrix, done right" from the
proposal: encoding/framing/structure are decorators that compose with the
underlying attack goal, sampled on failure rather than enumerated upfront as
separate catalog scenarios.

This module only defines the interface and a small dataclass for weighted
selection; concrete decorators live in :mod:`.encodings` (this phase),
:mod:`.framing` and :mod:`.structure` (stubs — populated once the W3 pretext
library and the C05/C08 structure lift land in a later phase).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from nuguard.models.exploit_chain import ExploitChain, ExploitStep

DecoratorCategory = Literal["encoding", "framing", "structure"]


class PayloadDecorator(Protocol):
    """A named, deterministic payload transform.

    ``applies`` lets a decorator opt out of chains it would break (e.g. a
    decorator that changes a payload's byte-for-byte shape must not apply to
    a scenario whose success_signal is an exact canary string match).
    Takes ``ExploitChain``/``ExploitStep`` — what's actually available at
    the executor layer where decorators are invoked — rather than the
    ``AttackScenario`` wrapper; the coarser ``decorator_allowed`` opt-out is
    checked by the caller via ``ExploitChain.decorator_allowed`` before
    reaching here, so this is for a decorator's own narrower reasons.
    """

    name: str
    category: DecoratorCategory

    def applies(self, chain: "ExploitChain", step: "ExploitStep") -> bool: ...

    def decorate(self, payload: str) -> str: ...


@dataclass(frozen=True)
class WeightedDecorator:
    """One registry entry: a decorator plus its sampling weight."""

    decorator: PayloadDecorator
    weight: float = 1.0
