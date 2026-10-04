"""Pure parsing helpers for Kubernetes workload facts (no ComponentDetection building).

Used by :class:`~nuguard.sbom.adapters.iac.K8sAdapter` for workloads, Services, HPAs,
Ingress rules, Helm ``values.yaml`` and Kustomize overlays. The cross-resource joins
(Service → workload by selector, HPA → workload by target, Ingress → backend,
Kustomize overrides) happen in :mod:`nuguard.sbom.deployment_topology`, because the
resources usually live in different files.

Only names are carried — env var names and secret *names*, never values.
"""

from __future__ import annotations

from typing import Any

from ..workload import as_dict, workload_metadata


def pod_spec_of(data: dict[str, Any]) -> dict[str, Any]:
    """Pod spec of a Deployment/StatefulSet/DaemonSet/Job, or a CronJob's job template."""
    spec = data.get("spec") or {}
    if str(data.get("kind")) == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    return (spec.get("template") or {}).get("spec") or {}


def pod_labels_of(data: dict[str, Any]) -> dict[str, str]:
    """Labels on the pod template (what a Service selector matches); falls back to metadata."""
    spec = data.get("spec") or {}
    if str(data.get("kind")) == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    labels = ((spec.get("template") or {}).get("metadata") or {}).get("labels")
    if not isinstance(labels, dict) or not labels:
        labels = (data.get("metadata") or {}).get("labels")
    return {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}


def _probe(kind: str, probe: Any) -> str | None:
    if not isinstance(probe, dict):
        return None
    if isinstance(probe.get("httpGet"), dict):
        return f"{kind}:{probe['httpGet'].get('path') or '/'}"
    if isinstance(probe.get("tcpSocket"), dict):
        return f"{kind}:tcp:{probe['tcpSocket'].get('port')}"
    if probe.get("grpc") is not None:
        return f"{kind}:grpc"
    return f"{kind}:exec"


def _first(containers: list[dict[str, Any]], *path: str) -> str | None:
    for c in containers:
        node: Any = c
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None:
            return str(node)
    return None


def workload_facts(data: dict[str, Any], namespace: str) -> dict[str, Any]:
    """``workload`` metadata dict for a K8s workload resource."""
    spec = data.get("spec") or {}
    pod_spec = pod_spec_of(data)
    containers = [c for c in (pod_spec.get("containers") or []) if isinstance(c, dict)]
    kind = str(data.get("kind") or "Deployment")

    ports: list[dict[str, Any]] = []
    for c in containers:
        for p in c.get("ports") or []:
            if isinstance(p, dict) and isinstance(p.get("containerPort"), int):
                ports.append(
                    {
                        "container_port": p["containerPort"],
                        "protocol": str(p.get("protocol") or "TCP").lower(),
                        "host_port": p.get("hostPort") if isinstance(p.get("hostPort"), int) else None,
                        "exposure": "cluster",
                        "source": "k8s containerPort",
                    }
                )

    env_names: list[str] = []
    secret_refs: list[str] = []
    for c in containers:
        for ev in c.get("env") or []:
            if not isinstance(ev, dict) or not ev.get("name"):
                continue
            env_names.append(str(ev["name"]))
            ref = (ev.get("valueFrom") or {}).get("secretKeyRef") if isinstance(ev.get("valueFrom"), dict) else None
            if isinstance(ref, dict) and ref.get("name"):
                secret_refs.append(str(ref["name"]))
        for src in c.get("envFrom") or []:
            if isinstance(src, dict) and isinstance(src.get("secretRef"), dict) and src["secretRef"].get("name"):
                secret_refs.append(str(src["secretRef"]["name"]))
    for vol in pod_spec.get("volumes") or []:
        if isinstance(vol, dict) and isinstance(vol.get("secret"), dict) and vol["secret"].get("secretName"):
            secret_refs.append(str(vol["secret"]["secretName"]))

    probes: list[str] = []
    for c in containers:
        for kind_name, key in (("liveness", "livenessProbe"), ("readiness", "readinessProbe"), ("startup", "startupProbe")):
            p = _probe(kind_name, c.get(key))
            if p and p not in probes:
                probes.append(p)

    replicas = spec.get("replicas") if isinstance(spec.get("replicas"), int) else None
    resources = {
        "cpu_request": _first(containers, "resources", "requests", "cpu"),
        "cpu_limit": _first(containers, "resources", "limits", "cpu"),
        "memory_request": _first(containers, "resources", "requests", "memory"),
        "memory_limit": _first(containers, "resources", "limits", "memory"),
    }
    sa = pod_spec.get("serviceAccountName") or pod_spec.get("serviceAccount")
    return workload_metadata(
        service_name=str((data.get("metadata") or {}).get("name") or "unknown"),
        workload_kind=f"k8s_{kind.lower()}",
        source_format="k8s",
        namespace=namespace,
        image_refs=[str(c["image"]) for c in containers if c.get("image")],
        ports=ports,
        scaling={"replicas": replicas} if replicas is not None else None,
        resources=resources,
        identity_ref=str(sa) if sa else None,
        env_var_names=env_names,
        secret_refs=secret_refs,
        probes=probes,
    )


