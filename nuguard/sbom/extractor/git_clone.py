"""Hardened ``git clone`` shared by the SBOM extractor and the remote scan service.

What this adds over a bare ``git clone``:

* **Token without URL embedding.** A repository token is handed to git through
  an ephemeral credential helper that reads ``NUGUARD_GIT_TOKEN`` from the git
  child process's environment. The token never appears in argv, the clone URL,
  ``.git/config``, logs, or exception text. (Chosen over ``GIT_ASKPASS`` because
  it needs no helper script on disk.)
* **Host policy.** With ``allowed_hosts`` set, only ``https://`` URLs on exactly
  those hosts are accepted — no userinfo, IP literals or ``localhost`` — and git
  runs with ``GIT_ALLOW_PROTOCOL=https``, no system/global config, ``HOME`` set
  to a private directory, no LFS smudge and symlinks checked out as plain files.
* **Limits.** A wall-clock timeout and a cap on the checked-out tree size.
* **Provenance.** Returns the commit SHA actually checked out.

Failures raise :class:`CloneError` (a ``RuntimeError``) with a stable ``code``.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nuguard.common.logging import get_logger
from nuguard.common.url_sanitization import (
    redact_repository_url_from_text,
    sanitize_repository_url,
)
from nuguard.sbom.extractor.git_safety import validate_ref, validate_url

_log = get_logger(__name__)

TOKEN_ENV = "NUGUARD_GIT_TOKEN"

# The helper runs under `sh`; it echoes the token from the environment, so the
# value itself is never part of any command line.
_CREDENTIAL_HELPER = (
    "!f() { echo username=x-access-token; "
    f'echo "password=${TOKEN_ENV}"; }}; f'
)

_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

# Environment variables a git child process may inherit from the parent, even
# under a host policy. Proxy and CA settings are kept so clones work behind a
# corporate proxy or TLS-inspecting gateway (git and libcurl read these names,
# in both cases). Credentials (GH_TOKEN, GITHUB_TOKEN, NUGUARD_*) are never in
# this list; a repository token reaches git only through ``TOKEN_ENV``.
_PROXY_ENV = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALL_PROXY",
    "all_proxy",
)
_CA_ENV = ("SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO", "CURL_CA_BUNDLE")
_INHERITED_ENV = (
    "PATH",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "TMPDIR",
    "TEMP",
    "TMP",
    *_PROXY_ENV,
    *_CA_ENV,
)


class CloneError(RuntimeError):
    """A clone failed. ``code`` is one of the stable values below; the message is sanitized.

    Codes: ``invalid_url``, ``host_not_allowed``, ``repo_not_found_or_no_access``,
    ``auth_failed``, ``ref_not_found``, ``clone_timeout``, ``repo_too_large``,
    ``git_unavailable``, ``clone_failed``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CloneOptions:
    """How to authenticate and constrain one clone."""

    token: str | None = field(default=None, repr=False)
    allowed_hosts: tuple[str, ...] | None = None
    timeout_s: float = 120.0
    max_repo_bytes: int = 500 * 1024 * 1024


def clone_options_for(config: Any, token: str | None = None) -> CloneOptions:
    """Build :class:`CloneOptions` from an ``AiSbomConfig`` plus an optional token."""
    hosts = getattr(config, "allowed_git_hosts", None)
    return CloneOptions(
        token=token or None,
        allowed_hosts=tuple(hosts) if hosts is not None else None,
        timeout_s=float(getattr(config, "clone_timeout_s", 120.0)),
        max_repo_bytes=int(getattr(config, "max_repo_bytes", 500 * 1024 * 1024)),
    )


