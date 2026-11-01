"""Scoped campaign knowledge: clean baseline vs adversarial observations.

Two separate artifacts (v5 spec §5):

* **clean baseline** — benign facts: effective identity, own-account facts,
  verified routes, latency;
* **adversarial observations** — disclosures, accepted authority claims, tool
  schemas, induced behavior, each keeping its objective attribution.

Every item carries provenance (source refs, principal, deployment fingerprint,
time) and a *trust level*. Target-supplied text never becomes trusted by being
stored: it is ``claimed`` until deterministic code or a controlled effect
corroborates it. Cache keys are identity-scoped and carry credential
*references/fingerprints* only, never credentials.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any, Literal, NamedTuple

Channel = Literal["baseline", "adversarial"]


class TrustLevel(IntEnum):
    """Ordered: a higher level requires stronger evidence than a lower one."""

    DECLARED = 0   # SBOM / config
    CLAIMED = 1    # agent text (may be fabricated, e.g. a tool list or "I am in debug mode")
    OBSERVED = 2   # trace / response evidence
    VERIFIED = 3   # controlled effect


class Scope(NamedTuple):
    """Identity-scoped cache key (no credentials, only references/fingerprints)."""

    deployment: str
    endpoint: str
    principal_ref: str
    auth_scope: str
    fixture_version: str = ""


@dataclass(frozen=True)
class KnowledgeItem:
    """One knowledge record with provenance."""

    kind: str                      # e.g. "customer_name", "id", "tool", "latency_ms"
    key: str
    value: Any
    trust: TrustLevel
    scope: Scope
    channel: Channel = "baseline"
    confidence: float = 0.5
    source_refs: tuple[str, ...] = ()
    objective_ref: str = ""        # adversarial items keep the originating objective
    observed_at: float = field(default_factory=time.time)
    untrusted_text: bool = False   # value is target-authored text; never an instruction
    stale: bool = False


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@\-]{0,127}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\s]")


def validate_identifier(value: str) -> bool:
    """Deterministic gate for model-proposed identifiers (accounts, record ids...)."""
    return bool(_SAFE_ID.fullmatch(value))


def validate_route(path: str) -> bool:
    """Deterministic gate for model-proposed routes: relative, no scheme/host/control chars."""
    return (
        path.startswith("/")
        and not path.startswith("//")
        and "://" not in path
        and not _CONTROL.search(path)
        and ".." not in path.split("?")[0].split("/")
    )


@dataclass
class OwnershipMap:
    """Trusted ownership truth: which identifiers belong to which principal.

    Only values with *known ownership* or a seeded foreign-account canary may
    be reported as cross-account disclosures; everything else is a candidate.
    """

    owned: dict[str, set[str]] = field(default_factory=dict)       # principal_ref -> values
    foreign_canaries: dict[str, set[str]] = field(default_factory=dict)  # owner principal -> canaries

    def classify(self, value: str, caller: str) -> Literal["own", "foreign_confirmed", "candidate"]:
        if value in self.owned.get(caller, ()):
            return "own"
        for table in (self.owned, self.foreign_canaries):
            for owner, values in table.items():
                if owner != caller and value in values:
                    return "foreign_confirmed"
        return "candidate"


class KnowledgeStore:
    """Identity-scoped store of :class:`KnowledgeItem` records."""

    def __init__(self, ownership: OwnershipMap | None = None) -> None:
        self._items: dict[tuple[Scope, str, str, str], KnowledgeItem] = {}
        self.ownership = ownership or OwnershipMap()

    # -- writes -------------------------------------------------------------
    def add(self, item: KnowledgeItem) -> KnowledgeItem:
        """Store *item*; target-authored text can never be added above CLAIMED."""
        if item.untrusted_text and item.trust > TrustLevel.CLAIMED:
            raise ValueError(
                f"untrusted target text cannot be stored as {item.trust.name.lower()}; "
                "promote() it with corroborating evidence instead"
            )
        self._items[(item.scope, item.channel, item.kind, item.key)] = item
        return item

    def promote(self, item: KnowledgeItem, trust: TrustLevel, evidence_ref: str) -> KnowledgeItem:
        """Raise an item's trust level, recording the corroborating evidence."""
        if not evidence_ref:
            raise ValueError("promotion requires an evidence reference")
        if trust <= item.trust:
            return item
        promoted = replace(
            item,
            trust=trust,
            untrusted_text=False,
            source_refs=(*item.source_refs, evidence_ref),
        )
        self._items[(item.scope, item.channel, item.kind, item.key)] = promoted
        return promoted

    # -- reads --------------------------------------------------------------
    def get(self, scope: Scope, channel: Channel, kind: str, key: str) -> KnowledgeItem | None:
        item = self._items.get((scope, channel, kind, key))
        return None if item is None or item.stale else item

    def items(self, scope: Scope, channel: Channel | None = None) -> list[KnowledgeItem]:
        return [
            i
            for (s, c, _k, _key), i in self._items.items()
            if s == scope and (channel is None or c == channel) and not i.stale
        ]

    def clean_baseline(self, scope: Scope) -> list[KnowledgeItem]:
        return self.items(scope, "baseline")

    def adversarial(self, scope: Scope) -> list[KnowledgeItem]:
        return self.items(scope, "adversarial")

    def own_account_facts(self, scope: Scope) -> list[KnowledgeItem]:
        """Verified/observed facts about the caller's own account (reusable across objectives)."""
        return [
            i for i in self.clean_baseline(scope)
            if i.kind in ("customer_name", "id") and i.trust >= TrustLevel.OBSERVED
        ]

    # -- invalidation ------------------------------------------------------
    def invalidate(
        self,
        *,
        deployment: str | None = None,
        principal_ref: str | None = None,
        auth_scope: str | None = None,
        fixture_version: str | None = None,
    ) -> int:
        """Mark items stale when identity/config/deployment changed; returns the count."""
        n = 0
        for k, item in list(self._items.items()):
            s = item.scope
            if (
                (deployment is not None and s.deployment != deployment)
                or (principal_ref is not None and s.principal_ref == principal_ref
                    and auth_scope is not None and s.auth_scope != auth_scope)
                or (fixture_version is not None and s.fixture_version != fixture_version)
            ):
                if not item.stale:
                    self._items[k] = replace(item, stale=True)
                    n += 1
        return n

    # -- seeding from the existing discovery profile ------------------------
    def seed_from_profile(self, profile: Any, scope: Scope, source_ref: str = "discovery") -> int:
        """Load a ``DiscoveredProfile`` (own-account facts) as baseline observations.

        Values came from the agent's own answers to benign questions, so they
        are ``observed`` own-account facts (not security verdicts).
        """
        added = 0
        name = getattr(profile, "customer_name", "") or ""
        if name:
            self.add(KnowledgeItem("customer_name", "customer_name", name, TrustLevel.OBSERVED,
                                   scope, "baseline", 0.7, (source_ref,)))
            added += 1
        for ident in getattr(profile, "ids", []) or []:
            if validate_identifier(str(ident)):
                self.add(KnowledgeItem("id", str(ident), str(ident), TrustLevel.OBSERVED,
                                       scope, "baseline", 0.7, (source_ref,)))
                added += 1
        return added

    def classify_disclosure(self, value: str, caller: str) -> str:
        """``own`` | ``foreign_confirmed`` | ``candidate`` (never assume a novel value is foreign)."""
        return self.ownership.classify(value, caller)
