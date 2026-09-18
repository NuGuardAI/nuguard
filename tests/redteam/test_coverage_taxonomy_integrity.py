from __future__ import annotations

from copy import deepcopy

from nuguard.redteam.catalog.coverage import CoverageReport, render_catalog_coverage_markdown
from nuguard.redteam.catalog.taxonomy import ScenarioCategory


def test_live_and_serialized_coverage_use_the_current_taxonomy() -> None:
    categories = list(ScenarioCategory)
    report = CoverageReport(profile="full", total_generated=100, categories_covered=categories)
    expected = f"**Categories covered**: {len(categories)} / {len(categories)}"
    assert expected in report.to_markdown()
    assert expected in render_catalog_coverage_markdown(
        {
            "categories_covered": [category.value for category in categories],
        }
    )


def test_duplicates_do_not_inflate_the_numerator() -> None:
    category = next(iter(ScenarioCategory))
    report = CoverageReport(profile="ci", categories_covered=[category, category])
    assert report.categories_covered_count == 1
    assert f"**Categories covered**: 1 / {len(ScenarioCategory)}" in report.to_markdown()


def test_unknown_snapshot_categories_are_reported_separately_without_mutation() -> None:
    category = next(iter(ScenarioCategory))
    data = {"categories_covered": [category.value, category.value, "Old category", "Old category"]}
    before = deepcopy(data)
    text = render_catalog_coverage_markdown(data)
    assert f"**Categories covered**: 1 / {len(ScenarioCategory)}" in text
    assert "**Unrecognized categories**: 1" in text
    assert data == before


def test_empty_coverage_uses_the_current_denominator() -> None:
    assert (
        f"**Categories covered**: 0 / {len(ScenarioCategory)}"
        in render_catalog_coverage_markdown({})
    )
