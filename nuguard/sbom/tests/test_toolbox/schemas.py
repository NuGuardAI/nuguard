"""
Pydantic schemas for benchmark ground truth data.

These schemas define the structure of ground_truth.json files
used to evaluate AI asset discovery accuracy.
"""

from datetime import date
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from enum import Enum


class AssetType(str, Enum):
    """Valid AI asset types for ground truth annotation."""

    AGENT = "AGENT"
    MODEL = "MODEL"
    TOOL = "TOOL"
    PROMPT = "PROMPT"
    DATASTORE = "DATASTORE"
    GUARDRAIL = "GUARDRAIL"
    AUTH = "AUTH"
    PRIVILEGE = "PRIVILEGE"
    EVAL_SYSTEM = "EVAL_SYSTEM"
    MCP_PROVIDER = "MCP_PROVIDER"


class GroundTruthAsset(BaseModel):
    """A single annotated asset in ground truth."""

    asset_type: AssetType = Field(description="Type of AI asset")
    name: str = Field(description="Name/identifier of the asset")
    file_path: str = Field(description="Relative path from repo root")
    line_start: Optional[int] = Field(
        default=None, ge=1, description="Starting line number (1-indexed)"
    )
    line_end: Optional[int] = Field(default=None, ge=1, description="Ending line number")
    description: str = Field(description="Human-readable description")
    framework: Optional[str] = Field(
        default=None, description="AI framework (langchain, crewai, etc.)"
    )
    evidence: List[str] = Field(default_factory=list, description="Evidence patterns found")
    synonyms: List[str] = Field(
        default_factory=list,
        description="Alternate names the discovery pipeline might extract for this asset "
        "(e.g. variable names like 'llm' for a model named 'OpenAIChat_GPT4o')",
    )
    alt_file_paths: List[str] = Field(
        default_factory=list,
        description="Alternate file paths where this asset may be detected "
        "(e.g. secondary evidence entries from a multi-evidence ground truth node)",
    )
    relationships: Optional[Dict[str, str | List[str]]] = Field(
        default=None, description="Relationships to other assets (uses_model, uses_tools, etc.)"
    )

    @field_validator("file_path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        """Normalize file paths to use forward slashes."""
        return v.replace("\\", "/")


class ExpectedCounts(BaseModel):
    """Expected asset counts by type for quick validation."""

    AGENT: int = 0
    MODEL: int = 0
    TOOL: int = 0
    PROMPT: int = 0
    DATASTORE: int = 0
    GUARDRAIL: int = 0
    AUTH: int = 0
    PRIVILEGE: int = 0
    EVAL_SYSTEM: int = 0
    MCP_PROVIDER: int = 0

    def total(self) -> int:
        """Calculate total expected assets."""
        return (
            self.AGENT
            + self.MODEL
            + self.TOOL
            + self.PROMPT
            + self.DATASTORE
            + self.GUARDRAIL
            + self.AUTH
            + self.PRIVILEGE
            + self.EVAL_SYSTEM
            + self.MCP_PROVIDER
        )


class GroundTruth(BaseModel):
    """Complete ground truth annotation for a repository."""

    repo_name: str = Field(description="Short name for the benchmark repo")
    repo_url: str = Field(description="Full GitHub URL")
    branch: str = Field(default="main", description="Git branch to analyze")
    subfolder: Optional[str] = Field(
        default=None, description="Subfolder to analyze (for large repos like langchain)"
    )
    commit_sha: Optional[str] = Field(
        default=None, description="Specific commit SHA for reproducibility"
    )
    annotated_at: date = Field(description="Date of annotation")
    annotator: str = Field(default="human", description="Who created annotation")
    frameworks: List[str] = Field(description="Expected frameworks to detect")
    assets: List[GroundTruthAsset] = Field(description="List of annotated assets")
    expected_counts: ExpectedCounts = Field(
        default_factory=ExpectedCounts, description="Expected counts by asset type"
    )
    notes: Optional[str] = Field(default=None, description="Additional notes about this benchmark")
    skip: bool = Field(
        default=False, description="Whether to skip this benchmark in evaluation runs"
    )
    skip_reason: Optional[str] = Field(
        default=None, description="Reason why this benchmark is skipped"
    )

    def validate_counts(self) -> bool:
        """Check if asset list matches expected counts."""
        actual = {}
        for asset in self.assets:
            asset_type = asset.asset_type.value
            actual[asset_type] = actual.get(asset_type, 0) + 1

        expected_dict = self.expected_counts.model_dump()
        for asset_type, expected in expected_dict.items():
            if actual.get(asset_type, 0) != expected:
                return False
        return True


class DiscoveredAsset(BaseModel):
    """An asset discovered by the pipeline (for comparison)."""

    asset_type: str
    name: str
    file_path: str
    alt_file_paths: List[str] = Field(default_factory=list)  # Other evidence file paths
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    description: Optional[str] = None
    confidence: Optional[float] = None
    regex_confidence: Optional[float] = None
    llm_confidence: Optional[float] = None
    framework: Optional[str] = None
    evidence_sources: Optional[List[str]] = None
    matched_pattern: Optional[str] = None
    additional_evidence: Optional[List[Dict]] = None  # Evidence from other files

    @field_validator("file_path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        """Normalize file paths to use forward slashes."""
        return v.replace("\\", "/")


class TypeMetrics(BaseModel):
    """Metrics for a single asset type."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0


class ScanEvaluationResult(BaseModel):
    """Complete evaluation results for a benchmark."""

    repo_name: str
    precision: float
    recall: float
    f1_score: float
    true_positives: int
    false_positives: int
    false_negatives: int
    by_type: Dict[str, TypeMetrics] = Field(default_factory=dict)
    false_positive_details: List[Dict] = Field(default_factory=list)
    false_negative_details: List[Dict] = Field(default_factory=list)
    discovered_assets: List["DiscoveredAsset"] = Field(
        default_factory=list, description="All assets discovered by the pipeline"
    )
    processing_time_ms: Optional[int] = None
    skipped: bool = Field(default=False, description="Whether this benchmark was skipped")
    skip_reason: Optional[str] = Field(default=None, description="Reason for skipping")

    def to_summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Benchmark: {self.repo_name}",
            f"  Precision: {self.precision:.2%}",
            f"  Recall: {self.recall:.2%}",
            f"  F1 Score: {self.f1_score:.2%}",
            f"  TP: {self.true_positives}, FP: {self.false_positives}, FN: {self.false_negatives}",
        ]
        if self.by_type:
            lines.append("  By Type:")
            for asset_type, metrics in sorted(self.by_type.items()):
                lines.append(
                    f"    {asset_type}: P={metrics.precision:.2%} R={metrics.recall:.2%} F1={metrics.f1_score:.2%}"
                )
        return "\n".join(lines)


class BenchmarkSuiteResult(BaseModel):
    """Aggregated results across all benchmarks."""

    total_repos: int
    overall_precision: float
    overall_recall: float
    overall_f1: float
    total_true_positives: int
    total_false_positives: int
    total_false_negatives: int
    by_repo: Dict[str, ScanEvaluationResult] = Field(default_factory=dict)
    by_type_aggregate: Dict[str, TypeMetrics] = Field(default_factory=dict)
    evaluated_at: str  # ISO timestamp
    """Types of assertions for compliance evaluation."""
