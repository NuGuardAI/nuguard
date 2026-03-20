# NuGuard OSS — Policy Implementation Plan

**Date:** March 2026
**Scope:** `nuguard/policy/` and `nuguard/redteam/policy_engine/`
**Reference:** `NuGuard-app/backend/assessment_service/` (existing commercial implementation)

---

## 1. What "Policy" Means in NuGuard OSS

There are two distinct policy subsystems in nuguard-oss. They are separate but interrelated:

### 1.1 Cognitive Policy (`nuguard/policy/`)

A **Cognitive Policy** is a Markdown document the AI application operator writes to govern their AI's behavior. It declares:

- What topics the AI is allowed/forbidden to discuss
- What actions the AI must not take autonomously
- When human-in-the-loop (HITL) approval is required
- Data classification rules

NuGuard's job is to:
1. **Parse** the policy into a structured `CognitivePolicy` model
2. **Lint** it for ambiguity, missing sections, and structural gaps
3. **Cross-check** it statically against the SBOM graph (e.g., HITL triggers with no enforcement path)
4. **Evaluate** it at runtime during red-team: did the agent violate the policy?

CLI surface: `nuguard policy validate`, `nuguard policy check`

### 1.2 Compliance Policy (`nuguard/policy/compliance/`)

A **Compliance Policy** maps an AI application against an external security/regulatory framework (OWASP LLM Top 10, NIST AI RMF, EU AI Act). It is **framework-driven**, not operator-written.

NuGuard's job is to:
1. **Load controls** from a NuGuard-defined JSON structure (v1) or extract them from an arbitrary document via LLM (v1.5+)
2. **Assess** whether the AI application (via its SBOM) satisfies each control
3. **Map** findings to their framework references for reporting

**v1 control format:** In v1, compliance controls are provided as pre-authored JSON files in a NuGuard-defined schema. The bundled framework files (`nuguard/policy/compliance/data/owasp_llm_top10.json` etc.) ship with the package and are loaded at runtime — no LLM call required to obtain the controls. Users can also supply their own JSON file conforming to the same schema.

**v1.5+:** `policy_extractor.py` enables LLM-based extraction of controls from arbitrary compliance documents (PDFs, Markdown, Word docs). This is how `assessment_service` works in NuGuard-app, but is deferred from OSS v1.

The reference implementation for assessment logic is `assessment_service/core/` in NuGuard-app.

---

## 2. Current State

All policy code in nuguard-oss is stubs (raise `NotImplementedError` or are empty):

| File | Status |
|---|---|
| `nuguard/policy/parser.py` | Stub — signature only |
| `nuguard/policy/validator.py` | Stub — signature only |
| `nuguard/policy/checker.py` | Stub — signature only |
| `nuguard/policy/compliance/loader.py` | Stub |
| `nuguard/redteam/policy_engine/evaluator.py` | Stub — TODO comment |
| `nuguard/redteam/policy_engine/detectors/topic_boundary.py` | Stub |
| `nuguard/redteam/policy_engine/detectors/restricted_action.py` | Stub |
| `nuguard/redteam/policy_engine/detectors/hitl_bypass.py` | Stub |
| `nuguard/models/policy.py` | Minimal — 6-field model only |

---

## 3. Reference Implementation in NuGuard-App

The `assessment_service` in NuGuard-app is the commercial implementation of compliance assessment. These files should be ported and adapted:

| Source file | What it does | Maps to | When |
|---|---|---|---|
| `core/ccd_parser.py` | Compliance Control Descriptor DSL | New: `nuguard/policy/ccd_parser.py` | v1 |
| `core/aibom_snapshot_builder.py` | Normalize SBOM nodes for assessment | New: `nuguard/policy/aibom_snapshot.py` | v1 |
| `core/scoring.py` | Score/result mapping + validation | New: `nuguard/policy/scoring.py` | v1 |
| `api/schemas.py` → compliance enums | `ComplianceResult`, `AssessmentStatus`, etc. | Extend: `nuguard/models/policy.py` | v1 |
| `core/llm_synthesizer.py` | LLM evidence synthesis (COVERED/PARTIAL/GAP) | New: `nuguard/policy/synthesizer.py` | v1 (optional path) |
| `core/policy_extractor.py` | LLM-based control extraction from arbitrary docs | New: `nuguard/policy/extractor.py` | v1.5 |

