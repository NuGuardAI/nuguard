"""GitHub subfolder-scoped clone: tree-manifest lookup + sparse-checkout.

Used by ``AiSbomExtractor.extract_from_repo_subfolder`` (and directly by
``nuguard analyze``'s remote re-clone) when ``--from-repo`` targets a
subfolder rather than a repository root. Mirrors NuGuard-app's own
``clone_github_repository`` (GitHub REST API tree-manifest + non-cone
``git sparse-checkout``), adapted for this package's synchronous CLI.

Only ever materializes the files that live under the requested subfolder —
a shallow, blobless ``git fetch`` combined with the commit-pinned file tree
from the GitHub REST API bounds how much is fetched before anything is
selected for checkout.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from nuguard.common.logging import get_logger
from nuguard.common.url_sanitization import (
    redact_repository_url_from_text,
    sanitize_repository_url,
)

from .git_safety import validate_ref, validate_url

_log = get_logger(__name__)

DEFAULT_MAX_FILES = 5000
DEFAULT_MAX_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MiB
_GITHUB_API_BASE = "https://api.github.com"
_WILDCARD_CHARS_RE = re.compile(r"([\\*?\[\]!])")

# Matches the exact failure shape from a nonexistent GitHub repo/path, e.g.:
#   remote: Not Found
#   fatal: repository 'https://github.com/org/repo/' not found
_NOT_FOUND_RE = re.compile(
    r"remote:\s*not found|fatal:\s*repository\b[^\n]*not found", re.IGNORECASE
)


def is_repository_not_found_error(exc: BaseException) -> bool:
    """True when *exc* (raised by ``_clone_repo``) is a definitive "not found".

    Used by the try-first-then-fallback logic in ``sbom.py``/``analyze.py``:
    a bare-shorthand ``--from-repo`` URL is first cloned as-is; only this
    specific failure shape triggers reinterpreting the trailing path
    segments as a subfolder. Any other failure (auth, network, timeout,
    rate-limit) must propagate unchanged instead of being silently
    reinterpreted as "maybe a subfolder."
    """
    return bool(_NOT_FOUND_RE.search(str(exc)))


def _sparse_pattern(path: str) -> bytes:
    """Escape git glob metacharacters so a literal filename can't be read as a pattern."""
    escaped = _WILDCARD_CHARS_RE.sub(r"\\\1", path)
    return f"/{escaped}\n".encode()


def _owner_repo_from_root(repo_root_url: str) -> tuple[str, str]:
    parts = urlsplit(repo_root_url).path.strip("/").split("/")
    if len(parts) != 2:
        raise ValueError(f"Not a GitHub owner/repo URL: {repo_root_url!r}")
    return parts[0], parts[1].removesuffix(".git")


def _run_git(
    args: list[str], *, cwd: Path, timeout: float, input_data: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=timeout,
        input=input_data,
    )


def _get_tree(
    owner: str, repo: str, commit_sha: str, *, token: str | None, timeout_seconds: float
) -> httpx.Response:
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{commit_sha}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "nuguard-sbom-cli"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            return client.get(url, params={"recursive": "1"}, headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"GitHub API request failed for {owner}/{repo}@{commit_sha}: {exc}"
        ) from exc


def _fetch_tree_manifest(
    owner: str, repo: str, commit_sha: str, *, token: str | None, timeout_seconds: float
) -> list[dict]:
    resp = _get_tree(owner, repo, commit_sha, token=token, timeout_seconds=timeout_seconds)

    if resp.status_code == 401 and token:
        # The token is well-formed but GitHub rejected it (expired/revoked/
        # unrelated ambient GH_TOKEN). Most repos targeted this way are
        # public and need no auth at all, so retry once unauthenticated
        # instead of hard-failing on a token nobody explicitly vouched for.
        _log.info(
            "GitHub API rejected the token (401 Bad credentials) fetching the file "
            "tree for %s/%s; retrying unauthenticated",
            owner,
            repo,
        )
        resp = _get_tree(owner, repo, commit_sha, token=None, timeout_seconds=timeout_seconds)
        token = None

    if resp.status_code in (403, 429) and not token:
        raise RuntimeError(
            f"GitHub API rate limit likely exceeded fetching the file tree for "
            f"{owner}/{repo} (unauthenticated requests are limited to 60/hour). "
            "Pass --token, or set GH_TOKEN/GITHUB_TOKEN, to authenticate."
        )
    if resp.status_code == 404:
        raise ValueError(
            f"Repository {owner}/{repo} or commit {commit_sha!r} not found via the GitHub API"
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"GitHub API error {resp.status_code} fetching the file tree for "
            f"{owner}/{repo}: {resp.text[:200]}"
        )

    data = resp.json()
    if data.get("truncated"):
        raise RuntimeError(
            f"GitHub file tree for {owner}/{repo}@{commit_sha} was truncated by the "
            "API (repository too large); cannot safely determine subfolder contents."
        )
    return list(data.get("tree", []))


