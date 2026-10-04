"""Static OS inference from a container base-image reference.

Table-driven and offline: it reads the image name and tag, never the image
itself. ``os_evidence`` says how confident the answer is:

- ``"tag"``           — the tag names the OS and version (``ubuntu:22.04``, ``alpine:3.19``,
                        ``python:3.11-slim-bookworm``)
- ``"image_default"`` — the image's documented default distro (``python:3.11-slim`` is
                        Debian); the version may be unknown or only the current default
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .image_ref import parse_image_ref


@dataclass(frozen=True)
class OsInfo:
    """Inferred operating system of an image."""

    name: str
    version: str | None
    family: str
    evidence: str  # "tag" | "image_default"


_DEBIAN_CODENAMES = {
    "bookworm": "12",
    "bullseye": "11",
    "buster": "10",
    "stretch": "9",
    "trixie": "13",
}
_UBUNTU_CODENAMES = {
    "noble": "24.04",
    "jammy": "22.04",
    "focal": "20.04",
    "bionic": "18.04",
    "xenial": "16.04",
}
_VERSION_RE = re.compile(r"(?<![\d.])(\d+\.\d+(?:\.\d+)?)(?![\d])")

# Official images that ship on Debian unless the tag says otherwise.
_DEBIAN_DEFAULT_IMAGES = frozenset(
    {
        "python", "node", "golang", "ruby", "php", "openjdk", "maven", "gradle", "redis",
        "postgres", "mysql", "mariadb", "mongo", "nginx", "httpd", "rabbitmq", "memcached",
        "debian", "buildpack-deps", "rust", "eclipse-temurin", "elasticsearch", "kibana",
    }
)
_ALPINE_ONLY_IMAGES = frozenset({"alpine", "traefik", "caddy"})
_DISTROLESS_PREFIXES = ("gcr.io/distroless", "cgr.dev/chainguard")


def _tag_tokens(tag: str) -> list[str]:
    return [t for t in re.split(r"[-_]", tag.lower()) if t]


def infer_os(ref: str) -> OsInfo | None:
    """Best-effort OS for *ref*; ``None`` when nothing can be said (e.g. ``scratch``)."""
    raw = ref.strip().lower()
    if not raw or raw == "scratch":
        return None
    parts = parse_image_ref(raw)
    name = (parts["name"] or raw).strip()
    base = name.rsplit("/", 1)[-1]
    tag = parts["tag"] or ""
    registry = parts["registry"] or ""
    tokens = _tag_tokens(tag)

    if any(raw.startswith(p) for p in _DISTROLESS_PREFIXES):
        debian = next((v for k, v in _DEBIAN_CODENAMES.items() if any(k in t for t in tokens)), None)
        if debian is None and (dm := re.search(r"debian(\d+)", raw)):
            debian = dm.group(1)
        return OsInfo("distroless", debian, "distroless", "tag" if debian else "image_default")

    # --- explicit OS images ------------------------------------------------
    if base == "ubuntu":
        ver = _UBUNTU_CODENAMES.get(tag) or (m.group(1) if (m := _VERSION_RE.search(tag)) else None)
        return OsInfo("ubuntu", ver, "debian", "tag" if ver else "image_default")
    if base == "debian":
        ver = next((v for k, v in _DEBIAN_CODENAMES.items() if k in tag), None)
        if ver is None and (m := re.match(r"(\d+)", tag)):
            ver = m.group(1)
        return OsInfo("debian", ver, "debian", "tag" if ver else "image_default")
    if base in _ALPINE_ONLY_IMAGES:
        m = _VERSION_RE.search(tag) if base == "alpine" else None
        return OsInfo("alpine", m.group(1) if m else None, "alpine", "tag" if m else "image_default")
    if base in ("amazonlinux", "amazon-linux"):
        return OsInfo("amazonlinux", tag if tag and tag[0].isdigit() else None, "rhel", "tag")
    if base in ("centos", "rockylinux", "almalinux", "fedora", "oraclelinux"):
        m = re.match(r"(?:stream)?(\d+)", tag)
        return OsInfo(base, m.group(1) if m else None, "rhel", "tag" if m else "image_default")
    if "ubi" in base or registry.endswith("redhat.com") or base.startswith("rhel"):
        m = re.match(r"(\d+)", tag)
        return OsInfo("rhel", m.group(1) if m else None, "rhel", "tag" if m else "image_default")
    if base == "busybox":
        return OsInfo("busybox", None, "busybox", "image_default")
    if registry == "mcr.microsoft.com" or name.startswith("dotnet/"):
        if any("nanoserver" in t or "windowsservercore" in t for t in tokens):
            return OsInfo("windows", None, "windows", "tag")
        if "alpine" in tokens:
            return OsInfo("alpine", None, "alpine", "tag")
        for k, v in _DEBIAN_CODENAMES.items():
            if k in tokens:
                return OsInfo("debian", v, "debian", "tag")
        for k, v in _UBUNTU_CODENAMES.items():
            if k in tokens:
                return OsInfo("ubuntu", v, "debian", "tag")
        return OsInfo("debian", None, "debian", "image_default")

    # --- language/runtime images: OS comes from the tag suffix -------------
    if any(t.startswith("alpine") for t in tokens):
        m = next((_VERSION_RE.search(t) for t in tokens if t.startswith("alpine") and _VERSION_RE.search(t)), None)
        return OsInfo("alpine", m.group(1) if m else None, "alpine", "tag")
    for k, v in _DEBIAN_CODENAMES.items():
        if k in tokens:
            return OsInfo("debian", v, "debian", "tag")
    for k, v in _UBUNTU_CODENAMES.items():
        if k in tokens:
            return OsInfo("ubuntu", v, "debian", "tag")
    if "windowsservercore" in tokens or "nanoserver" in tokens:
        return OsInfo("windows", None, "windows", "tag")
    if base in _DEBIAN_DEFAULT_IMAGES:
        return OsInfo("debian", None, "debian", "image_default")
    return None
