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
    # Labelled booking/confirmation/PNR codes: "confirmation: K7Q4MN", "PNR HN4P88", "PNR is HN4P88"
    re.compile(
        r'(?:confirmation|booking|reservation|pnr|record\s+locator)\s*'
        r'(?:number|code|ref(?:erence)?|no|is|#)?\s*[:\s#]+([A-Z0-9]{4,10})\b',
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

# Pattern for extracting a customer/passenger/patient name (requires label prefix).
_NAME_PATTERN = re.compile(
    r'(?:name|customer|passenger|patient|account\s+holder)\s*[:\s]+'
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    re.IGNORECASE,
)


def extract_customer_name(text: str) -> str:
    """Return the first customer/passenger/patient name found in *text*.

    Requires a label prefix (``name:``, ``customer:``, ``passenger:``, etc.) to
    avoid false positives on ordinary sentences.  Returns an empty string when
    no labelled name is present.
    """
    m = _NAME_PATTERN.search(text)
    return m.group(1).strip() if m else ""


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


# Pattern for extracting labelled entity values from agent responses.
# Captures pairs like "flight: BA205", "departure: 2026-08-15", "seat: 14A".
_ENTITY_PATTERN = re.compile(
    r'(?:flight|seat|departure|arrival|date|class|fare|route|origin|destination'
    r'|status|price|total|amount|balance|account\s+type|plan|tier)\s*[:\s]+'
    r'([^\n,\.;]{2,40})',
    re.IGNORECASE,
)

# Map raw label → normalised key for entity_map
_ENTITY_LABEL_RE = re.compile(
    r'(flight|seat|departure|arrival|date|class|fare|route|origin|destination'
    r'|status|price|total|amount|balance|account\s+type|plan|tier)',
    re.IGNORECASE,
)


def extract_entity_map(text: str) -> dict[str, str]:
    """Return a mapping of entity label → value extracted from *text*.

    Captures labelled fields such as ``flight: BA205`` or ``departure: 2026-08-15``
    that provide useful context for scenario grounding.  Only the *first* occurrence
    of each normalised label is kept.

    >>> extract_entity_map("Flight: BA205, Seat: 14A, Departure: 2026-08-15")
    {'flight': 'BA205', 'seat': '14A', 'departure': '2026-08-15'}
    """
    result: dict[str, str] = {}
    for m in _ENTITY_PATTERN.finditer(text):
        full_match = m.group(0)
        label_m = _ENTITY_LABEL_RE.match(full_match)
        if not label_m:
            continue
        label = label_m.group(1).lower().replace(" ", "_")
        value = m.group(1).strip().rstrip(".,;:")
        if label not in result and value:
            result[label] = value
    return result


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
