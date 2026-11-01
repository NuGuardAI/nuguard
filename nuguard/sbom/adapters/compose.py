"""docker-compose adapter — one DEPLOYMENT workload per compose service.

For every service it records the runtime shape an attacker or analyst cares about:
the image it runs or the build context it is built from, published/exposed ports
(and whether they are reachable from the host network), replicas and resource
limits, ``depends_on`` edges, health probes, environment variable *names* and
secret reference names (never values), and a few high-signal misconfigurations
(privileged, host network, docker-socket mount, literal secrets in ``environment``).

``image:`` references also produce a ``CONTAINER_IMAGE`` base node with the OS inferred
from the image, and a ``RUNS`` hint from the workload to it.

Evidence kind: ``"iac"`` (priority 8, like the other IaC adapters).
"""

from __future__ import annotations

import os
import re
from typing import Any

import yaml

from nuguard.common.logging import get_logger

from ..image_ref import is_templated
from ..types import ComponentType
from ..workload import as_dict, service_references, workload_metadata
from ._image_nodes import attach_images
from .base import ComponentDetection
from .iac import _make_det

_log = get_logger(__name__)

_COMPOSE_NAME_RE = re.compile(r"^(?:docker-)?compose(?:[.\-_][\w.\-]+)?\.ya?ml$", re.IGNORECASE)
_VAR_RE = re.compile(r"\$\{(?P<name>\w+)(?::?-(?P<default>[^}]*))?\}")
_SECRET_NAME_RE = re.compile(
    r"(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL)", re.IGNORECASE
)
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_HEALTH_PATH_RE = re.compile(r"https?://[^/\s\"']+(?P<path>/[^\s\"']*)")
_DOCKER_SOCK = "/var/run/docker.sock"


def is_compose_file(rel_path: str) -> bool:
    """True for ``docker-compose.yml``, ``compose.yaml``, ``docker-compose.prod.yml`` ..."""
    return bool(_COMPOSE_NAME_RE.match(os.path.basename(rel_path)))


def _resolve_vars(value: str) -> str:
    """Replace ``${VAR:-default}`` with its default; leave ``${VAR}`` untouched."""
    return _VAR_RE.sub(lambda m: m.group("default") if m.group("default") is not None else m.group(0), value)


def _normalize_path(compose_dir: str, ctx: str) -> str:
    joined = os.path.normpath(os.path.join(compose_dir, ctx)) if compose_dir else os.path.normpath(ctx)
    return "." if joined in ("", ".") else joined.replace(os.sep, "/")


def _parse_port(spec: Any, source: str) -> dict[str, Any] | None:
    """Short (``"127.0.0.1:8080:80/udp"``) and long (mapping) port syntax."""
    host_ip: str | None = None
    published: int | None = None
    target: int | None = None
    protocol = "tcp"
    if isinstance(spec, dict):
        target = _as_int(spec.get("target"))
        published = _as_int(spec.get("published"))
        host_ip = spec.get("host_ip")
        protocol = str(spec.get("protocol") or "tcp").lower()
    else:
        text = _resolve_vars(str(spec)).strip().strip("\"'")
        text, _, proto = text.partition("/")
        protocol = proto.lower() or "tcp"
        parts = text.split(":")
        if len(parts) == 1:
            target = _as_int(parts[0])
        elif len(parts) == 2:
            published, target = _as_int(parts[0]), _as_int(parts[1])
        elif len(parts) >= 3:
            host_ip = ":".join(parts[:-2])
            published, target = _as_int(parts[-2]), _as_int(parts[-1])
    if target is None:
        return None
    exposure = "internal" if host_ip in _LOOPBACK else "public"
    out: dict[str, Any] = {
        "container_port": target,
        "protocol": protocol,
        "exposure": exposure,
        "source": source,
    }
    if published is not None:
        out["host_port"] = published
    return out


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _env_entries(env: Any) -> list[tuple[str, str | None]]:
    """(name, literal value or None) pairs from list or mapping ``environment``."""
    out: list[tuple[str, str | None]] = []
    if isinstance(env, dict):
        for k, v in env.items():
            out.append((str(k), None if v is None else str(v)))
    elif isinstance(env, list):
        for item in env:
            if isinstance(item, str):
                name, sep, value = item.partition("=")
                out.append((name.strip(), value if sep else None))
    return out