def service_fact(data: dict[str, Any]) -> dict[str, Any]:
    """``k8s_service`` fact for a Service resource."""
    meta = data.get("metadata") or {}
    spec = data.get("spec") or {}
    ports: list[dict[str, Any]] = []
    for p in spec.get("ports") or []:
        if not isinstance(p, dict):
            continue
        ports.append(
            {
                "name": p.get("name"),
                "port": p.get("port"),
                "target_port": p.get("targetPort", p.get("port")),
                "node_port": p.get("nodePort"),
                "protocol": str(p.get("protocol") or "TCP").lower(),
            }
        )
    selector = spec.get("selector")
    return {
        "name": str(meta.get("name") or "unknown"),
        "namespace": str(meta.get("namespace") or "default"),
        "type": str(spec.get("type") or "ClusterIP"),
        "selector": {str(k): str(v) for k, v in selector.items()} if isinstance(selector, dict) else {},
        "ports": ports,
    }


def hpa_fact(data: dict[str, Any]) -> dict[str, Any] | None:
    """``k8s_hpa`` fact for a HorizontalPodAutoscaler (or KEDA ScaledObject)."""
    meta = data.get("metadata") or {}
    spec = data.get("spec") or {}
    target = spec.get("scaleTargetRef") or {}
    if not isinstance(target, dict) or not target.get("name"):
        return None
    keda = str(data.get("kind")) == "ScaledObject"
    metrics: list[str] = []
    for m in spec.get("metrics") or []:
        if not isinstance(m, dict):
            continue
        res = m.get("resource") or {}
        name = res.get("name") or m.get("type")
        util = (res.get("target") or {}).get("averageUtilization")
        metrics.append(f"{name}:{util}%" if util is not None else str(name))
    for trig in spec.get("triggers") or []:
        if isinstance(trig, dict) and trig.get("type"):
            metrics.append(str(trig["type"]))
    if spec.get("targetCPUUtilizationPercentage") is not None:
        metrics.append(f"cpu:{spec['targetCPUUtilizationPercentage']}%")
    lo = spec.get("minReplicaCount" if keda else "minReplicas")
    hi = spec.get("maxReplicaCount" if keda else "maxReplicas")
    return {
        "name": str(meta.get("name") or "unknown"),
        "namespace": str(meta.get("namespace") or "default"),
        "target_kind": str(target.get("kind") or "Deployment"),
        "target_name": str(target["name"]),
        "min_replicas": lo if isinstance(lo, int) else (0 if keda else 1),
        "max_replicas": hi if isinstance(hi, int) else None,
        "metrics": metrics,
        "autoscaler": "keda" if keda else "hpa",
    }