Key design differences between NuGuard-app and nuguard-oss:

- **No database layer** in nuguard-oss for policy assessment — results flow to findings/SARIF directly (no SQLAlchemy models, no `ControlLibrary` table, no `Assessment` row)
- **No multi-tenant context** — no org_id filtering, no user auth
- **No API server** — all assessment runs from CLI, not HTTP endpoints
- **SQLite persistence** is optional (findings can be stored via `nuguard/db/local.py`) but not required for a single run

---

## 4. Data Model Design

### 4.1 Extended `nuguard/models/policy.py`

Replace the minimal 6-field model with the full model hierarchy:

```python
# Enums
class ComplianceResult(str, Enum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    UNABLE_TO_ASSESS = "unable_to_assess"
    NOT_APPLICABLE = "not_applicable"

class ControlType(str, Enum):
    AUTOMATED = "automated"          # Assessable from SBOM alone
    ATTESTATION_REQUIRED = "attestation_required"  # Requires human attestation
    NOT_APPLICABLE = "not_applicable"

class FrameworkRef(BaseModel):
    framework: str          # "OWASP_LLM_TOP10" | "NIST_AI_RMF" | "EU_AI_ACT"
    control_id: str         # e.g. "LLM01", "GV-1.1", "Article 13"
    url: str | None = None

class ComplianceControl(BaseModel):
    id: str                          # stable slug, e.g. "owasp-llm01-prompt-injection"
    name: str
    description: str
    framework: str
    framework_refs: list[FrameworkRef] = []
    control_type: ControlType = ControlType.AUTOMATED
    ai_sbom_assessable: bool = True
    manual_attestation_required: bool = False
    ai_sbom_basis: list[str] = []    # Which SBOM node types are evidence (e.g. ["GUARDRAIL", "PROMPT"])
    severity: str = "medium"         # critical | high | medium | low
    gap_diagnosis: str = ""
    fix_guidance: str = ""
    tags: list[str] = []

class ControlEvaluation(BaseModel):
    control: ComplianceControl
    result: ComplianceResult
    score: float                     # 0.0–1.0
    evidence: list[str] = []         # SBOM node refs that informed the result
    gaps: list[str] = []
    remediation: str = ""

class PolicyAssessmentResult(BaseModel):
    framework: str
    total_controls: int
    pass_count: int
    partial_count: int
    fail_count: int
    unable_count: int
    score: float                     # weighted aggregate
    evaluations: list[ControlEvaluation]

# Cognitive policy (extended)
class CognitivePolicy(BaseModel):
    allowed_topics: list[str] = []
    restricted_topics: list[str] = []
    restricted_actions: list[str] = []
    hitl_triggers: list[str] = []
    data_classification: list[str] = []
    rate_limits: dict[str, int] = {}
    raw_sections: dict[str, str] = {}  # section heading → raw text, for reference
```

### 4.2 Compliance Control Descriptor (CCD)

Port `ccd_parser.py` from assessment_service with minimal changes. The CCD is a JSON DSL attached to each control that defines how to auto-assess it from an SBOM:

