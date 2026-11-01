"""Bicep workload parsing — one DEPLOYMENT per deployed compute resource.

Handles ``Microsoft.App/containerApps`` (incl. ``[for x in coll: {...}]`` loops expanded from a
``var`` array), ``Microsoft.Web/sites``, ``Microsoft.ContainerService/managedClusters`` and
``Microsoft.ContainerInstance/containerGroups``. Values are resolved from top-level ``var`` /
``param`` defaults where they are simple literals; anything dynamic is left out rather than
guessed. Only env var *names* and secret *names* are recorded.
"""

from __future__ import annotations

import re
from typing import Any

from ..image_ref import is_templated
from ..types import ComponentType
from ..workload import service_references, workload_metadata
from ._image_nodes import attach_images
from .base import ComponentDetection

_RES_RE = re.compile(r"resource\s+(\w+)\s+'([^']+)'\s*=\s*(?:\[\s*for\s+(\w+)\s+in\s+([^:]+?)\s*:\s*)?(?:if\s*\([^)]*\)\s*)?\{")
_VAR_RE = re.compile(r"^(?:var|param)\s+(\w+)(?:\s+\w+)?\s*=\s*", re.MULTILINE)
_PLACEHOLDER_IMAGES = ("containerapps-helloworld", "azuredocs/aci-helloworld")

_COMPUTE_TYPES = {
    "microsoft.app/containerapps": ("container_app", "azure_container_apps"),
    "microsoft.web/sites": ("app_service", "azure_app_service"),
    "microsoft.containerservice/managedclusters": ("aks_cluster", "azure_aks"),
    "microsoft.containerinstance/containergroups": ("container_instance", "azure_container_instances"),
}


def _balanced(text: str, start: int) -> str:
    """Expression text starting at *start*: balanced brackets, ends at newline at depth 0."""
    depth = 0
    in_str = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "'":
                in_str = False
        elif ch == "'":
            in_str = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                break
        elif ch in "\n," and depth == 0:
            break
        elif ch == "/" and text[i : i + 2] == "//":
            if depth == 0:
                break  # trailing comment ends the expression
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl
            continue
        i += 1
    return text[start:i].strip()


def _top_level_vars(content: str) -> dict[str, str]:
    """Raw expression of every top-level ``var`` / ``param`` default."""
    out: dict[str, str] = {}
    for m in _VAR_RE.finditer(content):
        out[m.group(1)] = _balanced(content, m.end())
    return out


def _expr_after(block: str, key: str) -> str | None:
    """Raw expression following ``key:`` (first occurrence)."""
    m = re.search(rf"(?<![\w.]){re.escape(key)}\s*:\s*", block)
    return _balanced(block, m.end()) if m else None


def _literal(expr: str | None, bindings: dict[str, Any], variables: dict[str, str]) -> str | None:
    """Value of a simple expression: quoted literal, bound/var identifier, ``json('x')``, number."""
    if expr is None:
        return None
    expr = expr.strip().rstrip(",")
    m = re.fullmatch(r"'([^']*)'", expr)
    if m:
        return m.group(1)
    m = re.fullmatch(r"json\('([^']*)'\)", expr)
    if m:
        return m.group(1)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", expr):
        return expr
    m = re.fullmatch(r"(\w+)(?:\.(\w+))?", expr)
    if m:
        ident, prop = m.groups()
        if ident in bindings:
            val = bindings[ident]
            if isinstance(val, dict):
                return val.get(prop) if prop else None
            return str(val) if not prop else None
        if ident in variables and not prop:
            return _literal(variables[ident], bindings, {k: v for k, v in variables.items() if k != ident})
    return None


def _int(expr: str | None, bindings: dict[str, Any], variables: dict[str, str]) -> int | None:
    val = _literal(expr, bindings, variables)
    try:
        return int(str(val)) if val is not None else None
    except ValueError:
        return None


def _names_in(expr: str | None, variables: dict[str, str], key: str = "name", depth: int = 0) -> list[str]:
    """Quoted ``key: '...'`` values in *expr*, following referenced top-level vars."""
    if not expr or depth > 3:
        return []
    out = re.findall(rf"(?<![\w.]){key}\s*:\s*'([^']+)'", expr)
    for ident in set(re.findall(r"(?<![\w.'])([A-Za-z_]\w*)(?![\w'(:])", expr)):
        if ident in variables and ident != key:
            out.extend(_names_in(variables[ident], variables, key, depth + 1))
    return out


def _values_in(expr: str | None, variables: dict[str, str], depth: int = 0) -> list[str]:
    """Quoted ``value: '...'`` literals in *expr*, following referenced top-level vars."""
    if not expr or depth > 3:
        return []
    out = re.findall(r"(?<![\w.])value\s*:\s*'([^']*)'", expr)
    for ident in set(re.findall(r"(?<![\w.'])([A-Za-z_]\w*)(?![\w'(:])", expr)):
        if ident in variables and ident != "value":
            out.extend(_values_in(variables[ident], variables, depth + 1))
    return out


