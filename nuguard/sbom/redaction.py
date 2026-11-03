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
   before the host ends it).
4. **Bearer / Basic credentials** - ``Bearer <token>`` / ``Basic <b64>``
   (8+ characters). A plain lowercase or capitalised word is kept, so prose
   such as "Bearer authentication" stays readable.
5. **Credential key/value pairs** - ``key=value``, prose ``key: value`` and
   JSON ``"key": "value"`` where the key is a credential name (password,
   passwd, pass, pwd, psw, secret, token, api key, access token, client
   secret, private key, account key, shared access key/signature, sig, ...).
   The key may carry a prefix (``DB_PASSWORD=``). An unquoted value runs to
   ``;``, ``&``, ``,``, a quote or whitespace (a trailing ``.`` is treated as
   the end of the sentence) - or, for ``key=`` only, when a run of
   space-separated words ends at ``;`` (a connection string), to that ``;``.

To avoid wrecking ordinary prompt text, a key/value value is kept when it is
*trivial*:

- a template placeholder (``{count}``, ``{{token}}``, ``${TOKEN}``,
  ``<token>``, ``%s``, ``$TOKEN``) or an already redacted marker;
- a sentinel word (``none``, ``null``, ``true``, ``required``, ``optional``,
  ``string``, ``hidden``, ...) or a run of one repeated mask character
  (``***``, ``xxxx``);
- purely numeric and shorter than 8 digits (``max_token: 4096``);
- for the prose ``key: value`` form with an unquoted value only: letters
  alone and shorter than 16 characters (``password: required``,
  ``token: the user's session``). A real credential in that form usually
  carries a digit or symbol, or is long.

Not handled: credentials stated in prose without a ``:``/``=`` separator
("the password is hunter2") and unknown key formats with no credential key
in front of them.
"""

from __future__ import annotations

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
    r"(?P<prefix>(?<![a-z0-9])" + _CREDENTIAL_KEY + r"(?![a-z0-9])"
    r"[\"']?\s*(?P<sep>[:=])(?![=>])[^\S\r\n]*)"
    r"(?P<value>"
    r"\"[^\"\r\n]*\"|'[^'\r\n]*'"  # quoted (JSON or code)
    r"|(?<==)[^\s;&,\"'<>=]+(?:[^\S\r\n]+[^\s;&,\"'<>=]+){0,8}(?=;)"  # key=spaced value;
    r"|[^\s;&,\"'<>]*[^\s;&,\"'<>.]"  # single token (a trailing "." ends the sentence)
    r")",
    re.IGNORECASE,
)

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
        return True
    return False


def _redact_kv(match: re.Match[str]) -> str:
    raw = match.group("value")
    if _is_trivial_value(raw, prose=match.group("sep") == ":"):
        return match.group(0)
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return f"{match.group('prefix')}{raw[0]}{REDACTED}{raw[0]}"
    return f"{match.group('prefix')}{REDACTED}"


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
    return _CREDENTIAL_KV_RE.sub(_redact_kv, text)


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
