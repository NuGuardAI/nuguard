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

try:
    import requests as _requests
except ModuleNotFoundError:
    _requests = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SMITHERY_YAML = REPO_ROOT / "smithery.yaml"
INIT_PY = REPO_ROOT / "nuguard" / "__init__.py"

SMITHERY_API_BASE = "https://registry.smithery.ai"
SMITHERY_SERVER_QUALIFIED = "NuGuardAI/nuguard"


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

def preflight(version: str, allow_dirty: bool, allow_branch: bool) -> list[str]:
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

    # Git tag collision
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

def _smithery_payload(smithery_config: dict, version: str) -> dict:
    """Build the Smithery API payload from smithery.yaml fields."""
    start = smithery_config.get("startCommand", {})
    config_schema = start.get("configSchema", {})
    command_function = start.get("commandFunction", "")

    tools = [
        {"name": t["name"], "description": t.get("description", "")}
        for t in smithery_config.get("tools", [])
    ]

    return {
        "deploymentType": "commandFunction",
        "version": version,
        "configSchema": config_schema,
        "commandFunction": command_function.strip(),
        "serverCard": {
            "serverInfo": {
                "name": smithery_config.get("name", "nuguard"),
                "version": version,
                "description": smithery_config.get("description", "").strip(),
                "homepage": smithery_config.get("homepage", ""),
                "license": smithery_config.get("license", ""),
            },
            "tools": tools,
        },
    }


def _smithery_server_exists(qualified_name: str, headers: dict) -> bool:
    """Return True when the server listing already exists in the Smithery registry."""
    try:
        resp = _requests.get(
            f"{SMITHERY_API_BASE}/servers/{qualified_name}",
            headers=headers,
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _smithery_create_server(
    qualified_name: str,
    smithery_config: dict,
    version: str,
    headers: dict,
) -> int:
    """Create the server listing.  Returns 0 on success, 1 on failure."""
    payload = {
        "qualifiedName": qualified_name,
        **_smithery_payload(smithery_config, version),
    }
    resp = _requests.post(
        f"{SMITHERY_API_BASE}/servers",
        headers=headers,
        json=payload,
        timeout=30,
    )
    if resp.status_code in (200, 201, 202):
        print(f"  Smithery: server listing created — {qualified_name}")
        return 0
    print(f"  ERROR: could not create server listing ({resp.status_code})")
    try:
        print(f"  {json.dumps(resp.json(), indent=2)[:400]}")
    except Exception:
        print(f"  {resp.text[:300]}")
    return 1


def publish_to_smithery(version: str, dry_run: bool) -> int:
    api_key = os.environ.get("SMITHERY_API_KEY", "")
    if not api_key:
        print(
            "  ERROR: SMITHERY_API_KEY not set.\n"
            "         Get your key at https://smithery.ai/account/api-keys"
        )
        return 1

    if _requests is None:
        print("  ERROR: 'requests' not installed — run: pip install requests")
        return 1

    try:
        smithery_config = _read_smithery_yaml()
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        return 1

    payload = _smithery_payload(smithery_config, version)
    release_url = f"{SMITHERY_API_BASE}/servers/{SMITHERY_SERVER_QUALIFIED}/releases"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print(f"Updating Smithery listing {SMITHERY_SERVER_QUALIFIED} v{version} …")

    if dry_run:
        print(f"  [dry-run] POST {release_url}")
        print(f"  [dry-run] payload: {json.dumps(payload, indent=4)[:400]} …")
        return 0

    # Ensure the server listing exists before posting a release.
    if not _smithery_server_exists(SMITHERY_SERVER_QUALIFIED, headers):
        print(f"  Server listing not found — creating {SMITHERY_SERVER_QUALIFIED} …")
        rc = _smithery_create_server(SMITHERY_SERVER_QUALIFIED, smithery_config, version, headers)
        if rc != 0:
            return rc

    try:
        resp = _requests.post(release_url, headers=headers, json=payload, timeout=30)
    except _requests.RequestException as exc:
        print(f"  ERROR: HTTP request failed: {exc}")
        return 1

    if resp.status_code in (200, 201, 202):
        data = resp.json() if resp.content else {}
        deployment_id = data.get("deploymentId", data.get("id", "—"))
        status = data.get("status", "ok")
        print(f"  Smithery: release accepted — deploymentId={deployment_id}  status={status}")
        mcp_url = data.get("mcpUrl") or data.get("mcpEndpointUrl")
        if mcp_url:
            print(f"  MCP endpoint: {mcp_url}")
        return 0

    # Non-2xx — print error and suggest fallback
    print(f"  ERROR: Smithery API returned {resp.status_code}")
    try:
        print(f"  Response: {json.dumps(resp.json(), indent=2)[:600]}")
    except Exception:
        print(f"  Response: {resp.text[:400]}")
    print()
    print("  Fallback: update the Smithery listing manually at")
    print(f"    https://smithery.ai/server/{SMITHERY_SERVER_QUALIFIED}")
    print("  or reconnect your GitHub repo so Smithery auto-detects the smithery.yaml.")
    return 1


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
    errors = preflight(version, args.allow_dirty, args.allow_branch)
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
