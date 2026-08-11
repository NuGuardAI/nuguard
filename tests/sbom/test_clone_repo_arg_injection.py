"""Regression tests for argument-injection hardening in ``AiSbomExtractor._clone_repo``.

The git-clone path is reached from ``nuguard sbom generate --from-repo --ref <ref>``
or directly via ``AiSbomExtractor.extract_from_repo()``.  Historically the ref
and url values were passed verbatim as positional arguments to ``git clone``;
that allows a hostile ref (or url) string starting with ``-`` to be
interpreted by git as a flag (e.g. ``--upload-pack=<cmd>``), which is a known
argument-injection vector.

These tests pin the contract that:

* refs beginning with ``-`` are rejected before any subprocess is started;
* empty / whitespace / null-byte refs are rejected;
* url strings lacking a scheme (or starting with ``-``) are rejected;
* well-formed values still flow through to ``git clone`` unchanged;
* the subprocess command now uses ``--`` to terminate option parsing
  (defence in depth, in case validation is ever loosened).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nuguard.sbom.extractor import AiSbomExtractor

# ---------------------------------------------------------------------------
# Ref / URL rejection — argument-injection defence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_ref",
    [
        # Leading-dash: would be parsed as a git flag (e.g. --upload-pack=...).
        "--upload-pack=touch /tmp/pwn",
        "-x",
        "--",
        # Embedded null byte / newline / tab — shell-confusion classic.
        "main\x00evil",
        "main\n--upload-pack=evil",
        "main\t--config=core.editor=vim",
        # Whitespace-only / empty.
        " ",
        "",
        # Trailing newline only — Python's ``$`` matches before a trailing
        # newline, so a bare ``^...$`` regex would let ``"main\n"`` through.
        # The hardening regex uses ``\Z`` (or ``re.fullmatch``) so this is
        # rejected.
        "main\n",
    ],
)
def test_clone_repo_rejects_unsafe_refs(bad_ref: str, tmp_path: Path) -> None:
    """A ref beginning with ``-`` or containing whitespace must raise ValueError.

    No subprocess should be started for these inputs.
    """
    with patch("nuguard.sbom.extractor.core.subprocess.run") as run_mock:
        with pytest.raises(ValueError, match="Invalid git ref"):
            AiSbomExtractor._clone_repo(
                url="https://github.com/example/repo.git",
                ref=bad_ref,
                dest=tmp_path,
            )
    assert run_mock.call_count == 0, "subprocess.run must NOT be invoked for an unsafe ref"


@pytest.mark.parametrize(
    "bad_url",
    [
        # Leading-dash URL would be parsed by git as a flag.
        "--upload-pack=evil",
        # No scheme.
        "github.com/example/repo",
        "example/repo.git",
        # Empty / whitespace.
        "",
        " ",
        # File:// — refused: not http(s).
        "file:///etc/passwd",
        # git:// — refused: not http(s).
        "git://github.com/example/repo.git",
    ],
)
def test_clone_repo_rejects_unsafe_urls(bad_url: str, tmp_path: Path) -> None:
    """A url lacking a recognised scheme or starting with ``-`` must raise ValueError."""
    with patch("nuguard.sbom.extractor.core.subprocess.run") as run_mock:
        with pytest.raises(ValueError, match="Invalid repository URL"):
            AiSbomExtractor._clone_repo(
                url=bad_url,
                ref="main",
                dest=tmp_path,
            )
    assert run_mock.call_count == 0, "subprocess.run must NOT be invoked for an unsafe url"


# ---------------------------------------------------------------------------
# Happy path — well-formed inputs still work
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good_url",
    [
        "https://github.com/example/repo",
        "https://github.com/example/repo.git",
        "http://internal.example.com/team/repo.git",
        "ssh://git@example.com/repo.git",
        # scp-style SSH — historically accepted by ``extract_from_repo`` and
        # still a valid git transport. The hardening regex explicitly permits
        # this form so we don't regress existing callers that use it.
        "git@github.com:example/repo.git",
        "git@github.com:/example/repo.git",
        "git@gitlab.com:group/subgroup/project.git",
    ],
)
def test_clone_repo_accepts_well_formed_urls(good_url: str, tmp_path: Path) -> None:
    """Common url formats must reach ``subprocess.run`` unchanged."""
    with patch(
        "nuguard.sbom.extractor.core.subprocess.run",
        return_value=_ok_completed_process(),
    ) as run_mock:
        AiSbomExtractor._clone_repo(url=good_url, ref="main", dest=tmp_path)

    assert run_mock.call_count == 1
    cmd = run_mock.call_args.args[0]
    assert cmd[0] == "git"
    assert cmd[1] == "clone"
    assert cmd[2:6] == ["--depth", "1", "--branch", "main"]
    # ``--`` must terminate option parsing so the url can never be
    # reinterpreted as a flag even if validation were ever weakened.
    assert "--" in cmd
    assert good_url in cmd
    assert str(tmp_path) in cmd


@pytest.mark.parametrize(
    "hostile_scp_url",
    [
        # scp-style URL with a leading-dash path. The colon already
        # separates host from path, but a hostile provider could try
        # ``git@host:--upload-pack=evil`` to look like a flag. The regex
        # rejects this even though the scp form is otherwise permitted.
        "git@github.com:--upload-pack=evil",
        "git@github.com:-flag",
        "git@github.com:--",
        "git@github.com: --option",
        "git@github.com:,foo",
    ],
)
def test_clone_repo_rejects_hostile_scp_urls(hostile_scp_url: str, tmp_path: Path) -> None:
    """An scp-style URL whose path begins with ``-`` must raise ValueError.

    Companion to ``test_clone_repo_accepts_well_formed_urls`` for the scp
    form: the hardening regex permits legitimate scp URLs
    (``git@host:path``) but still rejects scp URLs whose path portion
    starts with ``-`` so a hostile provider cannot smuggle a flag through.
    """
    with patch("nuguard.sbom.extractor.core.subprocess.run") as run_mock:
        with pytest.raises(ValueError, match="Invalid repository URL"):
            AiSbomExtractor._clone_repo(
                url=hostile_scp_url,
                ref="main",
                dest=tmp_path,
            )
    assert run_mock.call_count == 0, (
        "subprocess.run must NOT be invoked for a hostile scp URL"
    )


def test_clone_repo_preserves_ref_in_subprocess(tmp_path: Path) -> None:
    """The ref value is passed to git verbatim after validation."""
    with patch(
        "nuguard.sbom.extractor.core.subprocess.run",
        return_value=_ok_completed_process(),
    ) as run_mock:
        AiSbomExtractor._clone_repo(
            url="https://github.com/example/repo.git",
            ref="feature/some_branch",
            dest=tmp_path,
        )
    cmd = run_mock.call_args.args[0]
    assert "feature/some_branch" in cmd
    # The ref must appear before the ``--`` separator (still positional).
    ref_index = cmd.index("feature/some_branch")
    sep_index = cmd.index("--")
    assert ref_index < sep_index


def test_clone_repo_subprocess_uses_double_dash_separator(tmp_path: Path) -> None:
    """Defence in depth: ``--`` must appear between ref and url in the command.

    This prevents any future ref/url value that passes validation from being
    misinterpreted as a git flag, even if a regression weakens the regex.
    """
    with patch(
        "nuguard.sbom.extractor.core.subprocess.run",
        return_value=_ok_completed_process(),
    ) as run_mock:
        AiSbomExtractor._clone_repo(
            url="https://github.com/example/repo.git",
            ref="main",
            dest=tmp_path,
        )
    cmd = run_mock.call_args.args[0]
    # ``--`` must come after ``--branch <ref>`` and before the url.
    sep_index = cmd.index("--")
    assert cmd[sep_index - 2 : sep_index] == ["--branch", "main"]
    assert cmd[sep_index + 1] == "https://github.com/example/repo.git"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self) -> None:
        self.returncode = 0
        self.stdout = b""
        self.stderr = b""


def _ok_completed_process() -> _FakeCompletedProcess:
    return _FakeCompletedProcess()
