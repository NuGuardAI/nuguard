"""Shared value-level PII/PHI/PFI detection patterns.

Regex-based, no LLM call — used wherever a response body needs a fast,
deterministic signal that it contains structured personal data (account
numbers, names, phone numbers, emails, SSNs, balances, ...), as opposed to
field-*name* matching (see :data:`nuguard.redteam.llm_engine.signals.PII_LABEL_RE`
for that).
"""
from __future__ import annotations

import re

# Org-type suffixes that follow a company/product name, not a person — "Pinnacle
# Bank is...", "Acme Corp has..." should not match the Full-Name pattern below.
_ORG_SUFFIX_ALT = r"Bank|Inc|LLC|Corp|Corporation|Systems|App|Shop|Technologies"

# Context words that mark a dollar amount as an actual balance/transaction
# figure rather than incidental marketing copy ("$50 referral bonus", "no
# fees under $0").
_MONEY_CONTEXT_ALT = r"balance|account|transfer|debit|credit|payment"

PII_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b([A-Z]{3,6}-\d{3,8})\b"),                    # e.g. ACCT-001, LOAN-12345
    re.compile(r"\bKYC\s+(?:Level\s+)?\d\b", re.IGNORECASE),    # KYC Level 2
    re.compile(r"\bRisk\s+Score[:\s]+\d+\b", re.IGNORECASE),    # Risk Score: 15
    # Full Name, followed by a possessive/verb — NOT a bare parenthesis: "X Y ("
    # matches far too much incidental text (app names, framework banners,
    # version strings like "OWASP Juice Shop (Express ^4.22.1)") to be a
    # useful name signal on its own. Also excludes a following org-type
    # suffix ("Pinnacle Bank is...", "Acme Corp has...") — those are company
    # names, not person names.
    re.compile(
        r"\b[A-Z][a-z]+\s+(?!(?:" + _ORG_SUFFIX_ALT + r")\b)[A-Z][a-z]+\b"
        r"(?=\s*(?:is|has|'s))",
    ),
    # Dollar amount adjacent (within ~30 chars) to a balance/transaction
    # context word — not a bare "$NNN" anywhere in running prose, which
    # matches marketing copy just as readily as an actual leaked balance.
    re.compile(
        r"\b(?:" + _MONEY_CONTEXT_ALT + r")\b\D{0,30}\$[\d,]+(?:\.\d{2})?\b"
        r"|\$[\d,]+(?:\.\d{2})?\b(?=\D{0,30}\b(?:" + _MONEY_CONTEXT_ALT + r")\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),           # phone numbers
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),  # emails
    re.compile(r"\bSSN[:\s]+\d{3}-\d{2}-\d{4}\b", re.IGNORECASE),          # SSN
    re.compile(r"\b(?:account|balance|credit|debit)\s*[:#]?\s*[\d,]+\b", re.IGNORECASE),
]


def find_pii_values(text: str, limit: int = 8) -> list[str]:
    """Return up to *limit* distinct PII-shaped value matches found in *text*."""
    hits: list[str] = []
    for pattern in PII_VALUE_PATTERNS:
        for m in pattern.finditer(text):
            hit = m.group(0).strip()
            if hit and hit not in hits:
                hits.append(hit)
                if len(hits) >= limit:
                    return hits
    return hits
