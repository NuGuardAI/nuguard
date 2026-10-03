"""Per-branch transport state.

:class:`TargetAppClient` historically keeps conversation state client-wide
(``_session_context``, path params, ``chat_payload_extras``, the cookie jar).
Campaign mode runs several conversation *branches* over one pooled client, so
that state must live on the branch instead. A :class:`BranchTransport` carries
it, and is passed to ``TargetAppClient.send`` / ``invoke_endpoint`` as an
opt-in ``transport=`` argument. With no transport the client behaves exactly
as before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RetryDeferred(Exception):
    """Raised instead of sleeping when a branch send hits a retriable condition.

    The campaign cooldown queue (not the request slot) owns the wait: the
    caller re-schedules the objective once ``delay_seconds`` have elapsed.
    """

    def __init__(
        self,
        delay_seconds: float,
        *,
        reason: str,
        status_text: str = "",
    ) -> None:
        super().__init__(f"retry deferred {delay_seconds:.1f}s: {reason}")
        self.delay_seconds = delay_seconds
        self.reason = reason
        self.status_text = status_text


@dataclass
class BranchTransport:
    """Mutable transport state for one conversation branch.

    Attributes:
        branch_id: Stable id; also used as the framework-adapter scenario key so
            each branch owns its own server-side session.
        principal_ref: Reference (never the credential) of the identity this
            branch is pinned to; a branch never mixes principals.
        session_context: Server-issued session/conversation ids (replaces the
            client-wide ``_session_context`` for this branch).
        path_params: Overlay on the client's path-param values.
        extras_overlay: Overlay on ``chat_payload_extras`` (schema-heal writes here).
        headers: Per-request headers (e.g. this branch's own auth token).
        cookies: Per-branch cookies, sent as an explicit ``Cookie`` header.
        defer_retries: Raise :class:`RetryDeferred` rather than sleeping on 429 /
            transient errors.
    """

    branch_id: str
    principal_ref: str = ""
    auth_scope: str = ""
    session_context: dict[str, Any] = field(default_factory=dict)
    path_params: dict[str, str] = field(default_factory=dict)
    extras_overlay: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    defer_retries: bool = True
    turns: int = 0
    estimated_tokens: int = 0

    def pin_principal(self, principal: Any) -> None:
        """Pin this branch to *principal*; rebinding to a different one is rejected.

        Also adopts the principal's auth headers so every request from this branch
        is made under that identity only.
        """
        if self.principal_ref and (
            self.principal_ref != principal.ref or self.auth_scope != principal.auth_scope
        ):
            raise ValueError(
                f"branch {self.branch_id!r} is pinned to principal {self.principal_ref!r}; "
                f"refusing to mix in {principal.ref!r}"
            )
        self.principal_ref = principal.ref
        self.auth_scope = principal.auth_scope
        self.headers.update(principal.headers)

    def request_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Headers for one request: branch headers, branch cookies, then *extra*.

        An explicit ``Cookie`` header is always emitted so httpx's shared jar
        never leaks another branch's cookies into this branch's requests.
        """
        merged: dict[str, str] = dict(self.headers)
        merged["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if extra:
            merged.update(extra)
        return merged

    def absorb_response_cookies(self, cookies: Any) -> None:
        """Store Set-Cookie values from a response onto this branch."""
        try:
            for name, value in dict(cookies).items():
                self.cookies[str(name)] = str(value)
        except (TypeError, ValueError):
            return

    def record_turn(self, estimated_tokens: int = 0) -> None:
        """Count one completed turn against rotation limits."""
        self.turns += 1
        self.estimated_tokens += max(0, estimated_tokens)
