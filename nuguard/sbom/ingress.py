"""Ingress facts for DEPLOYMENT nodes (pentest-proposal A4).

IaC adapters record *how a service is reachable from outside* — a reverse
proxy/gateway, the backend exposing itself directly, or a static frontend. When
both a ``gateway`` and a ``direct`` ingress exist for one app, a pentest must
scan both and treat them as one logical surface (the origin-bypass blind spot).

Only statically declared facts are stored; never credentials and never a
runtime observation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import IngressDetail
from .types import ComponentType

if TYPE_CHECKING:
    from .models import AiSbomDocument


def ingress_entry(role: str, evidence: str, url: str | None = None) -> dict[str, Any]:
    """One ``ingresses[]`` item in the plain-dict form adapters put in ``metadata``."""
    return {"role": role, "url": url, "evidence": evidence}


def collect_ingresses(doc: AiSbomDocument) -> list[IngressDetail]:
    """Every distinct ingress recorded across the document's DEPLOYMENT nodes."""
    seen: set[tuple[str, str | None, str]] = set()
    result: list[IngressDetail] = []
    for node in doc.nodes:
        if node.component_type != ComponentType.DEPLOYMENT:
            continue
        for item in node.metadata.ingresses or []:
            key = (item.role, item.url, item.evidence)
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def has_split_ingress(doc: AiSbomDocument) -> bool:
    """True when a gateway *and* a directly exposed backend are both recorded."""
    roles = {item.role for item in collect_ingresses(doc)}
    return {"gateway", "direct"} <= roles
