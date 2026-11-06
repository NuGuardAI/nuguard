"""Shared secret redaction for prompt text copied into the SBOM.

Prompt adapters (Python, Go, TypeScript, C#, ...) copy prompt literals into
PROMPT nodes verbatim. When a literal embeds a credential, the credential
would be written into the SBOM. :func:`redact_secrets` strips credential
values from free text and :func:`redact_prompt_nodes` applies it to every
prompt-bearing node of a finished :class:`~nuguard.sbom.models.AiSbomDocument`,
so redaction happens in one place for every language.

Rules (the value is replaced with ``<redacted>``; the key, scheme and
surrounding prose are kept so the prompt stays readable):

1. **Private key blocks** - a PEM ``-----BEGIN ... PRIVATE KEY-----`` block.
2. **Known key formats** - the whole token: OpenAI/Anthropic ``sk-...``,
   Stripe ``sk_live_...``/``pk_test_...``, AWS ``AKIA...``/``ASIA...``,
   GitHub ``ghp_...``/``github_pat_...``, Slack ``xox[abposr]-...``,
   Google ``AIza...`` and JWTs (``eyJ...``).
3. **URL userinfo** - ``scheme://user:pass@host`` becomes
   ``scheme://<redacted>@host``. The user part may contain spaces when a
   ``:password`` follows it; a password may contain ``@`` (the last ``@``
   before the host ends it). A password may also contain ``/``, ``?`` or
   ``#`` when an ``@`` follows before any whitespace or later ``://``
   (``postgres://u:pa/ss@h/db``); a ``host:port/...`` is never userinfo.
4. **Bearer / Basic credentials** - ``Bearer <token>`` / ``Basic <b64>``
   (8+ characters). A plain lowercase or capitalised word is kept, so prose
   such as "Bearer authentication" stays readable.
5. **Credential key/value pairs** - ``key=value``, prose ``key: value`` and
   JSON ``"key": "value"`` where the key is a credential name (password,
   passwd, pass, pwd, psw, secret, token, api key, access token, client
   secret, private key, account key, shared access key/signature, sig, ...).
   The key may carry an identifier prefix (``DB_PASSWORD=``,
   ``stripe_api_key=``) and a version suffix (``apikey2=``,
   ``DB_PASSWORD_2=``, ``github_token_v2=``); other suffixes
   (``token_count``, ``max_tokens``, ``tokenizer``) are not credential keys.
   An unquoted ``key: value`` runs to ``;``, ``&``, ``,``, a quote or
   whitespace (a trailing ``.`` is treated as the end of the sentence). An
   unquoted ``key=value`` runs over up to 8 further space-separated words to
   ``;``, ``&``, ``,``, a quote, the end of the line or the next ``key=``, so
   ``password=my pass word`` is redacted whole. This fails closed:
   ``password=abc then do x`` redacts the rest of the line.
6. **Prose credentials** - a credential word (password, passcode, passwd,
   pwd, secret, token, api key, access key, private key) followed by
   ``is``/``was``/``are``/``equals``/``set to`` and a value: "the password
   is hunter2". The value is kept when trivial under the prose rule below
   ("the password is required").
7. **Bare high-entropy tokens** - a standalone token of 24-200 characters
   from ``[A-Za-z0-9_+/=-]`` with a letter, a digit and Shannon entropy of at
   least 4.0 bits per character. Kept: UUIDs, paths, dotted names,
   identifier-like names made of words (``MyVeryLongClassNameForTesting123``)
   and pure hex of 32+ characters (commit SHAs, hashes) unless a credential
   word directly precedes it. A generic ``key=`` value is redacted only
   through this rule.

To avoid wrecking ordinary prompt text, a key/value value is kept when it is
*trivial*:

- a template placeholder (``{count}``, ``{{token}}``, ``${TOKEN}``,
  ``<token>``, ``%s``, ``$TOKEN``) or an already redacted marker;
- a sentinel word (``none``, ``null``, ``true``, ``required``, ``optional``,
  ``string``, ``hidden``, ...) or a run of one repeated mask character
  (``***``, ``xxxx``);
- purely numeric and shorter than 8 digits (``max_token: 4096``);
- for the prose ``key: value`` and "key is value" forms with an unquoted
  value only: letters alone and shorter than 16 characters
  (``password: required``, ``token: the user's session``), unless 6+
  characters in mixed case (``HunterTwo``). A real credential in that form
  usually carries a digit or symbol, or is long.
- for a multi-word ``key=value``: the whole run is kept when its first word
  is trivial (``token={token} and ...``).

Not handled: low-entropy or short credentials with no credential word in
front of them, and bare tokens longer than 200 characters.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from .types import ComponentType

if TYPE_CHECKING:
    from .models import AiSbomDocument, Node

REDACTED = "<redacted>"

_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)

_KNOWN_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"sk-[A-Za-z0-9_-]{16,}"  # OpenAI / Anthropic
    r"|(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"  # Stripe
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"  # AWS access key id
    r"|gh[pousr]_[A-Za-z0-9]{30,}"  # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"  # Slack
    r"|AIza[0-9A-Za-z_-]{35}"  # Google API key
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r")(?![A-Za-z0-9_-])"
)

# Characters that end a URL authority (besides "@").
_URL_STOP = r"\s/?#\"'<>`"
_URL_USERINFO_RE = re.compile(
    r"(?P<prefix>(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*://)"
    r"(?:"
    # user[:password] without spaces; the password may contain "@"
    rf"[^{_URL_STOP}@:]*:[^{_URL_STOP}]*"
    rf"|[^{_URL_STOP}@:]+"
    # user with spaces, then a ":password" without spaces
    rf"|[^/?#\"'<>`@:\r\n]+:[^{_URL_STOP}]*"
    # password with "/", "?" or "#" up to the first "@" (never host:port/...)
    rf"|[^{_URL_STOP}@:]*:(?!\d+(?:[/?#]|$))(?:(?!://)[^\s\"'<>`]){{0,256}}?"
    r")@",
    re.IGNORECASE,
)

_AUTH_SCHEME_RE = re.compile(
    r"(?P<prefix>\b(?:Bearer|Basic)\s+)(?P<value>[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)

_CREDENTIAL_KEY = (
    r"(?:user\s*password|password|passwd|pass|pwd|psw"
    r"|secret[\s_-]?access[\s_-]?key|shared\s*access\s*(?:key|signature)"
    r"|account[\s_-]?key|access[\s_-]?key|private[\s_-]?key|secret[\s_-]?key"
    r"|access[\s_-]?token|refresh[\s_-]?token|auth[\s_-]?token|bearer[\s_-]?token"
    r"|api[\s_-]?key|client[\s_-]?secret|secret|token|sig)"
)

_CREDENTIAL_KV_RE = re.compile(
    r"(?P<prefix>(?<![a-z0-9])" + _CREDENTIAL_KEY + r"(?:[_-]?v?\d{1,4})?(?![a-z0-9])"
    r"[\"']?\s*(?P<sep>[:=])(?![=>])[^\S\r\n]*)"
    r"(?P<value>"
    r"\"[^\"\r\n]*\"|'[^'\r\n]*'"  # quoted (JSON or code)
    # key=spaced value: up to 8 more words, never into the next "key="
    r"|(?<==)[^\s;&,\"'<>]++(?:[^\S\r\n]+[^\s;&,\"'<>=]++(?!=)){0,8}"
    r"|[^\s;&,\"'<>]*[^\s;&,\"'<>.]"  # single token (a trailing "." ends the sentence)
    r")",
    re.IGNORECASE,
)

_PROSE_CREDENTIAL_RE = re.compile(
    r"(?P<prefix>(?<![a-z0-9])"
    r"(?:password|passcode|passwd|pwd|secret|token|api[\s_-]?key"
    r"|access[\s_-]?key|private[\s_-]?key)s?"
    r"[^\S\r\n]+(?:is|was|are|equals|set[^\S\r\n]+to)[^\S\r\n]+)"
    r"(?P<value>\"[^\"\r\n]{1,200}\"|'[^'\r\n]{1,200}'"
    r"|[^\s;&,\"'<>]{0,199}[^\s;&,\"'<>.])",
    re.IGNORECASE,
)

_BARE_TOKEN_RE = re.compile(
    r"(?<![^\s\"'`(\[{<,;:=&?])"
    r"[A-Za-z0-9_+/-]{24,200}={0,2}"
    r"(?![^\s\"'`)\]}>,;:&.])(?!\.\S)"
)
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX_RE = re.compile(r"[0-9a-f]{32,}", re.I)
_HEX_CONTEXT_RE = re.compile(
    r"(?<![a-z0-9])" + _CREDENTIAL_KEY + r"s?[\s:=\"']{1,4}\Z", re.IGNORECASE
)
_IDENT_SEGMENT_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

_PLACEHOLDER_RE = re.compile(
    r"^(?:\{.*\}|\$\{.*\}|<.*>|%[-0-9.]*[sdrif]|\$[A-Za-z_][A-Za-z0-9_]*|\[.*\])$",
    re.DOTALL,
)

_SENTINEL_WORDS = frozenset(
    {
        "none",
        "null",
        "nil",
        "true",
        "false",
        "empty",
        "unset",
        "required",
        "optional",
        "string",
        "str",
        "hidden",
        "masked",
        "redacted",
        "n/a",
        "na",
        "tbd",
        "todo",
        "changeme",
    }
)


def _is_trivial_value(raw: str, *, prose: bool) -> bool:
    """True when a key/value value is not worth redacting (see module docstring)."""
    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'"
    value = raw[1:-1].strip() if quoted else raw.strip()
    if not value or value in (REDACTED, "[REDACTED]"):
        return True
    if _PLACEHOLDER_RE.match(value):
        return True
    if value.lower() in _SENTINEL_WORDS:
        return True
    if len(set(value)) == 1 and value[0] in "*x.X-_#":
        return True
    if value.isdigit() and len(value) < 8:
        return True
    if prose and not quoted and value.isalpha() and len(value) < 16:
        mixed_case = any(c.isupper() for c in value[1:]) and any(c.islower() for c in value)
        return not (mixed_case and len(value) >= 6)
    return False


def _redact_kv(match: re.Match[str]) -> str:
    raw = match.group("value")
    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'"
    words = [raw] if quoted else raw.split()
    # A placeholder or sentinel first word keeps the run only when the rest is plain prose.
    if _is_trivial_value(words[0], prose=match.group("sep") == ":") and all(
        w.rstrip(".").isalpha() and _is_trivial_value(w.rstrip("."), prose=True) for w in words[1:]
    ):
        return match.group(0)
    if quoted:
        return f"{match.group('prefix')}{raw[0]}{REDACTED}{raw[0]}"
    after = match.string[match.end() : match.end() + 1]
    tail = raw[len(raw.rstrip(".")) :] if after in ("", " ", "\t", "\r", "\n") else ""
    return f"{match.group('prefix')}{REDACTED}{tail}"


def _redact_prose(match: re.Match[str]) -> str:
    raw = match.group("value")
    if _is_trivial_value(raw, prose=True):
        return match.group(0)
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return f"{match.group('prefix')}{raw[0]}{REDACTED}{raw[0]}"
    return f"{match.group('prefix')}{REDACTED}"


def _entropy(value: str) -> float:
    counts = [value.count(c) for c in set(value)]
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts)


def _looks_like_identifier(token: str) -> bool:
    """True for names built from words: ``MyVeryLongClassName123``, ``snake_case_name``."""
    if any(c in "+/=" for c in token):
        return False
    segments = _IDENT_SEGMENT_RE.findall(token)
    if sum(s.isdigit() for s in segments) > 2:
        return False
    for seg in segments:
        if seg.isdigit():
            continue
        if seg.isupper():
            if len(seg) < 2:
                return False
        elif len(seg) < 2 or not any(c in "aeiouyAEIOUY" for c in seg):
            return False
    return True


def _redact_bare_token(match: re.Match[str]) -> str:
    token = match.group(0)
    if not any(c.isdigit() for c in token) or not any(c.isalpha() for c in token):
        return token
    if token.startswith(("/", "./")) or _UUID_RE.fullmatch(token):
        return token
    if _HEX_RE.fullmatch(token):
        before = match.string[max(0, match.start() - 48) : match.start()]
        return REDACTED if _HEX_CONTEXT_RE.search(before) else token
    if _entropy(token) < 4.0 or _looks_like_identifier(token):
        return token
    return REDACTED


def _redact_auth_scheme(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.isalpha() and value[1:].islower():  # a plain word: "Bearer authentication"
        return match.group(0)
    return f"{match.group('prefix')}{REDACTED}"


def redact_secrets(text: str) -> str:
    """Return *text* with credential values replaced by ``<redacted>``.

    See the module docstring for the shapes handled and the values kept.
    """
    if not text:
        return text
    if "PRIVATE KEY-----" in text:
        text = _PRIVATE_KEY_BLOCK_RE.sub(REDACTED, text)
    text = _KNOWN_KEY_RE.sub(REDACTED, text)
    if "://" in text:
        text = _URL_USERINFO_RE.sub(lambda m: f"{m.group('prefix')}{REDACTED}@", text)
    text = _AUTH_SCHEME_RE.sub(_redact_auth_scheme, text)
    text = _CREDENTIAL_KV_RE.sub(_redact_kv, text)
    text = _PROSE_CREDENTIAL_RE.sub(_redact_prose, text)
    return _BARE_TOKEN_RE.sub(_redact_bare_token, text)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    return value


# Node types whose text fields carry copied prompt literals.
_PROMPT_BEARING_TYPES = frozenset({ComponentType.PROMPT, ComponentType.AGENT})


def redact_node(node: Node) -> None:
    """Redact secrets in a node's name, string metadata, extras and evidence."""
    node.name = redact_secrets(node.name)
    meta = node.metadata
    for field_name in type(meta).model_fields:
        current = getattr(meta, field_name)
        if isinstance(current, (str, dict, list)) and current:
            redacted = _redact_value(current)
            if redacted != current:
                setattr(meta, field_name, redacted)
    for ev in node.evidence:
        ev.detail = redact_secrets(ev.detail)


def redact_prompt_nodes(doc: AiSbomDocument) -> None:
    """Redact secrets on every prompt-bearing node of *doc*, in place.

    PROMPT nodes hold the prompt literal (content, extras, evidence); AGENT
    nodes hold an excerpt of the same literal (``system_prompt_excerpt`` and
    ``*_preview`` extras).
    """
    for node in doc.nodes:
        if node.component_type in _PROMPT_BEARING_TYPES:
            redact_node(node)
