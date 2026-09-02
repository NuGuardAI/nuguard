"""Shared static MITRE ATLAS v2 dataset, used by analysis/, behavior/, and redteam/.

All data is embedded here so lookups work fully offline.  When ATLAS releases a new
version, update ATLAS_VERSION and the dicts below.

Three lookup tables, one per producer's natural key:
  - ``NGA_TO_ATLAS``      — analysis/ static rules, keyed by NGA rule_id
  - ``BA_RULE_TO_ATLAS``  — behavior/ static alignment rules, keyed by BA rule_id
  - ``GOAL_TYPE_TO_ATLAS``— redteam/ scenarios, keyed by GoalType (coarse fallback;
    individual scenario builders may set a more specific mitre_atlas_technique literal,
    which takes precedence over this table)

Sources:
  https://atlas.mitre.org/techniques
  https://atlas.mitre.org/mitigations
  https://atlas.mitre.org/tactics
"""
from __future__ import annotations

from nuguard.models.exploit_chain import GoalType

ATLAS_VERSION = "v2"
ATLAS_BASE_URL = "https://atlas.mitre.org"

# ---------------------------------------------------------------------------
# Tactics
# ---------------------------------------------------------------------------

TACTICS: dict[str, dict[str, str]] = {
    "AML.TA0000": {
        "tactic_id":   "AML.TA0000",
        "tactic_name": "Reconnaissance",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0000",
    },
    "AML.TA0001": {
        "tactic_id":   "AML.TA0001",
        "tactic_name": "Resource Development",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0001",
    },
    "AML.TA0002": {
        "tactic_id":   "AML.TA0002",
        "tactic_name": "Initial Access",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0002",
    },
    "AML.TA0004": {
        "tactic_id":   "AML.TA0004",
        "tactic_name": "Persistence",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0004",
    },
    "AML.TA0005": {
        "tactic_id":   "AML.TA0005",
        "tactic_name": "Defense Evasion",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0005",
    },
    "AML.TA0007": {
        "tactic_id":   "AML.TA0007",
        "tactic_name": "Discovery",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0007",
    },
    "AML.TA0008": {
        "tactic_id":   "AML.TA0008",
        "tactic_name": "Collection",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0008",
    },
    "AML.TA0009": {
        "tactic_id":   "AML.TA0009",
        "tactic_name": "Exfiltration",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0009",
    },
    "AML.TA0010": {
        "tactic_id":   "AML.TA0010",
        "tactic_name": "Impact",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0010",
    },
    "AML.TA0011": {
        "tactic_id":   "AML.TA0011",
        "tactic_name": "ML Attack Staging",
        "tactic_url":  f"{ATLAS_BASE_URL}/tactics/AML.TA0011",
    },
}

# ---------------------------------------------------------------------------
# Mitigations
# ---------------------------------------------------------------------------

MITIGATIONS: dict[str, dict[str, str]] = {
    "AML.M0002": {
        "mitigation_id":   "AML.M0002",
        "mitigation_name": "Passive ML Output Obfuscation",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0002",
    },
    "AML.M0004": {
        "mitigation_id":   "AML.M0004",
        "mitigation_name": "Restrict Number of ML Model Queries",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0004",
    },
    "AML.M0007": {
        "mitigation_id":   "AML.M0007",
        "mitigation_name": "Sanitize Training Data",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0007",
    },
    "AML.M0012": {
        "mitigation_id":   "AML.M0012",
        "mitigation_name": "Encrypt Sensitive Information",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0012",
    },
    "AML.M0013": {
        "mitigation_id":   "AML.M0013",
        "mitigation_name": "Code Signing",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0013",
    },
    "AML.M0014": {
        "mitigation_id":   "AML.M0014",
        "mitigation_name": "Verify ML Artifacts",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0014",
    },
    "AML.M0015": {
        "mitigation_id":   "AML.M0015",
        "mitigation_name": "Adversarial Input Detection",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0015",
    },
    "AML.M0016": {
        "mitigation_id":   "AML.M0016",
        "mitigation_name": "Vulnerability Scanning",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0016",
    },
    "AML.M0017": {
        "mitigation_id":   "AML.M0017",
        "mitigation_name": "Model Distribution Methods",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0017",
    },
    "AML.M0019": {
        "mitigation_id":   "AML.M0019",
        "mitigation_name": "Control Access to ML Models and Data at Rest",
        "mitigation_url":  f"{ATLAS_BASE_URL}/mitigations/AML.M0019",
    },
}

