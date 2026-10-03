"""Cross-resource deployment topology (container / orchestrator / cloud layer).

Adapters emit per-file facts: workload nodes (compose service, K8s workload, container app,
ECS service, ...), image nodes, and *fact markers* (K8s Service/HPA/Kustomize, azd services).
This pass runs once after extraction — from :func:`nuguard.sbom.enricher.enrich`, right after
the HTTP-semantics pass — and:

1. **merges facts** onto workloads (Service ports/selectors, HPA bounds, Kustomize overrides,
   azd project mapping) and drops the pure-fact marker nodes;
2. **links** ``RUNS`` (workload → image), ``HOSTS`` (workload → agent/endpoint/MCP server/tool
   found in its source directory), ``EXPOSES`` (public workload → hosted endpoint),
   ``ROUTES_TO`` (gateway → backend) and ``DEPENDS_ON`` (workload → workload);
3. sets ``hosted_by`` / ``network_exposure`` on hosted components, so a pentest can tell which
   endpoints are internet-reachable and which sit behind internal ingress.

Idempotent and additive. Links backed by a declared build context / azd project / Service
selector are ``derivation="hint"``; name- or path-guessed links are ``fallback_heuristic`` with
a confidence below 1.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from nuguard.common.logging import get_logger

from .image_ref import image_basename, normalize_image_ref
from .models import AiSbomDocument, Edge, Node, PortDetail, ScalingDetail
from .types import ComponentType, RelationshipType

_log = get_logger(__name__)

_HOSTED_TYPES = frozenset(
    {
        ComponentType.AGENT,
        ComponentType.API_ENDPOINT,
        ComponentType.MCP_SERVER,
        ComponentType.TOOL,
    }
)
_REACHABLE_TYPES = frozenset({ComponentType.API_ENDPOINT, ComponentType.MCP_SERVER})
#: components for which "reachable from outside" is meaningful (a TOOL is only a function)
_EXPOSURE_TYPES = _REACHABLE_TYPES | {ComponentType.AGENT}
_EXPOSURE_RANK = {"unknown": 0, "cluster": 1, "internal": 2, "public": 3}


def _extras(node: Node) -> dict[str, Any]:
    return node.metadata.extras


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _paths(node: Node) -> list[str]:
    return [e.location.path.replace("\\", "/") for e in node.evidence if e.location and e.location.path]


def _under(path: str, directory: str) -> bool:
    d = directory.rstrip("/")
    return d not in ("", ".") and (path == d or path.startswith(d + "/"))


class _Topology:
    def __init__(self, doc: AiSbomDocument) -> None:
        self.doc = doc
        self.edges: set[tuple[UUID, UUID, RelationshipType]] = {
            (e.source, e.target, e.relationship_type) for e in doc.edges
        }
        self.workloads: list[Node] = []
        self.markers: list[Node] = []
        self.alias: dict[tuple[str, str], list[Node]] = {}  # (namespace, service name) -> workloads

    # -- helpers -----------------------------------------------------------
    def add_edge(
        self,
        src: Node,
        tgt: Node,
        rel: RelationshipType,
        *,
        declared: bool,
        confidence: float = 0.6,
    ) -> None:
        key = (src.id, tgt.id, rel)
        if key in self.edges:
            return
        self.edges.add(key)
        if declared:
            self.doc.edges.append(Edge(source=src.id, target=tgt.id, relationship_type=rel, derivation="hint"))
        else:
            self.doc.edges.append(
                Edge(
                    source=src.id,
                    target=tgt.id,
                    relationship_type=rel,
                    derivation="fallback_heuristic",
                    confidence=confidence,
                )
            )

    def collect(self) -> None:
        for node in self.doc.nodes:
            if node.component_type != ComponentType.DEPLOYMENT:
                continue
            if _extras(node).get("fact_marker"):
                self.markers.append(node)
            elif node.metadata.workload is not None:
                self.workloads.append(node)
        for node in self.workloads:
            wl = node.metadata.workload
            assert wl is not None
            ns = wl.namespace or "default"
            for name in {wl.service_name or node.name, node.name}:
                self.alias.setdefault((ns, _norm(name)), []).append(node)

    # -- 1. merge facts ----------------------------------------------------
    def merge_facts(self) -> None:
        for node in [*self.markers, *self.workloads]:
            svc = _extras(node).get("k8s_service")
            if isinstance(svc, dict):
                self._merge_service(svc)
        for node in self.markers:
            hpa = _extras(node).get("k8s_hpa")
            if isinstance(hpa, dict):
                self._merge_hpa(hpa)
            kz = _extras(node).get("kustomize")
            if isinstance(kz, dict):
                self._merge_kustomize(kz)
        azd = {
            str(f["name"]).lower(): f
            for n in self.markers
            if isinstance(f := _extras(n).get("azd_service"), dict)
        }
        if azd:
            self._merge_azd(azd)

    def _merge_service(self, svc: dict[str, Any]) -> None:
        ns = str(svc.get("namespace") or "default")
        selector = svc.get("selector") or {}
        public = svc.get("type") in ("LoadBalancer", "NodePort")
        targets = [
            w for w in self.workloads
            if (w.metadata.workload.namespace or "default") == ns  # type: ignore[union-attr]
            and selector
            and selector.items() <= (_extras(w).get("k8s_pod_labels") or {}).items()
        ]
        for w in targets:
            self.alias.setdefault((ns, _norm(str(svc["name"]))), []).append(w)
            wl = w.metadata.workload
            assert wl is not None
            for sp in svc.get("ports") or []:
                target = sp.get("target_port")
                container = target if isinstance(target, int) else None
                if container is None and len(wl.ports) == 1:
                    container = wl.ports[0].container_port
                if container is None:
                    continue
                existing = next((p for p in wl.ports if p.container_port == container), None)
                if existing is None:
                    existing = PortDetail(container_port=container, source="k8s Service")
                    wl.ports.append(existing)
                if isinstance(sp.get("port"), int):
                    existing.service_port = sp["port"]
                existing.source = existing.source or "k8s Service"
                if public:
                    existing.exposure = "public"
                elif existing.exposure == "unknown":
                    existing.exposure = "cluster"
            if public:
                wl.internal_only = False

    def _merge_hpa(self, hpa: dict[str, Any]) -> None:
        ns = str(hpa.get("namespace") or "default")
        for w in self.alias.get((ns, _norm(str(hpa["target_name"]))), []):
            wl = w.metadata.workload
            assert wl is not None
            sc = wl.scaling or ScalingDetail()
            sc.min_replicas = hpa.get("min_replicas")
            sc.max_replicas = hpa.get("max_replicas")
            sc.autoscaler = hpa.get("autoscaler") or "hpa"
            metrics = hpa.get("metrics") or []
            if metrics:
                sc.metric, _, target = str(metrics[0]).partition(":")
                sc.target = target or None
            wl.scaling = sc

    def _merge_kustomize(self, kz: dict[str, Any]) -> None:
        directory = str(kz.get("directory") or ".")
        for w in self.workloads:
            wl = w.metadata.workload
            assert wl is not None
            if wl.source_format not in ("k8s", "helm_values"):
                continue
            if directory not in (".", "") and not any(_under(p, directory) or p.startswith(directory + "/") for p in _paths(w)):
                # base resources live in a sibling directory; accept when referenced by name below
                if not any(_norm(str(r.get("name"))) == _norm(wl.service_name or "") for r in kz.get("replicas") or []):
                    continue
            for rep in kz.get("replicas") or []:
                if _norm(str(rep["name"])) == _norm(wl.service_name or ""):
                    wl.scaling = (wl.scaling or ScalingDetail()).model_copy(update={"replicas": rep["count"]})
            for img in kz.get("images") or []:
                for i, ref in enumerate(wl.image_refs):
                    if image_basename(ref) == image_basename(str(img["name"])) or ref.split(":")[0] == img["name"]:
                        base = img.get("new_name") or ref.rsplit(":", 1)[0]
                        tag = img.get("new_tag")
                        wl.image_refs[i] = f"{base}:{tag}" if tag else str(base)
            if kz.get("namespace") and wl.namespace in (None, "default"):
                wl.namespace = str(kz["namespace"])

    def _merge_azd(self, azd: dict[str, dict[str, Any]]) -> None:
        for w in self.workloads:
            wl = w.metadata.workload
            assert wl is not None
            key = str(_extras(w).get("azd_service_name") or "").lower()
            fact = azd.get(key)
            if fact is None:
                continue
            wl.source_dir = wl.source_dir or str(fact["project"])
            wl.build_context = wl.build_context or str(fact["project"])
            if fact.get("dockerfile") and not wl.dockerfile:
                wl.dockerfile = str(fact["dockerfile"])
            _extras(w)["source_link"] = "declared"

    # -- 2. source directory, images, code ---------------------------------
    def resolve_source_dirs(self) -> None:
        app_images = [
            n for n in self.doc.nodes
            if n.component_type == ComponentType.CONTAINER_IMAGE and n.metadata.image_role == "app"
        ]
        by_build_dir = {
            str(_extras(n).get("build_dir") or ""): n for n in app_images if _extras(n).get("build_dir")
        }
        for w in self.workloads:
            wl = w.metadata.workload
            assert wl is not None
            if wl.source_dir:
                _extras(w).setdefault("source_link", "declared")
                continue
            ctx = (wl.build_context or "").strip()
            if wl.dockerfile and ctx in ("", "."):
                wl.source_dir = posixpath.dirname(wl.dockerfile) or None
                _extras(w)["source_link"] = "declared" if wl.source_dir else "heuristic"
            elif ctx and ctx != ".":
                wl.source_dir = ctx
                _extras(w)["source_link"] = "declared"
            if wl.source_dir:
                continue
            # image basename or service name matches a Dockerfile directory
            candidates = [image_basename(r) for r in wl.image_refs] + [wl.service_name or w.name]
            for build_dir in by_build_dir:
                leaf = _norm(posixpath.basename(build_dir))
                if leaf and any(_norm(c) == leaf for c in candidates):
                    wl.source_dir = build_dir
                    wl.dockerfile = wl.dockerfile or str(_extras(by_build_dir[build_dir]).get("dockerfile") or "") or None
                    _extras(w)["source_link"] = "heuristic"
                    break

    def link_images(self) -> None:
        images = [n for n in self.doc.nodes if n.component_type == ComponentType.CONTAINER_IMAGE]
        by_ref: dict[str, Node] = {}
        for n in images:
            if n.metadata.base_image and n.metadata.image_role != "app":
                by_ref.setdefault(normalize_image_ref(n.metadata.base_image), n)
        by_dockerfile = {
            str(_extras(n).get("dockerfile")): n for n in images if n.metadata.image_role == "app"
        }
        for w in self.workloads:
            wl = w.metadata.workload
            assert wl is not None
            if wl.dockerfile and wl.dockerfile in by_dockerfile:
                self.add_edge(
                    w, by_dockerfile[wl.dockerfile], RelationshipType.RUNS,
                    declared=_extras(w).get("source_link") == "declared", confidence=0.7,
                )
            for ref in wl.image_refs:
                target = by_ref.get(normalize_image_ref(ref))
                if target is not None:
                    self.add_edge(w, target, RelationshipType.RUNS, declared=True)

    def link_code(self) -> None:
        sourced = [
            (w, w.metadata.workload.source_dir)  # type: ignore[union-attr]
            for w in self.workloads
            if w.metadata.workload and w.metadata.workload.source_dir
        ]
        if not sourced:
            return
        for node in self.doc.nodes:
            if node.component_type not in _HOSTED_TYPES:
                continue
            best_len = -1
            best: list[tuple[Node, str]] = []
            for path in _paths(node):
                for w, src in sourced:
                    if _under(path, str(src)):
                        length = len(str(src).rstrip("/"))
                        if length > best_len:
                            best_len, best = length, [(w, str(src))]
                        elif length == best_len and all(b[0] is not w for b in best):
                            best.append((w, str(src)))
            for w, _src in best:
                declared = _extras(w).get("source_link") == "declared"
                self.add_edge(w, node, RelationshipType.HOSTS, declared=declared, confidence=0.65)

    # -- 3. gateway routing, dependencies ----------------------------------
    def link_routing(self) -> None:
        for node in self.doc.nodes:
            if node.component_type != ComponentType.DEPLOYMENT:
                continue
            for rule in _extras(node).get("k8s_ingress_rules") or []:
                ns = str(_extras(node).get("k8s_namespace") or "default")
                for backend in self.alias.get((ns, _norm(str(rule["service"]))), []):
                    self.add_edge(node, backend, RelationshipType.ROUTES_TO, declared=True)
                    self._mark_public(backend, rule.get("port"), "k8s Ingress")
            upstream = _extras(node).get("upstream_url")
            if isinstance(upstream, str) and upstream:
                parsed = urlparse(upstream if "://" in upstream else f"http://{upstream}")
                host, port = (parsed.hostname or ""), parsed.port
                direct = [b for (_ns, name), bs in self.alias.items() if name == _norm(host) for b in bs]
                for backend in direct:
                    self.add_edge(node, backend, RelationshipType.ROUTES_TO, declared=True)
                    self._mark_public(backend, port, "nginx gateway")
                if not direct:
                    # ``upstream grp { server ${ENV}; }`` — the target is injected at deploy
                    # time, so route to what the workload shipping this config depends on.
                    for path in _paths(node):
                        for host_w in self.workloads:
                            src = host_w.metadata.workload.source_dir  # type: ignore[union-attr]
                            if src and _under(path, str(src)):
                                for backend in self.dependencies_of(host_w):
                                    self.add_edge(
                                        node, backend, RelationshipType.ROUTES_TO,
                                        declared=False, confidence=0.6,
                                    )
                                    self._mark_public(backend, None, "nginx gateway")

    def _mark_public(self, node: Node, port: Any, source: str) -> None:
        wl = node.metadata.workload
        assert wl is not None
        matched = False
        for p in wl.ports:
            if port is None or port in (p.container_port, p.service_port):
                if p.exposure != "public":
                    p.exposure = "public"
                    p.source = f"{p.source or 'port'} via {source}"
                matched = True
        if not matched and isinstance(port, int):
            wl.ports.append(PortDetail(container_port=port, exposure="public", source=source))
        wl.internal_only = False

    @staticmethod
    def _group(node: Node) -> str:
        wl = node.metadata.workload
        fmt = (wl.source_format if wl else "") or ""
        return fmt + ":" + posixpath.dirname(_paths(node)[0] if _paths(node) else "")

    def dependencies_of(self, node: Node) -> list[Node]:
        """Sibling workloads (same source file group) named in ``depends_on``."""
        wl = node.metadata.workload
        if wl is None:
            return []
        group = self._group(node)
        by_name = {
            _norm(w.metadata.workload.service_name or w.name): w  # type: ignore[union-attr]
            for w in self.workloads
            if self._group(w) == group
        }
        return [t for dep in wl.depends_on if (t := by_name.get(_norm(dep))) is not None and t is not node]

    def link_dependencies(self) -> None:
        for w in self.workloads:
            for target in self.dependencies_of(w):
                self.add_edge(w, target, RelationshipType.DEPENDS_ON, declared=True)

    def link_identity(self) -> None:
        iam = [
            n for n in self.doc.nodes
            if n.component_type == ComponentType.IAM and n.metadata.iam_type == "service_account"
        ]
        for w in self.workloads:
            wl = w.metadata.workload
            assert wl is not None
            if not wl.identity_ref or wl.identity_ref.endswith("assigned"):
                continue
            for node in iam:
                if node.metadata.principal == wl.identity_ref:
                    self.add_edge(node, w, RelationshipType.USES, declared=True)

    # -- 4. exposure --------------------------------------------------------
    def apply_exposure(self) -> None:
        hosts: dict[UUID, list[Node]] = {}
        for edge in self.doc.edges:
            if edge.relationship_type == RelationshipType.HOSTS:
                hosts.setdefault(edge.target, []).append(next(w for w in self.workloads if w.id == edge.source))
        for node in self.doc.nodes:
            owners = [w for w in hosts.get(node.id, []) if w.metadata.workload is not None]
            if not owners:
                continue
            best = "unknown"
            for w in owners:
                wl = w.metadata.workload
                assert wl is not None
                for p in wl.ports:
                    if _EXPOSURE_RANK[p.exposure] > _EXPOSURE_RANK[best]:
                        best = p.exposure
            node.metadata.hosted_by = ", ".join(
                dict.fromkeys(str(w.metadata.workload.service_name or w.name) for w in owners)  # type: ignore[union-attr]
            )
            if node.component_type not in _EXPOSURE_TYPES:
                continue
            node.metadata.network_exposure = best  # type: ignore[assignment]
            _extras(node)["exposure_by_host"] = {
                str(w.metadata.workload.service_name or w.name): max(  # type: ignore[union-attr]
                    (p.exposure for p in w.metadata.workload.ports),  # type: ignore[union-attr]
                    key=lambda e: _EXPOSURE_RANK[e],
                    default="unknown",
                )
                for w in owners
            }
            if node.component_type in _REACHABLE_TYPES and best == "public":
                for w in owners:
                    wl = w.metadata.workload
                    assert wl is not None
                    if any(p.exposure == "public" for p in wl.ports):
                        self.add_edge(w, node, RelationshipType.EXPOSES, declared=False, confidence=0.7)

    def drop_markers(self) -> None:
        drop = {n.id for n in self.markers}
        if not drop:
            return
        self.doc.nodes = [n for n in self.doc.nodes if n.id not in drop]
        self.doc.edges = [e for e in self.doc.edges if e.source not in drop and e.target not in drop]


def apply_deployment_topology(doc: AiSbomDocument) -> None:
    """Merge deployment facts and link workloads, images, code and gateways (idempotent)."""
    topo = _Topology(doc)
    topo.collect()
    if not topo.workloads and not topo.markers:
        return
    topo.merge_facts()
    topo.resolve_source_dirs()
    topo.link_images()
    topo.link_code()
    topo.link_routing()
    topo.link_dependencies()
    topo.link_identity()
    topo.apply_exposure()
    topo.drop_markers()
    _log.debug(
        "deployment topology: %d workloads, %d edges", len(topo.workloads), len(doc.edges)
    )
