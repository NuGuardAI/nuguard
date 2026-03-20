"""
Pydantic schemas for Risk Assessment benchmark ground truth data.

These schemas define the structure of risk_ground_truth.json files
used to evaluate AI risk assessment accuracy (Phase 2 evaluation).

The risk assessment evaluates:
- Compliance gap findings (severity, control mapping, evidence)
- Covered controls with audit-ready evidence
- Risk score accuracy
- Red team attack generation quality
"""
from datetime import date
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from enum import Enum


# ============================================================================
# Enums
# ============================================================================

class MatchFlexibility(str, Enum):
    """How strictly to match findings/controls against ground truth."""
    EXACT = "EXACT"                    # Exact title + control_id + file match
    EXACT_CONTROL = "EXACT_CONTROL"    # Same control_id, flexible description
    SEMANTIC = "SEMANTIC"              # Same severity + gap_type + policy, fuzzy title match
    TYPE_ONLY = "TYPE_ONLY"            # Same gap_type/severity category only


class Severity(str, Enum):
    """Severity levels for findings."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class GapType(str, Enum):
    """Types of compliance/security gaps."""
    COMPLIANCE = "COMPLIANCE"
    SECURITY = "SECURITY"
    AI_SAFETY = "AI_SAFETY"
    PRIVACY = "PRIVACY"
    DATA_PROTECTION = "DATA_PROTECTION"


class EvidenceType(str, Enum):
    """Types of evidence for covered controls."""
    CODE = "CODE"
    CONFIG = "CONFIG"
    DOCUMENTATION = "DOCUMENTATION"
    ARCHITECTURE = "ARCHITECTURE"


class RedTeamAttackType(str, Enum):
    """Types of red team attacks."""
    PROMPT_INJECTION = "PROMPT_INJECTION"
    JAILBREAK = "JAILBREAK"
    PII_LEAKAGE = "PII_LEAKAGE"
    HALLUCINATION = "HALLUCINATION"
    MODEL_EXTRACTION = "MODEL_EXTRACTION"
    DATA_POISONING = "DATA_POISONING"
    DENIAL_OF_SERVICE = "DENIAL_OF_SERVICE"


class RiskBand(str, Enum):
    """Risk score bands."""
    LOW = "LOW"           # 0-25
    MEDIUM = "MEDIUM"     # 26-50
    HIGH = "HIGH"         # 51-75
    CRITICAL = "CRITICAL" # 76-100


# ============================================================================
# Ground Truth Models
# ============================================================================

class GroundTruthFinding(BaseModel):
    """
    A single expected finding in ground truth.
    
    Represents a compliance gap or security issue that should be detected
    by the risk assessment phase.
    """
    title: str = Field(..., description="Expected finding title (can be fuzzy matched)")
    severity: Severity = Field(..., description="Expected severity level")
    gap_type: Optional[str] = Field(None, description="Type of gap: COMPLIANCE, SECURITY, AI_SAFETY, etc.")
    control_id: Optional[str] = Field(None, description="Expected control ID (e.g., OWASP-A03)")
    control_name: Optional[str] = Field(None, description="Control name for human readability")
    policy_name: str = Field(..., description="Policy this finding should map to")
    affected_file: Optional[str] = Field(None, description="Expected file path for the finding")
    line_number: Optional[int] = Field(None, ge=1, description="Expected line number")
    remediation_keywords: List[str] = Field(
        default_factory=list,
        description="Keywords expected in remediation guidance"
    )
    evidence_keywords: List[str] = Field(
        default_factory=list,
        description="Keywords expected in the finding evidence"
    )
    confidence_min: int = Field(
        default=50, ge=0, le=100,
        description="Minimum expected confidence score"
    )
    match_flexibility: MatchFlexibility = Field(
        default=MatchFlexibility.SEMANTIC,
        description="How strictly to match this finding"
    )

    @field_validator('affected_file')
    @classmethod
    def normalize_path(cls, v: Optional[str]) -> Optional[str]:
        """Normalize file paths to use forward slashes."""
        return v.replace('\\', '/') if v else None


class GroundTruthCoveredControl(BaseModel):
    """
    A control that should be detected as covered/satisfied.
    
    Represents a security or compliance control that has evidence
    of implementation in the codebase.
    """
    control_id: str = Field(..., description="Control ID (e.g., OWASP-A01)")
    control_name: str = Field(..., description="Control name")
    policy_name: str = Field(..., description="Policy this control belongs to")
    evidence_type: EvidenceType = Field(..., description="Type of expected evidence")
    evidence_keywords: List[str] = Field(
        default_factory=list,
        description="Keywords expected in the evidence"
    )
    evidence_file: Optional[str] = Field(None, description="Expected file containing evidence")
    confidence_min: int = Field(
        default=50, ge=0, le=100,
        description="Minimum expected confidence score"
    )
    match_flexibility: MatchFlexibility = Field(
        default=MatchFlexibility.EXACT_CONTROL,
        description="How strictly to match this control"
    )

    @field_validator('evidence_file')
    @classmethod
    def normalize_path(cls, v: Optional[str]) -> Optional[str]:
        """Normalize file paths to use forward slashes."""
        return v.replace('\\', '/') if v else None


class ExpectedRiskScore(BaseModel):
    """Expected risk score with tolerance for matching."""
    score: int = Field(..., ge=0, le=100, description="Expected risk score")
    band: RiskBand = Field(..., description="Expected risk band")
    tolerance: int = Field(
        default=15, ge=0, le=50,
        description="±tolerance for score matching"
    )


class ExpectedRiskSummary(BaseModel):
    """Expected distribution of findings by severity."""
    critical_count: int = Field(default=0, ge=0)
    high_count: int = Field(default=0, ge=0)
    medium_count: int = Field(default=0, ge=0)
    low_count: int = Field(default=0, ge=0)
    count_tolerance: int = Field(
        default=2, ge=0,
        description="±tolerance per severity bucket"
    )


class ExpectedRedTeamAttack(BaseModel):
    """An expected red team attack in ground truth."""
    type: str = Field(..., description="Attack type (PROMPT_INJECTION, JAILBREAK, etc.)")
    target_description: Optional[str] = Field(
        None,
        description="Description of the attack target for semantic matching"
    )
    match_flexibility: MatchFlexibility = Field(
        default=MatchFlexibility.TYPE_ONLY,
        description="How strictly to match this attack"
    )


class ExpectedRedTeamAttacks(BaseModel):
    """Expected red team attack configuration."""
    min_count: int = Field(default=1, ge=0, description="Minimum number of attacks expected")
    expected_types: List[str] = Field(
        default_factory=list,
        description="Attack types that should be generated"
    )
    attacks: List[ExpectedRedTeamAttack] = Field(
        default_factory=list,
        description="Specific attacks to match"
    )


class RiskGroundTruth(BaseModel):
    """
    Complete risk assessment ground truth for a repository.
    
    This is the main schema for risk_ground_truth.json files.
    """
    repo_name: str = Field(..., description="Short name for the benchmark repo")
    repo_url: str = Field(..., description="Full GitHub URL")
    branch: str = Field(default="main", description="Git branch to analyze")
    commit_sha: Optional[str] = Field(
        None,
        description="Specific commit SHA for reproducibility"
    )
    annotated_at: date = Field(..., description="Date of annotation")
    annotator: str = Field(default="human", description="Who created annotation")
    
    # Policies
    policies_evaluated: List[str] = Field(
        ...,
        description="Policy names to evaluate against (e.g., ['OWASP AI Top 10', 'HIPAA'])"
    )
    
    # Expected outputs
    expected_findings: List[GroundTruthFinding] = Field(
        default_factory=list,
        description="Expected compliance gaps and security findings"
    )
    expected_covered_controls: List[GroundTruthCoveredControl] = Field(
        default_factory=list,
        description="Expected controls that are satisfied"
    )
    expected_risk_score: ExpectedRiskScore = Field(
        ...,
        description="Expected overall risk score"
    )
    expected_risk_summary: Optional[ExpectedRiskSummary] = Field(
        None,
        description="Expected severity distribution"
    )
    expected_red_team_attacks: Optional[ExpectedRedTeamAttacks] = Field(
        None,
        description="Expected red team attack generation"
    )
    
    # Metadata
    notes: Optional[str] = Field(
        None,
        description="Additional notes about this benchmark"
    )

    def validate_internal_consistency(self) -> List[str]:
        """
        Validate internal consistency of ground truth.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Check that expected_risk_summary counts match finding counts
        if self.expected_risk_summary:
            actual_critical = sum(1 for f in self.expected_findings if f.severity == Severity.CRITICAL)
            actual_high = sum(1 for f in self.expected_findings if f.severity == Severity.HIGH)
            actual_medium = sum(1 for f in self.expected_findings if f.severity == Severity.MEDIUM)
            actual_low = sum(1 for f in self.expected_findings if f.severity == Severity.LOW)
            
            summary = self.expected_risk_summary
            if abs(actual_critical - summary.critical_count) > summary.count_tolerance:
                errors.append(
                    f"CRITICAL count mismatch: {actual_critical} findings vs {summary.critical_count} expected"
                )
            if abs(actual_high - summary.high_count) > summary.count_tolerance:
                errors.append(
                    f"HIGH count mismatch: {actual_high} findings vs {summary.high_count} expected"
                )
            if abs(actual_medium - summary.medium_count) > summary.count_tolerance:
                errors.append(
                    f"MEDIUM count mismatch: {actual_medium} findings vs {summary.medium_count} expected"
                )
            if abs(actual_low - summary.low_count) > summary.count_tolerance:
                errors.append(
                    f"LOW count mismatch: {actual_low} findings vs {summary.low_count} expected"
                )
        
        # Check for duplicate control IDs in findings vs covered controls (mutual exclusivity)
        finding_controls = {
            (f.control_id, f.policy_name) 
            for f in self.expected_findings 
            if f.control_id
        }
        covered_controls = {
            (c.control_id, c.policy_name) 
            for c in self.expected_covered_controls
        }
        
        overlaps = finding_controls & covered_controls
        if overlaps:
            errors.append(
                f"Controls appear in both findings and covered_controls (should be mutually exclusive): {overlaps}"
            )
        
        return errors


