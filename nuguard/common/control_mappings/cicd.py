"""OWASP Top 10 CI/CD Security Risks (2023) mapping for NGA-SC rules.

Mirrors ``owasp.py`` and ``atlas.py``: a rule_id-keyed lookup table plus an
accessor that never raises. Only ``NGA-SC-*`` (supply-chain) rules are mapped
here — the OWASP CI/CD Top 10 is a pipeline-security taxonomy, not a general
LLM/agentic one, so the structural ``NGA-*`` rules are out of scope.

Source: OWASP Top 10 CI/CD Security Risks (owasp.org/www-project-top-10-ci-cd-security-risks)
"""
from __future__ import annotations

CICD_TOP10: dict[str, dict[str, str]] = {
    "CICD-SEC-1": {
        "name": "Insufficient Flow Control Mechanisms",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-01-Insufficient-Flow-Control-Mechanisms",
    },
    "CICD-SEC-2": {
        "name": "Inadequate Identity and Access Management",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-02-Inadequate-Identity-And-Access-Management",
    },
    "CICD-SEC-3": {
        "name": "Dependency Chain Abuse",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-03-Dependency-Chain-Abuse",
    },
    "CICD-SEC-4": {
        "name": "Poisoned Pipeline Execution (PPE)",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-04-Poisoned-Pipeline-Execution",
    },
    "CICD-SEC-5": {
        "name": "Insufficient PBAC (Pipeline-Based Access Controls)",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-05-Insufficient-PBAC",
    },
    "CICD-SEC-6": {
        "name": "Insufficient Credential Hygiene",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-06-Insufficient-Credential-Hygiene",
    },
    "CICD-SEC-7": {
        "name": "Insecure System Configuration",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-07-Insecure-System-Configuration",
    },
    "CICD-SEC-8": {
        "name": "Ungoverned Usage of 3rd Party Services",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-08-Ungoverned-Usage-of-3rd-Party-Services",
    },
    "CICD-SEC-9": {
        "name": "Improper Artifact Integrity Validation",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-09-Improper-Artifact-Integrity-Validation",
    },
    "CICD-SEC-10": {
        "name": "Insufficient Logging and Visibility",
        "url": "https://owasp.org/www-project-top-10-ci-cd-security-risks/CICD-SEC-10-Insufficient-Logging-And-Visibility",
    },
}

# ---------------------------------------------------------------------------
# NGA-SC-xxx -> CICD-SEC-x (a rule may map to more than one category)
# ---------------------------------------------------------------------------

NGA_TO_CICD_TOP10: dict[str, tuple[str, ...]] = {
    "NGA-SC-001": ("CICD-SEC-4", "CICD-SEC-3"),   # OIDC + unpinned action
    "NGA-SC-002": ("CICD-SEC-4",),                # dispatch/PR-target reaches publish
    "NGA-SC-003": ("CICD-SEC-2", "CICD-SEC-1"),   # OIDC + contents:write + mutable checkout
    "NGA-SC-004": ("CICD-SEC-3",),                # unpinned global install
    "NGA-SC-005": ("CICD-SEC-6",),                # CI reads creds/env/memory
    "NGA-SC-006": ("CICD-SEC-9",),                # no provenance attestation
    "NGA-SC-007": ("CICD-SEC-7", "CICD-SEC-2"),   # unrestricted shell permission
    "NGA-SC-008": ("CICD-SEC-7",),                # repo-controlled agent config
    "NGA-SC-009": ("CICD-SEC-8",),                # untrusted MCP server reference
    "NGA-SC-010": ("CICD-SEC-7",),                # auto-run/auto-approve
    "NGA-SC-011": ("CICD-SEC-4", "CICD-SEC-3"),   # postinstall network download
    "NGA-SC-012": ("CICD-SEC-4",),                # curl | bash lifecycle hook
    "NGA-SC-013": ("CICD-SEC-4",),                # download+run Bun
    "NGA-SC-014": ("CICD-SEC-6",),                # lifecycle script reads creds
    "NGA-SC-015": ("CICD-SEC-4",),                # build hook network call
    "NGA-SC-016": ("CICD-SEC-4", "CICD-SEC-9"),   # obfuscated lifecycle script
    "NGA-SC-017": ("CICD-SEC-9",),                # oversized file in hidden AI-tool dir
    "NGA-SC-018": ("CICD-SEC-9",),                # high-entropy blob
    "NGA-SC-019": ("CICD-SEC-9",),                # minified obfuscated JS
    "NGA-SC-020": ("CICD-SEC-1", "CICD-SEC-10"),  # misleading commit message
    "NGA-SC-021": ("CICD-SEC-1",),                # skip-ci on sensitive change
    "NGA-SC-022": ("CICD-SEC-1",),                # workflow change w/o manifest change
    "NGA-SC-023": ("CICD-SEC-3",),                # mutable dependency ref
    "NGA-SC-024": ("CICD-SEC-3",),                # missing lockfile
    "NGA-SC-025": ("CICD-SEC-3",),                # known-malicious IOC match
    "NGA-SC-026": ("CICD-SEC-5",),                # deploy job lacks environment protection
    "NGA-SC-027": ("CICD-SEC-10",),               # security step silently swallows failures
}


def cicd_refs_for_rule(rule_id: str) -> tuple[str, ...]:
    """Return the OWASP CI/CD Top 10 category IDs for an NGA-SC *rule_id*, or empty if unmapped."""
    return NGA_TO_CICD_TOP10.get(rule_id, ())
