"""Unit tests for CVE/GHSA finding titles from grype/osv converters.

Titles used to be a generic "Known vulnerability in {pkg} (ID)" template that
repeated the advisory id already shown in the report's ID column. They should
now be a condensed, human-readable summary of the actual vulnerability, with
no id repetition.
"""

from __future__ import annotations

from nuguard.analysis.plugins.nga_rules import (
    _finding_title,
    _grype_to_finding,
    _osv_to_finding,
    _summarize_description,
)


def test_osv_title_uses_advisory_summary_not_generic_template() -> None:
    osv = {
        "dep_name": "langchain",
        "dep_version": "0.0.300",
        "purl": "pkg:pypi/langchain@0.0.300",
        "advisory_id": "GHSA-7gfq-f96f-g85j",
        "cve_ids": ["CVE-2023-36281"],
        "summary": "langchain vulnerable to arbitrary code execution via load_prompt.",
        "severity": "CRITICAL",
        "affected_versions": "<0.0.325",
        "url": "https://osv.dev/x",
    }
    title = _osv_to_finding(osv)["title"]
    assert title == "langchain vulnerable to arbitrary code execution via load_prompt."
    assert "Known vulnerability" not in title
    assert "GHSA-7gfq-f96f-g85j" not in title
    assert "CVE-2023-36281" not in title


def test_grype_title_condenses_long_multiline_description() -> None:
    grype = {
        "dep_name": "curl",
        "dep_version": "8.5.0-r0",
        "purl": "pkg:apk/alpine/curl@8.5.0-r0",
        "advisory_id": "CVE-2024-6119",
        "cve_ids": ["CVE-2024-6119"],
        "summary": (
            "Issue summary: Applications performing certificate name checks "
            "(e.g., TLS clients checking server certificates) may attempt to "
            "read an invalid memory address resulting in abnormal termination "
            "of the application process.\n\nImpact summary: more detail here."
        ),
        "severity": "HIGH",
        "affected_versions": "<8.9.0",
        "url": "https://x",
    }
    title = _grype_to_finding(grype)["title"]
    assert "Known vulnerability" not in title
    assert "CVE-2024-6119" not in title
    assert title.startswith("Applications performing certificate name checks")
    assert len(title) <= 141  # max_len + ellipsis


def test_finding_title_falls_back_to_package_name_when_no_summary() -> None:
    assert _finding_title("foo", "CVE-2099-1", "CVE-2099-1") == "Vulnerability in foo"
    assert _finding_title("foo", "CVE-2099-1", "") == "Vulnerability in foo"


def test_summarize_description_skips_leading_list_marker_as_sentence_end() -> None:
    # A leading "1." must not be mistaken for a one-word "sentence" — observed
    # as a real bug: CVE-2025-9086's description starts with a numbered list.
    text = "1. libcurl leaks credentials to a second proxy when redirected. 2. more."
    assert _summarize_description(text) == (
        "1. libcurl leaks credentials to a second proxy when redirected."
    )


def test_summarize_description_skips_unpunctuated_list_markers_too() -> None:
    # Some advisories format numbered steps without a period after each item
    # ("1. do X 2. do Y") — a naive scan would stop at "2." with no real
    # sentence captured; the summary should keep scanning past it instead.
    text = "1. A cookie is set for `target` 2. The value is read via a bug. 3. done."
    summary = _summarize_description(text, max_len=200)
    assert summary == "1. A cookie is set for `target` 2. The value is read via a bug."


def test_summarize_description_strips_advisory_header_and_truncates() -> None:
    assert (
        _summarize_description("Issue summary: Buffer overflow in parser.")
        == "Buffer overflow in parser."
    )
    long_text = "A " + ("very " * 40) + "long description without punctuation"
    summary = _summarize_description(long_text, max_len=50)
    assert len(summary) <= 51
    assert summary.endswith("…")
