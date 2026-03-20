"""Static MITRE ATLAS v2 dataset used by AtlasAnnotatorPlugin.

All data is embedded here so the plugin works fully offline.  When ATLAS
releases a new version, update ATLAS_VERSION and the dicts below.

Sources:
  https://atlas.mitre.org/techniques
  https://atlas.mitre.org/mitigations
  https://atlas.mitre.org/tactics
"""
from __future__ import annotations

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
}

# ---------------------------------------------------------------------------
# VLA rule → ATLAS technique mapping
#
# Each entry: (technique_id, confidence)
# confidence: "HIGH" — direct structural evidence
#             "MEDIUM" — circumstantial / partial evidence
#             "LOW" — possible but requires runtime confirmation
# ---------------------------------------------------------------------------

VLA_TO_ATLAS: dict[str, list[tuple[str, str]]] = {
    "XELO-001": [
        ("AML.T0051", "HIGH"),    # LLM Jailbreak — no guardrail means no jailbreak detection
        ("AML.T0054", "HIGH"),    # LLM Prompt Injection — unfiltered input reaches model
        ("AML.T0015", "HIGH"),    # Evade ML Model — no output validation in the path
        ("AML.T0047", "HIGH"),    # Erode ML Model Integrity — unguarded model output
    ],
    "XELO-002": [
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — PHI exits trust boundary
        ("AML.T0024", "HIGH"),    # Exfiltration via ML Inference API — PHI → external LLM
    ],
    "XELO-003": [
        ("AML.T0040", "HIGH"),    # ML Model Inference API Access — PHI API with weak auth
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — PHI exposed via API
        ("AML.T0000", "MEDIUM"),  # Active Scanning — discoverable unauthenticated endpoint
        ("AML.T0016", "MEDIUM"),  # Verify Victim ML Model — weak auth enables probing
    ],
    "XELO-004": [
        ("AML.T0047", "HIGH"),    # Erode ML Model Integrity — privileged actions unguarded
        ("AML.T0036", "MEDIUM"),  # Develop Capabilities — privileged tool reachable by agent
    ],
    "XELO-005": [
        ("AML.T0024", "HIGH"),    # Exfiltration via ML Inference API — PHI in voice data
        ("AML.T0037", "HIGH"),    # Data from Information Repositories — audio contains PHI
    ],
    "XELO-006": [
        ("AML.T0051", "HIGH"),    # LLM Jailbreak — no output filter
        ("AML.T0015", "HIGH"),    # Evade ML Model — adversarial output passes unchecked
        ("AML.T0047", "MEDIUM"),  # Erode ML Model Integrity — unvalidated output propagates
    ],
    "XELO-007": [
        ("AML.T0054", "HIGH"),    # LLM Prompt Injection — template variables inject adversarial input
        ("AML.T0051", "HIGH"),    # LLM Jailbreak — injection can escalate to jailbreak
    ],
    "XELO-008": [
        ("AML.T0024", "HIGH"),    # Exfiltration via ML Inference API — PHI to multiple providers
        ("AML.T0010", "MEDIUM"),  # Acquire Public ML Artifacts — multiple external providers
    ],
    "XELO-009": [
        ("AML.T0040", "HIGH"),    # ML Model Inference API Access — endpoints lack auth
        ("AML.T0000", "MEDIUM"),  # Active Scanning — unauthenticated query surface
    ],
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
