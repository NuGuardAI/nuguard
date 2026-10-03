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
falling back to a live :func:`~nuguard.common.endpoint_detection.live_probe.probe_endpoint`
scan if none of the SBOM candidates work either.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from nuguard.common.discovery import auth_identity_string
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig
    from nuguard.common.target_client_builder import TargetClient
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

# How many alternative SBOM candidates the pre-flight compares when the
# current endpoint's reply is not clearly conversational.
DEFAULT_PREFLIGHT_CANDIDATES = 3


def _response_indicates_wrong_endpoint(response: str) -> bool:
    """True when *response* signals the current endpoint is not the real chat
    endpoint — either an explicit rejection status, or a blank/whitespace-only
    body.

    A clean HTTP 200 with an empty payload is just as strong a "wrong
    endpoint" signal as a 400/404/405 — e.g. a vision-only route silently
    no-opping instead of erroring when the required image field is absent.
    Checking for emptiness rather than any content-based/keyword heuristic
    keeps this generic across arbitrary target apps.
    """
    return response.startswith(_ROTATION_TRIGGER_PREFIXES) or not response.strip()


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


class CachedEndpointResolution(BaseModel):
    """A live-validated chat endpoint resolution cached on the SBOM (see
    :func:`cached_endpoint_resolution` / :func:`persist_endpoint_resolution`),
    so Target Verify/Behavior/Redteam can share one validated resolution
    instead of each re-probing the target live.
    """

    chat_path: str
    chat_payload_key: str = "message"
    chat_payload_list: bool = False
    chat_response_key: str | None = None
    endpoint_source: str | None = None


def _path_param_sources_for(sbom: "AiSbomDocument", chat_path: str) -> dict[str, str]:
    """The SBOM node declaring *chat_path*'s ``path_param_sources``, or ``{}``."""
    for n in sbom.nodes:
        if n.metadata and (n.metadata.endpoint or "") == chat_path:
            return dict(n.metadata.path_param_sources or {})
    return {}


def endpoint_cache_fingerprint(
    target_url: str,
    auth_config: "AuthConfig | None",
    chat_path: str,
    path_param_sources: "dict[str, str] | None",
) -> str:
    """Stable fingerprint of (target_url, auth identity, resolved chat_path,
    that path's currently-declared ``path_param_sources``) a cached
    :class:`CachedEndpointResolution` is captured against — used to detect
    when a cached resolution no longer applies (see
    :func:`cached_endpoint_resolution`).

    Extends :func:`nuguard.common.discovery.profile_cache_fingerprint`'s
    target/auth identity half with the endpoint's own declared shape, since
    endpoint resolution also depends on SBOM-declared candidates — which can
    change independently of target/auth (e.g. a regenerated SBOM moving
    where a templated endpoint's creation route lives).
    """
    identity = auth_identity_string(auth_config)
    sources_part = json.dumps(dict(path_param_sources or {}), sort_keys=True)
    return hashlib.sha256(f"{target_url}|{identity}|{chat_path}|{sources_part}".encode()).hexdigest()


def cached_endpoint_resolution(
    sbom: "AiSbomDocument | None",
    target_url: str,
    auth_config: "AuthConfig | None",
    *,
    required_chat_path: str | None = None,
) -> "tuple[CachedEndpointResolution, dict[str, str]] | None":
    """Return a previously-validated chat-endpoint resolution cached on
    *sbom*, or ``None`` if absent/stale/inapplicable.

    When *required_chat_path* is given (the caller has an explicitly
    configured endpoint), the cached entry is only used when it resolved to
    that exact path — a cached SBOM/probe-rotated resolution for a
    *different* path must never silently override an explicit config
    endpoint (config-wins precedence is non-negotiable; see
    ``documentation/docs/endpoint-resolution-precedence-plan.md``). When
    *required_chat_path* is ``None`` (no explicit endpoint configured), any
    fingerprint-valid cached resolution is reused regardless of which path
    it resolved to.
    """
    if sbom is None:
        return None
    data = getattr(sbom, "resolved_chat_endpoint", None)
    if data is None:
        return None
    stored_fingerprint = getattr(sbom, "resolved_chat_endpoint_fingerprint", None)
    if stored_fingerprint is None:
        return None
    try:
        resolved = CachedEndpointResolution.model_validate(data)
    except Exception as exc:
        _log.warning("endpoint cache: could not parse cached resolution: %s", exc)
        return None
    if required_chat_path is not None and resolved.chat_path != required_chat_path:
        return None
    sources = _path_param_sources_for(sbom, resolved.chat_path)
    if stored_fingerprint != endpoint_cache_fingerprint(target_url, auth_config, resolved.chat_path, sources):
        return None
    raw_params = getattr(sbom, "resolved_path_param_values", None)
    path_param_values = dict(raw_params) if isinstance(raw_params, dict) else {}
    return resolved, path_param_values


