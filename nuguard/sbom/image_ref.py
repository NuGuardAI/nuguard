"""Container image reference parsing shared by Dockerfile, compose, K8s and cloud adapters."""

from __future__ import annotations

import re

# Splits an image reference into registry + name + tag + digest
#   python:3.12-slim               → name=python  tag=3.12-slim
#   gcr.io/myproj/app:latest       → registry=gcr.io name=myproj/app tag=latest
#   ubuntu@sha256:abc123           → name=ubuntu   digest=sha256:abc123
#   localhost:5000/img:tag         → registry=localhost:5000
_REF_RE = re.compile(
    r"^"
    r"(?:(?P<registry>[a-zA-Z0-9._\-]+\.[a-zA-Z]{2,}(?::[0-9]+)?|localhost(?::[0-9]+)?)/)?"
    r"(?P<name>[^:@\s]+)"
    r"(?::(?P<tag>[^@\s]+))?"
    r"(?:@(?P<digest>sha256:[a-f0-9]{7,}))?"
    r"$",
)

# Unresolved template / variable references (Helm, Bicep, compose, Terraform)
_TEMPLATED_RE = re.compile(r"\$\{|\{\{|\$\(|\[parameters|^\$[A-Za-z_]|%\(")


def is_templated(ref: str) -> bool:
    """True when *ref* still contains an unresolved variable or template expression."""
    return bool(_TEMPLATED_RE.search(ref))


def parse_image_ref(ref: str) -> dict[str, str | None]:
    """Break *ref* into registry, name, tag, digest components."""
    m = _REF_RE.match(ref.strip())
    if not m:
        return {"registry": None, "name": ref, "tag": None, "digest": None}
    return {
        "registry": m.group("registry"),
        "name": m.group("name"),
        "tag": m.group("tag"),
        "digest": m.group("digest"),
    }


def normalize_image_ref(ref: str) -> str:
    """Lower-cased ref without the implicit ``docker.io/`` / ``library/`` / ``:latest``.

    Used to match a workload's image reference against CONTAINER_IMAGE nodes.
    """
    parts = parse_image_ref(ref.strip().lower())
    name = parts["name"] or ref.lower()
    if name.startswith("library/"):
        name = name[len("library/") :]
    registry = parts["registry"]
    if registry in (None, "docker.io"):
        registry = None
    tag = parts["tag"] or ("" if parts["digest"] else "latest")
    out = f"{registry}/{name}" if registry else name
    if tag:
        out += f":{tag}"
    if parts["digest"]:
        out += f"@{parts['digest']}"
    return out


def image_basename(ref: str) -> str:
    """Final path segment of the image name, e.g. ``acr.io/org/web:1`` → ``web``."""
    name = parse_image_ref(ref.strip().lower())["name"] or ref.lower()
    return name.rsplit("/", 1)[-1]