# ============================================================================
# Evaluation Result Models
# ============================================================================

class FindingMatchResult(BaseModel):
    """Result of matching a single finding against ground truth."""
    ground_truth_title: str
    ground_truth_control_id: Optional[str] = None
    ground_truth_severity: str
    ground_truth_policy: str
    discovered_title: Optional[str] = None
    discovered_control_id: Optional[str] = None
    discovered_severity: Optional[str] = None
    matched: bool = False
    match_level: MatchFlexibility = MatchFlexibility.TYPE_ONLY
    confidence: int = 0


class CoveredControlMatchResult(BaseModel):
    """Result of matching a single covered control."""
    ground_truth_control_id: str
    ground_truth_policy: str
    discovered_control_id: Optional[str] = None
    matched: bool = False
    evidence_quality: Optional[str] = None  # STRONG, WEAK, NONE


class RiskTypeMetrics(BaseModel):
    """Metrics for a single category (findings, controls, etc.)."""
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0


class RiskEvaluationResult(BaseModel):
    """Complete risk evaluation result for a single repository."""
    repo_name: str
    policies_evaluated: List[str]
    
    # Finding metrics
    finding_metrics: RiskTypeMetrics = Field(default_factory=RiskTypeMetrics)
    finding_matches: List[FindingMatchResult] = Field(default_factory=list)
    finding_false_positive_details: List[Dict] = Field(default_factory=list)
    
    # Covered control metrics
    covered_metrics: RiskTypeMetrics = Field(default_factory=RiskTypeMetrics)
    covered_matches: List[CoveredControlMatchResult] = Field(default_factory=list)
    
    # Risk score metrics
    expected_risk_score: int = 0
    actual_risk_score: int = 0
    risk_score_error: int = 0  # absolute error
    risk_score_within_tolerance: bool = False
    expected_band: str = ""
    actual_band: str = ""
    band_match: bool = False
    
    # Severity distribution accuracy
    severity_distribution_accuracy: float = 0.0
    
    # Red team metrics
    red_team_type_coverage: float = 0.0  # % of expected types found
    red_team_count_sufficient: bool = False
    
    # Quality violations
    mutual_exclusivity_violations: int = 0
    
    # Composite score
    quality_score: float = 0.0
    
    # Timing
    discovery_time_seconds: float = 0.0
    risk_assessment_time_seconds: float = 0.0
    total_time_seconds: float = 0.0
    
    # Error
    error: Optional[str] = None

    def to_summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"\n── {self.repo_name} " + "─" * max(0, 60 - len(self.repo_name)),
            f"  Policies: {', '.join(self.policies_evaluated)}",
            f"  Findings:        P={self.finding_metrics.precision:.2f}  R={self.finding_metrics.recall:.2f}  F1={self.finding_metrics.f1_score:.2f}  "
            f"({self.finding_metrics.true_positives} TP, {self.finding_metrics.false_positives} FP, {self.finding_metrics.false_negatives} FN)",
            f"  Covered Controls: P={self.covered_metrics.precision:.2f}  R={self.covered_metrics.recall:.2f}  F1={self.covered_metrics.f1_score:.2f}  "
            f"({self.covered_metrics.true_positives} TP, {self.covered_metrics.false_positives} FP, {self.covered_metrics.false_negatives} FN)",
            f"  Risk Score:       Expected={self.expected_risk_score}  Actual={self.actual_risk_score}  Error={self.risk_score_error}  "
            f"{'✓' if self.risk_score_within_tolerance else '✗'} {'within' if self.risk_score_within_tolerance else 'outside'} tolerance",
            f"  Risk Band:        Expected={self.expected_band}  Actual={self.actual_band}  "
            f"{'✓' if self.band_match else '✗'} {'match' if self.band_match else 'mismatch'}",
            f"  Severity Dist:    Accuracy={self.severity_distribution_accuracy:.0%}",
            f"  Red Team:         {self.red_team_type_coverage:.0%} types covered  "
            f"{'✓' if self.red_team_count_sufficient else '✗'}",
            f"  MX Violations:    {self.mutual_exclusivity_violations}  "
            f"{'✓' if self.mutual_exclusivity_violations == 0 else '✗'}",
            f"  Quality Score:    {self.quality_score:.2f}",
            f"  Time:             {self.total_time_seconds:.1f}s (discovery: {self.discovery_time_seconds:.1f}s, risk: {self.risk_assessment_time_seconds:.1f}s)",
        ]
        
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        
        return "\n".join(lines)


