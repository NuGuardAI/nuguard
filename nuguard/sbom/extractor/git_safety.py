"""Shared argument-injection defenses for anything that shells out to ``git``.

Both :class:`~nuguard.sbom.extractor.core.AiSbomExtractor` (plain ``git
clone``) and :mod:`~nuguard.sbom.extractor.github_clone` (sparse-checkout
subfolder clone) accept a ``ref``/``url`` pair that ultimately becomes a
positional argument to a ``git`` subprocess. A hostile ref or URL starting
with ``-`` could be reinterpreted as a flag (``--upload-pack=<cmd>``,
``--config=<key>=<value>``, ...), so both validate against the same regexes
here to avoid the definitions drifting apart.
"""

from __future__ import annotations

import re

# Git rejects positional refs that begin with ``-`` by refusing them as
# "ambiguous argument", but only after it has already consumed earlier
# options. Some versions of git also accept leading ``-`` arguments as flags
# in older builds (see CVE-2017-1000117 et al.), so we reject the value
# up-front rather than rely on the child process's behaviour.
SAFE_REF_RE = re.compile(r"^[^-,\s\x00][^,\s\x00]*\Z")

# Accepted at the clone boundary:
#   - http(s)://host/path
#   - ssh://[user@]host[:port]/path
#   - scp-style SSH: [user@]host:path  where the path either starts with
#     ``/`` (absolute) or with a non-flag character (so a hostile provider
#     cannot smuggle a flag through ``git@host:--option``).
# The scp path sub-pattern forbids ``-``/``,``/``\s``/``\x00``/``\Z`` at the
# start (no leading flag or whitespace) and continues with safe characters.
# The host portion also forbids ``-`` so a hostile user@ portion cannot
# itself look like an option.
SAFE_URL_RE = re.compile(
    r"(?:https?|ssh)://[^\s\x00]*\Z"
    r"|"
    r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^-,:\s\x00][^,\s\x00]*\Z"
)


def validate_ref(ref: str | None) -> None:
    if ref is not None and not SAFE_REF_RE.match(ref):
        raise ValueError(
            f"Invalid git ref {ref!r}: must not be empty, start with '-', "
            "or contain whitespace."
        )


def validate_url(url: str) -> None:
    if not SAFE_URL_RE.match(url):
        raise ValueError(
            f"Invalid repository URL {url!r}: must be an absolute URL with "
            "an explicit scheme (e.g. https://...)."
        )