# ---------------------------------------------------------------------------
# Technique catalogue
#
# Each entry:
#   technique_id, technique_name, tactic_id, mitigation_ids, url
# ---------------------------------------------------------------------------

TECHNIQUES: dict[str, dict[str, object]] = {
    "AML.T0000": {
        "technique_id":   "AML.T0000",
        "technique_name": "Active Scanning",
        "tactic_id":      "AML.TA0000",
        "mitigation_ids": ["AML.M0004"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0000",
    },
    "AML.T0010": {
        "technique_id":   "AML.T0010",
        "technique_name": "Acquire Public ML Artifacts",
        "tactic_id":      "AML.TA0001",
        "mitigation_ids": ["AML.M0014", "AML.M0016"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0010",
    },
    "AML.T0015": {
        "technique_id":   "AML.T0015",
        "technique_name": "Evade ML Model",
        "tactic_id":      "AML.TA0005",
        "mitigation_ids": ["AML.M0015", "AML.M0002"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0015",
    },
    "AML.T0016": {
        "technique_id":   "AML.T0016",
        "technique_name": "Verify Victim ML Model",
        "tactic_id":      "AML.TA0007",
        "mitigation_ids": ["AML.M0002", "AML.M0004"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0016",
    },
    "AML.T0020": {
        "technique_id":   "AML.T0020",
        "technique_name": "Poison Training Data",
        "tactic_id":      "AML.TA0011",
        "mitigation_ids": ["AML.M0007", "AML.M0019"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0020",
    },
    "AML.T0024": {
        "technique_id":   "AML.T0024",
        "technique_name": "Exfiltration via ML Inference API",
        "tactic_id":      "AML.TA0009",
        "mitigation_ids": ["AML.M0004", "AML.M0012", "AML.M0002"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0024",
    },
    "AML.T0035": {
        "technique_id":   "AML.T0035",
        "technique_name": "ML Artifact Collection",
        "tactic_id":      "AML.TA0008",
        "mitigation_ids": ["AML.M0019", "AML.M0014"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0035",
    },
    "AML.T0036": {
        "technique_id":   "AML.T0036",
        "technique_name": "Develop Capabilities",
        "tactic_id":      "AML.TA0001",
        "mitigation_ids": ["AML.M0016"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0036",
    },
    "AML.T0037": {
        "technique_id":   "AML.T0037",
        "technique_name": "Data from Information Repositories",
        "tactic_id":      "AML.TA0008",
        "mitigation_ids": ["AML.M0012", "AML.M0019"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0037",
    },
    "AML.T0040": {
        "technique_id":   "AML.T0040",
        "technique_name": "ML Model Inference API Access",
        "tactic_id":      "AML.TA0002",
        "mitigation_ids": ["AML.M0004", "AML.M0019"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0040",
    },
    "AML.T0047": {
        "technique_id":   "AML.T0047",
        "technique_name": "Erode ML Model Integrity",
        "tactic_id":      "AML.TA0010",
        "mitigation_ids": ["AML.M0015", "AML.M0007"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0047",
    },
    "AML.T0048": {
        "technique_id":   "AML.T0048",
        "technique_name": "Compromise ML Model",
        "tactic_id":      "AML.TA0004",
        "mitigation_ids": ["AML.M0014", "AML.M0013", "AML.M0017"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0048",
    },
    "AML.T0051": {
        "technique_id":   "AML.T0051",
        "technique_name": "LLM Jailbreak",
        "tactic_id":      "AML.TA0005",
        "mitigation_ids": ["AML.M0015", "AML.M0002"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0051",
    },
    "AML.T0054": {
        "technique_id":   "AML.T0054",
        "technique_name": "LLM Prompt Injection",
        "tactic_id":      "AML.TA0005",
        "mitigation_ids": ["AML.M0015"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0054",
    },
    "AML.T0029": {
        "technique_id":   "AML.T0029",
        "technique_name": "Denial of ML Service",
        "tactic_id":      "AML.TA0010",
        "mitigation_ids": ["AML.M0004"],
        "technique_url":  f"{ATLAS_BASE_URL}/techniques/AML.T0029",
    },
}

# ---------------------------------------------------------------------------
# VLA rule → ATLAS technique mapping
#
# Each entry: (technique_id, confidence)
# confidence: "HIGH" — direct structural evidence
#             "MEDIUM" — circumstantial / partial evidence
#             "LOW" — possible but requires runtime confirmation
# ---------------------------------------------------------------------------

NGA_TO_ATLAS: dict[str, list[tuple[str, str]]] = {
    # NGA-001: PHI/PII sent to external LLM without guardrail
    "NGA-001": [
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — PHI exits trust boundary
        ("AML.T0024", "HIGH"),    # Exfiltration via ML Inference API — PHI → external LLM
    ],
    # NGA-002: Insufficient guardrails on model/agent (sub-checks A and B)
    "NGA-002": [
        ("AML.T0051", "HIGH"),    # LLM Jailbreak — no guardrail means no jailbreak detection
        ("AML.T0054", "HIGH"),    # LLM Prompt Injection — unfiltered input reaches model
        ("AML.T0015", "HIGH"),    # Evade ML Model — no output validation in the path
        ("AML.T0047", "HIGH"),    # Erode ML Model Integrity — unguarded model output
    ],
    # NGA-003: Secrets committed / exposed in environment variables
    "NGA-003": [
        ("AML.T0040", "HIGH"),    # ML Model Inference API Access — leaked key enables direct access
        ("AML.T0016", "MEDIUM"),  # Verify Victim ML Model — key enables probing without detection
    ],
    # NGA-004: Container / process running as root
    "NGA-004": [
        ("AML.T0047", "HIGH"),    # Erode ML Model Integrity — root enables model tampering
        ("AML.T0036", "MEDIUM"),  # Develop Capabilities — root access extends attacker footprint
    ],
    # NGA-005: Unencrypted datastore containing PII/PHI
    "NGA-005": [
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — plaintext PII at rest
        ("AML.T0024", "MEDIUM"),  # Exfiltration via ML Inference API — extracted via inference
    ],
    # NGA-006: Missing authentication on external API endpoint
    "NGA-006": [
        ("AML.T0040", "HIGH"),    # ML Model Inference API Access — unauthenticated endpoint
        ("AML.T0000", "MEDIUM"),  # Active Scanning — discoverable unauthenticated surface
        ("AML.T0016", "MEDIUM"),  # Verify Victim ML Model — probing without auth
    ],
    # NGA-007: Overly permissive IAM role
    "NGA-007": [
        ("AML.T0036", "HIGH"),    # Develop Capabilities — excessive permissions expand blast radius
        ("AML.T0047", "MEDIUM"),  # Erode ML Model Integrity — IAM grants write access to model
    ],
    # NGA-008: Untrusted or unverified model registry source
    "NGA-008": [
        ("AML.T0010", "HIGH"),    # Acquire Public ML Artifacts — model from untrusted registry
        ("AML.T0047", "HIGH"),    # Erode ML Model Integrity — tampered/backdoored artifact
    ],
    # NGA-012: Missing HITL for irreversible tool actions
    "NGA-012": [
        ("AML.T0047", "HIGH"),    # Erode ML Model Integrity — irreversible action without human check
        ("AML.T0036", "MEDIUM"),  # Develop Capabilities — automated capability bypasses oversight
    ],
    # NGA-015: No CPU/memory resource limits
    "NGA-015": [
        ("AML.T0029", "MEDIUM"),  # Denial of ML Service — unbounded resource consumption
    ],
    # NGA-009: No audit logging enabled
    "NGA-009": [
        ("AML.T0016", "MEDIUM"),  # Verify Victim ML Model — no log trail to detect probing
    ],
    # NGA-010: pull_request_target with untrusted context injection
    "NGA-010": [
        ("AML.T0010", "HIGH"),    # Acquire Public ML Artifacts — untrusted fork content reaches CI
        ("AML.T0036", "MEDIUM"),  # Develop Capabilities — privileged CI context abused
    ],
    # NGA-011: GITHUB_ENV written from untrusted input
    "NGA-011": [
        ("AML.T0010", "HIGH"),    # Acquire Public ML Artifacts — untrusted content injects env vars
        ("AML.T0036", "MEDIUM"),  # Develop Capabilities — env injection into later CI steps
    ],
    # NGA-013: No K8s NetworkPolicy for AI workload
    "NGA-013": [
        ("AML.T0036", "MEDIUM"),  # Develop Capabilities — unrestricted lateral movement
    ],
    # NGA-014: ACTIONS_RUNNER_DEBUG secret exposure
    "NGA-014": [
        ("AML.T0040", "HIGH"),    # ML Model Inference API Access — leaked secrets in debug logs
        ("AML.T0016", "MEDIUM"),  # Verify Victim ML Model — verbose logs aid reconnaissance
    ],
    # NGA-016: Container image using 'latest' tag
    "NGA-016": [
        ("AML.T0010", "HIGH"),    # Acquire Public ML Artifacts — mutable tag can be swapped
        ("AML.T0048", "MEDIUM"),  # Compromise ML Model — silent image substitution
    ],
    # NGA-017: AI workload missing health check
    "NGA-017": [
        ("AML.T0016", "LOW"),     # Verify Victim ML Model — degraded service harder to detect
    ],
    # NGA-018: Shared datastore, no IAM isolation
    "NGA-018": [
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — cross-agent data access
    ],
    # NGA-019: Unguarded write path to sensitive datastore
    "NGA-019": [
        ("AML.T0020", "HIGH"),    # Poison Training Data — unguarded write path
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — sensitive data exposed
    ],
    # NGA-020: Unguarded agent delegation chain
    "NGA-020": [
        ("AML.T0054", "HIGH"),    # LLM Prompt Injection — propagates across delegation boundary
        ("AML.T0036", "MEDIUM"),  # Develop Capabilities — chained agent capabilities
    ],
    # NGA-021: IDOR-prone endpoint without authorization checks
    "NGA-021": [
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — cross-account data access
        ("AML.T0000", "MEDIUM"),  # Active Scanning — path-param enumeration
    ],
    # NGA-022: Untrusted MCP tool with no guardrail
    "NGA-022": [
        ("AML.T0010", "HIGH"),    # Acquire Public ML Artifacts — untrusted MCP server as source
        ("AML.T0048", "MEDIUM"),  # Compromise ML Model — poisoned tool descriptor/response
    ],
    # NGA-023: Unprotected vector/embedding store
    "NGA-023": [
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — embeddings recoverable
        ("AML.T0024", "MEDIUM"),  # Exfiltration via ML Inference API — retrieval-layer extraction
    ],
    # NGA-024: Unauthenticated inter-agent delegation
    "NGA-024": [
        ("AML.T0054", "MEDIUM"),  # LLM Prompt Injection — spoofed delegated instructions
        ("AML.T0040", "MEDIUM"),  # ML Model Inference API Access — unauthenticated agent-to-agent call
    ],
    # NGA-025: Credential embedded in system prompt / hidden context
    "NGA-025": [
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — secret embedded in context
        ("AML.T0024", "MEDIUM"),  # Exfiltration via ML Inference API — leaked via prompt extraction
    ],
    # NGA-026: AI endpoint without application-level rate limiting
    "NGA-026": [
        ("AML.T0029", "HIGH"),    # Denial of ML Service — unbounded request volume/cost
        ("AML.T0040", "MEDIUM"),  # ML Model Inference API Access — unmetered API abuse
    ],
    # NGA-027: AI endpoint missing security headers (CSP/X-Frame-Options/HSTS)
    "NGA-027": [
        ("AML.T0024", "MEDIUM"),  # Exfiltration via ML Inference API — weaker response-side controls
    ],
    # NGA-028: API endpoint has an overly permissive CORS policy
    "NGA-028": [
        ("AML.T0024", "HIGH"),    # Exfiltration via ML Inference API — cross-origin credentialed reads
    ],
    # NGA-029: API endpoint's error handler leaks stack traces
    "NGA-029": [
        ("AML.T0024", "MEDIUM"),  # Exfiltration via ML Inference API — internals leaked via error responses
    ],
    # NGA-030: JWT verification with no pinned algorithm allow-list
    "NGA-030": [
        ("AML.T0040", "HIGH"),    # ML Model Inference API Access — forged token accepted as valid identity
    ],
    # ── NGA-SC-xxx: supply-chain rules ──────────────────────────────────────
    "NGA-SC-001": [("AML.T0010", "HIGH"), ("AML.T0048", "MEDIUM")],
    "NGA-SC-002": [("AML.T0010", "HIGH"), ("AML.T0036", "MEDIUM")],
    "NGA-SC-003": [("AML.T0010", "HIGH"), ("AML.T0048", "MEDIUM")],
    "NGA-SC-004": [("AML.T0010", "HIGH"), ("AML.T0036", "MEDIUM")],
    "NGA-SC-005": [("AML.T0040", "HIGH"), ("AML.T0016", "MEDIUM")],
    "NGA-SC-006": [("AML.T0010", "HIGH"), ("AML.T0048", "HIGH")],
    "NGA-SC-007": [("AML.T0036", "HIGH"), ("AML.T0047", "MEDIUM")],
    "NGA-SC-008": [("AML.T0010", "MEDIUM")],
    "NGA-SC-009": [("AML.T0010", "HIGH"), ("AML.T0048", "MEDIUM")],
    "NGA-SC-010": [("AML.T0036", "HIGH")],
    "NGA-SC-011": [("AML.T0010", "HIGH"), ("AML.T0036", "HIGH")],
    "NGA-SC-012": [("AML.T0036", "HIGH"), ("AML.T0047", "HIGH")],
    "NGA-SC-013": [("AML.T0036", "HIGH")],
    "NGA-SC-014": [("AML.T0040", "HIGH")],
    "NGA-SC-015": [("AML.T0010", "MEDIUM"), ("AML.T0036", "MEDIUM")],
    "NGA-SC-016": [("AML.T0036", "MEDIUM")],
    "NGA-SC-017": [("AML.T0036", "LOW")],
    "NGA-SC-018": [("AML.T0040", "MEDIUM")],
    "NGA-SC-019": [("AML.T0036", "LOW")],
    "NGA-SC-020": [("AML.T0036", "LOW")],
    "NGA-SC-021": [("AML.T0036", "MEDIUM")],
    "NGA-SC-022": [("AML.T0010", "MEDIUM")],
    "NGA-SC-023": [("AML.T0010", "HIGH"), ("AML.T0048", "MEDIUM")],
    "NGA-SC-024": [("AML.T0010", "MEDIUM")],
    "NGA-SC-025": [("AML.T0048", "HIGH"), ("AML.T0010", "HIGH")],
}

# ---------------------------------------------------------------------------
# Native ATLAS check definitions
#
# These are ATLAS signals not fully covered by any VLA rule.
# Each has:
#   check_id, title, description, affected_types, technique_map, confidence
# ---------------------------------------------------------------------------

NATIVE_CHECKS: list[dict[str, object]] = [
    {
        "check_id":      "ATLAS-NC-001",
        "title":         "External ML model without integrity verification",
        "description": (
            "One or more MODEL nodes reference an external provider but carry no "
            "integrity hash, signature, or provenance metadata. An adversary could "
            "substitute a trojanised model artifact without detection."
        ),
        "affected_types": ["MODEL"],
        "techniques": [
            ("AML.T0010", "HIGH"),   # Acquire Public ML Artifacts
            ("AML.T0048", "HIGH"),   # Compromise ML Model
        ],
        "remediation": (
            "Record a cryptographic hash (SHA-256) of each model artifact in the SBOM "
            "extras field ('integrity_hash'). Verify hashes during deployment. "
            "Consider model signing via Sigstore or a private model registry."
        ),
    },
    {
        "check_id":      "ATLAS-NC-002",
        "title":         "Writable datastore reachable by unguarded model/agent",
        "description": (
            "A DATASTORE node is reachable from a MODEL or AGENT node via the edge graph "
            "with no GUARDRAIL protecting the write path. An adversary with model "
            "influence could poison training or application data."
        ),
        "affected_types": ["DATASTORE"],
        "techniques": [
            ("AML.T0020", "MEDIUM"),  # Poison Training Data
        ],
        "remediation": (
            "Insert a GUARDRAIL node between the MODEL/AGENT and the DATASTORE for any "
            "write-capable edge. Apply input validation and anomaly detection on all "
            "data written by AI components."
        ),
    },
    {
        "check_id":      "ATLAS-NC-003",
        "title":         "Model artifact reachable from deployment without auth",
        "description": (
            "A MODEL node and a DEPLOYMENT node are present in the SBOM with no AUTH "
            "node on the path between them in the edge graph. Model weights or artefacts "
            "may be downloadable without authentication."
        ),
        "affected_types": ["MODEL", "DEPLOYMENT"],
        "techniques": [
            ("AML.T0035", "MEDIUM"),  # ML Artifact Collection
        ],
        "remediation": (
            "Ensure model serving endpoints require authentication. Store model "
            "artefacts in access-controlled object storage and log all download events."
        ),
    },
    {
        "check_id":      "ATLAS-NC-004",
        "title":         "Agent or tool with outbound external API capability",
        "description": (
            "AGENT or TOOL nodes are present that make outbound calls to external "
            "services (inferred from node name, metadata, or tool type). This provides "
            "an adversary with a capability-development or exfiltration channel."
        ),
        "affected_types": ["AGENT", "TOOL"],
        "techniques": [
            ("AML.T0036", "MEDIUM"),  # Develop Capabilities
            ("AML.T0024", "LOW"),     # Exfiltration via ML Inference API
        ],
        "remediation": (
            "Enumerate all outbound domains reachable by agents and tools. "
            "Apply an allow-list policy for external API calls and log all "
            "outbound requests from AI components."
        ),
    },
]

# ---------------------------------------------------------------------------
# External provider keywords (mirrors vulnerability.py)
# ---------------------------------------------------------------------------

EXTERNAL_PROVIDERS: frozenset[str] = frozenset({
    "openai", "anthropic", "google", "cohere", "mistral",
    "deepseek", "ai21", "amazon", "azure", "huggingface",
})

# keywords that hint a TOOL/AGENT makes outbound calls
OUTBOUND_KEYWORDS: frozenset[str] = frozenset({
    "http", "api", "request", "webhook", "search", "browser",
    "fetch", "email", "slack", "gmail", "calendar", "web",
    "serpapi", "tavily", "bing", "duckduckgo",
})

# keywords in tool/agent descriptions that indicate datastore access (NC-002)
DB_ACCESS_KEYWORDS: frozenset[str] = frozenset({
    "database", "datastore", "data store", "query", "sql", "sqlite",
    "postgres", "postgresql", "mysql", "mongo", "mongodb", "redis",
    "dynamodb", "cassandra", "elasticsearch", "table", "schema",
    "record", "persist", "storage",
})


def atlas_technique_label(technique_id: str) -> str:
    """Return the canonical ``"AML.T0054 – LLM Prompt Injection"``-style label for
    *technique_id*, or the bare id if it isn't in the catalogue.

    Scenario builders can call this instead of hand-typing labels, so the
    human-readable name always matches the catalogue entry in ``TECHNIQUES``.
    """
    tech = TECHNIQUES.get(technique_id)
    if tech is None:
        return technique_id
    return f"{technique_id} – {tech['technique_name']}"


# ---------------------------------------------------------------------------
# BA-xxx (behavior static alignment rules — nuguard/behavior/alignment.py)
# ---------------------------------------------------------------------------

BA_RULE_TO_ATLAS: dict[str, list[tuple[str, str]]] = {
    "BA-001": [("AML.T0037", "HIGH")],   # restricted topic in system prompt
    "BA-002": [("AML.T0047", "HIGH"), ("AML.T0036", "MEDIUM")],  # risky tool, no guardrail
    "BA-003": [("AML.T0047", "HIGH")],   # restricted-action tool reachable
    "BA-004": [("AML.T0037", "HIGH")],   # PII datastore without guardrail
    "BA-005": [("AML.T0036", "HIGH")],   # no-auth agent, high-priv tool
    "BA-006": [("AML.T0010", "HIGH"), ("AML.T0048", "MEDIUM")],  # untrusted MCP server, write tool
    "BA-007": [("AML.T0054", "HIGH")],   # blocked_topics doesn't cover restricted_topics
    "BA-008": [("AML.T0047", "HIGH")],   # no HITL gate for hitl_triggers
    "BA-009": [("AML.T0040", "HIGH")],   # AUTH doesn't protect sensitive endpoints/agents
    "BA-010": [("AML.T0040", "HIGH")],   # high-priv component reachable w/o AUTH/GUARDRAIL
    "BA-011": [("AML.T0020", "HIGH")],   # DATASTORE write access lacks HITL/auth/guardrail
    "BA-012": [("AML.T0024", "MEDIUM")],  # sensitive data reaches external MODEL
    "BA-013": [("AML.T0037", "HIGH")],   # restricted topic in AGENT's PROMPT
    "BA-014": [("AML.T0054", "MEDIUM")],  # handoff to higher-priv agent w/o boundary
    "BA-015": [("AML.T0010", "MEDIUM")],  # DEPLOYS path security posture issues
    "BA-016": [("AML.T0037", "HIGH"), ("AML.T0000", "MEDIUM")],  # API_ENDPOINT returns sensitive data w/o auth
}


def atlas_refs_for_ba_rule(rule_id: str) -> list[tuple[str, str]]:
    """Return the ATLAS technique refs for a behavior BA-*** *rule_id*, or empty if unmapped."""
    return BA_RULE_TO_ATLAS.get(rule_id, [])


# ---------------------------------------------------------------------------
# GoalType (redteam scenarios — coarse fallback keyed on the 9-value GoalType
# enum). Individual scenario builders may set a more specific
# mitre_atlas_technique literal on the ExploitChain, which takes precedence.
# ---------------------------------------------------------------------------

GOAL_TYPE_TO_ATLAS: dict[GoalType, list[tuple[str, str]]] = {
    GoalType.PROMPT_DRIVEN_THREAT: [("AML.T0054", "HIGH"), ("AML.T0051", "HIGH")],
    GoalType.DATA_EXFILTRATION: [("AML.T0024", "HIGH"), ("AML.T0037", "MEDIUM")],
    GoalType.PRIVILEGE_ESCALATION: [("AML.T0047", "HIGH"), ("AML.T0036", "MEDIUM")],
    GoalType.TOOL_ABUSE: [("AML.T0036", "HIGH")],
    GoalType.POLICY_VIOLATION: [("AML.T0051", "MEDIUM")],
    GoalType.MCP_TOXIC_FLOW: [("AML.T0010", "HIGH"), ("AML.T0048", "MEDIUM")],
    GoalType.API_ATTACK: [("AML.T0037", "HIGH"), ("AML.T0000", "MEDIUM")],
    GoalType.AGENTIC_TRUST_ABUSE: [("AML.T0054", "MEDIUM"), ("AML.T0036", "MEDIUM")],
    GoalType.RECON_INFERENCE: [("AML.T0000", "HIGH"), ("AML.T0016", "MEDIUM")],
}


def atlas_refs_for_goal(goal_type: GoalType) -> list[tuple[str, str]]:
    """Return the ATLAS technique refs for a redteam *goal_type*, or empty if unmapped."""
    return GOAL_TYPE_TO_ATLAS.get(goal_type, [])


# ---------------------------------------------------------------------------
# BehaviorFindingType (behavior dynamic findings — nuguard/behavior/runner.py).
# Keyed by the plain finding_type string, mirroring BEHAVIOR_FINDING_TYPE_TO_OWASP.
# ---------------------------------------------------------------------------

BEHAVIOR_FINDING_TYPE_TO_ATLAS: dict[str, list[tuple[str, str]]] = {
    "CAPABILITY_GAP": [("AML.T0016", "LOW")],
    "TOOL_CHAIN_BROKEN": [("AML.T0047", "MEDIUM")],
    "INTENT_MISALIGNMENT": [("AML.T0054", "MEDIUM")],
    "POLICY_VIOLATION": [("AML.T0051", "MEDIUM")],
    "SECRET_DISCLOSURE": [("AML.T0024", "HIGH"), ("AML.T0037", "MEDIUM")],
    "DATA_LEAK": [("AML.T0024", "HIGH"), ("AML.T0037", "MEDIUM")],
    "DATA_HANDLING_VIOLATION": [("AML.T0037", "HIGH")],
    "ESCALATION_BYPASS": [("AML.T0047", "HIGH"), ("AML.T0036", "MEDIUM")],
}


def atlas_refs_for_finding_type(finding_type: str) -> list[tuple[str, str]]:
    """Return the ATLAS technique refs for a behavior dynamic-finding *finding_type*."""
    return BEHAVIOR_FINDING_TYPE_TO_ATLAS.get(str(finding_type).upper(), [])