def ingress_rules(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Ingress ``host``/``path`` → backend service rules."""
    spec = data.get("spec") or {}
    rules: list[dict[str, Any]] = []

    def backend(b: Any) -> tuple[str | None, Any]:
        if not isinstance(b, dict):
            return None, None
        svc = b.get("service")
        if isinstance(svc, dict):
            port = svc.get("port") or {}
            return svc.get("name"), (port.get("number") or port.get("name")) if isinstance(port, dict) else port
        return b.get("serviceName"), b.get("servicePort")

    default_svc, default_port = backend(spec.get("defaultBackend") or spec.get("backend"))
    if default_svc:
        rules.append({"host": None, "path": "/", "service": str(default_svc), "port": default_port})
    for rule in spec.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        for path in (rule.get("http") or {}).get("paths") or []:
            if not isinstance(path, dict):
                continue
            svc, port = backend(path.get("backend"))
            if svc:
                rules.append(
                    {
                        "host": str(rule["host"]) if rule.get("host") else None,
                        "path": str(path.get("path") or "/"),
                        "service": str(svc),
                        "port": port,
                    }
                )
    return rules


_VALUES_KEYS = ("replicaCount", "image", "service", "autoscaling", "resources", "ingress", "containerPort")


def is_helm_values(data: Any) -> bool:
    """True for a Helm values mapping that describes a deployable (not an arbitrary YAML)."""
    return isinstance(data, dict) and any(k in data for k in _VALUES_KEYS) and (
        "image" in data or "replicaCount" in data or "autoscaling" in data
    )


def helm_values_workload(values: dict[str, Any], chart_name: str) -> dict[str, Any]:
    """``workload`` dict from conventional Helm values (``helm create`` layout)."""
    image = values.get("image")
    refs: list[str] = []
    if isinstance(image, dict) and image.get("repository"):
        repo = str(image["repository"])
        tag = image.get("tag")
        refs.append(f"{repo}:{tag}" if tag else repo)
    elif isinstance(image, str) and image:
        refs.append(image)

    svc = as_dict(values.get("service"))
    ingress = as_dict(values.get("ingress"))
    svc_type = str(svc.get("type") or "ClusterIP")
    public = bool(ingress.get("enabled")) or svc_type in ("LoadBalancer", "NodePort")
    container_port = values.get("containerPort") or svc.get("targetPort") or svc.get("port")
    ports: list[dict[str, Any]] = []
    if isinstance(container_port, int):
        ports.append(
            {
                "container_port": container_port,
                "service_port": svc.get("port") if isinstance(svc.get("port"), int) else None,
                "exposure": "public" if public else "cluster",
                "source": "helm values",
            }
        )

    scaling: dict[str, Any] = {}
    auto = as_dict(values.get("autoscaling"))
    if auto.get("enabled"):
        scaling = {
            "min_replicas": auto.get("minReplicas"),
            "max_replicas": auto.get("maxReplicas"),
            "autoscaler": "hpa",
            "metric": "cpu" if auto.get("targetCPUUtilizationPercentage") else None,
            "target": f"{auto['targetCPUUtilizationPercentage']}%" if auto.get("targetCPUUtilizationPercentage") else None,
        }
    elif isinstance(values.get("replicaCount"), int):
        scaling = {"replicas": values["replicaCount"]}

    res = as_dict(values.get("resources"))
    limits = as_dict(res.get("limits"))
    requests = as_dict(res.get("requests"))
    resources = {
        "cpu_limit": str(limits["cpu"]) if "cpu" in limits else None,
        "memory_limit": str(limits["memory"]) if "memory" in limits else None,
        "cpu_request": str(requests["cpu"]) if "cpu" in requests else None,
        "memory_request": str(requests["memory"]) if "memory" in requests else None,
    }
    return workload_metadata(
        service_name=chart_name,
        workload_kind="helm_chart",
        source_format="helm_values",
        image_refs=refs,
        ports=ports,
        scaling=scaling or None,
        resources=resources,
        internal_only=not public,
    )


def kustomize_fact(data: dict[str, Any], directory: str) -> dict[str, Any]:
    """``kustomize`` fact: overrides applied to workloads under *directory*."""
    images = []
    for img in data.get("images") or []:
        if isinstance(img, dict) and img.get("name"):
            images.append(
                {
                    "name": str(img["name"]),
                    "new_name": str(img["newName"]) if img.get("newName") else None,
                    "new_tag": str(img["newTag"]) if img.get("newTag") is not None else None,
                }
            )
    replicas = [
        {"name": str(r["name"]), "count": r["count"]}
        for r in data.get("replicas") or []
        if isinstance(r, dict) and r.get("name") and isinstance(r.get("count"), int)
    ]
    return {
        "directory": directory,
        "namespace": str(data["namespace"]) if data.get("namespace") else None,
        "images": images,
        "replicas": replicas,
        "resources": [str(r) for r in data.get("resources") or [] if isinstance(r, str)],
    }
