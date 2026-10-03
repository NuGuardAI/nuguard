"""Helpers shared by the container / workload extractors and the extractor core.

- :func:`promote_image_fields` / :func:`promote_workload_fields` copy adapter metadata
  (plain dicts) onto the typed :class:`~nuguard.sbom.models.NodeMetadata` fields.
- :func:`workload_metadata` builds the ``workload`` metadata dict adapters emit, so
  compose, K8s and cloud adapters all produce the same shape.

Only names are carried (env var names, secret reference names) — never values.
"""

from __future__ import annotations

from typing import Any

from nuguard.common.logging import get_logger

from .models import ImagePackage, NodeMetadata, PortDetail, WorkloadDetail

_log = get_logger(__name__)

def as_dict(value: Any) -> dict[str, Any]:
    """*value* when it is a mapping, else ``{}`` (safe ``.get`` chains over parsed YAML/JSON)."""
    return value if isinstance(value, dict) else {}


_IMAGE_STR_FIELDS = (
    "image_role", "os_name", "os_version", "os_family", "os_evidence",
    "stage_alias", "entrypoint", "cmd", "workdir",
)


def _port_from_dict(raw: dict[str, Any]) -> PortDetail | None:
    """Accept both the legacy ``{port, protocol}`` and the typed PortDetail shapes."""
    data = dict(raw)
    if "port" in data and "container_port" not in data:
        data["container_port"] = data.pop("port")
    try:
        return PortDetail.model_validate(data)
    except ValueError:
        return None


def promote_image_fields(meta: NodeMetadata, raw: dict[str, Any]) -> None:
    """Copy CONTAINER_IMAGE adapter metadata onto typed NodeMetadata fields."""
    for key in _IMAGE_STR_FIELDS:
        value = raw.get(key)
        if value:
            setattr(meta, key, str(value))
    if raw.get("has_dockerignore") is not None:
        meta.has_dockerignore = bool(raw["has_dockerignore"])
    manifests = raw.get("dependency_manifests")
    if isinstance(manifests, list) and manifests:
        meta.dependency_manifests = [str(m) for m in manifests]
    packages = raw.get("image_packages")
    if isinstance(packages, list) and packages:
        pkgs: list[ImagePackage] = []
        for p in packages:
            try:
                pkgs.append(ImagePackage.model_validate(p))
            except ValueError:
                continue
        if pkgs:
            meta.image_packages = pkgs
    ports = raw.get("exposed_ports")
    if isinstance(ports, list) and ports:
        parsed = [pd for p in ports if isinstance(p, dict) and (pd := _port_from_dict(p))]
        if parsed:
            meta.exposed_ports = parsed


def promote_workload_fields(meta: NodeMetadata, raw: dict[str, Any]) -> None:
    """Copy DEPLOYMENT adapter metadata (``workload`` dict, ``cloud_provider``) onto NodeMetadata."""
    provider = raw.get("cloud_provider")
    if provider:
        meta.cloud_provider = str(provider)
    workload = raw.get("workload")
    if isinstance(workload, dict) and workload:
        try:
            meta.workload = WorkloadDetail.model_validate(workload)
        except ValueError:
            _log.debug("Ignoring malformed workload metadata")


def workload_metadata(
    *,
    service_name: str,
    workload_kind: str,
    source_format: str,
    cloud_service: str | None = None,
    namespace: str | None = None,
    image_refs: list[str] | None = None,
    build_context: str | None = None,
    dockerfile: str | None = None,
    ports: list[dict[str, Any]] | None = None,
    scaling: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
    identity_ref: str | None = None,
    depends_on: list[str] | None = None,
    env_var_names: list[str] | None = None,
    secret_refs: list[str] | None = None,
    probes: list[str] | None = None,
    internal_only: bool | None = None,
) -> dict[str, Any]:
    """Build the ``workload`` dict for ``ComponentDetection.metadata`` (None/empty omitted)."""
    out: dict[str, Any] = {
        "service_name": service_name,
        "workload_kind": workload_kind,
        "source_format": source_format,
    }
    optional: dict[str, Any] = {
        "cloud_service": cloud_service,
        "namespace": namespace,
        "image_refs": image_refs,
        "build_context": build_context,
        "dockerfile": dockerfile,
        "ports": [{"protocol": "tcp", **{k: v for k, v in p.items() if v is not None}} for p in ports or []],
        "scaling": {k: v for k, v in (scaling or {}).items() if v is not None} or None,
        "resources": {k: v for k, v in (resources or {}).items() if v is not None} or None,
        "identity_ref": identity_ref,
        "depends_on": depends_on,
        "env_var_names": sorted(set(env_var_names)) if env_var_names else None,
        "secret_refs": sorted(set(secret_refs)) if secret_refs else None,
        "probes": probes,
        "internal_only": internal_only,
    }
    out.update({k: v for k, v in optional.items() if v not in (None, [], {})})
    return out


def service_references(values: list[str], service_names: list[str], own_name: str) -> list[str]:
    """Names of sibling services that env *values* point at (``http://svc:8080``, ``redis://svc``).

    Only the referenced service name is returned — the value itself is never stored. A bare
    value equal to a service name also counts (``UPSTREAM=agent-orchestrator``).
    """
    import re

    found: list[str] = []
    for name in service_names:
        if name == own_name:
            continue
        pat = re.compile(rf"(?:://|@){re.escape(name)}(?:[:/\s?]|$)|^{re.escape(name)}(?::\d+)?$", re.IGNORECASE)
        if any(pat.search(v) for v in values):
            found.append(name)
    return found
