"""CampaignPlanner: group objectives into campaigns and generate payloads just in time.

* **Campaigns** group objectives that can share a conversation branch (same
  principal and compatible session policy) and are ordered by level, so related
  attacks resume on a warm branch instead of repeating setup.
* **Prerequisites** are explicit: an objective is *ready* when its prerequisites
  (baseline level markers, or other catalog IDs) are satisfied. A tool-misuse
  test never waits on prompt extraction; generic alternatives stay available
  when reconnaissance fails.
* **JIT generation** replaces eager ``enrich_all``: only the small set of ready
  campaigns is enriched, batched per goal family, with a plan cache keyed by an
  evidence/config fingerprint.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from nuguard.redteam.catalog.scheduling import SessionPolicy

from .scheduler import Objective


@dataclass
class Campaign:
    """Objectives that may share branches (same principal, compatible policy)."""

    campaign_id: str
    principal_ref: str
    objectives: list[Objective] = field(default_factory=list)

    def ready(self, satisfied: set[str]) -> list[Objective]:
        """Objectives whose prerequisites are all satisfied (``L<n>`` markers or catalog IDs)."""
        return [o for o in self.objectives if set(o.meta.prerequisites) <= satisfied]


class _Enricher(Protocol):
    async def enrich_family(self, scenarios: list[Any]) -> dict[str, list[list[str]]]: ...


def plan_fingerprint(*, evidence: dict[str, Any], config: dict[str, Any]) -> str:
    """Stable key over the evidence/config a plan was generated from."""
    blob = json.dumps({"e": evidence, "c": config}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class CampaignPlanner:
    """Builds campaigns and enriches only what is about to run."""

    def __init__(self, enricher: _Enricher | None = None, fingerprint: str = "") -> None:
        self._enricher = enricher
        self._fingerprint = fingerprint
        #: (fingerprint, catalog_id, scenario slug) -> sequences; persisted by the checkpoint.
        self.plan_cache: dict[tuple[str, str, str], list[list[str]]] = {}
        self.llm_batches = 0

    # -- grouping ------------------------------------------------------------
    def group(self, objectives: list[Objective]) -> list[Campaign]:
        """Group by principal; isolated objectives get their own single-objective campaign."""
        campaigns: dict[str, Campaign] = {}
        out: list[Campaign] = []
        for o in sorted(objectives, key=lambda o: (o.meta.primary_level, o.catalog_id)):
            if o.meta.session_policy == SessionPolicy.ISOLATED:
                out.append(Campaign(f"iso:{o.objective_id}", o.req.principal_ref, [o]))
                continue
            key = o.req.principal_ref
            camp = campaigns.get(key)
            if camp is None:
                camp = campaigns[key] = Campaign(f"camp:{key}", key)
                out.append(camp)
            camp.objectives.append(o)
        return out

    # -- JIT generation ------------------------------------------------------
    @staticmethod
    def _slug(o: Objective) -> str:
        s = o.scenario
        return f"{s.goal_type.value}|{s.scenario_type.value}|{s.title}"

    async def enrich_ready(self, ready: list[Objective]) -> dict[str, list[list[str]]]:
        """Generate payload sequences for *ready* objectives only, one batch per goal family.

        Returns ``{scenario_id: sequences}``. Cached plans (same evidence/config
        fingerprint) are reused without an LLM call; with no enricher configured
        nothing is generated and the static builder payloads are used.
        """
        out: dict[str, list[list[str]]] = {}
        misses: dict[str, list[Objective]] = {}
        for o in ready:
            cached = self.plan_cache.get((self._fingerprint, o.catalog_id, self._slug(o)))
            if cached:
                out[o.scenario.scenario_id] = cached
            elif self._enricher is not None:
                misses.setdefault(o.scenario.goal_type.value, []).append(o)
        for family in misses.values():
            self.llm_batches += 1
            result = await self._enricher.enrich_family([o.scenario for o in family])  # type: ignore[union-attr]
            for o in family:
                seqs = result.get(o.scenario.scenario_id)
                if seqs:
                    out[o.scenario.scenario_id] = seqs
                    self.plan_cache[(self._fingerprint, o.catalog_id, self._slug(o))] = seqs
        return out