def _select_tree_files(
    entries: list[dict], subpath: str, *, max_files: int, max_total_bytes: int
) -> list[str]:
    prefix = f"{subpath.rstrip('/')}/"
    selected: list[str] = []
    total_bytes = 0
    for entry in entries:
        path = entry.get("path", "")
        if not path.startswith(prefix) or entry.get("type") != "blob":
            continue
        if entry.get("mode") == "120000":  # symlink — do not materialize
            continue
        relative = path[len(prefix) :]
        if not relative or ".." in Path(relative).parts:
            continue
        selected.append(path)
        total_bytes += int(entry.get("size") or 0)
        if len(selected) > max_files:
            raise RuntimeError(
                f"Subfolder {subpath!r} contains more than {max_files} files; "
                "refusing to clone (adjust max_files if this is intentional)."
            )
        if total_bytes > max_total_bytes:
            raise RuntimeError(
                f"Subfolder {subpath!r} exceeds {max_total_bytes} bytes; refusing "
                "to clone (adjust max_total_bytes if this is intentional)."
            )
    return selected


def clone_github_subfolder(
    repo_root_url: str,
    ref: str | None,
    subpath: str,
    dest: Path,
    *,
    token: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    timeout_seconds: float = 120.0,
) -> str:
    """Clone only *subpath* of a GitHub repo into *dest* via sparse-checkout.

    Returns the resolved commit SHA that was checked out.

    Raises:
        ValueError: *subpath* does not exist / contains no eligible files
            in the repository at *ref*.
        RuntimeError: a git or GitHub-API operation failed (network, auth,
            git-not-found, rate limit, caps exceeded, ...). Never wrapped
            in ``SbomError`` — propagates the same way ``_clone_repo``'s
            ``RuntimeError`` does today, so CLI exit-code behavior for
            "repo not found" is unchanged.
    """
    import shutil

    validate_url(repo_root_url)
    validate_ref(ref)

    display_url = sanitize_repository_url(repo_root_url)
    owner, repo = _owner_repo_from_root(repo_root_url)
    dest.mkdir(parents=True, exist_ok=True)

    _log.info(
        "Cloning GitHub subfolder %r from %s (ref=%s)",
        subpath,
        display_url,
        ref or "default branch",
    )

    try:
        _run_git(["init", "-q"], cwd=dest, timeout=timeout_seconds)
        _run_git(["remote", "add", "origin", repo_root_url], cwd=dest, timeout=timeout_seconds)

        fetch_ref = ref or "HEAD"
        try:
            _run_git(
                ["fetch", "--depth", "1", "--filter=blob:none", "origin", fetch_ref],
                cwd=dest,
                timeout=timeout_seconds,
            )
        except subprocess.CalledProcessError:
            if ref is not None:
                raise
            _log.info(
                "Default-branch fetch failed for %s; retrying with 'master'", display_url
            )
            _run_git(
                ["fetch", "--depth", "1", "--filter=blob:none", "origin", "master"],
                cwd=dest,
                timeout=timeout_seconds,
            )

        commit_sha = (
            _run_git(["rev-parse", "FETCH_HEAD"], cwd=dest, timeout=timeout_seconds)
            .stdout.decode()
            .strip()
        )

        entries = _fetch_tree_manifest(
            owner, repo, commit_sha, token=token, timeout_seconds=timeout_seconds
        )
        selected = _select_tree_files(
            entries, subpath, max_files=max_files, max_total_bytes=max_total_bytes
        )
        _log.info(
            "Fetched tree manifest for %s@%s: %d entries, %d matched subpath %r",
            display_url,
            commit_sha,
            len(entries),
            len(selected),
            subpath,
        )
        if not selected:
            raise ValueError(
                f"Requested repository folder {subpath!r} not found in "
                f"{display_url!r} @ {ref or commit_sha!r}"
            )

        sparse_input = b"".join(_sparse_pattern(path) for path in selected)
        _run_git(
            ["sparse-checkout", "set", "--no-cone", "--stdin"],
            cwd=dest,
            timeout=timeout_seconds,
            input_data=sparse_input,
        )
        _run_git(
            ["checkout", "--detach", "FETCH_HEAD", "-q"], cwd=dest, timeout=timeout_seconds
        )

        scan_root = dest / subpath
        if not scan_root.is_dir():
            raise ValueError(
                f"Requested repository folder {subpath!r} does not exist or is "
                f"invalid in {display_url!r}"
            )
        return commit_sha
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else ""
        stderr = redact_repository_url_from_text(stderr, repo_root_url)
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(
            f"git clone (subfolder {subpath!r}) failed for {display_url!r} @ {ref!r}"
            + (f": {stderr}" if stderr else "")
        ) from exc
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
