"""Optional ``syft`` scan of pulled base images (opt-in: ``--scan-images``).

Static inference (:mod:`nuguard.sbom.image_os`, Dockerfile ``RUN`` parsing) is always on;
this adds the *real* OS and installed packages for images that exist in a registry.

- Only ``image_role == "base"`` nodes are scanned (built app images are not pulled).
- Needs ``syft`` on ``PATH`` and registry access; otherwise it logs once and does nothing.
- Failures (pull error, timeout, bad JSON) skip that image and never fail generation.
- Only package names/versions and distro identifiers are recorded; no credentials, no
  file contents.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

from nuguard.common.logging import get_logger

from .image_ref import is_templated
from .models import AiSbomDocument, ImagePackage
from .types import ComponentType

_log = get_logger(__name__)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

_FAMILY = {"debian": "debian", "ubuntu": "debian", "alpine": "alpine", "rhel": "rhel",
           "centos": "rhel", "fedora": "rhel", "amzn": "rhel", "rocky": "rhel", "almalinux": "rhel"}


def _family(distro: dict[str, Any]) -> str | None:
    ident = str(distro.get("id") or "").lower()
    if ident in _FAMILY:
        return _FAMILY[ident]
    for like in str(distro.get("idLike") or "").lower().replace(",", " ").split():
        if like in _FAMILY:
            return _FAMILY[like]
    return ident or None


def parse_syft_json(raw: str, max_packages: int) -> tuple[dict[str, Any], list[ImagePackage]]:
    """``(distro dict, packages)`` from ``syft -o json`` output."""
    data = json.loads(raw)
    distro = data.get("distro") if isinstance(data.get("distro"), dict) else {}
    packages: list[ImagePackage] = []
    seen: set[tuple[str, str]] = set()
    for art in data.get("artifacts") or []:
        if not isinstance(art, dict) or not art.get("name"):
            continue
        key = (str(art["name"]), str(art.get("version") or ""))
        if key in seen:
            continue
        seen.add(key)
        packages.append(
            ImagePackage(
                name=str(art["name"]),
                version=str(art["version"]) if art.get("version") else None,
                manager=f"syft:{art.get('type') or 'unknown'}",
                source="syft",
            )
        )
        if len(packages) >= max_packages:
            break
    return distro, packages


def scan_images(
    doc: AiSbomDocument,
    *,
    max_packages: int = 200,
    timeout: int = 120,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    """Scan pulled base images with syft; returns the number of images enriched."""
    if which("syft") is None:
        _log.warning("--scan-images requested but `syft` is not on PATH; skipping image scan")
        return 0
    enriched = 0
    cache: dict[str, tuple[dict[str, Any], list[ImagePackage]] | None] = {}
    for node in doc.nodes:
        meta = node.metadata
        ref = meta.base_image
        if (
            node.component_type != ComponentType.CONTAINER_IMAGE
            or meta.image_role == "app"
            or not ref
            or is_templated(ref)
        ):
            continue
        if ref not in cache:
            try:
                proc = runner(
                    ["syft", ref, "-o", "json", "-q"],
                    capture_output=True, text=True, timeout=timeout, check=False,
                )
                cache[ref] = (
                    parse_syft_json(proc.stdout, max_packages) if proc.returncode == 0 else None
                )
                if proc.returncode != 0:
                    _log.warning("syft failed for %s (exit %d)", ref, proc.returncode)
            except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
                _log.warning("syft scan of %s failed: %s", ref, exc)
                cache[ref] = None
        result = cache[ref]
        if result is None:
            continue
        distro, packages = result
        if distro.get("id"):
            meta.os_name = str(distro["id"]).lower()
            meta.os_version = str(distro.get("versionID") or distro.get("version") or "") or meta.os_version
            meta.os_family = _family(distro) or meta.os_family
            meta.os_evidence = "syft"
        if packages:
            meta.image_packages = packages
        enriched += 1
    return enriched
