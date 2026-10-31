"""Bundled workflow templates and applicability checks.

Templates live in ``workflows/library/<name>.yaml``. ``redteam.workflow_templates``
names the ones to use, or ``auto`` for all of them; each template workflow then
runs only if its ``requires`` hold on the target, so enabling ``auto`` against an
unrelated app is a no-op.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

import yaml

from nuguard.common.logging import get_logger
from nuguard.redteam.workflows.models import WorkflowSpec

_log = get_logger(__name__)
_LIBRARY_DIR = Path(__file__).parent / "library"
_PARAM_RE = re.compile(r"\{[^/}]+\}")


class _Prober(Protocol):
    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]: ...


def available_templates() -> list[str]:
    """Names of the bundled templates."""
    return sorted(p.stem for p in _LIBRARY_DIR.glob("*.yaml"))


def load_template(name: str) -> list[WorkflowSpec]:
    """Load the workflows of bundled template *name* (raises ``KeyError`` if unknown)."""
    path = _LIBRARY_DIR / f"{name}.yaml"
    if not path.is_file():
        raise KeyError(f"unknown workflow template {name!r}; available: {available_templates()}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [WorkflowSpec.model_validate(w) for w in data.get("workflows", [])]


def resolve_templates(names: list[str]) -> list[WorkflowSpec]:
    """Expand template names (``auto`` = every bundled template) into specs."""
    selected = available_templates() if "auto" in names else names
    specs: list[WorkflowSpec] = []
    for name in selected:
        try:
            specs.extend(load_template(name))
        except KeyError as exc:
            _log.warning("%s", exc)
    return specs


def _path_pattern(path: str) -> re.Pattern[str]:
    parts = [re.escape(seg) if not _PARAM_RE.fullmatch(seg) else "[^/]+" for seg in path.strip("/").split("/")]
    return re.compile("^/" + "/".join(parts) + "/?$")


def sbom_has_path(declared_paths: list[str], wanted: str) -> bool:
    """True when *wanted* matches a declared endpoint path (``{param}`` segments are wildcards)."""
    pattern = _path_pattern(wanted)
    return any(pattern.match(_PARAM_RE.sub("x", p) if "{" in p else p) for p in declared_paths)


async def filter_applicable(
    specs: list[WorkflowSpec],
    *,
    sbom_paths: list[str],
    client: _Prober,
    tenant_count: int,
) -> list[WorkflowSpec]:
    """Keep the specs whose ``requires`` hold; probe each distinct path at most once."""
    probe_cache: dict[str, bool] = {}

    async def _alive(path: str) -> bool:
        if path not in probe_cache:
            try:
                status, _text, _data = await client.invoke_endpoint(path, method="GET")
                probe_cache[path] = status != 404 and status < 500
            except Exception as exc:  # noqa: BLE001 — an unreachable probe means not applicable
                _log.debug("workflow probe %s failed: %s", path, exc)
                probe_cache[path] = False
        return probe_cache[path]

    kept: list[WorkflowSpec] = []
    for spec in specs:
        req = spec.requires
        reason = ""
        if tenant_count < req.min_tenants:
            reason = f"needs {req.min_tenants} canary tenant(s), have {tenant_count}"
        elif not all(sbom_has_path(sbom_paths, p) for p in req.sbom_paths):
            reason = "required SBOM endpoint(s) missing"
        else:
            for path in req.probe_paths:
                if not await _alive(path):
                    reason = f"probe {path} not reachable"
                    break
        if reason:
            _log.info("Workflow %s not applicable: %s", spec.id, reason)
        else:
            kept.append(spec)
    return kept


def merge_specs(explicit: list[dict[str, Any]], templated: list[WorkflowSpec]) -> list[WorkflowSpec]:
    """Combine user-written workflows with template ones (user entries win on id)."""
    user = [WorkflowSpec.model_validate(w) for w in explicit]
    user_ids = {s.id for s in user}
    return user + [s for s in templated if s.id not in user_ids]
