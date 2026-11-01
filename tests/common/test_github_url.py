"""Tests for GitHub subfolder URL parsing (``nuguard.common.github_url``).

Covers the two supported subfolder forms (``/tree/<ref>/<subpath>`` and the
bare shorthand ``org/repo/<subpath>``), the plain repo-root case (unaffected
by this feature), non-GitHub hosts (must return ``None`` so callers fall
through to the existing clone path unchanged), and rejection of path
traversal / encoded-slash attempts.
"""

from __future__ import annotations

import pytest

from nuguard.common.github_url import (
    GitHubRepoRef,
    parse_github_repo_and_subfolder,
    try_parse_github_subfolder,
)

# ---------------------------------------------------------------------------
# Plain repo-root URLs — today's unaffected case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo",
        "https://github.com/org/repo/",
        "https://github.com/org/repo.git",
        "https://github.com/org/repo.git/",
    ],
)
def test_repo_root_urls_have_no_subpath(url: str) -> None:
    ref = parse_github_repo_and_subfolder(url)
    assert ref == GitHubRepoRef(
        repo_root_url="https://github.com/org/repo", url_ref=None, subpath=None
    )
    assert try_parse_github_subfolder(url) is None


# ---------------------------------------------------------------------------
# GitHub tree-URL form: /tree/<ref>/<subpath>
# ---------------------------------------------------------------------------


def test_tree_url_single_segment_subpath() -> None:
    ref = try_parse_github_subfolder("https://github.com/org/repo/tree/main/python-backend")
    assert ref is not None
    assert ref.repo_root_url == "https://github.com/org/repo"
    assert ref.url_ref == "main"
    assert ref.subpath == "python-backend"
    assert not ref.is_ambiguous_shorthand


def test_tree_url_nested_subpath() -> None:
    ref = try_parse_github_subfolder(
        "https://github.com/org/repo/tree/feature/x/services/api/v2"
    )
    assert ref is not None
    assert ref.url_ref == "feature"
    assert ref.subpath == "x/services/api/v2"


def test_tree_url_tolerates_dot_git_suffix() -> None:
    ref = try_parse_github_subfolder("https://github.com/org/repo.git/tree/main/sub")
    assert ref is not None
    assert ref.repo_root_url == "https://github.com/org/repo"


# ---------------------------------------------------------------------------
# Bare shorthand: org/repo/<subpath>, no /tree/, ambiguous
# ---------------------------------------------------------------------------


def test_bare_shorthand_single_segment_subpath() -> None:
    ref = try_parse_github_subfolder("https://github.com/org/repo/python-backend")
    assert ref is not None
    assert ref.repo_root_url == "https://github.com/org/repo"
    assert ref.url_ref is None
    assert ref.subpath == "python-backend"
    assert ref.is_ambiguous_shorthand


def test_bare_shorthand_nested_subpath() -> None:
    ref = try_parse_github_subfolder("https://github.com/org/repo/services/api")
    assert ref is not None
    assert ref.subpath == "services/api"
    assert ref.is_ambiguous_shorthand


# ---------------------------------------------------------------------------
# Non-GitHub hosts — must fall through untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/org/repo/sub",
        "https://bitbucket.org/org/repo/sub",
        "https://example.com/org/repo/sub",
        "git@gitlab.com:org/repo.git",
    ],
)
def test_non_github_host_returns_none(url: str) -> None:
    assert try_parse_github_subfolder(url) is None
    with pytest.raises(ValueError, match="Not a GitHub URL"):
        parse_github_repo_and_subfolder(url)


# ---------------------------------------------------------------------------
# Rejected subpaths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo/tree/main/../secrets",
        "https://github.com/org/repo/tree/main/a/../../b",
        "https://github.com/org/repo/a/../b",
    ],
)
def test_path_traversal_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="path traversal"):
        parse_github_repo_and_subfolder(url)
    assert try_parse_github_subfolder(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo/tree/main/a%2Fb",
        "https://github.com/org/repo/a%2fb",
    ],
)
def test_encoded_slash_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="encoded slash"):
        parse_github_repo_and_subfolder(url)
    assert try_parse_github_subfolder(url) is None
