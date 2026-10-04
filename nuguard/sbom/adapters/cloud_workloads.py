"""Workload parsing for Terraform, CloudFormation/SAM and standalone service descriptors.

One DEPLOYMENT per deployed compute resource (container app, ECS service, Lambda,
Cloud Run service, App Service, AKS cluster) carrying a ``workload`` fact: image refs,
ports and exposure, scaling bounds, resources, identity, and env/secret *names*.
Values that are not simple literals are left out rather than guessed.

Bicep lives in :mod:`.bicep_workloads`; Kubernetes in :mod:`.k8s_workload`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from ..image_ref import is_templated
from ..types import ComponentType
from ..workload import as_dict, workload_metadata
from ._image_nodes import attach_images
from .base import ComponentDetection

# ---------------------------------------------------------------------------
# Shared emit helper
# ---------------------------------------------------------------------------


def _emit(
    *,
    adapter_name: str,
    file_path: str,
    line: int,
    canonical: str,
    name: str,
    workload_kind: str,
    cloud_service: str,
    cloud_provider: str,
    iac_format: str,
    parsed: dict[str, Any],
    extra_meta: dict[str, Any] | None = None,
) -> list[ComponentDetection]:
    wl = workload_metadata(
        service_name=name,
        workload_kind=workload_kind,
        source_format=iac_format,
        cloud_service=cloud_service,
        **{k: v for k, v in parsed.items() if v not in (None, [], {}) and k != "security_findings"},
    )
    meta: dict[str, Any] = {
        "iac_format": iac_format,
        "deployment_target": cloud_provider,
        "cloud_provider": cloud_provider,
        "workload": wl,
    }
    if parsed.get("security_findings"):
        meta["security_findings"] = parsed["security_findings"]
    meta.update(extra_meta or {})
    det = ComponentDetection(
        component_type=ComponentType.DEPLOYMENT,
        canonical_name=canonical[:160].lower(),
        display_name=name,
        adapter_name=adapter_name,
        priority=8,
        confidence=0.9,
        metadata=meta,
        file_path=file_path,
        line=line,
        snippet=f"{workload_kind}: {name}"[:120],
        evidence_kind="iac",
    )
    refs = [r for r in parsed.get("image_refs") or [] if r and not is_templated(r)]
    return [det, *attach_images(det, refs, adapter_name=adapter_name, file_path=file_path, line=line)]


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Terraform
# ---------------------------------------------------------------------------

_TF_RES_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')
_TF_VAR_RE = re.compile(r'variable\s+"(\w+)"\s*\{')


def _tf_block(content: str, open_index: int) -> str:
    depth = 0
    in_str = False
    for idx in range(open_index, len(content)):
        ch = content[idx]
        if ch == '"' and content[idx - 1] != "\\":
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return content[open_index : idx + 1]
    return content[open_index:]


def _tf_vars(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _TF_VAR_RE.finditer(content):
        d = re.search(r'\bdefault\s*=\s*("([^"]*)"|(\d+)|(true|false))', _tf_block(content, m.end() - 1))
        if d:
            out[m.group(1)] = d.group(2) if d.group(2) is not None else d.group(1)
    return out


def _tf_attr(block: str, key: str, variables: dict[str, str]) -> str | None:
    """String/number/bool attribute value (resolving ``var.x`` defaults)."""
    m = re.search(rf'(?<![\w.]){re.escape(key)}\s*=\s*(?:"([^"]*)"|([\w.\-]+))', block)
    if not m:
        return None
    if m.group(1) is not None:
        val = m.group(1)
        v = re.fullmatch(r"\$\{var\.(\w+)\}", val)
        return variables.get(v.group(1), val) if v else val
    raw = m.group(2)
    if raw.startswith("var."):
        return variables.get(raw[4:])
    return raw


def _tf_env_names(block: str) -> list[str]:
    names = re.findall(r'\benv\s*\{[^{}]*?\bname\s*=\s*"([^"]+)"', block, re.DOTALL)
    for vm in re.finditer(r"\bvariables\s*=\s*\{", block):
        inner = _tf_block(block, vm.end() - 1)
        names += re.findall(r'(?:^|[{,\s])"?([A-Za-z_]\w*)"?\s*=\s*(?=["\w$])', inner.strip("{}"))
    return names


def _tf_container_app(block: str, v: dict[str, str]) -> dict[str, Any]:
    external = (_tf_attr(block, "external_enabled", v) or "false").lower() == "true"
    target = _int(_tf_attr(block, "target_port", v))
    ingress = re.search(r"\bingress\s*\{", block) is not None
    ports = (
        [{"container_port": target, "exposure": "public" if external else "internal", "source": "aca ingress"}]
        if ingress and target is not None
        else []
    )
    lo, hi = _int(_tf_attr(block, "min_replicas", v)), _int(_tf_attr(block, "max_replicas", v))
    return {
        "image_refs": [_tf_attr(block, "image", v)] if _tf_attr(block, "image", v) else [],
        "ports": ports,
        "scaling": {"min_replicas": lo, "max_replicas": hi, "autoscaler": "aca_rule" if lo != hi else "none"}
        if lo is not None or hi is not None
        else None,
        "resources": {"cpu_limit": _tf_attr(block, "cpu", v), "memory_limit": _tf_attr(block, "memory", v)},
        "identity_ref": "system-assigned" if "SystemAssigned" in block else None,
        "env_var_names": _tf_env_names(block),
        "secret_refs": re.findall(r'\bsecret\s*\{[^{}]*?\bname\s*=\s*"([^"]+)"', block, re.DOTALL),
        "internal_only": not (ingress and external),
    }


def _tf_web_app(block: str, v: dict[str, str]) -> dict[str, Any]:
    image = _tf_attr(block, "docker_image_name", v) or _tf_attr(block, "docker_image", v)
    public = (_tf_attr(block, "public_network_access_enabled", v) or "true").lower() != "false"
    return {
        "image_refs": [image] if image else [],
        "ports": [{"container_port": 443, "exposure": "public" if public else "internal", "source": "app service"}],
        "identity_ref": "system-assigned" if "SystemAssigned" in block else None,
        "env_var_names": _tf_env_names(block),
        "internal_only": not public,
        "security_findings": ["https_not_enforced"]
        if (_tf_attr(block, "https_only", v) or "").lower() == "false"
        else None,
    }


def _tf_aks(block: str, v: dict[str, str]) -> dict[str, Any]:
    lo, hi = _int(_tf_attr(block, "min_count", v)), _int(_tf_attr(block, "max_count", v))
    count = _int(_tf_attr(block, "node_count", v))
    private = (_tf_attr(block, "private_cluster_enabled", v) or "").lower() == "true"
    return {
        "scaling": {"replicas": count, "min_replicas": lo, "max_replicas": hi,
                    "autoscaler": "hpa" if lo is not None or hi is not None else None},
        "internal_only": True if private else None,
    }


def _tf_cloud_run(block: str, v: dict[str, str]) -> dict[str, Any]:
    ingress = (_tf_attr(block, "ingress", v) or "INGRESS_TRAFFIC_ALL").upper()
    public = "ALL" in ingress
    port = _int(_tf_attr(block, "container_port", v))
    lo = _int(_tf_attr(block, "min_instance_count", v))
    hi = _int(_tf_attr(block, "max_instance_count", v))
    if lo is None and (m := re.search(r"minScale\"?\s*=\s*\"?(\d+)", block)):
        lo = int(m.group(1))
    if hi is None and (m := re.search(r"maxScale\"?\s*=\s*\"?(\d+)", block)):
        hi = int(m.group(1))
    return {
        "image_refs": [_tf_attr(block, "image", v)] if _tf_attr(block, "image", v) else [],
        "ports": [{"container_port": port or 8080, "exposure": "public" if public else "internal", "source": "cloud run ingress"}],
        "scaling": {"min_replicas": lo, "max_replicas": hi, "autoscaler": "cloud_run"} if lo is not None or hi is not None else None,
        "resources": {"cpu_limit": _tf_attr(block, "cpu", v), "memory_limit": _tf_attr(block, "memory", v)},
        "identity_ref": _tf_attr(block, "service_account", v) or _tf_attr(block, "service_account_name", v),
        "env_var_names": _tf_env_names(block),
        "internal_only": not public,
    }


def _tf_lambda(block: str, v: dict[str, str]) -> dict[str, Any]:
    image = _tf_attr(block, "image_uri", v)
    runtime = _tf_attr(block, "runtime", v)
    return {
        "image_refs": [image] if image else [],
        "resources": {"memory_limit": _tf_attr(block, "memory_size", v)},
        "env_var_names": _tf_env_names(block),
        "identity_ref": _tf_attr(block, "role", v),
        "internal_only": True,
        "runtime": runtime,
    }


def _tf_ecs_container_defs(text: str) -> dict[str, Any]:
    image = re.search(r'"?\bimage"?\s*[=:]\s*"([^"]+)"', text)
    ports = [int(p) for p in re.findall(r'"?containerPort"?\s*[=:]\s*(\d+)', text)]
    env = re.findall(r'"?\bname"?\s*[=:]\s*"([A-Z][A-Z0-9_]+)"', text)
    secrets = re.findall(r'"?valueFrom"?\s*[=:]\s*"([^"]+)"', text)
    return {"image": image.group(1) if image else None, "ports": ports, "env": env, "secrets": secrets}


def terraform_workloads(content: str, file_path: str, adapter_name: str = "terraform") -> list[ComponentDetection]:
    """One DEPLOYMENT per compute resource in a ``.tf`` file."""
    variables = _tf_vars(content)
    resources = [(m, _tf_block(content, m.end() - 1)) for m in _TF_RES_RE.finditer(content)]
    by_name = {(m.group(1), m.group(2)): block for m, block in resources}
    file_label = file_path.replace("/", "_").replace("\\", "_")
    out: list[ComponentDetection] = []

    autoscale: dict[str, tuple[int | None, int | None]] = {}
    for m, block in resources:
        if m.group(1) == "aws_appautoscaling_target":
            tgt = re.search(r"aws_ecs_service\.(\w+)", block)
            if tgt:
                autoscale[tgt.group(1)] = (
                    _int(_tf_attr(block, "min_capacity", variables)),
                    _int(_tf_attr(block, "max_capacity", variables)),
                )
    invoker_public: set[str] = set()
    for m, block in resources:
        if m.group(1) in ("google_cloud_run_service_iam_member", "google_cloud_run_service_iam_binding",
                          "google_cloud_run_v2_service_iam_member", "google_cloud_run_v2_service_iam_binding") \
                and "allUsers" in block:
            ref = re.search(r"google_cloud_run(?:_v2)?_service\.(\w+)", block)
            if ref:
                invoker_public.add(ref.group(1))
    lambda_url_auth: dict[str, str] = {}
    for m, block in resources:
        if m.group(1) == "aws_lambda_function_url":
            ref = re.search(r"aws_lambda_function\.(\w+)", block)
            if ref:
                lambda_url_auth[ref.group(1)] = (_tf_attr(block, "authorization_type", variables) or "AWS_IAM").upper()

    for m, block in resources:
        rtype, tf_name = m.group(1), m.group(2)
        line = content[: m.start()].count("\n") + 1
        name = _tf_attr(block, "name", variables) or _tf_attr(block, "function_name", variables) or tf_name
        kind: tuple[str, str, str] | None = None
        parsed: dict[str, Any] = {}
        if rtype == "azurerm_container_app":
            kind, parsed = ("container_app", "azure_container_apps", "azure"), _tf_container_app(block, variables)
        elif rtype in ("azurerm_linux_web_app", "azurerm_windows_web_app", "azurerm_linux_function_app",
                       "azurerm_windows_function_app", "azurerm_app_service"):
            kind, parsed = ("app_service", "azure_app_service", "azure"), _tf_web_app(block, variables)
        elif rtype == "azurerm_kubernetes_cluster":
            kind, parsed = ("aks_cluster", "azure_aks", "azure"), _tf_aks(block, variables)
        elif rtype in ("aws_eks_cluster", "google_container_cluster"):
            prov = "aws" if rtype.startswith("aws") else "gcp"
            kind, parsed = ("k8s_cluster", "aws_eks" if prov == "aws" else "gcp_gke", prov), {}
        elif rtype in ("google_cloud_run_service", "google_cloud_run_v2_service"):
            parsed = _tf_cloud_run(block, variables)
            if tf_name in invoker_public:
                parsed["security_findings"] = ["unauthenticated_invoker"]
                for p in parsed.get("ports") or []:
                    p["exposure"] = "public"
                parsed["internal_only"] = False
            kind = ("cloud_run", "gcp_cloud_run", "gcp")
        elif rtype == "aws_lambda_function":
            parsed = _tf_lambda(block, variables)
            auth = lambda_url_auth.get(tf_name)
            if auth:
                parsed["ports"] = [{"exposure": "public", "source": f"lambda function url ({auth} auth)"}]
                parsed["internal_only"] = False
                if auth == "NONE":
                    parsed["security_findings"] = ["unauthenticated_function_url"]
            parsed.pop("runtime", None)
            kind = ("lambda", "aws_lambda", "aws")
        elif rtype == "aws_ecs_service":
            td_ref = re.search(r"aws_ecs_task_definition\.(\w+)", block)
            td_block = by_name.get(("aws_ecs_task_definition", td_ref.group(1))) if td_ref else None
            if td_block is None:
                td_block = next((b for (t, _), b in by_name.items() if t == "aws_ecs_task_definition"), None)
            defs = _tf_ecs_container_defs(td_block or "")
            public_ip = (_tf_attr(block, "assign_public_ip", variables) or "false").lower() == "true"
            lb_port = _int(_tf_attr(block, "container_port", variables))
            ports = [
                {"container_port": p, "exposure": "public" if public_ip else "internal", "source": "ecs portMappings"}
                for p in (defs["ports"] or ([lb_port] if lb_port else []))
            ]
            lo, hi = autoscale.get(tf_name, (None, None))
            desired = _int(_tf_attr(block, "desired_count", variables))
            parsed = {
                "image_refs": [defs["image"]] if defs["image"] else [],
                "ports": ports,
                "scaling": {"replicas": desired, "min_replicas": lo, "max_replicas": hi,
                            "autoscaler": "ecs_autoscaling" if lo is not None or hi is not None else None},
                "resources": {"cpu_limit": _tf_attr(td_block or "", "cpu", variables),
                              "memory_limit": _tf_attr(td_block or "", "memory", variables)},
                "env_var_names": defs["env"],
                "secret_refs": defs["secrets"],
                "internal_only": not public_ip and lb_port is None,
            }
            kind = ("ecs_service", "aws_ecs", "aws")
        if kind is None:
            continue
        out.extend(
            _emit(
                adapter_name=adapter_name,
                file_path=file_path,
                line=line,
                canonical=f"deployment:terraform:workload:{file_label}:{rtype}.{tf_name}",
                name=name,
                workload_kind=kind[0],
                cloud_service=kind[1],
                cloud_provider=kind[2],
                iac_format="terraform",
                parsed=parsed,
                extra_meta={"terraform_resource": f"{rtype}.{tf_name}"},
            )
        )
    return out


# ---------------------------------------------------------------------------
# CloudFormation / SAM
# ---------------------------------------------------------------------------


def _cf(value: Any) -> Any:
    """Collapse ``{"Ref": x}`` / ``!Ref x`` style values to a printable token (or None)."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict) and len(value) == 1:
        (k, v), = value.items()
        return f"${{{k}:{v}}}" if isinstance(v, str) else None
    return None


