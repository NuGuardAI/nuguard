"""Professional-role pretext library (redteam-proposal.md W3).

Generic jailbreak personas (fiction, "developer mode") are the *most
likely* refusals in a hardened app — the live Pinnacle Bank run showed
that vertical-professional pretexts (compliance auditor, on-call fraud
analyst, incident-response window, runbook reference, ticket/case IDs,
"evidence exhibit" numbering) are the reliable bypass instead.

Each :class:`PretextTemplate` implements the same ``decorate(payload) ->
payload`` shape as :class:`~nuguard.redteam.decorators.base.PayloadDecorator`
(category ``"framing"``) so it composes with the W6 decorator pipeline —
applied on failure, before falling back to free-form LLM paraphrase,
exactly like the encoding decorators.

Deliberately generic — no domain-flavored variants in this phase (that
would need threading ``AppCapabilityProfile.domain`` through the shared
decorator interface, a larger change than this pass's value warrants); see
each template's wording for the closed, non-app-specific vocabulary used.
"""
from __future__ import annotations

from .templates import (
    PRETEXT_DECORATORS,
    AuditComplianceDecorator,
    AuthorityAttributionDecorator,
    MaintenanceDebugDecorator,
    OperationalRunbookDecorator,
    SystemMarkerSpoofDecorator,
)

__all__ = [
    "PRETEXT_DECORATORS",
    "AuditComplianceDecorator",
    "AuthorityAttributionDecorator",
    "MaintenanceDebugDecorator",
    "OperationalRunbookDecorator",
    "SystemMarkerSpoofDecorator",
]
