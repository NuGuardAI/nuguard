"""Stable finding fingerprints (``nf1_…``).

A fingerprint identifies "the same defect in the same repository" across runs.
It never includes line numbers, absolute paths, run ids, timestamps or titles,
so reformatting a file (e.g. adding blank lines) does not change it::

    "nf1_" + sha256("\\x1f".join([repo, kind, source_tool, rule_id, identity_key]))[:32]

``identity_key`` depends on the finding kind:

=============  ===============================================================
code           ``path#symbol`` (enclosing Python function/class), else
               ``path#<sha256 of the matched source, whitespace-normalized>``
posture        SBOM node ``NODE_TYPE:canonical name`` (node UUIDs change per run)
dependency     ``ecosystem:package@<major>#<manifest path>``
iac            ``path#<resource address>``
container      image reference without tag/digest + ``#`` + package name
supply_chain   ``path`` (the rule id already disambiguates)
=============  ===============================================================
"""

from __future__ import annotations

import ast
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from nuguard.models.finding import Finding

PREFIX = "nf1_"
_WS_RE = re.compile(r"\s+")


def normalize_repo(url: str) -> str:
    """``https://GitHub.com/Org/Repo.git/tree/x`` → ``github.com/org/repo``."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s][:2]
    if segments:
        segments[-1] = segments[-1].removesuffix(".git")
    return "/".join([(parts.hostname or "").lower(), *segments]).lower()


def compute(repo: str, kind: str, source_tool: str, rule_id: str, identity_key: str) -> str:
    payload = "\x1f".join([repo, kind, source_tool, rule_id, identity_key])
    return PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def identity_key(finding: Finding, kind: str, scan_root: Path | None) -> str:
    loc = finding.locations[0] if finding.locations else None
    if kind == "code":
        if loc is None:
            return finding.affected_component or ""
        symbol = loc.symbol or python_symbol(scan_root, loc.path, loc.start_line)
        if symbol:
            return f"{loc.path}#{symbol}"
        return f"{loc.path}#{snippet_hash(scan_root, loc.path, loc.start_line, loc.end_line)}"
    if kind == "posture":
        if finding.component_ref:
            return finding.component_ref
        if finding.affected_component:
            return "affected:" + finding.affected_component.strip().lower()
        return loc.path if loc else ""
    if kind == "dependency":
        return f"{_package_key(finding.affected_component or '')}#{loc.path if loc else ''}"
    if kind == "iac":
        return f"{loc.path if loc else ''}#{(loc.symbol if loc else None) or ''}"
    if kind == "container":
        image = _strip_tag(finding.container_image or "")
        package = _package_key(finding.affected_component or "", with_version=False)
        return f"{image}#{package}"
    return loc.path if loc else (finding.affected_component or "")


def _package_key(component: str, *, with_version: bool = True) -> str:
    """``pkg:pypi/requests@2.19.0`` → ``pypi:requests@2``; ``lib@1.2`` → ``:lib@1``."""
    first = component.split(",")[0].strip()
    ecosystem = ""
    if first.startswith("pkg:"):
        body = first[4:]
        ecosystem, _, rest = body.partition("/")
        first = rest
    first = first.split("?")[0].split("#")[0]
    name, _, version = first.rpartition("@") if "@" in first[1:] else (first, "", "")
    name = name or first
    major = re.split(r"[.\-+]", version.lstrip("vV"))[0] if version else ""
    key = f"{ecosystem.lower()}:{name.lower()}"
    return f"{key}@{major}" if with_version else key


def _strip_tag(image: str) -> str:
    image = image.split("@")[0]
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        image = image[: len(image) - len(last)] + last.split(":")[0]
    return image.lower()


def _safe_file(scan_root: Path | None, rel: str) -> Path | None:
    if scan_root is None:
        return None
    root = scan_root.resolve()
    try:
        path = (root / rel).resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def snippet_hash(scan_root: Path | None, rel: str, start: int | None, end: int | None) -> str:
    path = _safe_file(scan_root, rel)
    if path is None or start is None:
        return "nosnippet"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "nosnippet"
    text = "\n".join(lines[start - 1 : (end or start)])
    normalized = _WS_RE.sub(" ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def python_symbol(scan_root: Path | None, rel: str, line: int | None) -> str | None:
    """Qualified name of the innermost Python def/class containing *line*."""
    if line is None or not rel.endswith(".py"):
        return None
    path = _safe_file(scan_root, rel)
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    spans = _python_spans(str(path), stat.st_mtime_ns, stat.st_size)
    best: tuple[int, str] | None = None
    for start, end, name in spans:
        if start <= line <= end and (best is None or start >= best[0]):
            best = (start, name)
    return best[1] if best else None


@lru_cache(maxsize=512)
def _python_spans(path: str, _mtime_ns: int, _size: int) -> tuple[tuple[int, int, str], ...]:
    """Def/class spans of a Python file (cache keyed by path + mtime + size)."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return ()
    spans: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                spans.append((child.lineno, child.end_lineno or child.lineno, name))
                walk(child, name + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return tuple(spans)


def clear_caches() -> None:
    _python_spans.cache_clear()
