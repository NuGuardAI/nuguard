"""ID extraction and similar-ID generation for golden data IDOR probes."""
from __future__ import annotations

import re

# LLM chat agents commonly wrap labels in markdown emphasis, e.g.
# "**Account Holder Name:** Alice Johnson" or "- **Account ID:** ACCT-001".
# The label/value separator patterns below only tolerate whitespace between
# the label and its value, so the emphasis markers are stripped up front —
# this is a no-op for plain-text responses.
_MARKDOWN_EMPHASIS_RE = re.compile(r'\*{1,2}')


def _strip_markdown_emphasis(text: str) -> str:
    return _MARKDOWN_EMPHASIS_RE.sub('', text)


_ID_PATTERNS: list[re.Pattern[str]] = [
    # Labelled IDs: "account_id: ACCT-0001", "customer-id=CU12345"
    re.compile(
        r'(?:account|customer|cust|user|tenant|client|member|order|ref|booking)'
        r'[\s_-]?(?:id|no|number|#)[\s:=]+([A-Z0-9][A-Z0-9\-]{2,19})',
        re.IGNORECASE,
    ),
    # Labelled booking/confirmation/PNR codes: "confirmation: K7Q4MN", "PNR HN4P88", "PNR code: K7Q4MN"
    # Require a non-space separator (: # =) OR an explicit label word (number/code/ref) so that
    # "booking team" or "booking agent" is NOT captured as an ID.
    # "is"/"was" connector is handled by the dedicated pattern below (with digit requirement).
    re.compile(
        r'(?:confirmation|booking|reservation|pnr|record\s+locator)\s*'
        r'(?:number|code|ref(?:erence)?|no|#)?\s*[:\s#]+([A-Z0-9]{4,10})\b',
        re.IGNORECASE,
    ),
    # "booking number is HN4P88" / "booking reference is K7Q4MN" — "is"/"was" connector pattern.
    # Requires at least one digit in the captured value to avoid matching English words like
    # "confirmed", "available", etc.
    re.compile(
        r'(?:confirmation|booking|reservation|pnr|record\s+locator)'
        r'(?:\s+(?:number|code|ref(?:erence)?|no|#))?\s*'
        r'(?:is|was)\s+([A-Z0-9]*\d[A-Z0-9]*)\b',
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

# Patterns for extracting a customer/passenger/patient name.
# Ordered from highest to lowest precision — first match wins.
#
# IMPORTANT: the label/trigger portion uses inline (?i:...) so it matches
# case-insensitively, but the *captured* name group ([A-Z][a-z]+...) is kept
# case-sensitive.  This prevents common lowercase words such as "or", "email",
# "number", "reservation" from being accepted as the start of a name — which
# would otherwise happen when an agent says "I don't have your name or email"
# and the IGNORECASE flag makes `or` satisfy `[A-Z][a-z]+`.
_NAME_PATTERNS: list[re.Pattern[str]] = [
    # Label-prefix with colon OR clear multi-space/newline separator:
    # "Name: Alice Johnson", "Passenger: Alice Johnson", "Name: Alice"
    # Single-space separators without a colon are intentionally excluded to
    # avoid false-positive matches on "your name or …" phrases.
    re.compile(
        r'(?i:(?:name|customer|passenger|patient|account\s+holder)\s*[:\-]\s*)'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
    ),
    # Greeting: "Hello Alice Johnson," / "Hi Alice," — single word is unambiguous
    # because the greeting keyword makes it clear the capture is a name.
    # Stop-word guard below rejects false positives like "Hello There,".
    re.compile(
        r'(?:Hello|Hi|Dear|Welcome(?:\s+back)?),?\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
    ),
    # Logged-in-as / registered-to: "logged in as Alice Johnson"
    re.compile(
        r'(?i:(?:logged\s+in\s+as|registered\s+to|authenticated\s+as)\s+)'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    ),
    # Contextual "for/to": "booking for Alice Johnson", "reserved for Alice Johnson"
    re.compile(
        r'(?i:(?:booking|reservation|flight|ticket|account)\s+(?:for|to)\s+)'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    ),
    # Possessive: "Alice Johnson's booking", "Alice Johnson's account"
    re.compile(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)'
        r"'s\s+(?:booking|reservation|account|flight|ticket)",
    ),
]

# Words that should never be the first word of a valid name capture.
# Used as a post-match sanity check to catch residual false positives.
_NAME_LEADING_STOP_WORDS: frozenset[str] = frozenset({
    "or", "and", "the", "a", "an", "no", "not", "my", "your", "their",
    "our", "its", "email", "address", "number", "details", "information",
    "profile", "data", "reservation", "booking", "file", "record",
    "please", "provide", "contact", "security", "reasons", "access",
})


def extract_customer_name(text: str) -> str:
    """Return the first customer/passenger/patient name found in *text*.

    Tries multiple strategies (label prefix, greeting, possessive, contextual)
    in order of precision.  Returns an empty string when no name is found.
    """
    text = _strip_markdown_emphasis(text)
    for pattern in _NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            candidate = m.group(1).strip()
            first_word = candidate.split()[0].lower()
            if first_word in _NAME_LEADING_STOP_WORDS:
                continue
            return candidate
    return ""


# Common English words that the ID patterns can incorrectly match.
_ID_STOP_WORDS: frozenset[str] = frozenset({
    "reference", "references", "details", "contact", "number", "system",
    "record", "records", "account", "accounts", "booking", "bookings",
    "flight", "flights", "ticket", "tickets", "please", "provide",
    "confirm", "request", "itinerary", "service", "support", "status",
    "cancel", "cancellation", "information", "passenger", "profile",
    # Common English words that booking-context patterns may capture via
    # space-only separation (e.g. "booking team" → "team").
    "team", "agent", "desk", "dept", "page", "code", "note", "name",
    "type", "date", "time", "seat", "gate", "zone", "plan", "term",
})

# Airline/railway flight-number pattern: 2–3 letters + hyphen + 2–5 digits (e.g. DL-401, UA-892).
# These look like IDs but are route codes, not booking references.
_FLIGHT_NUMBER_RE = re.compile(r'^[A-Z]{2,3}-\d{2,5}$', re.IGNORECASE)


def extract_ids(text: str) -> list[str]:
    """Return deduplicated IDs found in *text*, ordered by pattern specificity.

    Labelled IDs (e.g. ``account_id: ACCT-1001``) appear before bare tokens.
    UUIDs appear last.  Stop-words and airline flight-number formats are excluded.
    """
    text = _strip_markdown_emphasis(text)
    seen: set[str] = set()
    results: list[str] = []

    for pattern in _ID_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1)
            upper = value.upper()
            # Exclude common English words that are not real IDs
            if value.lower() in _ID_STOP_WORDS:
                continue
            # Reject pure-alphabetic captures shorter than 6 chars with no digits —
            # these are plain English words caught by the space-separator booking
            # pattern (e.g. "booking team" → "team", "booking agent" → "agent").
            if value.isalpha() and len(value) < 6:
                continue
            # Exclude airline/train flight numbers (e.g. DL-401, UA-892)
            if _FLIGHT_NUMBER_RE.match(value):
                continue
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
    text = _strip_markdown_emphasis(text)
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
