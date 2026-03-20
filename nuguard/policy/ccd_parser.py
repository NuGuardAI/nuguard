"""Compliance Control Descriptor (CCD) parser.

Ported from assessment_service/core/ccd_parser.py.  All SQLAlchemy references
have been removed.  The CCD DSL is self-contained and independent of the DB.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from nuguard.common.logging import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Assertion models
# ---------------------------------------------------------------------------


class AssertionType(str, Enum):
    """Types of assertions for compliance evaluation."""

    MUST_EXIST = "must_exist"
    MUST_NOT_EXIST = "must_not_exist"
    MUST_EXIST_PER_INSTANCE = "must_exist_per_instance"
    MUST_EXIST_ON_PATH = "must_exist_on_path"
    COUNT_THRESHOLD = "count_threshold"
    PROPERTY_CONSTRAINT = "property_constraint"
    # Simplified aliases used in JSON control definitions
    EXISTS = "exists"
    COUNT = "count"
    ATTRIBUTE = "attribute"
    PATH = "path"
    ABSENCE = "absence"


class AssertionSeverity(str, Enum):
    """Severity of assertion failure."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class GraphQueryFilter(BaseModel):
    """Filter conditions for graph queries."""

    type: str | None = Field(default=None, description="Node/edge type to filter")
    properties: dict[str, Any] | None = Field(
        default=None, description="Property filters"
    )

    model_config = {"extra": "allow"}


class PathQuery(BaseModel):
    """Path query specification for path-based assertions."""

    from_: dict[str, Any] = Field(..., alias="from", description="Source node filter")
    to: dict[str, Any] = Field(..., description="Target node filter")
    via_any: list[str] | None = Field(None, description="Edge types to traverse")
    via_all: list[str] | None = Field(None, description="Required edge types")
    max_depth: int = Field(10, description="Maximum path depth")

    model_config = {"populate_by_name": True}


class Assertion(BaseModel):
    """Assertion definition for control evaluation."""

    id: str | None = Field(default=None, description="Unique assertion identifier")
    type: AssertionType = Field(..., description="Assertion type")
    severity: AssertionSeverity = Field(AssertionSeverity.MEDIUM)
    description: str = Field(default="", description="Human-readable description")
    weight: float = Field(1.0, ge=0.0, description="Weight for scoring")

    # For MUST_EXIST / MUST_NOT_EXIST / EXISTS / ABSENCE
    query: dict[str, Any] | None = Field(None, description="Graph query")
    # Shorthand for simple EXISTS checks
    node_type: str | None = Field(None, description="Node type for exists/absence checks")
    filter: dict[str, Any] | None = Field(None, description="Optional filter dict")
    min_count: int | None = Field(None, description="Minimum count for must_exist")
    max_count: int | None = Field(None, description="Maximum count for must_not_exist")

    # For MUST_EXIST_PER_INSTANCE
    for_each: dict[str, Any] | None = Field(None, description="Query to find instances")
    require: dict[str, Any] | None = Field(None, description="Required properties")

    # For MUST_EXIST_ON_PATH / PATH
    path_query: dict[str, Any] | None = Field(None, description="Path specification")
    require_intermediate: list[str] | None = Field(None, description="Required intermediate nodes")

    # For COUNT_THRESHOLD / COUNT
    threshold_min: int | None = Field(None)
    threshold_max: int | None = Field(None)
    # Generic operator / value for COUNT
    operator: str | None = Field(None, description="Comparison operator: gte/lte/eq/neq")
    value: Any = Field(None, description="Comparison value")

    # For PROPERTY_CONSTRAINT / ATTRIBUTE
    node_filter: dict[str, Any] | None = Field(None)
    property_path: str | None = Field(None)
    attribute: str | None = Field(None, description="Attribute key for ATTRIBUTE assertions")
    expected_value: Any = Field(None)


# ---------------------------------------------------------------------------
# Query models
# ---------------------------------------------------------------------------


class QueryType(str, Enum):
    """Types of graph queries."""

    FIND_NODES = "find_nodes"
    FIND_EDGES = "find_edges"
    FIND_PATHS = "find_paths"
    REACHABILITY = "reachability"
    COUNT = "count"
    EXISTS = "exists"
    FORALL = "forall"


class GraphQuery(BaseModel):
    """Graph Query DSL specification."""

    id: str = Field(..., description="Unique query identifier")
    description: str | None = Field(None)
    type: QueryType = Field(QueryType.FIND_NODES)

    # For FIND_NODES / FIND_EDGES
    filter: dict[str, Any] | None = Field(None, description="Node/edge filter")

    # For FIND_PATHS / REACHABILITY
    from_node_type: str | None = Field(None, alias="from")
    to_node_type: str | None = Field(None, alias="to")
    via_edge_types: list[str] | None = Field(None)
    max_depth: int = Field(10)

    # For FORALL
    for_each_query: dict[str, Any] | None = Field(None)
    condition: dict[str, Any] | None = Field(None)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# CCD applicability condition
# ---------------------------------------------------------------------------


class AppliesIfCondition(BaseModel):
    """Conditions that determine if a CCD applies to an AIBOM."""

    aibom_has_nodes: list[str] | None = Field(
        None, description="Required node types"
    )
    aibom_has_edges: list[str] | None = Field(
        None, description="Required edge types"
    )
    properties: dict[str, Any] | None = Field(
        None, description="Required properties"
    )

    def matches(self, aibom_summary: dict[str, Any]) -> bool:
        """Return True when all conditions are satisfied by *aibom_summary*."""
        if self.aibom_has_nodes:
            aibom_nodes = set(aibom_summary.get("node_types", []))
            if not all(n in aibom_nodes for n in self.aibom_has_nodes):
                return False
        if self.aibom_has_edges:
            aibom_edges = set(aibom_summary.get("edge_types", []))
            if not all(e in aibom_edges for e in self.aibom_has_edges):
                return False
        return True


