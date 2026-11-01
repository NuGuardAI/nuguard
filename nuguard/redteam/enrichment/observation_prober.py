"""Observation-channel passive tap (redteam-proposal.md W7, catalog L-series).

Runs *alongside* the normal scenario dispatch rather than in sequence: once
the W1 ASM has found an unauthenticated-reachable WS/SSE channel
(:attr:`AsmObservationChannel.connect_auth_required` is ``False``), this
module holds a passive connection — sending nothing — for a short window
and checks whether anything received contains a structured identifier.

That check is deliberately simpler than literal timestamp-correlation
against the scan's own traffic (one way the proposal describes proving
cross-session leakage, not the only one): the scanner's connection never
sends a single byte, so *any* identifier-shaped value arriving on it did
not originate from this scan's own requests — it is cross-session/other-
user leakage by construction, not an echo of something the scanner itself
sent.
"""
from __future__ import annotations

import re
import uuid

from nuguard.common.id_extractor import extract_ids
from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.target.ws_client import connect_and_listen

from .asm_models import AsmObservationChannel

_log = get_logger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

DEFAULT_LISTEN_SECONDS = 3.0
DEFAULT_MAX_CHANNELS = 2


async def run_observation_pass(
    channels: list[AsmObservationChannel],
    duration_s: float = DEFAULT_LISTEN_SECONDS,
    max_channels: int = DEFAULT_MAX_CHANNELS,
) -> dict[str, list[str]]:
    """Passively listen on up to *max_channels* unauthenticated channels.

    Only channels the ASM already confirmed reachable without a credential
    are worth listening to — one that requires auth has nothing to prove
    here (W03/W01 already report the auth-enforcement fact itself).
    Best-effort: never raises, returns ``{}`` on no reachable channels.
    """
    results: dict[str, list[str]] = {}
    candidates = [c for c in channels if not c.connect_auth_required][:max_channels]
    for channel in candidates:
        events = await connect_and_listen(channel.url, duration_s=duration_s)
        if events:
            results[channel.url] = events
            _log.info(
                "Observation pass: %d event(s) received on unauthenticated channel %s",
                len(events), channel.url,
            )
    return results


def build_observation_findings(events_by_channel: dict[str, list[str]]) -> list[Finding]:
    """L02 — a structured identifier on a passively-listened, nothing-sent
    connection is cross-session leakage proof."""
    findings: list[Finding] = []
    for url, events in events_by_channel.items():
        leaked: set[str] = set()
        for event in events:
            leaked.update(extract_ids(event))
            leaked.update(_EMAIL_RE.findall(event))
        if not leaked:
            continue
        findings.append(
            Finding(
                finding_id=f"obs-l02-{uuid.uuid4().hex[:8]}",
                title=f"Cross-Session Broadcast Leakage ({url})",
                severity=Severity.HIGH,
                description=(
                    "An unauthenticated observation channel broadcast identifier-"
                    "shaped data to a connection that never sent anything of its "
                    "own — the data did not originate from this scan's traffic."
                ),
                evidence=f"Leaked identifiers: {sorted(leaked)[:10]} | channel: {url}",
                reasoning=(
                    "Passive-tap connection sent zero bytes; any identifier in the "
                    "received stream is another session's/user's data (W7)."
                ),
                affected_component=url,
                authorization_decision="allow",
                guardrail_control="none",
            )
        )
    return findings
