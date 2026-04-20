"""Shared CLI helpers used across multiple command modules."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.sbom.ai_sbom import AiSbomDocument

_log = logging.getLogger(__name__)


async def enrich_sbom_for_run(
    sbom: "AiSbomDocument",
    sbom_path: "Path | None" = None,
    target_url: str | None = None,
    llm_enabled: bool = False,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_api_base: str | None = None,
    probe_auth_header: str | None = None,
    log_prefix: str = "",
) -> "AiSbomDocument":
    """Run SBOM auto-enrichment and return the best available SBOM.

    Returns the cached enriched SBOM, a freshly generated enriched SBOM, or
    the original SBOM unchanged — whichever applies.  All logging is handled
    here so callers need no post-call enrichment logic.

    On failure, logs a warning and returns the original SBOM unchanged.
    """
    from nuguard.common.auto_sbom_enricher import maybe_auto_enrich_sbom  # noqa: PLC0415

    prefix = f"{log_prefix}: " if log_prefix else ""
    try:
        enrichment = await maybe_auto_enrich_sbom(
            sbom=sbom,
            sbom_path=sbom_path,
            target_url=target_url,
            llm_enabled=llm_enabled,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_api_base=llm_api_base,
            probe_auth_header=probe_auth_header,
        )
    except Exception as exc:
        _log.warning(
            "%sSBOM enrichment failed, continuing with original SBOM: %s",
            prefix,
            exc,
        )
        return sbom

    artifact = f" (artifact: {enrichment.artifact_path})" if enrichment.artifact_path else ""

    if "enrichment_cache_hit" in enrichment.reasons:
        _log.info(
            "%sSBOM enrichment reused from cache: confidence %.2f -> %.2f%s",
            prefix,
            enrichment.confidence_before,
            enrichment.confidence_after,
            artifact,
        )
    elif "confidence is already high" in enrichment.reasons:
        _log.debug(
            "%sSBOM enrichment skipped — confidence already sufficient (%.2f)",
            prefix,
            enrichment.confidence_before,
        )
    elif enrichment.enriched:
        _log.info(
            "%sSBOM enrichment generated: confidence %.2f -> %.2f%s",
            prefix,
            enrichment.confidence_before,
            enrichment.confidence_after,
            artifact,
        )
    else:
        _log.debug(
            "%sSBOM enrichment complete — no structural changes (confidence %.2f)",
            prefix,
            enrichment.confidence_before,
        )

    return enrichment.sbom