# ---------------------------------------------------------------------------
# Scoring config
# ---------------------------------------------------------------------------


class ScoringConfig(BaseModel):
    """Scoring configuration for the control."""

    method: str = Field("binary", description="binary or graded")
    pass_threshold: float = Field(0.80, ge=0.0, le=1.0)
    fail_threshold: float = Field(0.20, ge=0.0, le=1.0)
    weighted: bool = Field(True, description="Use assertion weights")


# ---------------------------------------------------------------------------
# ParsedCCD
# ---------------------------------------------------------------------------


class ParsedCCD(BaseModel):
    """Parsed Compliance Control Descriptor."""

    control_id: str = Field(default="unknown", description="Control identifier")
    check_id: str | None = Field(None, description="Specific check within control")

    applies_if: AppliesIfCondition | None = Field(None)
    queries: list[GraphQuery] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=lambda: ScoringConfig())  # type: ignore[call-arg]
    gap_diagnosis: dict[str, str] | None = Field(None)
    fix_guidance: list[dict[str, Any]] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_ccd(self) -> "ParsedCCD":
        if not self.queries and not self.assertions:
            raise ValueError("CCD must have at least one query or assertion")
        return self


# ---------------------------------------------------------------------------
# CCDParser
# ---------------------------------------------------------------------------


class CCDParser:
    """Parser for Compliance Control Descriptors (CCDs)."""

    def parse(self, ccd_json: dict[str, Any]) -> ParsedCCD:
        """Parse and validate a CCD definition dict.

        Args:
            ccd_json: Raw CCD JSON object.

        Returns:
            Validated ParsedCCD instance.

        Raises:
            ValueError: If the CCD is structurally invalid.
        """
        try:
            queries: list[GraphQuery] = []
            for q in ccd_json.get("queries", []):
                queries.append(GraphQuery(**q))

            assertions: list[Assertion] = []
            for a in ccd_json.get("assertions", []):
                assertions.append(Assertion(**a))

            applies_if: AppliesIfCondition | None = None
            if "applies_if" in ccd_json:
                applies_if = AppliesIfCondition(**ccd_json["applies_if"])

            fix_guidance: list[dict[str, Any]] = ccd_json.get("fix_guidance", [])

            scoring = ScoringConfig(**ccd_json["scoring"]) if "scoring" in ccd_json else ScoringConfig()  # type: ignore[call-arg]

            return ParsedCCD(
                control_id=ccd_json.get("control_id", "unknown"),
                check_id=ccd_json.get("check_id"),
                applies_if=applies_if,
                queries=queries,
                assertions=assertions,
                required_evidence=ccd_json.get("required_evidence", []),
                scoring=scoring,
                gap_diagnosis=ccd_json.get("gap_diagnosis"),
                fix_guidance=fix_guidance,
                verification_steps=ccd_json.get("verification_steps", []),
            )

        except Exception as exc:
            _log.error("Failed to parse CCD: %s", exc)
            raise ValueError(f"Invalid CCD format: {exc}") from exc

    def validate(self, ccd_json: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate a CCD dict without fully parsing it.

        Returns:
            Tuple of (is_valid, error_messages).
        """
        errors: list[str] = []

        if "queries" not in ccd_json and "assertions" not in ccd_json:
            errors.append("CCD must have at least one query or assertion")

        for i, q in enumerate(ccd_json.get("queries", [])):
            if "id" not in q:
                errors.append(f"Query {i}: missing 'id' field")

        for i, a in enumerate(ccd_json.get("assertions", [])):
            if "type" not in a:
                errors.append(f"Assertion {i}: missing 'type' field")
            elif a["type"] not in {t.value for t in AssertionType}:
                errors.append(f"Assertion {i}: invalid type '{a['type']}'")

        return len(errors) == 0, errors

    def extract_asset_types(self, parsed_ccd: ParsedCCD) -> list[str]:
        """Return unique AIBOM asset type names referenced in *parsed_ccd*."""
        asset_types: set[str] = set()

        if parsed_ccd.applies_if and parsed_ccd.applies_if.aibom_has_nodes:
            asset_types.update(parsed_ccd.applies_if.aibom_has_nodes)

        for query in parsed_ccd.queries:
            if query.from_node_type:
                asset_types.add(query.from_node_type)
            if query.to_node_type:
                asset_types.add(query.to_node_type)
            if query.filter and "type" in query.filter:
                asset_types.add(query.filter["type"])

        for assertion in parsed_ccd.assertions:
            if assertion.node_type:
                asset_types.add(assertion.node_type)
            if assertion.query and "filter" in assertion.query:
                f = assertion.query["filter"]
                if isinstance(f, dict) and "type" in f:
                    asset_types.add(f["type"])
            if assertion.path_query:
                from_ = assertion.path_query.get("from", {})
                to_ = assertion.path_query.get("to", {})
                if isinstance(from_, dict) and "type" in from_:
                    asset_types.add(from_["type"])
                if isinstance(to_, dict) and "type" in to_:
                    asset_types.add(to_["type"])
            if assertion.require_intermediate:
                asset_types.update(assertion.require_intermediate)

        return list(asset_types)
