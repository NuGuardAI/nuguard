"""Tests for ``_clone_and_extract`` in ``nuguard sbom generate --from-repo``.

GitHub-subfolder dispatch itself (plain vs. ``/tree/<ref>/<subpath>`` vs.
ambiguous bare-shorthand) now lives in
``nuguard.sbom.extractor.github_clone.resolve_and_clone`` — the single shared
implementation used by both the CLI and ``AiSbomExtractor.extract_from_repo``
directly (see ``tests/sbom/test_github_subfolder_clone.py``). ``_clone_and_extract``
is now a thin wrapper: it only resolves ref precedence (explicit ``--ref`` vs.
a URL-embedded ref vs. ``nuguard.yaml``'s ``source_ref``) before delegating to
``extractor.extract_from_repo``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nuguard.cli.commands.sbom import _clone_and_extract


def _extractor(doc="doc"):
    extractor = MagicMock()
    extractor.extract_from_repo.return_value = doc
    return extractor


def test_plain_url_delegates_to_extract_from_repo() -> None:
    extractor = _extractor()
    doc = _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo",
        clone_url="https://github.com/org/repo",
        ref=None,
        cfg_source_ref=None,
        config=MagicMock(),
    )
    assert doc == "doc"
    extractor.extract_from_repo.assert_called_once()
    _, kwargs = extractor.extract_from_repo.call_args
    assert kwargs["ref"] is None
    assert kwargs["source_ref"] == "https://github.com/org/repo"


def test_non_github_url_delegates_unchanged() -> None:
    extractor = _extractor()
    doc = _clone_and_extract(
        extractor=extractor,
        from_repo="https://gitlab.com/org/repo",
        clone_url="https://gitlab.com/org/repo",
        ref="main",
        cfg_source_ref=None,
        config=MagicMock(),
    )
    assert doc == "doc"
    _, kwargs = extractor.extract_from_repo.call_args
    assert kwargs["ref"] == "main"


def test_tree_url_ref_precedence_uses_url_embedded_ref() -> None:
    """A ``/tree/<ref>/<subpath>`` URL's embedded ref is passed through as the
    effective ref when --ref is not given; extract_from_repo does the actual
    subfolder detection/dispatch."""
    extractor = _extractor()
    _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo/tree/main/python-backend",
        clone_url="https://github.com/org/repo/tree/main/python-backend",
        ref=None,
        cfg_source_ref=None,
        config=MagicMock(),
    )
    _, kwargs = extractor.extract_from_repo.call_args
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
    )
    _, kwargs = extractor.extract_from_repo.call_args
    assert kwargs["ref"] == "develop"


def test_bare_shorthand_ref_precedence_has_no_url_embedded_ref() -> None:
    """Bare shorthand carries no ref of its own — falls back to cfg_source_ref."""
    extractor = _extractor()
    _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo/python-backend",
        clone_url="https://github.com/org/repo/python-backend",
        ref=None,
        cfg_source_ref="release-branch",
        config=MagicMock(),
    )
    _, kwargs = extractor.extract_from_repo.call_args
    assert kwargs["ref"] == "release-branch"


def test_ref_precedence_falls_back_to_config_source_ref() -> None:
    extractor = _extractor()
    _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo",
        clone_url="https://github.com/org/repo",
        ref=None,
        cfg_source_ref="release-branch",
        config=MagicMock(),
    )
    _, kwargs = extractor.extract_from_repo.call_args
    assert kwargs["ref"] == "release-branch"


def test_clone_url_with_embedded_token_is_what_gets_passed_through() -> None:
    """``clone_url`` (token-embedded) is what's handed to extract_from_repo,
    not the plain ``from_repo`` — the token travels as URL userinfo, per the
    contract ``extract_from_repo``/``resolve_and_clone`` expect."""
    extractor = _extractor()
    _clone_and_extract(
        extractor=extractor,
        from_repo="https://github.com/org/repo/tree/main/python-backend",
        clone_url="https://ghp_faketoken@github.com/org/repo/tree/main/python-backend",
        ref=None,
        cfg_source_ref=None,
        config=MagicMock(),
    )
    args, _ = extractor.extract_from_repo.call_args
    assert args[0] == "https://ghp_faketoken@github.com/org/repo/tree/main/python-backend"
