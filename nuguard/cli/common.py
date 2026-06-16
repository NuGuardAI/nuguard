"""Shared CLI helpers used across multiple command modules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.sbom.ai_sbom import AiSbomDocument

_log = get_logger(__name__)


def parse_output_formats(
    raw_formats: Iterable[str] | None,
    *,
    default_format: str,
    allowed_formats: set[str],
) -> list[str]:
    """Parse, normalize, and validate one-or-many output formats.

    Supports repeated values and comma-separated values in the same list.
    Returns a de-duplicated list in first-seen order.
    """
    values = list(raw_formats or [])
    if not values:
        return [default_format]

    parsed: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in str(value).split(","):
            fmt = token.strip().lower()
            if not fmt:
                continue
            if fmt not in allowed_formats:
                allowed = " | ".join(sorted(allowed_formats))
                raise ValueError(f"Unknown format '{fmt}'. Allowed: {allowed}")
            if fmt not in seen:
                seen.add(fmt)
                parsed.append(fmt)

    if not parsed:
        allowed = " | ".join(sorted(allowed_formats))
        raise ValueError(f"No valid format provided. Allowed: {allowed}")

    return parsed


def output_path_for_format(
    output_path: Path,
    *,
    fmt: str,
    all_formats: list[str],
    extension_map: dict[str, str],
) -> Path:
    """Resolve per-format output paths for single or multi-format output.

    For single-format output, preserves the user-provided path unchanged.
    For multi-format output, treats the provided path as a base name and writes
    sibling files with format-specific extensions.
    """
    if len(all_formats) <= 1:
        return output_path

    base = output_path.with_suffix("") if output_path.suffix else output_path
    ext = extension_map.get(fmt, f".{fmt}")
    return base.parent / f"{base.name}{ext}"


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
