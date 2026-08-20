"""Pre-flight chat-endpoint validation shared by ``behavior`` and ``redteam``.

Both capabilities resolve a chat endpoint from the SBOM (or config) before
running any scenario, but static SBOM scoring can pick the wrong candidate
(e.g. an image-upload endpoint like ``/api/chat/respond-visual`` outscoring
the app's actual text-chat endpoint ``/api/chat``). Left unchecked, that
produces a run that silently 400/404/405s on every request without ever
tripping the transport circuit breaker (which only counts 5xx / network
failures — a 4xx means "target reachable, rejected our payload").

:func:`validate_and_rotate_chat_endpoint` sends one lightweight test request
against the currently configured endpoint and, on 400/404/405, rotates
through the SBOM's ranked chat-endpoint candidates (mutating *client* in
place via :meth:`~nuguard.redteam.target.client.TargetAppClient.set_chat_endpoint`),
falling back to a live :func:`~nuguard.common.endpoint_probe.probe_chat_endpoints`
scan if none of the SBOM candidates work either.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.redteam.target.client import TargetAppClient
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

_TEST_MESSAGE = "Hello"
# 404/405 mean the path itself is wrong. 400 and 422 are included too — a
# benign "Hello" test message rejected with a validation error strongly
# suggests the endpoint expects a different payload shape entirely (e.g. an
# image-upload route auto-selected over the real text-chat endpoint because
# it scored higher, or an unrelated domain endpoint like a letter-generator
# requiring fields we don't send), not that this specific request happened
# to be malformed. 422 is FastAPI/Pydantic's dedicated validation-error
# status (as opposed to 400, which apps also use for their own hand-rolled
# validation) and is just as strong a "wrong endpoint" signal.
_ROTATION_TRIGGER_PREFIXES = ("[HTTP 405]", "[HTTP 404]", "[HTTP 400]", "[HTTP 422]")


class PreflightOutcome(BaseModel):
    """Result of :func:`validate_and_rotate_chat_endpoint`.

    ``ok`` is ``True`` when *client* is left pointed at a chat endpoint that
    did not 400/404/405 on the test request (whether that was the original
    endpoint or a rotated one). ``rotated_endpoint`` is populated whenever
    the endpoint changed, so callers can update their own tracked
    ``chat_path``/``chat_payload_key``/... state to match.
    """

    ok: bool
    rotated_endpoint: "tuple[str, str, bool, str | None] | None" = None
    endpoint_source: 'Literal["sbom", "probe"] | None' = None
    notes: list[str] = Field(default_factory=list)


async def validate_and_rotate_chat_endpoint(
    client: "TargetAppClient",
    sbom: "AiSbomDocument | None",
    *,
    has_explicit_endpoint: bool,
    target_url: str = "",
    auth_headers: dict[str, str] | None = None,
) -> PreflightOutcome:
    """Send a test request to *client*'s current endpoint; rotate on 400/404/405.

    Args:
        client: Ready-to-use client (auth headers already set) whose chat
            endpoint is mutated in place on rotation.
        sbom: Parsed SBOM used to rank fallback candidates via
            :func:`~nuguard.common.endpoint_probe.discover_chat_candidates_from_sbom`.
        has_explicit_endpoint: When ``True``, a 400/404/405 is reported without
            attempting rotation — an explicitly configured endpoint takes
            precedence and silently substituting another one would be
            surprising.
        target_url: Base URL, forwarded to the live-probe fallback.
        auth_headers: Auth headers forwarded to the live-probe fallback.

    Returns:
        :class:`PreflightOutcome` — never raises; failures are reported via
        ``ok=False`` and ``notes``.
    """
    from nuguard.redteam.target.session import AttackSession as _PF_AS  # noqa: PLC0415

    notes: list[str] = []
    session = _PF_AS(session_id="preflight", target_url=target_url, chain_id="preflight")

    try:
        response, _ = await client.send(_TEST_MESSAGE, session)
    except Exception as exc:
        _log.debug("Pre-flight: test request failed (non-fatal): %s", exc)
        return PreflightOutcome(ok=True)

    if not response.startswith(_ROTATION_TRIGGER_PREFIXES):
        return PreflightOutcome(ok=True)

    _log.warning("Pre-flight: chat endpoint returned %s — attempting rotation", response[:15])

    if has_explicit_endpoint:
        note = (
            "Configured chat endpoint rejected the test request (400/404/405). Explicit "
            "endpoint precedence is enforced; no SBOM/probe rotation was attempted. "
            "Fix 'target_endpoint' in nuguard.yaml or remove it to allow fallback discovery."
        )
        notes.append(note)
        _log.error("Pre-flight: explicit endpoint rejected test request (400/404/405); skipping rotation")
        return PreflightOutcome(ok=False, notes=notes)

    if sbom is not None:
        from nuguard.common.endpoint_probe import (  # noqa: PLC0415
            discover_chat_candidates_from_sbom as _dcandidates,
        )

        for candidate in _dcandidates(sbom)[1:]:
            client.set_chat_endpoint(candidate[0], candidate[1], candidate[2], candidate[3])
            resp2, _ = await client.send(_TEST_MESSAGE, session)
            if not resp2.startswith(_ROTATION_TRIGGER_PREFIXES):
                _log.info("Pre-flight: rotated to working endpoint %s", candidate[0])
                notes.append(f"Chat endpoint rotated to {candidate[0]!r} after 400/404/405 on the discovered path.")
                return PreflightOutcome(ok=True, rotated_endpoint=candidate, endpoint_source="sbom", notes=notes)

        # Live probe as last resort.
        from nuguard.common.endpoint_probe import probe_chat_endpoints as _probe  # noqa: PLC0415

        try:
            probed = await _probe(target_url, sbom, auth_headers=auth_headers)
        except Exception as exc:
            _log.warning("Pre-flight live probe failed: %s", exc)
            probed = None
        if probed:
            path, pay_key, pay_list = probed
            client.set_chat_endpoint(path, pay_key, pay_list)
            _log.info("Pre-flight: live probe found working endpoint %s", path)
            notes.append(f"Chat endpoint rotated to {path!r} via live probe after SBOM candidates failed.")
            return PreflightOutcome(
                ok=True,
                rotated_endpoint=(path, pay_key, pay_list, None),
                endpoint_source="probe",
                notes=notes,
            )

    note = (
        "Chat endpoint unreachable — all SBOM candidates and live probe returned "
        "400/404/405. Check 'target_endpoint' in nuguard.yaml or re-run 'nuguard sbom generate'."
    )
    notes.append(note)
    _log.error("Pre-flight: aborting — no working chat endpoint found")
    return PreflightOutcome(ok=False, notes=notes)
