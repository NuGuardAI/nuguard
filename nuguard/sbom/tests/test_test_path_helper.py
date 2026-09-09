"""Tests for the shared looks_like_test_path() helper
(docs/sbom-accuracy-plan.md #3)."""
from __future__ import annotations

from nuguard.sbom.adapters._test_paths import looks_like_test_path


def test_test_directory_component_detected():
    assert looks_like_test_path("src/tests/foo.ts")
    assert looks_like_test_path("__tests__/foo.ts")
    assert looks_like_test_path("packages/app/spec/foo.ts")
    assert looks_like_test_path("e2e/login.spec.ts")


def test_test_suffix_filename_detected():
    assert looks_like_test_path("src/foo.test.ts")
    assert looks_like_test_path("src/foo.spec.ts")


def test_normal_source_path_not_flagged():
    assert not looks_like_test_path("src/routes/chat.ts")
    assert not looks_like_test_path("packages/app/lib/testimonials.ts")


def test_windows_style_separators_normalized():
    assert looks_like_test_path("src\\tests\\foo.ts")
