from nuguard.common.auto_sbom_enricher import (
    _infer_required_field_from_error,
    maybe_auto_enrich_sbom,
)
from nuguard.redteam.enrichment.auto_enricher import (
    _infer_required_field_from_error as redteam_infer_required_field_from_error,
)
from nuguard.redteam.enrichment.auto_enricher import (
    maybe_auto_enrich_sbom as redteam_maybe_auto_enrich_sbom,
)


def test_redteam_enricher_reexports_common_implementation() -> None:
    assert redteam_maybe_auto_enrich_sbom is maybe_auto_enrich_sbom


def test_redteam_helper_reexports_common_implementation() -> None:
    assert redteam_infer_required_field_from_error is _infer_required_field_from_error