```python
# nuguard/policy/ccd_parser.py
class AssertionType(str, Enum):
    EXISTS = "exists"            # Node of type X must exist
    COUNT = "count"              # Count of nodes meeting filter must be ≥ N
    ATTRIBUTE = "attribute"      # Node attribute must match value
    PATH = "path"                # Graph path must/must not exist
    ABSENCE = "absence"          # Node must NOT exist

class Assertion(BaseModel):
    type: AssertionType
    node_type: str               # SBOM node type to query
    filter: dict = {}            # attribute filters
    operator: str = "gte"        # gte | lte | eq | neq
    value: Any = None
    severity: str = "high"       # severity if assertion fails

class ParsedCCD(BaseModel):
    assertions: list[Assertion]
    applies_if: dict = {}        # conditional: only evaluate if SBOM contains X
    scoring: dict = {}           # custom scoring weights
```

---

## 5. Work Streams

### Work Stream 1: Cognitive Policy Parser

**Goal:** Parse a Cognitive Policy Markdown document into `CognitivePolicy`.

**File:** `nuguard/policy/parser.py`

**Approach:** Pure regex/Markdown parsing (no LLM required). The policy uses a structured Markdown format with standard section headings.

Implementation strategy:
1. Split document by `##` headings
2. Map known headings to `CognitivePolicy` fields using a heading → field mapping table
3. Within each section, extract bullet lists as string items
4. Store unrecognized sections in `raw_sections` for reference
5. Return populated `CognitivePolicy`

```python
_HEADING_MAP = {
    "allowed topics": "allowed_topics",
    "permitted topics": "allowed_topics",
    "restricted topics": "restricted_topics",
    "forbidden topics": "restricted_topics",
    "restricted actions": "restricted_actions",
    "prohibited actions": "restricted_actions",
    "human in the loop": "hitl_triggers",
    "hitl triggers": "hitl_triggers",
    "human approval required": "hitl_triggers",
    "data classification": "data_classification",
    "rate limits": "rate_limits",
}

def parse_policy(text: str) -> CognitivePolicy:
    # 1. Extract sections
    # 2. Map headings → fields via _HEADING_MAP
    # 3. Parse bullet lists within each section
    # 4. Return CognitivePolicy
```

**Acceptance criteria:**
- Parses the example `healthtech_policy.md` without error
- All 6 core fields populate correctly when headings match
- Unrecognized sections land in `raw_sections`
- Empty or malformed sections do not raise — they produce empty lists

---

### Work Stream 2: Cognitive Policy Linter

**Goal:** Validate a parsed `CognitivePolicy` for completeness and internal consistency.

**File:** `nuguard/policy/validator.py`

Rules to implement:

| Rule ID | Description | Severity |
|---|---|---|
| `POLICY-001` | Policy has no `allowed_topics` and no `restricted_topics` | warning |
| `POLICY-002` | Policy has HITL triggers but no `restricted_actions` | warning |
| `POLICY-003` | A HITL trigger text is vague (< 10 chars) | warning |
| `POLICY-004` | Rate limit value is zero or negative | error |
| `POLICY-005` | Policy is completely empty (all sections empty) | error |
| `POLICY-006` | Duplicate entries within a section | warning |

```python
@dataclass
class LintIssue:
    rule_id: str
    message: str
    severity: str  # "error" | "warning"

def lint_policy(policy: CognitivePolicy) -> list[LintIssue]:
    # Run all rules, return list of LintIssues
```

**Acceptance criteria:**
- Returns empty list for a well-formed policy
- Returns `POLICY-005` error for a completely empty policy
- Does not raise on any input

---

### Work Stream 3: Policy ↔ SBOM Static Checker

**Goal:** Cross-check a `CognitivePolicy` against an `AiSbomDocument` to find structural gaps.

**File:** `nuguard/policy/checker.py`

Checks to implement:

