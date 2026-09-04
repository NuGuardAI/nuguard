from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr, ValidationError

from nuguard.common.auto_sbom_enricher import EnrichmentResult
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.public_api import (
    SbomEnrichmentLlmConfig,
    SbomEnrichmentRequest,
    SbomProbeAuthConfig,
    enrich_sbom,
)


@pytest.mark.asyncio
async def test_enrich_sbom_is_copy_on_write_and_does_not_persist_by_default() -> None:
    """A mutating enricher cannot alter input and no implicit artifact is written."""
    source = AiSbomDocument(target="app")

    async def mutate_copy(*, sbom: AiSbomDocument, **kwargs: object) -> EnrichmentResult:
        sbom.target = "enriched-app"
        return EnrichmentResult(
            sbom=sbom,
            enriched=True,
            confidence_before=0.5,
            confidence_after=0.8,
            reasons=["enriched"],
            probe_attempted=False,
            probe_requests=0,
            artifact_path=None,
        )

    with (
        patch(
            "nuguard.common.auto_sbom_enricher.maybe_auto_enrich_sbom",
            new=AsyncMock(side_effect=mutate_copy),
        ) as enrich_mock,
        patch("nuguard.sbom.public_api.Path.mkdir") as mkdir_mock,
        patch("nuguard.sbom.public_api.Path.write_text") as write_mock,
    ):
        result = await enrich_sbom(SbomEnrichmentRequest(sbom=source))

    delegated = enrich_mock.await_args.kwargs
    assert delegated["sbom"] is not source
    assert delegated["sbom_path"] is None
    assert source.target == "app"
    assert result.sbom.target == "enriched-app"
    assert result.artifact_path is None
    mkdir_mock.assert_not_called()
    write_mock.assert_not_called()


@pytest.mark.asyncio
async def test_enrichment_secrets_are_transport_only_and_do_not_affect_fingerprint() -> None:
    """Credentials are SecretStr inputs, transport-only, and excluded from cache keys."""
    source = AiSbomDocument(target="app")
    first_secret = "llm-secret-one"
    second_secret = "llm-secret-two"
    auth_secret = "probe-secret"
    private_result = EnrichmentResult(
        sbom=source,
        enriched=False,
        confidence_before=0.7,
        confidence_after=0.7,
        reasons=[],
        probe_attempted=False,
        probe_requests=0,
        artifact_path=None,
    )

    async_mock = AsyncMock(return_value=private_result)
    with patch("nuguard.common.auto_sbom_enricher.maybe_auto_enrich_sbom", new=async_mock):
        first = await enrich_sbom(
            SbomEnrichmentRequest(
                sbom=source,
                target_url=f"https://user:{first_secret}@example.com/chat?access_token={auth_secret}",
                llm_config=SbomEnrichmentLlmConfig(enabled=True, api_key=first_secret),
                probe_auth=SbomProbeAuthConfig(
                    header_name="Authorization", header_value=auth_secret
                ),
            )
        )
        second = await enrich_sbom(
            SbomEnrichmentRequest(
                sbom=source,
                target_url=(
                    f"https://user:{second_secret}@example.com/chat?access_token="
                    "different-probe-secret"
                ),
                llm_config=SbomEnrichmentLlmConfig(enabled=True, api_key=second_secret),
                probe_auth=SbomProbeAuthConfig(
                    header_name="Authorization", header_value="different-probe-secret"
                ),
            )
        )

    assert isinstance(
        async_mock.await_args_list[0].kwargs["llm_api_key"],
        str,
    )
    assert async_mock.await_args_list[0].kwargs["llm_api_key"] == first_secret
    assert async_mock.await_args_list[0].kwargs["probe_auth_header"].endswith(auth_secret)
    assert first.cache_fingerprint == second.cache_fingerprint
    request = SbomEnrichmentRequest(
        sbom=source,
        llm_config=SbomEnrichmentLlmConfig(enabled=True, api_key=first_secret),
        probe_auth=SbomProbeAuthConfig(
            header_name="Authorization",
            header_value=auth_secret,
        ),
    )
    assert isinstance(request.llm_config.api_key, SecretStr)
    assert isinstance(request.probe_auth.header_value, SecretStr)
    assert first_secret not in request.model_dump_json()
    assert auth_secret not in request.model_dump_json()
    serialized = first.model_dump_json()
    assert first_secret not in serialized
    assert auth_secret not in serialized


@pytest.mark.asyncio
async def test_enrichment_fingerprint_is_deterministic_and_cache_version_invalidates() -> None:
    """Equivalent requests reuse a key while cache_version explicitly invalidates it."""
    source = AiSbomDocument(target="app")
    private_result = EnrichmentResult(
        sbom=source,
        enriched=False,
        confidence_before=0.7,
        confidence_after=0.7,
        reasons=[],
        probe_attempted=False,
        probe_requests=0,
        artifact_path=None,
    )

    with patch(
        "nuguard.common.auto_sbom_enricher.maybe_auto_enrich_sbom",
        new=AsyncMock(return_value=private_result),
    ):
        first = await enrich_sbom(SbomEnrichmentRequest(sbom=source))
        repeated = await enrich_sbom(SbomEnrichmentRequest(sbom=source))
        invalidated = await enrich_sbom(
            SbomEnrichmentRequest(sbom=source, cache_version="v2")
        )

    assert first.cache_fingerprint == repeated.cache_fingerprint
    assert invalidated.cache_fingerprint != first.cache_fingerprint


@pytest.mark.asyncio
async def test_enrichment_sanitizes_legacy_sbom_target_without_mutating_input() -> None:
    token = "legacy-repository-token"
    source = AiSbomDocument(target=f"https://x-access-token:{token}@github.com/org/repo.git")

    async def _return_input(**kwargs):
        sbom = kwargs["sbom"]
        return EnrichmentResult(
            sbom=sbom,
            enriched=False,
            confidence_before=0.7,
            confidence_after=0.7,
            reasons=[],
            probe_attempted=False,
            probe_requests=0,
            artifact_path=None,
        )

    with patch(
        "nuguard.common.auto_sbom_enricher.maybe_auto_enrich_sbom",
        new=AsyncMock(side_effect=_return_input),
    ):
        result = await enrich_sbom(SbomEnrichmentRequest(sbom=source))

    assert token in source.target
    assert result.sbom.target == "https://github.com/org/repo.git"
    assert token not in result.model_dump_json()


@pytest.mark.asyncio
async def test_enrich_sbom_persists_only_to_explicit_output_path(tmp_path) -> None:
    source = AiSbomDocument(target="app")
    output_path = tmp_path / "platform-cache" / "enriched.json"
    private_result = EnrichmentResult(
        sbom=source,
        enriched=True,
        confidence_before=0.2,
        confidence_after=0.8,
        reasons=["missing AGENT node"],
        probe_attempted=False,
        probe_requests=0,
        artifact_path=None,
    )

    with patch(
        "nuguard.common.auto_sbom_enricher.maybe_auto_enrich_sbom",
        new=AsyncMock(return_value=private_result),
    ):
        result = await enrich_sbom(
            SbomEnrichmentRequest(sbom=source, output_path=str(output_path))
        )

    assert result.artifact_path == str(output_path)
    assert AiSbomDocument.model_validate_json(output_path.read_text()) == source


def test_probe_auth_rejects_header_injection() -> None:
    with pytest.raises(ValidationError):
        SbomProbeAuthConfig(header_name="Authorization\r\nX-Leak", header_value="secret")