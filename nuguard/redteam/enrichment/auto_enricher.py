"""Compatibility wrappers for SBOM auto-enrichment.

The shared implementation now lives in ``nuguard.common.auto_sbom_enricher``.
This module re-exports the same public API to preserve redteam import paths.
"""

from nuguard.common.auto_sbom_enricher import (
    EnrichmentResult,
    _infer_required_field_from_error,
    maybe_auto_enrich_sbom,
)

__all__ = [
    "EnrichmentResult",
    "maybe_auto_enrich_sbom",
    "_infer_required_field_from_error",
]
