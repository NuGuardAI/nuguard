"""Atomic campaign checkpoints and safe resume.

A campaign checkpoint extends the shared :class:`~nuguard.common.run_checkpoint.RunCheckpoint`
(atomic temp-file + rename, best-effort) with campaign state: the coverage
ledger, branch lineage, knowledge, budgets consumed, deferred (cooldown) and
pending-reproduction work. It is saved after every completed objective and
before long cooldowns.

Safety rules on resume (v5 spec §10):

* the sbom/policy, target, auth-scope and fixture fingerprints must all match;
* remote conversations are probed for liveness and rebuilt from *permitted*
  setup when expired — **never by replaying writes**;
* credentials are never persisted: only principal refs and auth-scope
  fingerprints. Transport headers/cookies are not written to disk.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from nuguard.common.run_checkpoint import (
    CheckpointMismatchError,
    RunCheckpoint,
    attack_scenario_signature,
    fingerprint,
)

from .branches import Branch, BranchManager, BranchState, RotationReason
from .knowledge import KnowledgeItem, KnowledgeStore, Scope, TrustLevel
from .ledger import CoverageLedger, LedgerEntry, Status
from .scheduler import BudgetTracker
from .transport import Principal

CAMPAIGN_CHECKPOINT_VERSION = 2


def campaign_signature(scenario: Any, principal_ref: str = "") -> str:
    """Stable objective identity for resume matching.

    Unlike the legacy catalog signature (``goal|type|catalog:ID``, which ignores
    the target and so collapses the same catalog template instantiated for
    different agents/endpoints), this includes the target node ids and the
    principal.
    """
    targets = ",".join(sorted(getattr(scenario, "target_node_ids", []) or []))
    return f"{attack_scenario_signature(scenario)}|targets:{targets}|principal:{principal_ref}"


def target_fingerprint(base_url: str, chat_path: str = "") -> str:
    return hashlib.sha256(f"{base_url.rstrip('/')}|{chat_path}".encode()).hexdigest()[:16]


@dataclass
class DeferredObjective:
    """An objective waiting in the cooldown queue."""

    signature: str
    next_eligible_at: float
    resume_step_index: int = 0
    reason: str = ""


@dataclass
class ResumeState:
    """What :func:`restore` hands back to the orchestrator."""

    completed_signatures: set[str]
    deferred: list[DeferredObjective]
    pending_reproductions: list[dict[str, Any]]
    expired_branches: list[str] = field(default_factory=list)
    #: branch_id -> replay-safe setup objective refs (baseline + non-write objectives only).
    rebuild_plans: dict[str, list[str]] = field(default_factory=dict)
    plan_cache: dict[tuple[str, str, str], list[list[str]]] = field(default_factory=dict)


# -- (de)serialisation helpers -------------------------------------------------

def _scope_to_list(s: Scope) -> list[str]:
    return list(s)


def _item_to_dict(i: KnowledgeItem) -> dict[str, Any]:
    d = asdict(i)
    d["trust"] = int(i.trust)
    d["scope"] = _scope_to_list(i.scope)
    return d


def _item_from_dict(d: dict[str, Any]) -> KnowledgeItem:
    d = dict(d)
    d["trust"] = TrustLevel(d["trust"])
    d["scope"] = Scope(*d["scope"])
    d["source_refs"] = tuple(d.get("source_refs", ()))
    return KnowledgeItem(**d)


def _branch_to_dict(b: Branch) -> dict[str, Any]:
    return {
        "branch_id": b.branch_id,
        "principal_ref": b.principal.ref,
        "auth_scope": b.principal.auth_scope,
        "state": b.state.value,
        "parent_id": b.parent_id,
        "generation": b.generation,
        "contamination": sorted(b.contamination),
        "baseline_done": b.baseline_done,
        "attack_turns": b.attack_turns,
        "objective_history": list(b.objective_history),
        "retire_reason": b.retire_reason.value if b.retire_reason else None,
        # Server-issued conversation ids only — never headers or cookies.
        "session_context": dict(b.transport.session_context),
        "turns": [{"p": t.prompt[:4000], "r": t.response[:4000]} for t in b.session.turns],
        "transport_turns": b.transport.turns,
        "estimated_tokens": b.transport.estimated_tokens,
    }


def build_payload(
    *,
    sbom: Any,
    policy: Any | None,
    target_fp: str,
    auth_fps: dict[str, str],
    fixture_version: str,
    ledger: CoverageLedger,
    branches: BranchManager,
    store: KnowledgeStore,
    budget: BudgetTracker,
    completed: set[str],
    deferred: list[DeferredObjective],
    pending_reproductions: list[dict[str, Any]],
    write_objectives: set[str],
    plan_cache: dict[tuple[str, str, str], list[list[str]]] | None = None,
    status: str = "running",
) -> dict[str, Any]:
    """JSON-safe campaign state (no credentials). Embeddable in the legacy checkpoint."""
    return {
        "campaign_checkpoint_version": CAMPAIGN_CHECKPOINT_VERSION,
        "cache_key": fingerprint(sbom, policy),
        "target_fingerprint": target_fp,
        "auth_fingerprints": auth_fps,          # principal ref -> auth-scope fingerprint
        "fixture_version": fixture_version,
        "status": status,
        "ledger": [{**asdict(e), "status": e.status.value} for e in ledger.entries.values()],
        "branches": [_branch_to_dict(b) for b in branches.branches.values()],
        "knowledge": [_item_to_dict(i) for i in store._items.values()],
        "budget": {
            "requests_used": budget.requests_used,
            "llm_cost_used": budget.llm_cost_used,
            "elapsed_seconds": budget.clock() - budget._t0,
        },
        "completed_signatures": sorted(completed),
        "deferred": [asdict(d) for d in deferred],
        "pending_reproductions": pending_reproductions,
        "write_objectives": sorted(write_objectives),
        "plan_cache": [{"k": list(k), "v": v} for k, v in (plan_cache or {}).items()],
    }


class CampaignCheckpointStore:
    """Save/restore campaign state under a ``prompt_cache_dir``-style directory."""

    def __init__(self, output_dir: Path) -> None:
        self._dir = output_dir
        self._store = RunCheckpoint(output_dir, "redteam")

    def path_for(self, key: str) -> Path:
        return self._dir / f"redteam-campaign-checkpoint-{key}.json"

    def save(self, key: str, **state: Any) -> Path:
        return self._store.save(self.path_for(key), build_payload(**state))

    def load(self, path: Path) -> dict[str, Any] | None:
        data = self._store.load(path)
        if data is not None and data.get("campaign_checkpoint_version") != CAMPAIGN_CHECKPOINT_VERSION:
            return None
        return data

    def delete(self, path: Path) -> None:
        self._store.delete(path)


async def restore(
    payload: dict[str, Any],
    *,
    sbom: Any,
    policy: Any | None,
    target_fp: str,
    auth_fps: dict[str, str],
    fixture_version: str,
    ledger: CoverageLedger,
    branches: BranchManager,
    store: KnowledgeStore,
    budget: BudgetTracker,
    principals: dict[str, Principal],
    liveness: Callable[[Branch], Awaitable[bool]],
) -> ResumeState:
    """Validate fingerprints, then rebuild state without ever replaying writes.

    Raises:
        CheckpointMismatchError: sbom/policy, target, auth scope or fixture
            version differ from the checkpoint.
    """
    expected = fingerprint(sbom, policy)
    if payload.get("cache_key") != expected:
        raise CheckpointMismatchError(
            f"campaign checkpoint sbom/policy fingerprint {payload.get('cache_key')!r} "
            f"does not match the current inputs ({expected!r})"
        )
    if payload.get("target_fingerprint") != target_fp:
        raise CheckpointMismatchError("campaign checkpoint was recorded against a different target")
    saved_auth = payload.get("auth_fingerprints", {})
    for ref, fp in auth_fps.items():
        if ref in saved_auth and saved_auth[ref] != fp:
            raise CheckpointMismatchError(
                f"auth scope for principal {ref!r} changed since the checkpoint was written"
            )
    if payload.get("fixture_version") != fixture_version:
        raise CheckpointMismatchError("fixture version changed since the checkpoint was written")

    for d in payload.get("ledger", []):
        d = dict(d)
        d["status"] = Status(d["status"])
        d["owasp_versions"] = tuple(d.get("owasp_versions", ("2026",)))
        ledger.register(LedgerEntry(**d))

    for d in payload.get("knowledge", []):
        item = _item_from_dict(d)
        store._items[(item.scope, item.channel, item.kind, item.key)] = item

    b = payload.get("budget", {})
    budget.requests_used = int(b.get("requests_used", 0))
    budget.llm_cost_used = float(b.get("llm_cost_used", 0.0))
    budget._t0 = budget.clock() - float(b.get("elapsed_seconds", 0.0))  # consumed time stays consumed

    writes = set(payload.get("write_objectives", []))
    expired: list[str] = []
    rebuild: dict[str, list[str]] = {}
    for bd in payload.get("branches", []):
        principal = principals.get(bd["principal_ref"])
        if principal is None:
            continue  # identity no longer available: the branch cannot be resumed
        branch = branches.new_branch(principal)
        # Keep the original id so ledger / lineage references stay valid.
        del branches.branches[branch.branch_id]
        branch.branch_id = bd["branch_id"]
        branch.transport.branch_id = bd["branch_id"]
        branches.branches[branch.branch_id] = branch
        branch.parent_id = bd["parent_id"]
        branch.generation = bd["generation"]
        branch.contamination = set(bd["contamination"])
        branch.baseline_done = bd["baseline_done"]
        branch.attack_turns = bd["attack_turns"]
        branch.objective_history = list(bd["objective_history"])
        branch.transport.session_context.update(bd["session_context"])
        branch.transport.turns = bd["transport_turns"]
        branch.transport.estimated_tokens = bd["estimated_tokens"]
        for t in bd["turns"]:
            branch.session.add_turn(t["p"], t["r"], [])
        state = BranchState(bd["state"])
        if state == BranchState.RETIRED:
            branch.state = state
            reason = bd.get("retire_reason")
            branch.retire_reason = RotationReason(reason) if reason else None
            continue
        branch.state = state
        if not await liveness(branch):
            branch.state = BranchState.EXPIRED
            expired.append(branch.branch_id)
            # Rebuild only from permitted setup: the benign baseline plus
            # non-write objectives. A write is never replayed.
            rebuild[branch.branch_id] = [r for r in branch.objective_history if r not in writes]

    return ResumeState(
        completed_signatures=set(payload.get("completed_signatures", [])),
        deferred=[DeferredObjective(**d) for d in payload.get("deferred", [])],
        pending_reproductions=list(payload.get("pending_reproductions", [])),
        expired_branches=expired,
        rebuild_plans=rebuild,
        plan_cache={tuple(e["k"]): e["v"] for e in payload.get("plan_cache", [])},  # type: ignore[misc]
    )
