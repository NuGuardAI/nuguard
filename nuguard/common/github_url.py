"""Parse GitHub repository URLs that may include a subfolder path.

Supports two forms, both rooted at ``github.com``:

* GitHub's web-UI "tree" form, with the ref embedded in the URL::

    https://github.com/<owner>/<repo>/tree/<ref>/<subpath...>

* A bare shorthand with no explicit ref (the caller decides the ref via
  ``--ref``/config/default branch, same as a plain repo-root URL today)::

    https://github.com/<owner>/<repo>/<subpath...>

Any other host (GitLab, Bitbucket, self-hosted git, plain local paths) is
intentionally left alone — :func:`try_parse_github_subfolder` returns
``None`` so callers fall through to the existing, unmodified clone path.

The bare-shorthand form is structurally ambiguous: GitHub has no nested
orgs, so any 3-plus-segment ``github.com`` path *could* be a subfolder, but
it could also be one of GitHub's own UI pages (``.../releases``,
``.../wiki``, ...). This module deliberately does not try to guess that —
see ``nuguard/sbom/extractor/github_clone.py`` and its callers for the
"try the direct clone first, only reinterpret as a subfolder on a
definitive not-found failure" resolution strategy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})

_TREE_RE = re.compile(
    r"^(?P<root>https?://(?:www\.)?github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)"
    r"(?:\.git)?/tree/(?P<ref>[^/]+)/(?P<subpath>.+)$"
)
_ROOT_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+?(?:\.git)?/?$"
)
_BARE_SUBPATH_RE = re.compile(
    r"^(?P<root>https?://(?:www\.)?github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)"
    r"(?:\.git)?/(?P<subpath>[^/].*)$"
)


@dataclass(frozen=True)
class GitHubRepoRef:
    """A GitHub URL decomposed into its repo root, embedded ref, and subpath.

    ``subpath`` is ``None`` for a plain repo-root URL — the existing,
    unaffected case. ``url_ref`` is only ever set for the ``/tree/<ref>/``
    form; the bare-shorthand form always has ``url_ref=None`` since it
    carries no ref of its own.
    """

    repo_root_url: str
    url_ref: str | None
    subpath: str | None

    @property
    def is_ambiguous_shorthand(self) -> bool:
        """True when *subpath* came from the bare-shorthand form.

        Callers should attempt a direct clone of the original URL first and
        only fall back to treating *subpath* as a subfolder on a definitive
        "not found" failure — see ``github_clone.py``.
        """
        return self.subpath is not None and self.url_ref is None


def _reject_unsafe_subpath(subpath: str, url: str) -> None:
    if ".." in subpath.split("/"):
        raise ValueError(f"Invalid repository folder URL (path traversal): {url!r}")
    if "%2f" in subpath.lower():
        raise ValueError(f"Invalid repository folder URL (encoded slash): {url!r}")


def parse_github_repo_and_subfolder(url: str) -> GitHubRepoRef:
    """Split a GitHub URL into repo root, embedded ref, and optional subfolder.

    Raises:
        ValueError: *url* is not a ``github.com``/``www.github.com`` URL,
            or its subpath contains a ``..`` traversal segment or an
            encoded slash (``%2f``).
    """
    host = urlsplit(url).hostname or ""
    if host.lower() not in _GITHUB_HOSTS:
        raise ValueError(f"Not a GitHub URL: {url!r}")

    stripped = url.strip().rstrip("/")

    match = _TREE_RE.match(stripped)
    if match:
        subpath = match.group("subpath")
        _reject_unsafe_subpath(subpath, url)
        return GitHubRepoRef(
            repo_root_url=match.group("root"),
            url_ref=match.group("ref"),
            subpath=subpath,
        )

    if _ROOT_RE.match(stripped):
        root = stripped.removesuffix(".git")
        return GitHubRepoRef(repo_root_url=root, url_ref=None, subpath=None)

    match = _BARE_SUBPATH_RE.match(stripped)
    if match:
        subpath = match.group("subpath")
        _reject_unsafe_subpath(subpath, url)
        return GitHubRepoRef(
            repo_root_url=match.group("root"), url_ref=None, subpath=subpath
        )

    raise ValueError(f"Could not parse GitHub repository URL: {url!r}")


def try_parse_github_subfolder(url: str) -> GitHubRepoRef | None:
    """Return a :class:`GitHubRepoRef` with a subpath, or ``None``.

    ``None`` covers: non-``github.com`` hosts, plain repo-root URLs (no
    subfolder), and any URL this parser can't confidently interpret. In all
    of those cases the caller should fall through to the existing,
    unmodified clone path unchanged.
    """
    try:
        ref = parse_github_repo_and_subfolder(url)
    except ValueError:
        return None
    if ref.subpath is None:
        return None
    return ref