class RiskBenchmarkSuiteResult(BaseModel):
    """Aggregated results across all benchmark repositories."""
    total_repos: int
    successful_repos: int
    failed_repos: int
    
    # Aggregate metrics
    aggregate_finding_f1: float = 0.0
    aggregate_covered_f1: float = 0.0
    aggregate_risk_score_mae: float = 0.0  # Mean Absolute Error
    aggregate_band_accuracy: float = 0.0
    aggregate_quality_score: float = 0.0
    
    # Per-repo results
    results: List[RiskEvaluationResult] = Field(default_factory=list)
    
    # Timing
    total_time_seconds: float = 0.0
    evaluated_at: str = ""  # ISO timestamp

    def to_summary(self) -> str:
        """Generate aggregate summary."""
        lines = [
            "═" * 70,
            "AGGREGATE RISK ASSESSMENT BENCHMARK RESULTS:",
            f"  Repos Evaluated:   {self.successful_repos}/{self.total_repos}",
            f"  Finding F1:        {self.aggregate_finding_f1:.2f}",
            f"  Covered Control F1: {self.aggregate_covered_f1:.2f}",
            f"  Risk Score MAE:    {self.aggregate_risk_score_mae:.1f}",
            f"  Band Accuracy:     {self.aggregate_band_accuracy:.0%}",
            f"  Quality Score:     {self.aggregate_quality_score:.2f}",
            f"  Total Time:        {self.total_time_seconds:.1f}s",
            "═" * 70,
        ]
        return "\n".join(lines)