| Check ID | Description | How to detect |
|---|---|---|
| `CHECK-001` | HITL trigger declared but no GUARDRAIL node in SBOM with matching scope | Policy has `hitl_triggers`; SBOM has no `GUARDRAIL` node whose `metadata.scope` covers the trigger action |
| `CHECK-002` | Restricted action names a tool that is not in SBOM | Action text matches no `TOOL` node name (fuzzy match) |
| `CHECK-003` | Policy restricts data classification but SBOM has `DATASTORE` nodes with no PII classification | Node `pii_detected=False` when policy has `data_classification` entries |
| `CHECK-004` | Policy has rate limits but SBOM has `API_ENDPOINT` nodes with no rate-limit attribute | `rate_limit=None` on endpoint nodes |
| `CHECK-005` | HITL trigger requires human approval but no `AUTH` or `PRIVILEGE` node exists for the actor | No `AUTH` node that could gate the trigger |

```python
@dataclass
class PolicyGap:
    check_id: str
    message: str
    policy_section: str          # which part of the policy triggered this
    sbom_component: str | None   # which SBOM node (if applicable)
    severity: str

def check_policy_against_sbom(
    policy: CognitivePolicy,
    doc: AiSbomDocument,
) -> list[PolicyGap]:
```

The function should never raise — missing SBOM components produce a gap, not an exception.

**Acceptance criteria:**
- An SBOM with no GUARDRAIL nodes and a policy with HITL triggers → `CHECK-001` gap
- An SBOM with all required nodes and a matching policy → empty list
- Gaps produce enough context to generate SARIF findings downstream

---

### Work Stream 4: Runtime Policy Evaluator (Redteam)

**Goal:** During red-team execution, evaluate whether an agent response violates the cognitive policy.

**Files:**
- `nuguard/redteam/policy_engine/evaluator.py`
- `nuguard/redteam/policy_engine/detectors/topic_boundary.py`
- `nuguard/redteam/policy_engine/detectors/restricted_action.py`
- `nuguard/redteam/policy_engine/detectors/hitl_bypass.py`

#### 4.1 PolicyEvaluator

```python
@dataclass
class PolicyViolation:
    type: str                   # "topic_boundary_breach" | "restricted_action_executed" | "HITL_bypassed"
    severity: str               # "critical" | "high" | "medium" | "low"
    evidence: str               # excerpt from agent response
    policy_clause: str          # which policy rule was violated
    confidence: float           # 0.0–1.0

class PolicyEvaluator:
    def __init__(self, policy: CognitivePolicy) -> None: ...

    def evaluate(
        self,
        prompt: str,            # the adversarial prompt sent
        response: str,          # the agent's response
        tool_calls: list[dict] = [],  # tool invocations from the response trace
    ) -> list[PolicyViolation]:
        # Run all detectors, collect violations
```

#### 4.2 TopicBoundaryDetector

Port the two-tier detection from `assessment_service/core/policy_engine/detectors/topic_boundary.py`:

- **Tier 1 (restricted):** Response content matches a `restricted_topics` keyword → severity HIGH
- **Tier 2 (out-of-scope):** Response content is completely outside `allowed_topics` (no keyword overlap) → severity MEDIUM
- Use keyword matching (no LLM required for v1). LLM-based semantic matching deferred to v1.5.

```python
def detect(
    policy: CognitivePolicy,
    prompt: str,
    response: str,
) -> list[PolicyViolation]:
```

#### 4.3 RestrictedActionDetector

Check tool_calls list against `policy.restricted_actions`:

- If any tool call name or parameter pattern matches a restricted action → violation
- Also check response text for phrases matching restricted actions (e.g. "I will delete", "sending email to")
- Severity: CRITICAL if tool call, HIGH if text claim only

#### 4.4 HITLBypassDetector

Check whether a HITL-triggering scenario was executed without evidence of human approval:

- If prompt triggers a `hitl_triggers` clause (keyword match) and tool_calls contains the action without a preceding "approved"/"confirmed" signal → violation
- Severity: CRITICAL

**Acceptance criteria:**
- `PolicyEvaluator.evaluate()` is synchronous and takes < 50ms for v1 (no LLM calls)
- All three detectors return empty list when no violation
- Violations include enough context for SARIF finding generation

---

### Work Stream 5: Compliance Assessment (SBOM ↔ Framework)