def _collection_items(expr: str, variables: dict[str, str]) -> list[Any]:
    """Items of ``[ 'a' 'b' ]`` (strings) or ``[ { name: 'a' } ]`` (dicts) for a ``for`` loop."""
    raw = variables.get(expr.strip(), expr)
    if not raw.lstrip().startswith("["):
        return []
    items: list[Any] = [m for m in re.findall(r"^\s*'([^']+)'\s*$", raw, re.MULTILINE)]
    if items:
        return items
    dicts: list[Any] = []
    for obj in re.findall(r"\{([^{}]*)\}", raw):
        pairs = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", obj))
        if pairs:
            dicts.append(pairs)
    return dicts


def _brace(content: str, open_index: int) -> str:
    depth = 0
    for idx in range(open_index, len(content)):
        if content[idx] == "{":
            depth += 1
        elif content[idx] == "}":
            depth -= 1
            if depth == 0:
                return content[open_index : idx + 1]
    return content[open_index:]


def _probes(expr: str | None) -> list[str]:
    out: list[str] = []
    for obj in re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", expr or ""):
        t = re.search(r"type\s*:\s*'(\w+)'", obj)
        p = re.search(r"path\s*:\s*'([^']+)'", obj)
        if t and p:
            out.append(f"{t.group(1).lower()}:{p.group(1)}")
    return list(dict.fromkeys(out))


def _containerapp(
    body: str, bindings: dict[str, Any], variables: dict[str, str]
) -> dict[str, Any]:
    ingress = _expr_after(body, "ingress")
    ports: list[dict[str, Any]] = []
    internal_only: bool | None = True
    if ingress and ingress.startswith("{"):
        external = (_literal(_expr_after(ingress, "external"), bindings, variables) or "").lower()
        if external == "" and re.search(r"external\s*:\s*true", ingress, re.IGNORECASE):
            external = "true"
        target = _int(_expr_after(ingress, "targetPort"), bindings, variables)
        exposure = "public" if external == "true" else "internal"
        internal_only = exposure != "public"
        if target is not None:
            ports.append(
                {"container_port": target, "exposure": exposure, "source": "aca ingress"}
            )
    containers = _expr_after(body, "containers") or ""
    image = _literal(_expr_after(containers, "image"), bindings, variables)
    image_refs = [image] if image and not any(p in image for p in _PLACEHOLDER_IMAGES) else []
    cpu = _literal(_expr_after(containers, "cpu"), bindings, variables)
    memory = _literal(_expr_after(containers, "memory"), bindings, variables)
    scale = _expr_after(body, "scale") or ""
    lo = _int(_expr_after(scale, "minReplicas"), bindings, variables)
    hi = _int(_expr_after(scale, "maxReplicas"), bindings, variables)
    scaling: dict[str, Any] = {"min_replicas": lo, "max_replicas": hi}
    if scale and re.search(r"\brules\s*:", scale):
        scaling["autoscaler"] = "aca_rule"
    elif lo is not None or hi is not None:
        scaling["autoscaler"] = "aca_rule" if lo != hi else "none"
    identity = _expr_after(body, "identity") or ""
    identity_ref: str | None = None
    if re.search(r"SystemAssigned", identity):
        identity_ref = "system-assigned"
    elif "UserAssigned" in identity:
        ids = re.findall(r"'\$\{(\w+)\.id\}'|\[\s*(\w+)\.id\s*\]", identity)
        flat = [a or b for a, b in ids]
        identity_ref = flat[0] if flat else "user-assigned"
    env_expr = _expr_after(containers, "env")
    env_names = _names_in(env_expr, variables)
    secret_refs = _names_in(_expr_after(body, "secrets"), variables)
    secret_refs += re.findall(r"secretRef\s*:\s*'([^']+)'", containers)
    return {
        "ports": ports,
        "image_refs": image_refs,
        "resources": {"cpu_limit": cpu, "memory_limit": memory},
        "scaling": scaling,
        "identity_ref": identity_ref,
        "env_var_names": env_names,
        "secret_refs": secret_refs,
        "probes": _probes(_expr_after(containers, "probes")),
        "internal_only": internal_only,
        "_env_values": _values_in(env_expr, variables),
    }