def persist_endpoint_resolution(
    sbom: "AiSbomDocument | None",
    target_url: str,
    auth_config: "AuthConfig | None",
    *,
    chat_path: str,
    chat_payload_key: str,
    chat_payload_list: bool,
    chat_response_key: str | None,
    endpoint_source: str | None,
    path_param_values: "dict[str, str]",
) -> None:
    """Cache a freshly-validated chat-endpoint resolution onto *sbom*
    in-memory, so later runs against the same SBOM — by any of Target
    Verify/Behavior/Redteam, in any order, even within the same process with
    no backing file — can reuse it instead of re-probing live.

    In-memory only: callers persist to disk themselves via
    :func:`~nuguard.common.auto_sbom_enricher.persist_endpoint_resolution_sbom`
    when a ``sbom_path`` is available, mirroring
    :func:`nuguard.common.discovery.cached_discovery_profile`'s split write.
    """
    if sbom is None:
        return
    resolved = CachedEndpointResolution(
        chat_path=chat_path,
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
        chat_response_key=chat_response_key,
        endpoint_source=endpoint_source,
    )
    sources = _path_param_sources_for(sbom, chat_path)
    sbom.resolved_chat_endpoint = resolved.model_dump(mode="json")
    sbom.resolved_path_param_values = dict(path_param_values or {})
    sbom.resolved_chat_endpoint_fingerprint = endpoint_cache_fingerprint(
        target_url, auth_config, chat_path, sources
    )


async def _bootstrap_path_params(
    client: "TargetClient",
    sbom: "AiSbomDocument",
    chat_path: str,
    notes: list[str],
) -> None:
    """Resolve and bind any path params the resolved chat endpoint declares.

    Uses :func:`~nuguard.common.path_params.resolve_path_param_values` with
    *client*'s authenticated ``invoke_endpoint`` and binds each id via
    :meth:`~nuguard.redteam.target.client.TargetAppClient.set_path_param`.
    Best-effort: unresolved params stay unbound (the client's per-request
    ``[CONFIG_ERROR]`` guard reports them). Must run *after* rotation has
    settled, since :meth:`TargetAppClient.set_chat_endpoint` clears bound
    path params.
    """
    from nuguard.common.path_params import resolve_path_param_values  # noqa: PLC0415

    async def _post(path: str, body: dict) -> "tuple[int, object]":
        status, _text, data = await client.invoke_endpoint(path, method="POST", body=body)
        return status, data

    values = await resolve_path_param_values(_post, sbom, chat_path)
    sources = _path_param_sources_for(sbom, chat_path)
    for param, resolved_id in values.items():
        client.set_path_param(param, resolved_id)
        notes.append(
            f"Bootstrapped path param {param!r}={resolved_id!r} via POST {sources.get(param, '?')!r}."
        )


def _path_params_unbound(client: "TargetClient") -> bool:
    """True when *client*'s chat path has placeholders with no bound value."""
    from nuguard.common.endpoint_detection.constants import HAS_PATH_PARAM_RE  # noqa: PLC0415

    return bool(HAS_PATH_PARAM_RE.search(client.chat_path or "")) and not getattr(
        client, "path_param_values", {}
    )


async def _test_current_endpoint(
    client: "TargetClient",
    sbom: "AiSbomDocument | None",
    session: object,
    notes: list[str],
) -> tuple[str, float]:
    """Bootstrap path params if needed, send the test message, and score the reply.

    Returns ``(response_text, chat_fitness)``. Bootstrapping *before* the test
    send matters for templated routes (``/conversations/:id/messages``): sent
    unbound they can only ever answer ``[CONFIG_ERROR: unresolved path param]``.
    """
    from nuguard.common.response_extraction import chat_fitness  # noqa: PLC0415

    if sbom is not None and _path_params_unbound(client):
        await _bootstrap_path_params(client, sbom, client.chat_path, notes)
    if hasattr(client, "last_raw_response"):
        client.last_raw_response = None
    response, _ = await client.send(_TEST_MESSAGE, session)  # type: ignore[arg-type]
    return response, chat_fitness(response, getattr(client, "last_raw_response", None))