**Goal:** Assess an `AiSbomDocument` against a compliance framework and produce `PolicyAssessmentResult`.

This is the most substantial work stream and directly ports the `assessment_service` core.

#### 5.1 AIBOM Snapshot Builder

**File:** `nuguard/policy/aibom_snapshot.py`

Port `assessment_service/core/aibom_snapshot_builder.py` with these adaptations:
- Remove SQLAlchemy dependencies — take `AiSbomDocument` directly
- Remove `scan` metadata block (no DB scan row in OSS)
- Keep: node partitioning by type, detail extractors, `signals` pre-computation, `counts` aggregate
- Keep: `MAX_NODES_PER_BUCKET = 50`, sort by confidence

```python
def build_aibom_snapshot(doc: AiSbomDocument) -> dict:
    """Normalize AiSbomDocument into assessment-ready snapshot dict.

    Returns dict with keys: counts, signals, nodes
    where nodes is partitioned by node type.
    """
```

Signals to compute from SBOM:
- `has_guardrail` — any GUARDRAIL node present
- `has_auth` — any AUTH node present
- `has_hitl_enforcement` — GUARDRAIL or AUTH node with `hitl` in scope/name
- `tools_without_auth_count` — TOOL nodes with no incoming PROTECTS edge
- `datastores_with_pii_count` — DATASTORE nodes where `pii_detected=True`
- `prompts_with_injection_risk` — PROMPT nodes with `injection_risk_score > 0.7`
- `external_tools_count` — TOOL nodes with `external=True`

#### 5.2 Control Evaluator (Graph Query Engine)

**File:** `nuguard/policy/evaluator.py`

For controls that are `ai_sbom_assessable=True` and have a CCD, evaluate the assertions against the SBOM snapshot without an LLM call:

```python
def evaluate_control_from_sbom(
    control: ComplianceControl,
    snapshot: dict,
    doc: AiSbomDocument,
) -> ControlEvaluation:
    """Evaluate a compliance control against the AIBOM snapshot.

    Uses CCD assertions if present, falls back to signal-based heuristics.
    Returns PASS/PARTIAL/FAIL/UNABLE_TO_ASSESS/NOT_APPLICABLE.
    """
```

Graph query execution for CCD assertions:
- `EXISTS` — check `snapshot["nodes"][node_type]` is non-empty after applying filter
- `COUNT` — count matching nodes and apply operator/value comparison
- `ATTRIBUTE` — find nodes where attribute matches value
- `PATH` — traverse `doc.edges` to find path between node types
- `ABSENCE` — inverse of EXISTS

Score aggregation: each assertion is equally weighted; result = mean score mapped by thresholds from `scoring.py` (FAIL < 0.2, PARTIAL < 0.8, PASS ≥ 0.8).

#### 5.3 LLM Synthesizer

**File:** `nuguard/policy/synthesizer.py`

Port `assessment_service/core/llm_synthesizer.py` with these adaptations:
- Use `nuguard.common.llm_client.LLMClient` instead of the commercial LiteLLM wrapper
- Remove DB result persistence — return `ControlEvaluation` directly
- Keep: `_SYNTHESIS_SCHEMA`, conservative PARTIAL-over-COVERED instruction, `_truncate_nodes()`, result mapping

Used only for controls where `ai_sbom_assessable=True` but CCD assertions alone produce `UNABLE_TO_ASSESS`, or where `enable_llm=True` is set.

```python
async def llm_synthesize(
    control: ComplianceControl,
    snapshot: dict,
    llm: LLMClient,
) -> ControlEvaluation:
```

#### 5.4 Built-in Control Libraries

**Files:**
- `nuguard/policy/compliance/data/owasp_llm_top10.json` — bundled control definitions (NuGuard JSON schema)
- `nuguard/policy/compliance/data/nist_ai_rmf.json` — bundled control definitions (v1.5)
- `nuguard/policy/compliance/data/eu_ai_act.json` — bundled control definitions (v1.5)
- `nuguard/policy/compliance/loader.py` — single common utility for all frameworks