def _is_literal(value: str | None) -> bool:
    return bool(value) and "${" not in str(value) and not str(value).startswith("$")


def _depends(raw: Any) -> list[str]:
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _health_probe(hc: Any) -> list[str]:
    if not isinstance(hc, dict) or hc.get("disable") is True:
        return []
    test = hc.get("test")
    text = " ".join(str(t) for t in test) if isinstance(test, list) else str(test or "")
    if text.strip().upper() in ("NONE", "['NONE']"):
        return []
    m = _HEALTH_PATH_RE.search(text)
    return [f"healthcheck:{m.group('path')}" if m else "healthcheck"]


def _resources(svc: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    deploy = as_dict(svc.get("deploy"))
    res = as_dict(deploy.get("resources"))
    limits = as_dict(res.get("limits"))
    reserv = as_dict(res.get("reservations"))
    out = {
        "cpu_limit": str(limits["cpus"]) if limits.get("cpus") is not None else None,
        "memory_limit": str(limits["memory"]) if limits.get("memory") is not None else None,
        "cpu_request": str(reserv["cpus"]) if reserv.get("cpus") is not None else None,
        "memory_request": str(reserv["memory"]) if reserv.get("memory") is not None else None,
    }
    # legacy v2 keys
    if svc.get("mem_limit") is not None and out["memory_limit"] is None:
        out["memory_limit"] = str(svc["mem_limit"])
    if svc.get("cpus") is not None and out["cpu_limit"] is None:
        out["cpu_limit"] = str(svc["cpus"])
    return out, bool(out["cpu_limit"] or out["memory_limit"])


def _build(svc: dict[str, Any], compose_dir: str) -> tuple[str | None, str | None]:
    """(build_context, dockerfile) repo-relative; ``(None, None)`` for remote contexts."""
    build = svc.get("build")
    if build is None:
        return None, None
    if isinstance(build, str):
        ctx, df = build, None
    elif isinstance(build, dict):
        ctx, df = str(build.get("context") or "."), build.get("dockerfile")
    else:
        return None, None
    ctx = _resolve_vars(ctx)
    if re.match(r"^(?:https?|git|ssh)://|^git@|^github\.com/", ctx) or is_templated(ctx):
        return None, None
    context = _normalize_path(compose_dir, ctx)
    dockerfile = os.path.normpath(os.path.join(context, str(df) if df else "Dockerfile")).replace(os.sep, "/")
    return context, dockerfile


class ComposeAdapter:
    """Parses docker-compose files into workload DEPLOYMENT and image CONTAINER_IMAGE nodes."""

    name = "compose"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        """Return detections for every service in a compose file ([] when it is not one)."""
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            _log.debug("compose: invalid YAML in %s: %s", file_path, exc)
            return []
        if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict):
            return []

        compose_dir = os.path.dirname(file_path)
        top_secrets = as_dict(doc.get("secrets"))
        detections: list[ComponentDetection] = []
        image_nodes: dict[str, ComponentDetection] = {}
        env_values: dict[str, list[str]] = {}

        for svc_name, raw in doc["services"].items():
            if not isinstance(raw, dict):
                continue
            svc_name = str(svc_name)
            line = self._line_of(content, svc_name)
            canonical = f"deployment:compose:{compose_dir or '.'}:{svc_name}".lower()

            context, dockerfile = _build(raw, compose_dir)
            image = _resolve_vars(str(raw["image"])) if raw.get("image") else None
            image_refs = [image] if image else []

            ports: list[dict[str, Any]] = []
            for spec in raw.get("ports") or []:
                p = _parse_port(spec, "compose ports")
                if p:
                    ports.append(p)
            published_targets = {p["container_port"] for p in ports}
            for spec in raw.get("expose") or []:
                target = _as_int(str(spec).split("/")[0])
                if target is not None and target not in published_targets:
                    ports.append({"container_port": target, "protocol": "tcp",
                                  "exposure": "cluster", "source": "compose expose"})

            res, has_limits = _resources(raw)
            deploy = as_dict(raw.get("deploy"))
            replicas = _as_int(deploy.get("replicas")) if deploy else None
            scaling = {"replicas": replicas} if replicas is not None else None

            env = _env_entries(raw.get("environment"))
            env_values[svc_name] = [
                _resolve_vars(v) for n, v in env if v and not _SECRET_NAME_RE.search(n)
            ]
            secret_names = list(raw.get("secrets") or []) if isinstance(raw.get("secrets"), list) else []
            secret_refs = [
                str(s.get("source") if isinstance(s, dict) else s) for s in secret_names
            ]
            secret_refs = [s for s in secret_refs if s in top_secrets or s]

            findings: list[str] = []
            if any(_SECRET_NAME_RE.search(n) and _is_literal(v) for n, v in env):
                findings.append("secrets_in_env_vars")
            if raw.get("privileged") is True:
                findings.append("privileged_container")
            if str(raw.get("network_mode") or "").lower() == "host":
                findings.append("host_network")
            if raw.get("pid") == "host":
                findings.append("host_pid")
            if any(_DOCKER_SOCK in str(v) for v in (raw.get("volumes") or [])):
                findings.append("docker_socket_mount")
            if raw.get("cap_add"):
                findings.append("added_capabilities")

            user = str(raw.get("user")).split(":")[0].strip().lower() if raw.get("user") is not None else None
            probes = _health_probe(raw.get("healthcheck"))
            internal_only = not any(p["exposure"] == "public" for p in ports)

            meta: dict[str, Any] = {
                "deployment_target": "docker-compose",
                "iac_format": "docker-compose",
                "has_health_check": bool(probes),
                "has_resource_limits": has_limits,
                "workload": workload_metadata(
                    service_name=svc_name,
                    workload_kind="compose_service",
                    source_format="compose",
                    image_refs=image_refs,
                    build_context=context,
                    dockerfile=dockerfile,
                    ports=ports,
                    scaling=scaling,
                    resources=res,
                    depends_on=_depends(raw.get("depends_on")),
                    env_var_names=[n for n, _ in env],
                    secret_refs=secret_refs,
                    probes=probes,
                    internal_only=internal_only,
                ),
            }
            if user is not None:
                meta["runs_as_root"] = user in {"root", "0"}
            if findings:
                meta["security_findings"] = findings

            det = _make_det(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=svc_name,
                adapter_name=self.name,
                confidence=0.95,
                metadata=meta,
                file_path=file_path,
                line=line,
                snippet=f"{svc_name}:"[:120],
            )

            # An image that is pulled (not built here) is a CONTAINER_IMAGE the workload RUNS.
            if image and context is None:
                for img in attach_images(
                    det, [image], adapter_name=self.name, file_path=file_path, line=line
                ):
                    image_nodes.setdefault(img.canonical_name, img)
            detections.append(det)

        # Service-to-service references in env values become depends_on (names only).
        all_names = [str(n) for n in doc["services"]]
        for det in detections:
            wl = det.metadata.get("workload")
            if det.component_type != ComponentType.DEPLOYMENT or not wl:
                continue
            refs = service_references(env_values.get(wl["service_name"], []), all_names, wl["service_name"])
            merged = list(dict.fromkeys([*wl.get("depends_on", []), *refs]))
            if merged:
                wl["depends_on"] = merged

        detections.extend(image_nodes.values())
        _log.info("compose adapter: %d service(s) in %s", len(detections), file_path)
        return detections

    @staticmethod
    def _line_of(content: str, service: str) -> int:
        m = re.search(rf"^[ \t]+{re.escape(service)}\s*:", content, re.MULTILINE)
        return content[: m.start()].count("\n") + 1 if m else 1
