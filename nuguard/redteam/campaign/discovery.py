"""Campaign discovery: a clean baseline kept separate from adversarial probing.

The legacy ``run_capability_discovery`` mixes both in one conversation (a
developer-mode primer, capability probes, then an "exit developer mode" closer
treated as a reset). Campaign mode splits them:

* :func:`run_clean_baseline` — one benign, on-topic warm-up that doubles as the
  capability baseline. It never primes a debug persona.
* :func:`run_adversarial_discovery` — the developer-mode/tools/sub-agents/
  system-prompt probes, run on a branch the caller treats as *tainted*. Replies
  are recorded as ``claimed`` adversarial observations; no closing message is
  sent because "exit developer mode" cannot prove state was restored.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from nuguard.common import discovery as _d

from .knowledge import KnowledgeItem, KnowledgeStore, Scope, TrustLevel

SendFn = Callable[[str], Awaitable[str]]

DEFAULT_BENIGN_WARMUP = (
    "Hi! In a sentence or two, what can you help me with, and what kinds of "
    "requests should I send you?"
)


def _usable(reply: str) -> bool:
    return bool(reply) and not reply.startswith(("[HTTP ", "[REQUEST_ERROR:", "[CONFIG_ERROR:"))


async def run_clean_baseline(
    send: SendFn,
    store: KnowledgeStore,
    scope: Scope,
    *,
    message: str = DEFAULT_BENIGN_WARMUP,
    elapsed_ms: Callable[[], float] | None = None,
) -> bool:
    """One benign on-topic turn; stores the reply as a ``claimed`` capability baseline.

    Returns True when the target answered usefully (the branch is then ``ready``).
    """
    reply = await send(message)
    if not _usable(reply):
        return False
    store.add(KnowledgeItem(
        "capability_summary", "baseline", reply[:2000], TrustLevel.CLAIMED, scope,
        "baseline", 0.5, ("baseline:warmup",), untrusted_text=True,
    ))
    if elapsed_ms is not None:
        store.add(KnowledgeItem(
            "latency_ms", "baseline", elapsed_ms(), TrustLevel.OBSERVED, scope,
            "baseline", 0.9, ("baseline:warmup",),
        ))
    return True


async def run_adversarial_discovery(
    send: SendFn,
    store: KnowledgeStore,
    scope: Scope,
    *,
    want_tools: bool = True,
    want_subagents: bool = True,
    want_system_prompt: bool = True,
    objective_ref: str = "discovery",
) -> list[str]:
    """Run the adversarial capability probes on a (to-be-tainted) branch.

    Returns the probe names that produced a non-refusal answer. Everything is
    recorded as ``claimed`` + ``untrusted_text`` with the originating objective.
    """
    probes: list[tuple[str, str]] = [("developer_mode", _d._DEVELOPER_MODE_PRIMER)]
    if want_tools:
        probes.append(("tools", _d._TOOLS_PROBE))
    if want_subagents:
        probes.append(("subagents", _d._SUBAGENTS_PROBE))
    if want_system_prompt:
        probes.append(("system_prompt", _d._SYSTEM_PROMPT_PROBE))

    answered: list[str] = []
    for name, message in probes:
        reply = await send(message)
        if not _usable(reply):
            continue
        refused = name != "developer_mode" and _d._is_refusal(reply)
        store.add(KnowledgeItem(
            "refusal" if refused else "probe_reply", name, reply[:4000], TrustLevel.CLAIMED,
            scope, "adversarial", 0.4, (f"probe:{name}",), objective_ref, untrusted_text=True,
        ))
        if not refused:
            answered.append(name)
    return answered