def _appservice(body: str, bindings: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    fx = _literal(_expr_after(body, "linuxFxVersion"), bindings, variables) or ""
    image = fx.split("|", 1)[1] if fx.upper().startswith("DOCKER|") else None
    public_access = (_literal(_expr_after(body, "publicNetworkAccess"), bindings, variables) or "Enabled")
    public = public_access.lower() != "disabled"
    return {
        "image_refs": [image] if image else [],
        "ports": [{"container_port": 443, "exposure": "public" if public else "internal",
                   "source": "app service"}],
        "identity_ref": "system-assigned" if "SystemAssigned" in (_expr_after(body, "identity") or "") else None,
        "env_var_names": _names_in(_expr_after(body, "appSettings"), variables),
        "internal_only": not public,
    }


def _aks(body: str, bindings: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    pools = _expr_after(body, "agentPoolProfiles") or ""
    lo = _int(_expr_after(pools, "minCount"), bindings, variables)
    hi = _int(_expr_after(pools, "maxCount"), bindings, variables)
    count = _int(_expr_after(pools, "count"), bindings, variables)
    private = re.search(r"enablePrivateCluster\s*:\s*true", body, re.IGNORECASE) is not None
    scaling: dict[str, Any] = {"replicas": count, "min_replicas": lo, "max_replicas": hi}
    if lo is not None or hi is not None:
        scaling["autoscaler"] = "hpa"
    return {"scaling": scaling, "internal_only": private or None}


def _aci(body: str, bindings: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    image = _literal(_expr_after(body, "image"), bindings, variables)
    ports = [
        {"container_port": int(p), "exposure": "unknown", "source": "aci ports"}
        for p in re.findall(r"port\s*:\s*(\d+)", _expr_after(body, "ports") or "")
    ]
    public = re.search(r"type\s*:\s*'Public'", _expr_after(body, "ipAddress") or "") is not None
    for p in ports:
        p["exposure"] = "public" if public else "internal"
    return {
        "image_refs": [image] if image else [],
        "ports": ports,
        "resources": {
            "cpu_limit": _literal(_expr_after(body, "cpu"), bindings, variables),
            "memory_limit": _literal(_expr_after(body, "memoryInGB"), bindings, variables),
        },
        "env_var_names": _names_in(_expr_after(body, "environmentVariables"), variables),
        "internal_only": not public,
    }


_PARSERS = {
    "microsoft.app/containerapps": _containerapp,
    "microsoft.web/sites": _appservice,
    "microsoft.containerservice/managedclusters": _aks,
    "microsoft.containerinstance/containergroups": _aci,
}


def bicep_workloads(content: str, file_path: str, adapter_name: str = "bicep") -> list[ComponentDetection]:
    """One DEPLOYMENT (+ image nodes) per compute resource in a Bicep file."""
    variables = _top_level_vars(content)
    file_label = file_path.replace("/", "_").replace("\\", "_")
    results: list[ComponentDetection] = []
    seen: set[str] = set()
    env_by_det: dict[str, list[str]] = {}

    for m in _RES_RE.finditer(content):
        symbol, rtype_full, loop_var, loop_coll = m.groups()
        rtype = rtype_full.split("@")[0].lower()
        if rtype not in _PARSERS:
            continue
        workload_kind, cloud_service = _COMPUTE_TYPES[rtype]
        body = _brace(content, m.end() - 1)
        line = content[: m.start()].count("\n") + 1

        if loop_var:
            items = _collection_items(loop_coll, variables)
            binding_sets: list[dict[str, Any]] = [{loop_var: it} for it in items] or [{}]
        else:
            binding_sets = [{}]

        for bindings in binding_sets:
            name = _literal(_expr_after(body, "name"), bindings, variables) or symbol
            tag_expr = _expr_after(body, "tags") or ""
            azd = re.search(r"'azd-service-name'\s*:\s*([^\n}]+)", tag_expr)
            azd_name = _literal(azd.group(1).strip(), bindings, variables) if azd else None
            parsed = _PARSERS[rtype](body, bindings, variables)
            env_values = parsed.pop("_env_values", [])
            wl = workload_metadata(
                service_name=name,
                workload_kind=workload_kind,
                source_format="bicep",
                cloud_service=cloud_service,
                **{k: v for k, v in parsed.items() if v not in (None, [], {})},
            )
            canonical = f"deployment:bicep:workload:{file_label}:{symbol}:{name}".lower()[:160]
            if canonical in seen:
                continue
            seen.add(canonical)
            meta: dict[str, Any] = {
                "iac_format": "bicep",
                "deployment_target": "azure",
                "cloud_provider": "azure",
                "bicep_resource_type": rtype_full,
                "bicep_symbol": symbol,
                "workload": wl,
            }
            if azd_name:
                meta["azd_service_name"] = azd_name
            det = ComponentDetection(
                component_type=ComponentType.DEPLOYMENT,
                canonical_name=canonical,
                display_name=name,
                adapter_name=adapter_name,
                priority=8,
                confidence=0.9,
                metadata=meta,
                file_path=file_path,
                line=line,
                snippet=f"resource {symbol} '{rtype_full}'"[:120],
                evidence_kind="iac",
            )
            refs = [r for r in parsed.get("image_refs", []) if r and not is_templated(r)]
            results.append(det)
            results.extend(
                attach_images(det, refs, adapter_name=adapter_name, file_path=file_path, line=line)
            )
            env_by_det[det.canonical_name] = env_values

    # Service-to-service references in env values become depends_on (names only).
    names = [d.metadata["workload"]["service_name"] for d in results if d.canonical_name in env_by_det]
    for det in results:
        if det.canonical_name not in env_by_det:
            continue
        wl = det.metadata["workload"]
        refs = service_references(env_by_det[det.canonical_name], names, wl["service_name"])
        if refs:
            wl["depends_on"] = refs
    return results
