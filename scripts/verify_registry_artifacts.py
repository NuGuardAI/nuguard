#!/usr/bin/env python3
"""Verify that existing registry artifacts match locally built release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class VerificationError(RuntimeError):
    """Raised when registry state cannot be safely treated as a retry."""


def _load_registry_json(url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise VerificationError(f"Registry request failed with HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VerificationError(f"Registry request failed: {url}: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VerificationError(f"Registry returned invalid JSON: {url}") from exc

    if not isinstance(payload, dict):
        raise VerificationError(f"Registry returned a non-object response: {url}")
    return payload


def verify_pypi(project: str, version: str, dist_dir: Path) -> bool:
    """Return whether PyPI has the exact local artifact set for a version."""
    artifacts = sorted(path for path in dist_dir.iterdir() if path.is_file())
    if not artifacts:
        raise VerificationError(f"No release artifacts found in {dist_dir}")

    encoded_project = urllib.parse.quote(project, safe="")
    encoded_version = urllib.parse.quote(version, safe="")
    payload = _load_registry_json(
        f"https://pypi.org/pypi/{encoded_project}/{encoded_version}/json"
    )
    if payload is None:
        return False

    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise VerificationError("PyPI response is missing the artifact list")

    remote_hashes: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, dict):
            raise VerificationError("PyPI artifact entry is not an object")
        filename = item.get("filename")
        digests = item.get("digests")
        sha256 = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(filename, str) or not isinstance(sha256, str):
            raise VerificationError("PyPI artifact entry is missing filename or SHA-256")
        remote_hashes[filename] = sha256.lower()

    local_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts
    }
    if remote_hashes != local_hashes:
        raise VerificationError(
            "PyPI already contains this version, but its artifact names or SHA-256 "
            "digests do not match the tagged build"
        )
    return True


def _npm_package_metadata(package_dir: Path) -> dict[str, Any]:
    npm = shutil.which("npm")
    if npm is None:
        raise VerificationError("npm is required to verify npm artifacts")
    try:
        result = subprocess.run(
            [npm, "pack", "--dry-run", "--json"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VerificationError("npm is required to verify npm artifacts") from exc
    if result.returncode != 0:
        raise VerificationError(f"npm pack failed: {(result.stderr or result.stdout).strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError("npm pack returned invalid JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise VerificationError("npm pack returned an unexpected artifact list")
    return payload[0]


def verify_npm(package_name: str, version: str, package_dir: Path) -> bool:
    """Return whether npm has the exact locally packable artifact for a version."""
    local = _npm_package_metadata(package_dir)
    if local.get("name") != package_name or local.get("version") != version:
        raise VerificationError("Local npm package name or version does not match the release")
    local_integrity = local.get("integrity")
    if not isinstance(local_integrity, str):
        raise VerificationError("npm pack did not report artifact integrity")

    encoded_name = urllib.parse.quote(package_name, safe="")
    encoded_version = urllib.parse.quote(version, safe="")
    payload = _load_registry_json(
        f"https://registry.npmjs.org/{encoded_name}/{encoded_version}"
    )
    if payload is None:
        return False

    dist = payload.get("dist")
    remote_integrity = dist.get("integrity") if isinstance(dist, dict) else None
    if not isinstance(remote_integrity, str):
        raise VerificationError("npm response is missing dist.integrity")
    if not _integrity_matches(local_integrity, remote_integrity):
        raise VerificationError(
            "npm already contains this version, but its artifact integrity does not "
            "match the tagged build"
        )
    return True


def _integrity_matches(local: str, remote: str) -> bool:
    algorithm, separator, _ = local.partition("-")
    if not separator or algorithm != "sha512":
        raise VerificationError(f"Unsupported npm integrity algorithm: {algorithm!r}")
    return hmac.compare_digest(local, remote)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="registry", required=True)

    pypi = subparsers.add_parser("pypi")
    pypi.add_argument("--project", required=True)
    pypi.add_argument("--version", required=True)
    pypi.add_argument("--dist-dir", type=Path, required=True)

    npm = subparsers.add_parser("npm")
    npm.add_argument("--package", required=True)
    npm.add_argument("--version", required=True)
    npm.add_argument("--package-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.registry == "pypi":
            exists = verify_pypi(args.project, args.version, args.dist_dir)
        else:
            exists = verify_npm(args.package, args.version, args.package_dir)
    except VerificationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"exists={str(exists).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())