def _cfn_env_names(props: dict[str, Any]) -> list[str]:
    env = (props.get("Environment") or {}).get("Variables") if isinstance(props.get("Environment"), dict) else None
    return [str(k) for k in env] if isinstance(env, dict) else []


def cfn_workloads(data: dict[str, Any], file_path: str, adapter_name: str = "cloudformation") -> list[ComponentDetection]:
    """One DEPLOYMENT per ECS service / Lambda / SAM function in a CloudFormation template."""
    resources = as_dict(data.get("Resources"))
    out: list[ComponentDetection] = []
    file_label = file_path.replace("/", "_").replace("\\", "_")

    task_defs = {
        k: v for k, v in resources.items() if isinstance(v, dict) and v.get("Type") == "AWS::ECS::TaskDefinition"
    }
    scalable = {}
    for v in resources.values():
        if isinstance(v, dict) and v.get("Type") == "AWS::ApplicationAutoScaling::ScalableTarget":
            p = v.get("Properties") or {}
            scalable[json.dumps(p.get("ResourceId"), sort_keys=True, default=str)] = (
                _int(p.get("MinCapacity")), _int(p.get("MaxCapacity"))
            )

    for logical, res in resources.items():
        if not isinstance(res, dict):
            continue
        rtype = str(res.get("Type") or "")
        props = as_dict(res.get("Properties"))
        parsed: dict[str, Any] = {}
        kind: tuple[str, str] | None = None
        events: list[str] = []
        if rtype == "AWS::ECS::Service":
            td_ref = props.get("TaskDefinition")
            td_key = str(td_ref.get("Ref")) if isinstance(td_ref, dict) else str(td_ref or "")
            td = task_defs.get(td_key) or (next(iter(task_defs.values())) if len(task_defs) == 1 else None)
            td_props = (td or {}).get("Properties") or {}
            cdefs = [c for c in td_props.get("ContainerDefinitions") or [] if isinstance(c, dict)]
            c0 = cdefs[0] if cdefs else {}
            net = (props.get("NetworkConfiguration") or {}).get("AwsvpcConfiguration") or {}
            public_ip = str(net.get("AssignPublicIp", "DISABLED")).upper() == "ENABLED"
            lbs = props.get("LoadBalancers") or []
            ecs_ports = [
                {"container_port": _int(pm.get("ContainerPort")),
                 "exposure": "public" if (public_ip or lbs) else "internal", "source": "ecs portMappings"}
                for c in cdefs for pm in c.get("PortMappings") or [] if isinstance(pm, dict)
            ]
            lo = hi = None
            for rid, (a, b) in scalable.items():
                if logical in rid:
                    lo, hi = a, b
            parsed = {
                "image_refs": [str(c0["Image"])] if isinstance(c0.get("Image"), str) else [],
                "ports": ecs_ports,
                "scaling": {"replicas": _int(props.get("DesiredCount")), "min_replicas": lo, "max_replicas": hi,
                            "autoscaler": "ecs_autoscaling" if lo is not None or hi is not None else None},
                "resources": {"cpu_limit": str(_cf(td_props.get("Cpu"))) if td_props.get("Cpu") else None,
                              "memory_limit": str(_cf(td_props.get("Memory"))) if td_props.get("Memory") else None},
                "env_var_names": [str(e["Name"]) for c in cdefs for e in c.get("Environment") or []
                                  if isinstance(e, dict) and e.get("Name")],
                "secret_refs": [str(e["Name"]) for c in cdefs for e in c.get("Secrets") or []
                                if isinstance(e, dict) and e.get("Name")],
                "identity_ref": str(_cf(td_props.get("TaskRoleArn"))) if td_props.get("TaskRoleArn") else None,
                "internal_only": not (public_ip or lbs),
            }
            kind = ("ecs_service", "aws_ecs")
        elif rtype in ("AWS::Lambda::Function", "AWS::Serverless::Function"):
            code = as_dict(props.get("Code"))
            image = props.get("ImageUri") or code.get("ImageUri")
            sam_events = as_dict(props.get("Events"))
            public = False
            for ev in sam_events.values():
                if isinstance(ev, dict) and ev.get("Type") in ("Api", "HttpApi"):
                    p = ev.get("Properties") or {}
                    events.append(f"{p.get('Method', 'ANY')} {p.get('Path', '/')}")
                    public = True
            url = props.get("FunctionUrlConfig") if isinstance(props.get("FunctionUrlConfig"), dict) else None
            findings: list[str] = []
            ports: list[dict[str, Any]] = []
            if url:
                auth = str(url.get("AuthType", "AWS_IAM")).upper()
                ports.append({"exposure": "public", "source": f"lambda function url ({auth} auth)"})
                public = True
                if auth == "NONE":
                    findings.append("unauthenticated_function_url")
            if events:
                ports.append({"exposure": "public", "source": "api gateway event"})
            parsed = {
                "image_refs": [str(image)] if isinstance(image, str) else [],
                "ports": ports,
                "resources": {"memory_limit": str(props["MemorySize"]) if props.get("MemorySize") else None},
                "env_var_names": _cfn_env_names(props),
                "identity_ref": str(_cf(props.get("Role"))) if props.get("Role") else None,
                "internal_only": not public,
                "security_findings": findings or None,
            }
            kind = ("lambda", "aws_lambda")
        if kind is None:
            continue
        name = str(_cf(props.get("ServiceName")) or _cf(props.get("FunctionName")) or logical)
        out.extend(
            _emit(
                adapter_name=adapter_name,
                file_path=file_path,
                line=1,
                canonical=f"deployment:cfn:workload:{file_label}:{logical}",
                name=name,
                workload_kind=kind[0],
                cloud_service=kind[1],
                cloud_provider="aws",
                iac_format="cloudformation",
                parsed=parsed,
                extra_meta={"cfn_logical_id": logical, **({"sam_api_routes": events} if events else {})},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Standalone descriptors: Cloud Run / Knative service YAML, ECS task definition JSON
# ---------------------------------------------------------------------------


class ServiceDescriptorAdapter:
    """Cloud Run / Knative ``Service`` YAML and ECS task-definition JSON."""

    name = "service_descriptor"

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        """Workload detections for a standalone descriptor ([] when the file is not one)."""
        stripped = content.lstrip()
        if stripped.startswith("{"):
            try:
                data = json.loads(content)
            except ValueError:
                return []
            if isinstance(data, dict) and "containerDefinitions" in data:
                return self._ecs_taskdef(data, file_path)
            return []
        if "serving.knative.dev" not in content and "run.googleapis.com" not in content:
            return []
        try:
            docs = [d for d in yaml.safe_load_all(content) if isinstance(d, dict)]
        except yaml.YAMLError:
            return []
        out: list[ComponentDetection] = []
        for doc in docs:
            if str(doc.get("kind")) == "Service" and "knative" in str(doc.get("apiVersion", "")):
                out.extend(self._knative(doc, file_path))
        return out

    def _knative(self, doc: dict[str, Any], file_path: str) -> list[ComponentDetection]:
        meta = doc.get("metadata") or {}
        name = str(meta.get("name") or "service")
        ann = (meta.get("annotations") or {}) | (
            ((doc.get("spec") or {}).get("template") or {}).get("metadata", {}).get("annotations") or {}
        )
        spec = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
        containers = [c for c in spec.get("containers") or [] if isinstance(c, dict)]
        c0 = containers[0] if containers else {}
        ingress = str(ann.get("run.googleapis.com/ingress", "all")).lower()
        public = ingress == "all"
        lo = _int(ann.get("autoscaling.knative.dev/minScale"))
        hi = _int(ann.get("autoscaling.knative.dev/maxScale"))
        ports = [
            {"container_port": _int(p.get("containerPort")), "exposure": "public" if public else "internal",
             "source": "cloud run ingress"}
            for p in c0.get("ports") or [] if isinstance(p, dict)
        ] or [{"container_port": 8080, "exposure": "public" if public else "internal", "source": "cloud run ingress"}]
        limits = (c0.get("resources") or {}).get("limits") or {}
        parsed = {
            "image_refs": [str(c0["image"])] if c0.get("image") else [],
            "ports": ports,
            "scaling": {"min_replicas": lo, "max_replicas": hi, "autoscaler": "cloud_run"}
            if lo is not None or hi is not None else None,
            "resources": {"cpu_limit": str(limits["cpu"]) if "cpu" in limits else None,
                          "memory_limit": str(limits["memory"]) if "memory" in limits else None},
            "identity_ref": str(spec["serviceAccountName"]) if spec.get("serviceAccountName") else None,
            "env_var_names": [str(e["name"]) for e in c0.get("env") or [] if isinstance(e, dict) and e.get("name")],
            "internal_only": not public,
        }
        return _emit(
            adapter_name=self.name, file_path=file_path, line=1,
            canonical=f"deployment:cloudrun:{file_path}:{name}", name=name,
            workload_kind="cloud_run", cloud_service="gcp_cloud_run", cloud_provider="gcp",
            iac_format="cloud-run-yaml", parsed=parsed,
        )

    def _ecs_taskdef(self, data: dict[str, Any], file_path: str) -> list[ComponentDetection]:
        name = str(data.get("family") or "task-definition")
        cdefs = [c for c in data.get("containerDefinitions") or [] if isinstance(c, dict)]
        c0 = cdefs[0] if cdefs else {}
        ports = [
            {"container_port": _int(pm.get("containerPort")), "exposure": "unknown", "source": "ecs portMappings"}
            for c in cdefs for pm in c.get("portMappings") or [] if isinstance(pm, dict)
        ]
        parsed = {
            "image_refs": [str(c0["image"])] if c0.get("image") else [],
            "ports": ports,
            "resources": {"cpu_limit": str(data["cpu"]) if data.get("cpu") else None,
                          "memory_limit": str(data["memory"]) if data.get("memory") else None},
            "identity_ref": str(data["taskRoleArn"]) if data.get("taskRoleArn") else None,
            "env_var_names": [str(e["name"]) for c in cdefs for e in c.get("environment") or []
                              if isinstance(e, dict) and e.get("name")],
            "secret_refs": [str(e["name"]) for c in cdefs for e in c.get("secrets") or []
                            if isinstance(e, dict) and e.get("name")],
        }
        return _emit(
            adapter_name=self.name, file_path=file_path, line=1,
            canonical=f"deployment:ecs-taskdef:{file_path}:{name}", name=name,
            workload_kind="ecs_task_definition", cloud_service="aws_ecs", cloud_provider="aws",
            iac_format="ecs-task-definition", parsed=parsed,
        )
