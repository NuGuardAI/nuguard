#!/usr/bin/env python3
"""
publish_smithery.py — Release nuguard to PyPI and update the Smithery.ai listing.

Workflow
--------
1. Pre-flight  — version consistency (pyproject.toml, smithery.yaml, __init__.py)
               — git state (clean tree, on main, no pending push to origin)
2. PyPI build  — `uv build` produces dist/nuguard-{version}.*
3. PyPI publish — `uv publish` (token via $UV_PUBLISH_TOKEN or $PYPI_TOKEN)
4. Smithery    — sync the server listing via the Smithery REST API
5. Git tag     — create annotated v{version} tag and push it

Usage
-----
    python scripts/publish_smithery.py
    python scripts/publish_smithery.py --dry-run
    python scripts/publish_smithery.py --skip-pypi          # Smithery + tag only
    python scripts/publish_smithery.py --skip-smithery      # PyPI + tag only
    python scripts/publish_smithery.py --skip-git-tag
    python scripts/publish_smithery.py --allow-dirty        # skip git-clean check
    python scripts/publish_smithery.py --allow-branch       # skip branch == main check

Environment variables
---------------------
    UV_PUBLISH_TOKEN   PyPI token (preferred)
    PYPI_TOKEN         PyPI token (fallback)
    SMITHERY_API_KEY   Smithery API key  (https://smithery.ai/account/api-keys)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-reattr]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SMITHERY_YAML = REPO_ROOT / "smithery.yaml"
INIT_PY = REPO_ROOT / "nuguard" / "__init__.py"

SMITHERY_SERVER_QUALIFIED = "NuGuardAI/nuguard"
SMITHERY_BUNDLE = REPO_ROOT / "server.mcpb"


# ---------------------------------------------------------------------------
# Version reading
# ---------------------------------------------------------------------------

def _read_pyproject_version() -> str:
    if tomllib is None:
        # Fallback: regex parse
        text = PYPROJECT.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not m:
            raise ValueError("Could not parse version from pyproject.toml")
        return m.group(1)
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["version"]


def _read_smithery_version() -> str:
    text = SMITHERY_YAML.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
        return str(data.get("version", ""))
    # Fallback: regex
    m = re.search(r'^version:\s*["\']?([^\s"\']+)["\']?', text, re.MULTILINE)
    if not m:
        raise ValueError("Could not parse version from smithery.yaml")
    return m.group(1)


def _read_init_version() -> str:
    text = INIT_PY.read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        raise ValueError(f"Could not parse __version__ from {INIT_PY}")
    return m.group(1)


def _read_smithery_yaml() -> dict:
    text = SMITHERY_YAML.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text)
    raise RuntimeError("PyYAML not installed — run: pip install pyyaml")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run(
    cmd: list[str],
    *,
    capture: bool = True,
    dry_run: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    cwd = cwd or REPO_ROOT
    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    result = subprocess.run(cmd, capture_output=capture, text=True, cwd=cwd)
    return result


def _git_is_clean() -> bool:
    r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=REPO_ROOT)
    return r.stdout.strip() == ""


def _git_current_branch() -> str:
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT)
    return r.stdout.strip()


def _git_tag_exists(tag: str) -> bool:
    r = subprocess.run(["git", "tag", "--list", tag], capture_output=True, text=True, cwd=REPO_ROOT)
    return r.stdout.strip() == tag


def _git_commits_ahead_of_origin() -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "^origin/HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def preflight(version: str, allow_dirty: bool, allow_branch: bool, skip_git_tag: bool) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []

    # Version consistency
    pyproject_ver = _read_pyproject_version()
    if pyproject_ver != version:
        errors.append(
            f"Version mismatch: pyproject.toml={pyproject_ver!r}  expected={version!r}\n"
            f"  Update pyproject.toml or pass --version {pyproject_ver}"
        )

    smithery_ver = _read_smithery_version()
    if smithery_ver != version:
        errors.append(
            f"Version mismatch: smithery.yaml={smithery_ver!r}  expected={version!r}\n"
            f"  Update smithery.yaml version: field or pass --version {smithery_ver}"
        )

    try:
        init_ver = _read_init_version()
        if init_ver != version:
            warnings.append(
                f"nuguard/__init__.py __version__={init_ver!r} differs from pyproject.toml {version!r}"
            )
    except ValueError as e:
        warnings.append(str(e))

    # Git state
    if not allow_dirty and not _git_is_clean():
        errors.append(
            "Working tree is not clean. Commit or stash changes first.\n"
            "  Pass --allow-dirty to skip this check."
        )

    if not allow_branch:
        branch = _git_current_branch()
        if branch != "main":
            errors.append(
                f"Not on 'main' branch (current: {branch!r}).\n"
                "  Pass --allow-branch to skip this check."
            )

    # Git tag collision — only relevant if this run will try to create the
    # tag itself. When --skip-git-tag is passed (e.g. the release-triggered
    # CI path, where creating the GitHub Release is what created the tag),
    # the tag already existing is expected, not a collision.
    if not skip_git_tag:
        tag = f"v{version}"
        if _git_tag_exists(tag):
            errors.append(
                f"Git tag {tag!r} already exists. Bump the version first."
            )

    for w in warnings:
        print(f"  WARNING: {w}")

    return errors


# ---------------------------------------------------------------------------
# PyPI build + publish
# ---------------------------------------------------------------------------

def build_package(dry_run: bool) -> int:
    print("Building dist/ with uv build …")
    r = _run(["uv", "build"], capture=False, dry_run=dry_run, cwd=REPO_ROOT)
    if r.returncode != 0:
        print(f"  ERROR: uv build failed (exit {r.returncode})")
        return r.returncode
    return 0


def publish_to_pypi(dry_run: bool) -> int:
    token = os.environ.get("UV_PUBLISH_TOKEN") or os.environ.get("PYPI_TOKEN")
    if not token:
        print("  ERROR: Set UV_PUBLISH_TOKEN (or PYPI_TOKEN) before publishing to PyPI.")
        return 1

    print("Publishing to PyPI with uv publish …")
    env = {**os.environ, "UV_PUBLISH_TOKEN": token}
    cmd = ["uv", "publish"]
    if dry_run:
        print(f"  [dry-run] UV_PUBLISH_TOKEN=<redacted> {' '.join(cmd)}")
        return 0
    r = subprocess.run(cmd, env=env, cwd=REPO_ROOT)
    if r.returncode != 0:
        print(f"  ERROR: uv publish failed (exit {r.returncode})")
    return r.returncode


# ---------------------------------------------------------------------------
# Smithery publishing
# ---------------------------------------------------------------------------

def _build_smithery_bundle(version: str) -> Path:
    """Build server.mcpb from smithery.yaml and return the path."""
    import zipfile

    try:
        smithery_config = _read_smithery_yaml()
    except RuntimeError as e:
        raise RuntimeError(f"Could not read smithery.yaml: {e}") from e

    start = smithery_config.get("startCommand", {})
    config_schema = start.get("configSchema", {})

    # Convert JSON Schema configSchema → user_config (smithery bundle format)
    user_config: dict = {}
    for key, prop in config_schema.get("properties", {}).items():
        entry: dict = {"type": prop.get("type", "string")}
        if prop.get("description"):
            entry["description"] = prop["description"].strip()
        if "default" in prop:
            entry["default"] = prop["default"]
        user_config[key] = entry

    # Build env mapping that forwards user_config values as env vars
    env = {}
    if "litellm_api_key" in user_config:
        env["LITELLM_API_KEY"] = "${user_config.litellm_api_key}"
    if "nuguard_config_path" in user_config:
        env["NUGUARD_DEFAULT_CONFIG"] = "${user_config.nuguard_config_path}"
    if "redteam_llm_model" in user_config:
        env["NUGUARD_REDTEAM_LLM_MODEL"] = "${user_config.redteam_llm_model}"

    tools = [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "inputSchema": {"type": "object", "properties": {}},
        }
        for t in smithery_config.get("tools", [])
    ]

    manifest = {
        "name": smithery_config.get("name", "nuguard"),
        "version": version,
        "server": {
            "type": "binary",
            "mcp_config": {
                "command": "uvx",
                "args": ["--from", "nuguard[mcp]", "nuguard-mcp"],
                "env": env,
            },
        },
        "user_config": user_config,
        "tools": tools,
    }

    bundle_path = REPO_ROOT / "server.mcpb"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    return bundle_path


def publish_to_smithery(version: str, dry_run: bool) -> int:
    print(f"Updating Smithery listing {SMITHERY_SERVER_QUALIFIED} v{version} …")

    try:
        bundle_path = _build_smithery_bundle(version)
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        return 1

    print(f"  Built bundle: {bundle_path.name} ({bundle_path.stat().st_size} bytes)")

    cmd = [
        "smithery", "mcp", "publish",
        str(bundle_path),
        "-n", SMITHERY_SERVER_QUALIFIED,
    ]

    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return 0

    r = _run(cmd, capture=False)
    if r.returncode != 0:
        print(f"  ERROR: smithery mcp publish exited {r.returncode}")
        return r.returncode

    print(f"  Smithery: published https://smithery.ai/servers/{SMITHERY_SERVER_QUALIFIED}")
    return 0


# ---------------------------------------------------------------------------
# Git tag
# ---------------------------------------------------------------------------

def create_git_tag(version: str, dry_run: bool) -> int:
    tag = f"v{version}"
    print(f"Creating git tag {tag} …")

    r = _run(
        ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
        cwd=REPO_ROOT,
        dry_run=dry_run,
    )
    if r.returncode != 0:
        print(f"  ERROR: git tag failed\n  {r.stderr.strip()}")
        return r.returncode

    r = _run(["git", "push", "origin", tag], capture=False, dry_run=dry_run)
    if r.returncode != 0:
        print(f"  ERROR: git push tag failed (exit {r.returncode})")
        return r.returncode

    print(f"  Pushed {tag} to origin.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Publish nuguard to PyPI and update the Smithery.ai listing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--version",
        default=None,
        metavar="X.Y.Z",
        help="Version to publish (default: read from pyproject.toml)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print actions without executing them")
    p.add_argument("--skip-pypi", action="store_true", help="Skip PyPI build and publish")
    p.add_argument("--skip-smithery", action="store_true", help="Skip Smithery listing update")
    p.add_argument("--skip-git-tag", action="store_true", help="Skip creating and pushing the git tag")
    p.add_argument("--allow-dirty", action="store_true", help="Allow publishing with uncommitted changes")
    p.add_argument("--allow-branch", action="store_true", help="Allow publishing from a non-main branch")
    p.add_argument(
        "--smithery-server",
        default=SMITHERY_SERVER_QUALIFIED,
        metavar="ORG/NAME",
        help=f"Smithery qualifiedName (default: {SMITHERY_SERVER_QUALIFIED})",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    version = args.version or _read_smithery_version()
    print(f"nuguard publish {'(dry run) ' if args.dry_run else ''}— v{version}")
    print()

    # Pre-flight
    print("Pre-flight checks …")
    errors = preflight(version, args.allow_dirty, args.allow_branch, args.skip_git_tag)
    if errors:
        print()
        for e in errors:
            print(f"  FAIL: {e}")
        print()
        print("Fix the issues above and re-run. Use --dry-run to preview without publishing.")
        return 1
    print("  All checks passed.")
    print()

    # Override qualified name if supplied
    global SMITHERY_SERVER_QUALIFIED
    SMITHERY_SERVER_QUALIFIED = args.smithery_server

    # PyPI
    if not args.skip_pypi:
        rc = build_package(args.dry_run)
        if rc != 0:
            return rc
        rc = publish_to_pypi(args.dry_run)
        if rc != 0:
            return rc
        print()

    # Smithery
    if not args.skip_smithery:
        rc = publish_to_smithery(version, args.dry_run)
        if rc != 0:
            return rc
        print()

    # Git tag
    if not args.skip_git_tag:
        rc = create_git_tag(version, args.dry_run)
        if rc != 0:
            return rc
        print()

    print(f"Release v{version} complete.")
    if not args.skip_pypi:
        print(f"  PyPI:     https://pypi.org/project/nuguard/{version}/")
    if not args.skip_smithery:
        print(f"  Smithery: https://smithery.ai/server/{SMITHERY_SERVER_QUALIFIED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