async def validate_and_rotate_chat_endpoint(
    client: "TargetClient",
    sbom: "AiSbomDocument | None",
    *,
    has_explicit_endpoint: bool,
    target_url: str = "",
    auth_headers: dict[str, str] | None = None,
    max_candidates: int = DEFAULT_PREFLIGHT_CANDIDATES,
    exclude_paths: "list[str] | None" = None,
) -> PreflightOutcome:
    """Validate *client*'s chat endpoint and rotate to a better SBOM candidate.

    Sends one test message to the current endpoint (bootstrapping any path
    params first) and scores the reply with
    :func:`~nuguard.common.response_extraction.chat_fitness`. A prose reply is
    accepted immediately. Otherwise up to *max_candidates* other ranked SBOM
    candidates are tried the same way and the most conversational one wins.
    That covers 4xx/5xx/empty replies and also 2xx replies that are
    structured artefacts from a one-shot generator endpoint (e.g.
    ``/learning-paths/generate``) rather than chat. When no candidate answers
    at all, and the current endpoint signalled a wrong route (400/404/405/422
    or empty), falls back to a live probe and then a headless-browser sniff.

    Args:
        client: Ready-to-use client (auth headers already set) whose chat
            endpoint is mutated in place on rotation.
        sbom: Parsed SBOM used to rank fallback candidates via
            :func:`~nuguard.common.endpoint_detection.sbom.discover_chat_candidates`.
        has_explicit_endpoint: When ``True``, a 400/404/405 is reported without
            attempting rotation — an explicitly configured endpoint takes
            precedence and silently substituting another one would be
            surprising.
        target_url: Base URL, forwarded to the live-probe fallback.
        auth_headers: Auth headers forwarded to the live-probe fallback.
        max_candidates: How many alternative SBOM candidates to test when the
            current endpoint's reply is not clearly conversational
            (yaml: ``behavior.preflight_candidates`` / ``redteam.preflight_candidates``).
        exclude_paths: Candidate paths never to rotate to (e.g. an endpoint
            already found broken mid-run).

    Returns:
        :class:`PreflightOutcome` — never raises; failures are reported via
        ``ok=False`` and ``notes``.
    """
    from nuguard.common.response_extraction import (  # noqa: PLC0415
        CHAT_FITNESS_NONE,
        CHAT_FITNESS_PROSE,
    )
    from nuguard.redteam.target.session import AttackSession as _PF_AS  # noqa: PLC0415

    notes: list[str] = []
    session = _PF_AS(session_id="preflight", target_url=target_url, chain_id="preflight")
    excluded = set(exclude_paths or [])

    from nuguard.common.errors import TargetQuotaExhaustedError  # noqa: PLC0415

    try:
        response, fitness = await _test_current_endpoint(client, sbom, session, notes)
    except TargetQuotaExhaustedError:
        raise  # an exhausted usage quota won't clear by rotating endpoints
    except Exception as exc:
        _log.debug("Pre-flight: test request failed (non-fatal): %s", exc)
        return PreflightOutcome(ok=True, notes=notes)

    wrong_endpoint = _response_indicates_wrong_endpoint(response)

    if has_explicit_endpoint:
        if not wrong_endpoint:
            return PreflightOutcome(ok=True, notes=notes)
        note = (
            "Configured chat endpoint rejected the test request (400/404/405). Explicit "
            "endpoint precedence is enforced; no SBOM/probe rotation was attempted. "
            "Fix 'target_endpoint' in nuguard.yaml or remove it to allow fallback discovery."
        )
        notes.append(note)
        _log.error("Pre-flight: explicit endpoint rejected test request (400/404/405); skipping rotation")
        return PreflightOutcome(ok=False, notes=notes)

    if fitness >= CHAT_FITNESS_PROSE or sbom is None:
        if not wrong_endpoint:
            return PreflightOutcome(ok=True, notes=notes)
    else:
        from nuguard.common.endpoint_detection.sbom import (  # noqa: PLC0415
            discover_chat_candidates as _dcandidates,
        )

        _log.info(
            "Pre-flight: chat endpoint %s reply not clearly conversational "
            "(fitness=%.1f, %s) — comparing up to %d SBOM candidates",
            client.chat_path, fitness, response[:40] or "<empty response>", max_candidates,
        )
        original_path = client.chat_path
        ranked = _dcandidates(sbom)
        candidates = [
            c for c in ranked if c[0] != original_path and c[0] not in excluded
        ][: max(0, max_candidates)]
        original = next((c for c in ranked if c[0] == original_path), None)

        best: "tuple[float, tuple[str, str, bool, str | None] | None]" = (fitness, None)
        for candidate in candidates:
            client.set_chat_endpoint(candidate[0], candidate[1], candidate[2], candidate[3])
            cand_notes: list[str] = []
            try:
                cand_resp, cand_fit = await _test_current_endpoint(client, sbom, session, cand_notes)
            except TargetQuotaExhaustedError:
                raise
            except Exception as exc:
                _log.debug("Pre-flight: candidate %s raised (non-fatal): %s", candidate[0], exc)
                continue
            _log.info(
                "Pre-flight: candidate %s fitness=%.1f (%s)",
                candidate[0], cand_fit, cand_resp[:40] or "<empty response>",
            )
            if cand_fit > best[0]:
                best = (cand_fit, candidate)
            if cand_fit >= CHAT_FITNESS_PROSE:
                break

        best_fit, best_candidate = best
        if best_candidate is not None and best_fit > CHAT_FITNESS_NONE:
            # Re-select the winner: set_chat_endpoint clears bound path params,
            # so bootstrap again against the final endpoint.
            client.set_chat_endpoint(
                best_candidate[0], best_candidate[1], best_candidate[2], best_candidate[3]
            )
            if _path_params_unbound(client):
                await _bootstrap_path_params(client, sbom, best_candidate[0], notes)
            _log.info("Pre-flight: rotated to endpoint %s (fitness=%.1f)", best_candidate[0], best_fit)
            notes.append(
                f"Chat endpoint rotated to {best_candidate[0]!r} — it answered the test "
                f"message more conversationally than {original_path!r}."
            )
            return PreflightOutcome(
                ok=True, rotated_endpoint=best_candidate, endpoint_source="sbom", notes=notes
            )

        # No candidate beat the original — restore it.
        if client.chat_path != original_path:
            if original is not None:
                client.set_chat_endpoint(original[0], original[1], original[2], original[3])
            else:
                client.set_chat_endpoint(
                    original_path,
                    getattr(client, "_chat_payload_key", "message"),
                    bool(getattr(client, "_chat_payload_list", False)),
                )
            if sbom is not None and _path_params_unbound(client):
                await _bootstrap_path_params(client, sbom, original_path, notes)
        if not wrong_endpoint:
            return PreflightOutcome(ok=True, notes=notes)

    _log.warning(
        "Pre-flight: chat endpoint returned %s — attempting live discovery",
        response[:15] or "<empty response>",
    )

    if sbom is not None:
        # Live probe as last resort.
        from nuguard.common.endpoint_detection.live_probe import (  # noqa: PLC0415
            probe_endpoint as _probe,
        )

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
            await _bootstrap_path_params(client, sbom, path, notes)
            return PreflightOutcome(
                ok=True,
                rotated_endpoint=(path, pay_key, pay_list, None),
                endpoint_source="probe",
                notes=notes,
            )

        # Ground-truth fallback: every static candidate and the blind HTTP
        # probe failed to guess the right path/payload shape. Rather than add
        # more naming-convention heuristics, actually drive the target's own
        # chat UI in a headless browser and observe which request it fires —
        # this works for any app regardless of its endpoint naming, since it
        # never guesses at all. Best-effort: requires Playwright/Chromium and
        # a discoverable chat input; any failure here just falls through to
        # the final failure below, same as today.
        from nuguard.common.browser_login.session import (  # noqa: PLC0415
            sniff_chat_endpoint_headless,
        )

        try:
            sniffed = await sniff_chat_endpoint_headless(target_url, chat_message=_TEST_MESSAGE)
        except Exception as exc:
            _log.info("Pre-flight: browser-sniff fallback failed: %s", exc)
            sniffed = None
        if sniffed is not None:
            path, pay_key, pay_list = sniffed
            client.set_chat_endpoint(path, pay_key, pay_list)
            _log.info("Pre-flight: browser sniff found working endpoint %s", path)
            notes.append(
                f"Chat endpoint rotated to {path!r} via headless-browser sniff after SBOM "
                "candidates and live probe both failed."
            )
            await _bootstrap_path_params(client, sbom, path, notes)
            return PreflightOutcome(
                ok=True,
                rotated_endpoint=(path, pay_key, pay_list, None),
                endpoint_source="probe",
                notes=notes,
            )

    note = (
        "Chat endpoint unreachable — all SBOM candidates, the live probe, and the "
        "headless-browser sniff fallback returned 400/404/405 or an empty response. "
        "Check 'target_endpoint' in nuguard.yaml or re-run 'nuguard sbom generate'."
    )
    notes.append(note)
    _log.error("Pre-flight: aborting — no working chat endpoint found")
    return PreflightOutcome(ok=False, notes=notes)
