"""Tests for the GitHub subfolder sparse-checkout clone (``github_clone.py``).

Git operations are stubbed via ``_run_git`` (no real git subprocess, no
network) and the GitHub REST API tree-manifest call is stubbed via
``httpx.Client`` — mirroring the argument-injection test style already used
for ``AiSbomExtractor._clone_repo`` (``test_clone_repo_arg_injection.py``),
so these stay fast, hermetic unit tests rather than integration tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nuguard.sbom.extractor.github_clone import (
    _select_tree_files,
    _sparse_pattern,
    clone_github_subfolder,
    is_repository_not_found_error,
)

TREE_ENTRIES = [
    {"path": "README.md", "type": "blob", "mode": "100644", "size": 10},
    {"path": "python-backend/app.py", "type": "blob", "mode": "100644", "size": 100},
    {"path": "python-backend/utils.py", "type": "blob", "mode": "100644", "size": 50},
    {"path": "python-backend/link.py", "type": "blob", "mode": "120000", "size": 5},
    {"path": "python-backend/weird[name].py", "type": "blob", "mode": "100644", "size": 5},
]


def _completed(stdout: bytes = b"", stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _tree_response(entries=TREE_ENTRIES, truncated: bool = False, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"tree": entries, "truncated": truncated}
    resp.text = ""
    return resp


def _patched_httpx_client(response):
    client_cls = MagicMock()
    client_cls.return_value.__enter__.return_value.get.return_value = response
    return patch("nuguard.sbom.extractor.github_clone.httpx.Client", client_cls)


def _happy_path_run_git(dest: Path, subpath: str, commit_sha: str = "abc123"):
    def _run_git(args, *, cwd, timeout, input_data=None):
        if args[:2] == ["rev-parse", "FETCH_HEAD"]:
            return _completed(stdout=(commit_sha + "\n").encode())
        if args and args[0] == "checkout":
            (dest / subpath).mkdir(parents=True, exist_ok=True)
            (dest / subpath / "placeholder.py").write_text("# placeholder\n")
        return _completed()

    return _run_git


class TestCloneGithubSubfolder:
    def test_happy_path_materializes_and_returns_commit_sha(self, tmp_path: Path) -> None:
        dest = tmp_path / "clone"
        with (
            patch(
                "nuguard.sbom.extractor.github_clone._run_git",
                side_effect=_happy_path_run_git(dest, "python-backend"),
            ),
            _patched_httpx_client(_tree_response()),
        ):
            sha = clone_github_subfolder(
                "https://github.com/org/repo", "main", "python-backend", dest
            )
        assert sha == "abc123"
        assert (dest / "python-backend").is_dir()

    def test_nonexistent_subfolder_raises_value_error(self, tmp_path: Path) -> None:
        dest = tmp_path / "clone"
        with (
            patch(
                "nuguard.sbom.extractor.github_clone._run_git",
                side_effect=_happy_path_run_git(dest, "python-backend"),
            ),
            _patched_httpx_client(
                _tree_response(entries=[{"path": "README.md", "type": "blob", "mode": "100644", "size": 1}])
            ),
        ):
            with pytest.raises(ValueError, match="not found"):
                clone_github_subfolder(
                    "https://github.com/org/repo", "main", "python-backend", dest
                )

    def test_main_to_master_fallback_when_ref_none(self, tmp_path: Path) -> None:
        dest = tmp_path / "clone"
        calls: list[list[str]] = []

        def _run_git(args, *, cwd, timeout, input_data=None):
            calls.append(args)
            if args[:2] == ["fetch", "--depth"] and args[-1] == "HEAD":
                raise subprocess.CalledProcessError(1, args, stderr=b"fatal: fetch failed")
            if args[:2] == ["rev-parse", "FETCH_HEAD"]:
                return _completed(stdout=b"deadbeef\n")
            if args and args[0] == "checkout":
                (dest / "python-backend").mkdir(parents=True, exist_ok=True)
            return _completed()

        with (
            patch("nuguard.sbom.extractor.github_clone._run_git", side_effect=_run_git),
            _patched_httpx_client(_tree_response()),
        ):
            clone_github_subfolder("https://github.com/org/repo", None, "python-backend", dest)

        assert any(
            c[:2] == ["fetch", "--depth"] and c[-1] == "master" for c in calls
        ), calls

    def test_bad_token_retries_unauthenticated_on_401(self, tmp_path: Path) -> None:
        """A well-formed but rejected token (expired/revoked ambient GH_TOKEN)
        must not hard-fail a public repo — retry once unauthenticated."""
        dest = tmp_path / "clone"
        client_cls = MagicMock()
        client_cls.return_value.__enter__.return_value.get.side_effect = [
            _tree_response(status_code=401),
            _tree_response(),
        ]
        with (
            patch(
                "nuguard.sbom.extractor.github_clone._run_git",
                side_effect=_happy_path_run_git(dest, "python-backend"),
            ),
            patch("nuguard.sbom.extractor.github_clone.httpx.Client", client_cls),
        ):
            sha = clone_github_subfolder(
                "https://github.com/org/repo",
                "main",
                "python-backend",
                dest,
                token="github_pat_" + "x" * 30,
            )
        assert sha == "abc123"
        get_calls = client_cls.return_value.__enter__.return_value.get.call_args_list
        assert len(get_calls) == 2
        assert "Authorization" in get_calls[0].kwargs["headers"]
        assert "Authorization" not in get_calls[1].kwargs["headers"]

    def test_explicit_ref_fetch_failure_does_not_fall_back(self, tmp_path: Path) -> None:
        dest = tmp_path / "clone"

        def _run_git(args, *, cwd, timeout, input_data=None):
            if args and args[0] == "fetch":
                raise subprocess.CalledProcessError(
                    1, args, stderr=b"fatal: couldn't find remote ref v1.0"
                )
            return _completed()

        with patch("nuguard.sbom.extractor.github_clone._run_git", side_effect=_run_git):
            with pytest.raises(RuntimeError, match="git clone"):
                clone_github_subfolder(
                    "https://github.com/org/repo", "v1.0", "python-backend", dest
                )


class TestSelectTreeFiles:
    def test_symlinks_excluded(self) -> None:
        selected = _select_tree_files(
            TREE_ENTRIES, "python-backend", max_files=100, max_total_bytes=10**9
        )
        assert "python-backend/link.py" not in selected
        assert "python-backend/app.py" in selected
        assert "README.md" not in selected

    def test_max_files_exceeded_raises(self) -> None:
        entries = [
            {"path": f"sub/f{i}.py", "type": "blob", "mode": "100644", "size": 1}
            for i in range(5)
        ]
        with pytest.raises(RuntimeError, match="more than"):
            _select_tree_files(entries, "sub", max_files=3, max_total_bytes=10**9)

    def test_max_bytes_exceeded_raises(self) -> None:
        entries = [{"path": "sub/big.bin", "type": "blob", "mode": "100644", "size": 10**9}]
        with pytest.raises(RuntimeError, match="exceeds"):
            _select_tree_files(entries, "sub", max_files=100, max_total_bytes=10)


class TestSparsePattern:
    def test_escapes_wildcard_characters(self) -> None:
        pattern = _sparse_pattern("weird[name]*.py")
        assert pattern == b"/weird" + rb"\[name\]\*" + b".py\n"

    def test_plain_filename_unescaped(self) -> None:
        assert _sparse_pattern("python-backend/app.py") == b"/python-backend/app.py\n"


class TestIsRepositoryNotFoundError:
    def test_matches_not_found_stderr(self) -> None:
        exc = RuntimeError(
            "git clone failed for 'https://github.com/org/repo/sub' @ None: "
            "remote: Not Found\nfatal: repository 'https://github.com/org/repo/sub/' not found"
        )
        assert is_repository_not_found_error(exc)

    def test_does_not_match_auth_failure(self) -> None:
        exc = RuntimeError(
            "git clone failed for 'https://github.com/org/repo' @ None: "
            "fatal: Authentication failed"
        )
        assert not is_repository_not_found_error(exc)

    def test_does_not_match_timeout(self) -> None:
        exc = RuntimeError(
            "git clone failed for 'https://github.com/org/repo' @ None: Connection timed out"
        )
        assert not is_repository_not_found_error(exc)
