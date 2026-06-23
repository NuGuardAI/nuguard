"""Phase 2 tests: attack-surface graph, recon, and per-target catalog cache."""
from __future__ import annotations

import asyncio
from pathlib import Path

from nuguard.models.policy import CognitivePolicy
from nuguard.redteam.v2.surface import (
    AttackSurface,
    SurfaceCategory,
    TrustZone,
    build_target_catalog,
    compute_policy_hash,
    compute_sbom_hash,
    extract_user_data,
    resolve_chat_endpoint,
    run_recon,
)
from nuguard.sbom.models import AiSbomDocument


# ── attack surface ──────────────────────────────────────────────────────────────
def test_attack_surface_categorizes_nodes(minimal_sbom_doc: AiSbomDocument) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    present = surface.categories_present()
    assert SurfaceCategory.AGENTS in present
    assert SurfaceCategory.TOOLS in present
    assert SurfaceCategory.DATASTORES in present

    agents = surface.by_category(SurfaceCategory.AGENTS)
    assert agents and agents[0].trust_zone is TrustZone.AGENT_RUNTIME


def test_attack_surface_tags_sensitivity_and_side_effects(
    minimal_sbom_doc: AiSbomDocument,
) -> None:
    surface = AttackSurface.from_sbom(minimal_sbom_doc)
    datastores = surface.by_category(SurfaceCategory.DATASTORES)
    assert datastores
    ds = datastores[0]
    assert "PII" in ds.data_sensitivity
    assert "data_access" in ds.side_effects
    # The PII datastore should be reported as a sensitive node.
    assert ds.id in {n.id for n in surface.sensitive_nodes()}


def test_attack_surface_summary(minimal_sbom_doc: AiSbomDocument) -> None:
    summary = surface_summary = AttackSurface.from_sbom(minimal_sbom_doc).summary()
    assert summary["node_count"] == 3
    assert "chat" in surface_summary["capabilities"]
    assert summary["sensitive_node_count"] >= 1


# ── recon ───────────────────────────────────────────────────────────────────────
def test_resolve_chat_endpoint_explicit_is_authoritative(
    minimal_sbom_doc: AiSbomDocument,
) -> None:
    path, key, is_list, resp_key, source = asyncio.run(
        resolve_chat_endpoint(minimal_sbom_doc, chat_path="/my/chat")
    )
    assert path == "/my/chat"
    assert source == "config"


def test_extract_user_data_supported_format() -> None:
    text = "Hello Jane Doe! Your account ID: ACCT-0001 and customer number: CU12345."
    ids, name, _entity_map = extract_user_data([text])
    assert "ACCT-0001" in ids
    assert "CU12345" in ids
    assert name == "Jane Doe"


def test_run_recon_without_client_returns_endpoint_only(
    minimal_sbom_doc: AiSbomDocument,
) -> None:
    result = asyncio.run(run_recon(minimal_sbom_doc, chat_path="/chat"))
    assert result.chat_path == "/chat"
    assert result.has_user_data is False


def test_run_recon_with_client_extracts_user_data(
    minimal_sbom_doc: AiSbomDocument,
) -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def send(self, payload: str, session: object) -> tuple[str, list[dict]]:
            self.calls += 1
            return (
                "Hello Jane Doe! Your account ID: ACCT-0001 is on file.",
                [],
            )

    client = _FakeClient()
    result = asyncio.run(
        run_recon(minimal_sbom_doc, chat_path="/chat", client=client)
    )
    assert client.calls == 3  # default 3 discovery prompts
    assert result.has_user_data
    assert "ACCT-0001" in result.user_ids
    assert result.user_name == "Jane Doe"


def test_run_recon_tolerates_client_errors(minimal_sbom_doc: AiSbomDocument) -> None:
    class _ExplodingClient:
        async def send(self, payload: str, session: object) -> tuple[str, list[dict]]:
            raise RuntimeError("target down")

    result = asyncio.run(
        run_recon(minimal_sbom_doc, chat_path="/chat", client=_ExplodingClient())
    )
    # Endpoint still resolves; user data is empty; no exception propagates.
    assert result.chat_path == "/chat"
    assert result.has_user_data is False


# ── target catalog ──────────────────────────────────────────────────────────────
def test_sbom_hash_is_deterministic(minimal_sbom_doc: AiSbomDocument) -> None:
    assert compute_sbom_hash(minimal_sbom_doc) == compute_sbom_hash(minimal_sbom_doc)
    assert compute_policy_hash(None) == "none"


def test_build_target_catalog_selects_techniques_and_scenarios(
    minimal_sbom_doc: AiSbomDocument, tmp_path: Path
) -> None:
    catalog, from_cache = build_target_catalog(
        minimal_sbom_doc, cache_dir=tmp_path, scan_profile="full"
    )
    assert from_cache is False
    assert catalog.technique_ids
    assert catalog.scenario_spec_ids
    # Strategy-only techniques are always applicable.
    assert "AIT-ADVERSARY-LOOP-001" in catalog.technique_ids
    # All selected technique IDs are real KB records.
    from nuguard.redteam.v2.knowledge import load_technique_index

    index = load_technique_index()
    assert all(tid in index for tid in catalog.technique_ids)


def test_target_catalog_cache_hit_and_invalidation(
    minimal_sbom_doc: AiSbomDocument, tmp_path: Path
) -> None:
    _c1, miss = build_target_catalog(minimal_sbom_doc, cache_dir=tmp_path)
    assert miss is False
    _c2, hit = build_target_catalog(minimal_sbom_doc, cache_dir=tmp_path)
    assert hit is True

    # A policy change must invalidate the cache (different combined hash → miss).
    policy = CognitivePolicy(restricted_topics=["medical advice"], restricted_actions=["refund"])
    _c3, miss_after_policy = build_target_catalog(
        minimal_sbom_doc, policy=policy, cache_dir=tmp_path
    )
    assert miss_after_policy is False
    assert _c3.policy_clauses  # clauses enumerated from the policy
    # Re-running with the same policy is a hit.
    _c4, hit_again = build_target_catalog(minimal_sbom_doc, policy=policy, cache_dir=tmp_path)
    assert hit_again is True


def test_target_catalog_force_refresh(minimal_sbom_doc: AiSbomDocument, tmp_path: Path) -> None:
    build_target_catalog(minimal_sbom_doc, cache_dir=tmp_path)
    _c, from_cache = build_target_catalog(
        minimal_sbom_doc, cache_dir=tmp_path, force_refresh=True
    )
    assert from_cache is False


def test_target_catalog_no_cache_dir_always_builds(
    minimal_sbom_doc: AiSbomDocument,
) -> None:
    _c, from_cache = build_target_catalog(minimal_sbom_doc)
    assert from_cache is False
