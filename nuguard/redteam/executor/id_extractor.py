"""ID extraction and similar-ID generation for golden data IDOR probes."""
from __future__ import annotations

import re

_ID_PATTERNS: list[re.Pattern[str]] = [
    # Labelled IDs: "account_id: ACCT-0001", "customer-id=CU12345"
    re.compile(
        r'(?:account|customer|cust|user|tenant|client|member|order|ref|booking)'
        r'[\s_-]?(?:id|no|number|#)[\s:=]+([A-Z0-9][A-Z0-9\-]{2,19})',
        re.IGNORECASE,
    ),
    # Prefixed alphanumeric IDs: ACCT-0001, TEN-12345
    re.compile(r'\b([A-Z]{2,6}-\d{3,10})\b'),
    # Compact prefix+digit IDs: CUST00123456
    re.compile(r'\b([A-Z]{2,4}\d{5,12})\b'),
    # UUIDs
    re.compile(
        r'\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b',
        re.IGNORECASE,
    ),
]

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

# Regex to split an ID into its text prefix and trailing numeric segment.
_SPLIT_RE = re.compile(r'^(.*?)(\d+)$')


def extract_ids(text: str) -> list[str]:
    """Return deduplicated IDs found in *text*, ordered by pattern specificity.

    Labelled IDs (e.g. ``account_id: ACCT-1001``) appear before bare tokens.
    UUIDs appear last.  Stop-words like single-letter tokens are excluded.
    """
    seen: set[str] = set()
    results: list[str] = []

    for pattern in _ID_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1)
            upper = value.upper()
            if upper not in seen:
                seen.add(upper)
                results.append(value)

    return results


def generate_similar_ids(id_value: str, n: int = 3) -> list[str]:
    """Return *n* nearby IDs by incrementing/decrementing the trailing numeric segment.

    Examples::

        "ACCT-1001" → ["ACCT-1000", "ACCT-1002", "ACCT-1003"]
        "CUST00123456" → ["CUST00123455", "CUST00123457", "CUST00123458"]

    Returns an empty list for UUIDs (random, not incremental) or when the ID
    has no trailing numeric segment.
    """
    if _UUID_RE.match(id_value):
        return []

    m = _SPLIT_RE.match(id_value)
    if not m:
        return []

    prefix = m.group(1)
    numeric_str = m.group(2)
    num = int(numeric_str)
    pad = len(numeric_str)  # preserve zero-padding width

    variants: list[str] = []
    # Always try decrement first (gives the "previous" account — most useful for IDOR)
    if num > 0:
        variants.append(f"{prefix}{str(num - 1).zfill(pad)}")
    for delta in range(1, n + 1):
        candidate = f"{prefix}{str(num + delta).zfill(pad)}"
        if candidate not in variants:
            variants.append(candidate)
        if len(variants) >= n:
            break

    return variants[:n]
