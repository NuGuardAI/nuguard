"""Catalog YAML loader and exporter.

:func:`load_catalog_yaml` reads a human-edited YAML catalog file and returns a
validated ``tuple[ScenarioSpec, ...]`` suitable for use in place of the built-in
``SCENARIO_CATALOG``.

:func:`export_catalog_yaml` serializes a ``ScenarioSpec`` tuple to YAML, producing
a file that can be edited and fed back through ``load_catalog_yaml``.
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from nuguard.models.exploit_chain import GoalType, ScenarioType
from nuguard.redteam.catalog.spec import ScenarioSpec
from nuguard.redteam.catalog.taxonomy import (
    Capability,
    DeliveryChannel,
    EvidenceType,
    SafeExecution,
    ScenarioCategory,
    SinkType,
    SourceTrust,
)

_log = logging.getLogger(__name__)

__all__ = ["load_catalog_yaml", "export_catalog_yaml"]


class _ScenarioSpecEntry(BaseModel):
    """Pydantic model for one catalog YAML entry.

    All str-enum fields are validated automatically — an unknown value produces
    a clear error message naming the field and listing valid options.
    """

    id: str
    category: ScenarioCategory
    title: str
    goal_type: GoalType
    scenario_type: ScenarioType
    delivery_channel: DeliveryChannel
    source_trust: SourceTrust
    sink_type: SinkType
    required_capabilities: list[Capability]
    evidence_types: list[EvidenceType]
    safe_execution: SafeExecution
    expected_control: str
    success_signal: str
    owasp_llm: list[str] = Field(default_factory=list)
    owasp_agentic: list[str] = Field(default_factory=list)
    base_impact: float = 5.0
    builder_key: str = ""
    enabled: bool = True
    priority_rules: list[int] = Field(default_factory=list)

    def to_spec(self) -> ScenarioSpec:
        return ScenarioSpec(
            id=self.id,
            category=self.category,
            title=self.title,
            goal_type=self.goal_type,
            scenario_type=self.scenario_type,
            delivery_channel=self.delivery_channel,
            source_trust=self.source_trust,
            sink_type=self.sink_type,
            required_capabilities=frozenset(self.required_capabilities),
            evidence_types=tuple(self.evidence_types),
            safe_execution=self.safe_execution,
            expected_control=self.expected_control,
            success_signal=self.success_signal,
            owasp_llm=tuple(self.owasp_llm),
            owasp_agentic=tuple(self.owasp_agentic),
            base_impact=self.base_impact,
            builder_key=self.builder_key,
            enabled=self.enabled,
            priority_rules=tuple(self.priority_rules),
        )


def load_catalog_yaml(path: Path) -> tuple[ScenarioSpec, ...]:
    """Load and validate a catalog YAML file.

    Parameters
    ----------
    path:
        Path to the catalog YAML file (produced by ``nuguard redteam catalog-export``
        or hand-edited).

    Returns
    -------
    tuple[ScenarioSpec, ...]
        Validated catalog ready to substitute for ``SCENARIO_CATALOG``.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    yaml.YAMLError
        On malformed YAML syntax.
    ValueError
        On duplicate IDs or invalid field values, with a human-readable message
        listing every offending entry.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict) or "scenarios" not in raw:
        raise ValueError(
            f"Catalog YAML must have a top-level 'scenarios' key: {path}"
        )

    entries = raw["scenarios"]
    if not isinstance(entries, list):
        raise ValueError(f"'scenarios' must be a list in {path}")

    errors: list[str] = []
    specs: list[ScenarioSpec] = []
    seen_ids: dict[str, int] = {}

    for idx, entry in enumerate(entries):
        entry_id = (
            entry.get("id", f"<entry {idx}>")
            if isinstance(entry, dict)
            else f"<entry {idx}>"
        )
        try:
            validated = _ScenarioSpecEntry.model_validate(entry)
        except ValidationError as exc:
            for e in exc.errors():
                field = ".".join(str(loc) for loc in e["loc"])
                errors.append(f"  Entry {entry_id!r}, field '{field}': {e['msg']}")
            continue

        if validated.id in seen_ids:
            errors.append(
                f"  Duplicate ID {validated.id!r} at entries "
                f"{seen_ids[validated.id]} and {idx}"
            )
            continue
        seen_ids[validated.id] = idx

        spec = validated.to_spec()

        # Warn on unknown builder_key (not an error — future builders may not be wired)
        if spec.enabled:
            from nuguard.redteam.catalog.builders import BUILDER_FACTORIES

            bk = spec.resolved_builder_key()
            if bk not in BUILDER_FACTORIES:
                warnings.warn(
                    f"Catalog entry {spec.id!r}: builder_key {bk!r} is not in "
                    f"BUILDER_FACTORIES — this scenario will be skipped at selection "
                    f"time. This is expected for builders added in future versions.",
                    UserWarning,
                    stacklevel=2,
                )

        specs.append(spec)

    if errors:
        raise ValueError(
            f"Catalog YAML validation failed ({len(errors)} error(s)) in {path}:\n"
            + "\n".join(errors)
        )

    _log.info("Loaded %d scenario specs from %s", len(specs), path)
    return tuple(specs)