def check_clone_url(url: str, allowed_hosts: tuple[str, ...] | list[str] | None) -> None:
    """Validate *url* for cloning; with *allowed_hosts*, apply the strict policy."""
    try:
        validate_url(url)
    except ValueError:
        raise CloneError("invalid_url", "Repository URL is not an absolute git URL") from None
    if allowed_hosts is None:
        return
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise CloneError("invalid_url", "Repository URL must use https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise CloneError("invalid_url", "Repository URL must not contain credentials")
    if parts.query or parts.fragment:
        raise CloneError("invalid_url", "Repository URL must not contain a query or fragment")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.port not in (None, 443):
        raise CloneError("invalid_url", "Repository URL has no valid host")
    if host == "localhost" or host.endswith(".localhost") or _is_ip_literal(host):
        raise CloneError("host_not_allowed", "Repository host is not allowed")
    if host not in {h.lower().rstrip(".") for h in allowed_hosts}:
        raise CloneError("host_not_allowed", "Repository host is not allowed")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def git_env(options: CloneOptions, home: Path) -> dict[str, str]:
    """Environment for a git child process.

    Under a host policy the environment is minimal and isolated (private
    ``HOME``, no system/global git config). Without one, the caller's
    environment is kept so existing SSH-agent / keychain clones still work.
    """
    if options.allowed_hosts is not None:
        env = {k: os.environ[k] for k in _INHERITED_ENV if k in os.environ}
        env.update(
            {
                "HOME": str(home),
                "GIT_ALLOW_PROTOCOL": "https",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            }
        )
    else:
        env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GCM_INTERACTIVE": "never"})
    env.pop(TOKEN_ENV, None)
    if options.token:
        env[TOKEN_ENV] = options.token
    return env


def git_config_args(options: CloneOptions) -> list[str]:
    """``-c`` options: our credential helper (when a token is given) and policy hardening."""
    args: list[str] = []
    if options.token or options.allowed_hosts is not None:
        # An empty value resets the inherited helper list, so only ours can answer.
        args += ["-c", "credential.helper=", "-c", "core.askPass="]
    if options.token:
        args += ["-c", f"credential.helper={_CREDENTIAL_HELPER}"]
    if options.allowed_hosts is not None:
        args += ["-c", "core.symlinks=false", "-c", "protocol.file.allow=never"]
    return args


def redact(text: str, url: str, token: str | None) -> str:
    """Remove URL credentials and the token from *text* (e.g. git stderr)."""
    text = redact_repository_url_from_text(text, url)
    if token:
        text = text.replace(token, "***")
    return text


def classify_git_failure(stderr: str) -> str:
    """Map git stderr to a stable error code.

    GitHub answers 404 for both a missing repo and a private repo the token
    cannot see, so those share ``repo_not_found_or_no_access`` (callers must
    not be able to probe for private repositories).
    """
    low = stderr.lower()
    if "remote branch" in low and "not found" in low:
        return "ref_not_found"
    if "couldn't find remote ref" in low or "could not find remote ref" in low:
        return "ref_not_found"
    if "not our ref" in low or "unadvertised object" in low:
        return "ref_not_found"
    if "authentication failed" in low or "invalid username or password" in low:
        return "auth_failed"
    if (
        "repository not found" in low
        or ("not found" in low and "repository" in low)
        or "could not read username" in low
        or "terminal prompts disabled" in low
        or "403" in low
    ):
        return "repo_not_found_or_no_access"
    return "clone_failed"


def tree_size(root: Path, limit: int) -> int:
    """Bytes in the working tree (excluding .git); stops counting past *limit*."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
            if total > limit:
                return total
    return total


def secure_clone(url: str, ref: str | None, dest: Path, options: CloneOptions) -> str | None:
    """Shallow-clone *url* at *ref* into *dest* and return the checked-out commit SHA.

    *ref* may be a branch, a tag, or a full 40-character commit SHA (fetched
    directly; GitHub allows fetching reachable commits by SHA). ``None`` clones
    the default branch. Returns ``None`` only if HEAD cannot be read back.
    """
    if shutil.which("git") is None:
        raise CloneError("git_unavailable", "git executable not found on PATH")
    check_clone_url(url, options.allowed_hosts)
    try:
        validate_ref(ref)
    except ValueError:
        raise CloneError("ref_not_found", "Invalid git ref") from None

    display_url = sanitize_repository_url(url)
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nuguard_git_home_") as home:
        env = git_env(options, Path(home))
        base = ["git", *git_config_args(options)]
        if ref is not None and _FULL_SHA_RE.match(ref):
            steps = [
                [*base, "init", "-q", str(dest)],
                [*base, "-C", str(dest), "remote", "add", "origin", url],
                [*base, "-C", str(dest), "fetch", "--depth", "1", "--no-tags",
                 "--no-recurse-submodules", "origin", ref],
                [*base, "-C", str(dest), "checkout", "--detach", "-q", "FETCH_HEAD"],
            ]
        else:
            # `git clone` never recurses into submodules unless asked (and the
            # submodule.recurse setting does not apply to clone).
            cmd = [*base, "clone", "--depth", "1"]
            if ref is not None:
                cmd += ["--branch", ref]
            steps = [[*cmd, "--", url, str(dest)]]

        _log.debug("cloning %s @ %s", display_url, ref or "default branch")
        for step in steps:
            _run(step, env, options, url, display_url, ref, dest)

    # Read HEAD from the checkout instead of spawning `git rev-parse`.
    sha = read_head_commit(dest)

    size = tree_size(dest, options.max_repo_bytes)
    if size > options.max_repo_bytes:
        shutil.rmtree(dest, ignore_errors=True)
        raise CloneError(
            "repo_too_large",
            f"Repository exceeds the {options.max_repo_bytes} byte checkout limit",
        )
    return sha


def read_head_commit(repo: Path) -> str | None:
    """Commit SHA that ``HEAD`` points at, read from ``.git`` without running git."""
    git_dir = repo / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if _FULL_SHA_RE.match(head):
        return head.lower()
    if not head.startswith("ref: "):
        return None
    ref = head[5:].strip()
    if not ref.startswith("refs/") or ".." in ref.split("/"):
        return None
    try:
        value = (git_dir / ref).read_text(encoding="utf-8").strip()
        if _FULL_SHA_RE.match(value):
            return value.lower()
    except OSError:
        pass
    try:
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition(" ")
            if name.strip() == ref and _FULL_SHA_RE.match(sha):
                return sha.lower()
    except OSError:
        pass
    return None


def _run(
    cmd: list[str],
    env: dict[str, str],
    options: CloneOptions,
    url: str,
    display_url: str,
    ref: str | None,
    dest: Path,
) -> str:
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            timeout=options.timeout_s,
            check=True,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise CloneError(
            "clone_timeout", f"git clone timed out after {options.timeout_s:g}s for {display_url!r}"
        ) from None
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else ""
        stderr = redact(stderr, url, options.token)
        code = classify_git_failure(stderr)
        # The message keeps sanitized stderr so is_repository_not_found_error()
        # (bare-shorthand subfolder fallback) still recognizes "not found".
        raise CloneError(
            code,
            f"git clone failed for {display_url!r} @ {ref!r}" + (f": {stderr[:500]}" if stderr else ""),
        ) from None
    except OSError:
        raise CloneError("git_unavailable", "git could not be started") from None
    return result.stdout.decode(errors="replace")
