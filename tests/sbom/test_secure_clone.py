"""Token-authenticated, policy-constrained clones (``git_clone.secure_clone``).

Uses a real ``git`` against a local smart-HTTP server with basic auth, so the
credential-helper path is exercised end to end. The token must never reach
argv, the clone URL, ``.git/config``, logs, results or exception text.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor.git_clone import (
    TOKEN_ENV,
    CloneError,
    CloneOptions,
    check_clone_url,
    git_env,
    read_head_commit,
    secure_clone,
)
from nuguard.sbom.public_api import SbomGenerateRequest, generate_sbom
from tests.git_http_server import GitHttpServer, make_bare_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

TOKEN = "github_pat_TESTTOKEN_0123456789abcdefghijklmnopqrstuv"
APP = {
    "app.py": "from openai import OpenAI\n\nclient = OpenAI()\n\n"
              "def ask(q):\n    return client.chat.completions.create(model='gpt-4o', "
              "messages=[{'role': 'user', 'content': q}])\n",
    "requirements.txt": "openai==1.40.0\n",
    "pkg/data.bin": "x" * 2048,
}


@pytest.fixture()
def private_repo(tmp_path: Path):
    root, sha = make_bare_repo(tmp_path, "agent", APP)
    with GitHttpServer(root, TOKEN) as server:
        yield server, server.url("agent"), sha


# ── real clones ──────────────────────────────────────────────────────────────


def test_token_clone_succeeds_and_returns_commit(private_repo, tmp_path: Path) -> None:
    server, url, sha = private_repo
    got = secure_clone(url, None, tmp_path / "out", CloneOptions(token=TOKEN))
    assert got == sha
    assert (tmp_path / "out" / "app.py").exists()
    assert any(h.startswith("Basic ") for h in server.auth_headers)
    assert TOKEN not in (tmp_path / "out" / ".git" / "config").read_text()


def test_clone_without_token_is_not_found_or_no_access(private_repo, tmp_path: Path) -> None:
    _, url, _ = private_repo
    with pytest.raises(CloneError) as exc:
        secure_clone(url, None, tmp_path / "out", CloneOptions())
    assert exc.value.code == "repo_not_found_or_no_access"


def test_wrong_token_is_auth_failed_and_redacted(private_repo, tmp_path: Path) -> None:
    _, url, _ = private_repo
    wrong = "github_pat_WRONG_abcdefghijklmnopqrstuvwxyz012345"
    with pytest.raises(CloneError) as exc:
        secure_clone(url, None, tmp_path / "out", CloneOptions(token=wrong))
    assert exc.value.code == "auth_failed"
    assert wrong not in str(exc.value)


def test_clone_by_full_sha_and_missing_branch(private_repo, tmp_path: Path) -> None:
    _, url, sha = private_repo
    assert secure_clone(url, sha, tmp_path / "a", CloneOptions(token=TOKEN)) == sha
    with pytest.raises(CloneError) as exc:
        secure_clone(url, "no-such-branch", tmp_path / "b", CloneOptions(token=TOKEN))
    assert exc.value.code == "ref_not_found"


def test_repo_size_cap(private_repo, tmp_path: Path) -> None:
    _, url, _ = private_repo
    dest = tmp_path / "out"
    with pytest.raises(CloneError) as exc:
        secure_clone(url, None, dest, CloneOptions(token=TOKEN, max_repo_bytes=1024))
    assert exc.value.code == "repo_too_large"
    assert not dest.exists()


def test_token_never_in_argv_only_in_child_env(private_repo, tmp_path: Path) -> None:
    _, url, _ = private_repo
    real_run = subprocess.run
    calls: list[tuple[list[str], dict[str, str]]] = []

    def spy(cmd, *args, **kwargs):
        calls.append((list(cmd), dict(kwargs.get("env") or {})))
        return real_run(cmd, *args, **kwargs)

    with patch("nuguard.sbom.extractor.git_clone.subprocess.run", side_effect=spy):
        secure_clone(url, None, tmp_path / "out", CloneOptions(token=TOKEN))
    # The patch is process-wide; ignore the test server's own `git http-backend` calls.
    calls = [(argv, env) for argv, env in calls if "http-backend" not in argv]
    assert calls
    for argv, env in calls:
        assert not any(TOKEN in part for part in argv)
        assert env.get(TOKEN_ENV) == TOKEN


@pytest.mark.asyncio
async def test_generate_sbom_with_repo_token(private_repo, caplog) -> None:
    _, url, sha = private_repo
    caplog.set_level(logging.DEBUG)
    result = await generate_sbom(SbomGenerateRequest(repo_url=url, repo_ref="main", repo_token=TOKEN))
    assert result.resolved_commit == sha
    assert result.node_count > 0
    assert TOKEN not in result.model_dump_json()
    assert TOKEN not in caplog.text


def test_request_never_serializes_token() -> None:
    request = SbomGenerateRequest(repo_url="https://github.com/o/r", repo_token=TOKEN)
    assert TOKEN not in request.model_dump_json()
    assert TOKEN not in repr(request)
    with pytest.raises(ValueError):
        SbomGenerateRequest(source_path=".", repo_token=TOKEN)


# ── host policy ──────────────────────────────────────────────────────────────

HOSTS = ("github.com",)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://github.com/o/r", "invalid_url"),
        ("ssh://git@github.com/o/r.git", "invalid_url"),
        ("git@github.com:o/r.git", "invalid_url"),
        ("file:///etc/passwd", "invalid_url"),
        ("https://user:pw@github.com/o/r", "invalid_url"),
        ("https://x-access-token@github.com/o/r", "invalid_url"),
        ("https://github.com/o/r?x=1", "invalid_url"),
        ("https://github.com:8443/o/r", "invalid_url"),
        ("https://gitlab.com/o/r", "host_not_allowed"),
        ("https://127.0.0.1/o/r", "host_not_allowed"),
        ("https://[::1]/o/r", "host_not_allowed"),
        ("https://localhost/o/r", "host_not_allowed"),
        ("https://github.com.evil.example/o/r", "host_not_allowed"),
        ("--upload-pack=touch /tmp/pwned", "invalid_url"),
    ],
)
def test_policy_rejects(url: str, code: str) -> None:
    with pytest.raises(CloneError) as exc:
        check_clone_url(url, HOSTS)
    assert exc.value.code == code


def test_policy_accepts_allowed_host() -> None:
    check_clone_url("https://github.com/org/repo", HOSTS)
    check_clone_url("https://GitHub.com/org/repo.git", HOSTS)


def test_policy_rejects_before_running_git(tmp_path: Path) -> None:
    with patch("nuguard.sbom.extractor.git_clone.subprocess.run") as run:
        with pytest.raises(CloneError):
            secure_clone("https://127.0.0.1/o/r", None, tmp_path, CloneOptions(allowed_hosts=HOSTS))
    run.assert_not_called()


def test_extractor_honours_allowed_git_hosts(tmp_path: Path) -> None:
    from nuguard.sbom.extractor import AiSbomExtractor

    with patch("nuguard.sbom.extractor.git_clone.subprocess.run") as run:
        with pytest.raises(CloneError):
            AiSbomExtractor().extract_from_repo(
                "https://gitlab.com/o/r", None, AiSbomConfig(allowed_git_hosts=["github.com"])
            )
    run.assert_not_called()


def test_policy_env_is_isolated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")
    env = git_env(CloneOptions(allowed_hosts=HOSTS), tmp_path)
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["HOME"] == str(tmp_path)
    assert env["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert "GITHUB_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
    assert TOKEN_ENV not in env


_PROXY_AND_CA = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALL_PROXY",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "GIT_SSL_CAINFO",
    "CURL_CA_BUNDLE",
)


def test_policy_env_keeps_proxy_and_ca_but_drops_credentials(monkeypatch, tmp_path: Path) -> None:
    """Behind a proxy the clone must still work, without leaking ambient credentials."""
    for name in _PROXY_AND_CA:
        monkeypatch.setenv(name, f"value-of-{name}")
    monkeypatch.setenv("GH_TOKEN", "ambient-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-github")
    monkeypatch.setenv("NUGUARD_API_KEY", "ambient-api-key")
    monkeypatch.setenv("NUGUARD_SERVICE_TOKEN", "ambient-service")
    monkeypatch.setenv(TOKEN_ENV, "ambient-git-token")

    env = git_env(CloneOptions(allowed_hosts=HOSTS), tmp_path)

    for name in _PROXY_AND_CA:
        assert env[name] == f"value-of-{name}", name
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert not [k for k in env if k.startswith("NUGUARD_")]
    assert "ambient" not in " ".join(env.values())


def test_policy_env_passes_only_the_requested_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("NUGUARD_OTHER", "x")
    env = git_env(CloneOptions(token="tok-123", allowed_hosts=HOSTS), tmp_path)
    assert env["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert [k for k in env if k.startswith("NUGUARD_")] == [TOKEN_ENV]
    assert env[TOKEN_ENV] == "tok-123"


def test_github_tree_api_honours_proxy_env(monkeypatch) -> None:
    """The tree-API client must read HTTPS_PROXY / SSL_CERT_FILE from the environment."""
    from nuguard.sbom.extractor import github_clone

    seen: dict = {}

    class _Client:
        def __init__(self, **kwargs) -> None:
            seen.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def get(self, *args, **kwargs):
            return "response"

    monkeypatch.setattr(github_clone.httpx, "Client", _Client)
    assert github_clone._get_tree("o", "r", "a" * 40, token=None, timeout_seconds=5) == "response"
    assert seen.get("trust_env", True) is True
    assert "proxy" not in seen and "mounts" not in seen and "transport" not in seen


def test_no_policy_keeps_caller_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")
    env = git_env(CloneOptions(), tmp_path)
    assert env["SSH_AUTH_SOCK"] == "/tmp/agent"
    assert "GIT_ALLOW_PROTOCOL" not in env


def test_clone_timeout(tmp_path: Path) -> None:
    with patch(
        "nuguard.sbom.extractor.git_clone.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 1),
    ):
        with pytest.raises(CloneError) as exc:
            secure_clone("https://github.com/o/r", None, tmp_path / "x", CloneOptions(timeout_s=1))
    assert exc.value.code == "clone_timeout"


def test_read_head_commit_handles_packed_refs(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text("# pack-refs\n" + "a" * 40 + " refs/heads/main\n")
    assert read_head_commit(tmp_path) == "a" * 40
    (git / "HEAD").write_text("ref: refs/../../etc/passwd\n")
    assert read_head_commit(tmp_path) is None