def export_catalog_yaml(
    catalog: tuple[ScenarioSpec, ...],
    path: Path | None = None,
) -> str:
    """Serialize a catalog tuple to YAML.

    Parameters
    ----------
    catalog:
        Tuple of :class:`ScenarioSpec` objects to serialize (typically
        ``SCENARIO_CATALOG``).
    path:
        When provided, write the YAML to this path in addition to returning
        the string.

    Returns
    -------
    str
        The YAML string.
    """
    header = (
        "# NuGuard Redteam Scenario Catalog\n"
        "# Generated by: nuguard redteam catalog-export\n"
        "#\n"
        "# Edit to customize scenarios:\n"
        "#   - Set enabled: false to skip a scenario\n"
        "#   - Adjust base_impact (0-10) to change selection priority\n"
        "#   - Modify expected_control / success_signal descriptions\n"
        "#   - Add new entries with a unique id and matching builder_key\n"
        "#\n"
        "# Pass back to NuGuard with: nuguard redteam --catalog <this-file>\n\n"
    )

    data: dict[str, Any] = {
        "scenarios": [_spec_to_dict(spec) for spec in catalog],
    }
    yaml_body = yaml.dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )
    result = header + yaml_body

    if path is not None:
        path.write_text(result, encoding="utf-8")

    return result


def _spec_to_dict(spec: ScenarioSpec) -> dict[str, Any]:
    """Convert a ScenarioSpec to a plain dict suitable for YAML serialization."""
    d: dict[str, Any] = {
        "id": spec.id,
        "category": spec.category.value,
        "title": spec.title,
        "goal_type": spec.goal_type.value,
        "scenario_type": spec.scenario_type.value,
        "delivery_channel": spec.delivery_channel.value,
        "source_trust": spec.source_trust.value,
        "sink_type": spec.sink_type.value,
        "required_capabilities": sorted(c.value for c in spec.required_capabilities),
        "evidence_types": [e.value for e in spec.evidence_types],
        "safe_execution": spec.safe_execution.value,
        "expected_control": spec.expected_control,
        "success_signal": spec.success_signal,
    }
    # Only include non-empty optional fields to keep the file compact
    if spec.owasp_llm:
        d["owasp_llm"] = list(spec.owasp_llm)
    if spec.owasp_agentic:
        d["owasp_agentic"] = list(spec.owasp_agentic)
    d["base_impact"] = spec.base_impact
    if spec.builder_key:
        d["builder_key"] = spec.builder_key
    d["enabled"] = spec.enabled
    if spec.priority_rules:
        d["priority_rules"] = list(spec.priority_rules)
    return d
