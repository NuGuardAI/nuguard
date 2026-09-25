"""Dispatch tests for GitHub-subfolder handling in ``nuguard sbom generate --from-repo``.

Pins the "purely additive" contract from the implementation plan: a plain
repo-root URL must never touch the new subfolder code path, an unambiguous
``/tree/<ref>/<subpath>`` URL routes straight to it, and an ambiguous bare
shorthand URL only falls back to it after a direct clone attempt fails with
a definitive "not found" (any other failure must propagate unchanged).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nuguard.cli.commands.sbom import _clone_and_extract

_NOT_FOUND_ERROR = RuntimeError(
    "git clone failed for 'https://github.com/org/repo/python-backend' @ None: "
    "remote: Not Found\nfatal: repository 'https://github.com/org/repo/python-backend/' not found"
)
_AUTH_ERROR = RuntimeError(
    "git clone failed for 'https://github.com/org/repo/python-backend' @ None: "
    "fatal: Authentication failed"
)


def _extractor(root_doc="root_doc", subfolder_doc="subfolder_doc", root_side_effect=None):
    extractor = MagicMock()
    if root_side_effect is not None:
        extractor.extract_from_repo.side_effect = root_side_effect
    else:
        extractor.extract_from_repo.return_value = root_doc
    extractor.extract_from_repo_subfolder.return_value = subfolder_doc
    return extractor


def test_plain_repo_root_url_never_touches_subfolder_path() -> None:
    extractor = _extractor()
    doc = _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo",
        clone_url="https://github.com/org/repo",
        ref=None,
        cfg_source_ref=None,
        config=MagicMock(),
        token=None,
    )
    assert doc == "root_doc"
    extractor.extract_from_repo.assert_called_once()
    extractor.extract_from_repo_subfolder.assert_not_called()


def test_non_github_url_never_touches_subfolder_path() -> None:
    extractor = _extractor()
    doc = _clone_and_extract(
        extractor=extractor,
        from_repo="https://gitlab.com/org/repo",
        clone_url="https://gitlab.com/org/repo",
        ref="main",
        cfg_source_ref=None,
        config=MagicMock(),
        token=None,
    )
    assert doc == "root_doc"
    extractor.extract_from_repo_subfolder.assert_not_called()


def test_tree_url_routes_straight_to_subfolder_no_direct_attempt() -> None:
    extractor = _extractor()
    doc = _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo/tree/main/python-backend",
        clone_url="https://github.com/org/repo/tree/main/python-backend",
        ref=None,
        cfg_source_ref=None,
        config=MagicMock(),
        token=None,
    )
    assert doc == "subfolder_doc"
    extractor.extract_from_repo.assert_not_called()
    extractor.extract_from_repo_subfolder.assert_called_once()
    _, kwargs = extractor.extract_from_repo_subfolder.call_args
    assert kwargs["subpath"] == "python-backend"
    assert kwargs["ref"] == "main"
    assert kwargs["source_ref"] == "https://github.com/org/repo/tree/main/python-backend"


def test_explicit_ref_flag_overrides_url_embedded_ref() -> None:
    extractor = _extractor()
    _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo/tree/main/python-backend",
        clone_url="https://github.com/org/repo/tree/main/python-backend",
        ref="develop",
        cfg_source_ref=None,
        config=MagicMock(),
        token=None,
    )
    _, kwargs = extractor.extract_from_repo_subfolder.call_args
    assert kwargs["ref"] == "develop"


def test_bare_shorthand_succeeding_directly_never_calls_subfolder_path() -> None:
    extractor = _extractor()
    doc = _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo/python-backend",
        clone_url="https://github.com/org/repo/python-backend",
        ref=None,
        cfg_source_ref=None,
        config=MagicMock(),
        token=None,
    )
    assert doc == "root_doc"
    extractor.extract_from_repo.assert_called_once()
    extractor.extract_from_repo_subfolder.assert_not_called()


def test_bare_shorthand_falls_back_to_subfolder_on_not_found() -> None:
    extractor = _extractor(root_side_effect=_NOT_FOUND_ERROR)
    doc = _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo/python-backend",
        clone_url="https://github.com/org/repo/python-backend",
        ref=None,
        cfg_source_ref=None,
        config=MagicMock(),
        token=None,
    )
    assert doc == "subfolder_doc"
    extractor.extract_from_repo.assert_called_once()
    extractor.extract_from_repo_subfolder.assert_called_once()
    args, kwargs = extractor.extract_from_repo_subfolder.call_args
    assert args[0] == "https://github.com/org/repo"
    assert kwargs["subpath"] == "python-backend"


def test_bare_shorthand_other_failure_does_not_fall_back() -> None:
    extractor = _extractor(root_side_effect=_AUTH_ERROR)
    with pytest.raises(RuntimeError, match="Authentication failed"):
        _clone_and_extract(
            extractor=extractor,
            from_repo="https://github.com/org/repo/python-backend",
            clone_url="https://github.com/org/repo/python-backend",
            ref=None,
            cfg_source_ref=None,
            config=MagicMock(),
            token=None,
        )
    extractor.extract_from_repo_subfolder.assert_not_called()


def test_ref_precedence_falls_back_to_config_source_ref() -> None:
    extractor = _extractor()
    _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo",
        clone_url="https://github.com/org/repo",
        ref=None,
        cfg_source_ref="release-branch",
        config=MagicMock(),
        token=None,
    )
    _, kwargs = extractor.extract_from_repo.call_args
    assert kwargs["ref"] == "release-branch"