Per-framework Python modules (`owasp_mapper.py`, `nist_mapper.py`, `eu_ai_act_mapper.py`) are **not needed in v1**. All framework loading and ref mapping logic is generic: `loader.py` reads any conforming JSON file, and `framework_refs` are already embedded per-control in the JSON. Per-framework Python files would only be introduced if a specific framework requires unique logic (e.g. NIST's nested category/subcategory grouping, EU AI Act article-level rollups). None of that exists in v1.

**Control JSON schema** (NuGuard-defined format):

```json
{
  "framework": "OWASP_LLM_TOP10",
  "version": "1.0",
  "controls": [
    {
      "id": "owasp-llm01-prompt-injection",
      "name": "LLM01: Prompt Injection",
      "description": "...",
      "severity": "critical",
      "ai_sbom_assessable": true,
      "manual_attestation_required": false,
      "ai_sbom_basis": ["PROMPT", "GUARDRAIL"],
      "framework_refs": [
        {"framework": "OWASP_LLM_TOP10", "control_id": "LLM01"}
      ],
      "gap_diagnosis": "...",
      "fix_guidance": "...",
      "tags": ["injection", "input-validation"],
      "ccd": {
        "assertions": [
          {
            "type": "exists",
            "node_type": "GUARDRAIL",
            "filter": {"scope": "input"},
            "severity": "high"
          }
        ]
      }
    }
  ]
}
```

Users can supply their own JSON file in the same schema to assess against a custom control set:

```bash
nuguard policy check --sbom ./app.sbom.json --controls ./my-controls.json
```

**`loader.py`** validates the JSON against the schema and returns `list[ComplianceControl]`. This is the single point where controls enter the assessment pipeline regardless of source (bundled file, custom file, or future LLM extraction).

**`loader.py`** provides two functions used by the entire compliance pipeline:

```python
def load_controls(framework: str | None = None, path: Path | None = None) -> list[ComplianceControl]:
    """Load controls from a bundled framework JSON (by name) or a custom JSON file (by path).

    framework: "owasp-llm-top10" | "nist-ai-rmf" | "eu-ai-act"
    path: absolute path to a user-supplied JSON file in the NuGuard control schema
    """

def map_finding_to_refs(finding, controls: list[ComplianceControl]) -> list[FrameworkRef]:
    """Return framework refs from controls whose id matches the finding's control_id."""
```

If a framework needs unique logic in a future version, a `{framework}_mapper.py` is introduced at that point. Until then, `loader.py` is the only Python file in `compliance/`.

**OWASP LLM Top 10 controls to implement (v1):**

| ID | Control | CCD Assertion |
|---|---|---|
| LLM01 | Prompt Injection | PROMPT nodes with `injection_risk_score > 0.7` without GUARDRAIL protection → FAIL |
| LLM02 | Insecure Output Handling | TOOL nodes that output to external systems without validation attribute → PARTIAL |
| LLM06 | Sensitive Information Disclosure | DATASTORE nodes with `pii_detected=True` accessible from AGENT without AUTH → FAIL |
| LLM08 | Excessive Agency | PRIVILEGE nodes with `db_write` or `code_execution` without HITL enforcer → FAIL |
| LLM09 | Overreliance | No GUARDRAIL node with `type=hallucination_detection` → PARTIAL |
| LLM10 | Model Theft | MODEL nodes with no access control attribute → PARTIAL |

**NIST AI RMF controls to implement (v1.5):**
Defer to v1.5 — provide the mapper skeleton that returns empty list with a TODO comment.

**EU AI Act controls to implement (v1.5):**
Defer to v1.5 — same.

#### 5.5 Assessment Orchestrator

**File:** `nuguard/policy/assessment.py` (new file)

The top-level entry point called by `nuguard policy check`:

```python
async def run_compliance_assessment(
    doc: AiSbomDocument,
    framework: str,              # "OWASP_LLM_TOP10" | "NIST_AI_RMF" | "EU_AI_ACT"
    enable_llm: bool = False,
    llm: LLMClient | None = None,
) -> PolicyAssessmentResult:
    """Full assessment pipeline:
    1. Build AIBOM snapshot
    2. Load controls for framework
    3. For each control: CCD evaluation → optional LLM synthesis
    4. Aggregate scores
    5. Return PolicyAssessmentResult
    """
```

Scoring thresholds (from `assessment_service/core/scoring.py`):
- `FAIL_THRESHOLD = 0.2` — score below this → FAIL
- `PARTIAL_THRESHOLD = 0.8` — score below this → PARTIAL, above → PASS
- Overall score = weighted mean (critical controls weight 3×, high weight 2×, medium/low weight 1×)

---

### Work Stream 6: CLI Integration

**Goal:** Wire the implemented policy logic into `nuguard policy validate` and `nuguard policy check`.

**File:** `nuguard/cli/commands/policy.py`

```bash
# Lint a cognitive policy
nuguard policy validate --file ./cognitive-policy.md

# Cross-check cognitive policy against SBOM
nuguard policy check --sbom ./app.sbom.json --policy ./cognitive-policy.md

# Run compliance assessment using bundled framework controls
nuguard policy check --sbom ./app.sbom.json --framework owasp-llm-top10
nuguard policy check --sbom ./app.sbom.json --framework owasp-llm-top10 --format sarif

# Run compliance assessment using a custom control JSON file
nuguard policy check --sbom ./app.sbom.json --controls ./my-controls.json

# Show a stored policy
nuguard policy show --policy-id <id>
```

Exit codes (consistent with design doc §6):
- `0` — no findings at or above `--fail-on`
- `1` — findings at or above `--fail-on`
- `2` — CRITICAL findings
- `3` — error (invalid file, parse failure)

Output: rich table for terminal, SARIF via `nuguard/output/sarif_generator.py`.

---

## 6. File Map — Everything to Create or Modify

### New files

```
nuguard/
  models/policy.py              # REPLACE (extend with full model hierarchy)
  policy/
    parser.py                   # IMPLEMENT (WS1)
    validator.py                # IMPLEMENT (WS2)
    checker.py                  # IMPLEMENT (WS3)
    assessment.py               # NEW (WS5 orchestrator)
    aibom_snapshot.py           # NEW (WS5 — ported from assessment_service)
    evaluator.py                # NEW (WS5 — CCD graph query engine)
    synthesizer.py              # NEW (WS5 — ported from assessment_service)
    scoring.py                  # NEW (WS5 — ported from assessment_service)
    ccd_parser.py               # NEW (WS5 — ported from assessment_service)
    extractor.py                # NEW (WS5 — ported from assessment_service, optional/LLM)
    compliance/
      loader.py                 # NEW — load_controls() + map_finding_to_refs() (common util)
      data/
        owasp_llm_top10.json    # NEW — 6 bundled OWASP LLM Top 10 controls with CCDs
        nist_ai_rmf.json        # NEW — skeleton JSON (v1.5)
        eu_ai_act.json          # NEW — skeleton JSON (v1.5)
      # Per-framework .py files added only when framework-specific logic is needed (v1.5+)
  redteam/
    policy_engine/
      evaluator.py              # IMPLEMENT (WS4)
      detectors/
        topic_boundary.py       # IMPLEMENT (WS4)
        restricted_action.py    # IMPLEMENT (WS4)
        hitl_bypass.py          # IMPLEMENT (WS4)
  cli/commands/policy.py        # IMPLEMENT (WS6 — currently stub)
```

### Files to read before implementing (source of truth)

```
NuGuard-app/backend/assessment_service/core/
  aibom_snapshot_builder.py     # Port to aibom_snapshot.py
  llm_synthesizer.py            # Port to synthesizer.py
  scoring.py                    # Port to scoring.py
  ccd_parser.py                 # Port to ccd_parser.py
  policy_extractor.py           # Port to extractor.py (LLM-optional path)
```

---

## 7. Implementation Order and Dependencies

```
WS1: parser.py
  └── WS2: validator.py (depends on CognitivePolicy model)
      └── WS6: CLI validate command

WS3: checker.py (depends on parser.py + AiSbomDocument)
  └── WS6: CLI check command

WS4: policy_engine/ (depends on parser.py for CognitivePolicy)
  └── Consumed by: nuguard/redteam/executor/executor.py

WS5a: models/policy.py (extended) → no deps
WS5b: scoring.py (port) → no deps
WS5c: ccd_parser.py (port) → no deps
WS5d: aibom_snapshot.py (port) → depends on AiSbomDocument
WS5e: evaluator.py (CCD engine) → depends on ccd_parser.py + aibom_snapshot.py
WS5f: synthesizer.py (port) → depends on LLMClient + aibom_snapshot.py
WS5g: compliance/loader.py + data/owasp_llm_top10.json → depends on models/policy.py, ccd_parser.py
WS5h: assessment.py → depends on all WS5 files
  └── WS6: CLI check --framework command
```

Recommended order: WS5a → WS5b → WS5c → WS5d → WS1 → WS2 → WS3 → WS4 → WS5e → WS5f → WS5g → WS5h → WS6

---

## 8. Testing Plan

### Tests for Cognitive Policy (WS1–3)

```
nuguard/policy/tests/
  fixtures/
    healthtech_policy.md         # full example policy
    minimal_policy.md            # only required sections
    empty_policy.md              # all sections empty
  test_parser.py                 # parse → CognitivePolicy, all section types
  test_validator.py              # lint rules POLICY-001 through POLICY-006
  test_checker.py                # gap detection against fixture SBOMs
```

### Tests for Policy Evaluator (WS4)

```
nuguard/redteam/policy_engine/tests/
  test_topic_boundary.py         # keyword matching, two-tier detection
  test_restricted_action.py      # tool call matching, text claim matching
  test_hitl_bypass.py            # HITL trigger detection
  test_evaluator.py              # full evaluate() combining all detectors
```

### Tests for Compliance Assessment (WS5)

```
nuguard/policy/tests/
  test_aibom_snapshot.py         # snapshot structure from fixture SBOMs
  test_ccd_parser.py             # CCD DSL parsing
  test_evaluator.py              # CCD assertion evaluation against snapshots
  test_loader.py                 # load_controls() for bundled + custom files, map_finding_to_refs()
  test_owasp_controls.py        # 6 OWASP controls produce expected assessment results
  test_assessment.py             # full pipeline: AiSbomDocument → PolicyAssessmentResult
```

---

## 9. What is Explicitly Out of Scope for v1

- **LLM-based policy extraction** (`extractor.py`) — deferred to v1.5. In v1, controls are loaded from bundled JSON files (`compliance/data/`) using `loader.py`. Users can supply a custom JSON file in the same NuGuard schema; arbitrary document extraction (PDF, Markdown, Word) via LLM is v1.5.
- **NIST AI RMF and EU AI Act controls** — mapper skeletons only; full control libraries are v1.5.
- **Redis-backed caching** — `assessment_service/core/runtime_cache.py` has no equivalent. Single-run, no caching needed.
- **Attestation workflows** — the `AttestationStatus` pattern in `assessment_service` has no equivalent in OSS CLI.
- **Multi-framework combined scoring** — `nuguard policy check --framework all` is v1.5.
- **CCD authoring tools** — no UI or CLI for writing CCDs; they are coded directly in the mapper files.

---

*Policy Implementation Plan — NuGuard OSS — March 2026*
