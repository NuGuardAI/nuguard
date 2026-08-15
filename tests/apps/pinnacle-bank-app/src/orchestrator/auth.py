"""
FinTech GOAT — Authentication Module (Security-Hardened)
=========================================================
Previously vulnerable. All issues listed below have been fixed.

Fixed issues:
  VULN-AUTH-01 (FIXED): alg:none JWT bypass — decode_token now only accepts HS256.
  VULN-AUTH-02 (FIXED): Weak hardcoded secret removed — JWT_SECRET env var required;
                         falls back to an ephemeral random secret on missing env var.
  VULN-AUTH-03 (FIXED): Sensitive fields (balance, kyc_level, risk_score) removed
                         from JWT payload.
  VULN-AUTH-04 (FIXED): Plaintext passwords replaced with PBKDF2-HMAC-SHA256 hashes
                         generated at module load time.
  VULN-AUTH-05 (FIXED): Login returns a unified generic error — no user enumeration.
  VULN-AUTH-06 (FIXED): Refresh tokens now carry a 30-day expiry and are consumed
                         on redemption (single-use rotation).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets as _secrets
import time
import uuid
from typing import Any

logger = logging.getLogger("orchestrator.auth")

# ---------------------------------------------------------------------------
# JWT signing secret — must be set via JWT_SECRET env var.
# Falls back to an ephemeral random secret (tokens invalidated on restart).
# ---------------------------------------------------------------------------
_jwt_secret_env = os.getenv("JWT_SECRET")
if _jwt_secret_env:
    _JWT_SECRET: str = _jwt_secret_env
else:
    _JWT_SECRET = _secrets.token_hex(32)
    logger.warning(
        "JWT_SECRET env var is not set — using an ephemeral random secret. "
        "All tokens will be invalidated on service restart."
    )

# ---------------------------------------------------------------------------
# User store — passwords stored as static PBKDF2-HMAC-SHA256 hashes.
# Format: "<16-byte-hex-salt>:<sha256-hex-digest>" (260,000 iterations)
# ---------------------------------------------------------------------------
_USER_STORE: dict[str, dict] = {
    "alice": {
        "user_id":       "alice",
        "email":         "alice.johnson@pinnaclebank.com",
        "password_hash": "11cdae2365c70d938e415fae1b000973:4888c278de6f34337f462ed48d16f1739555b52bf6c937d9517f3dee27d12cbf",
        "name":          "Alice Johnson",
        "role":          "customer",
        "kyc_level":     2,
        "risk_score":    15,
        "checking":      50000.00,
        "savings":       18420.55,
        "investments":   37834.90,
    },
    "bob": {
        "user_id":       "bob",
        "email":         "bob.martinez@pinnaclebank.com",
        "password_hash": "06bc587158b80b7d8be3852cc26d62d3:d853f7f98005cd5afda9f1b633c09b2cf19ff14f73b6e77df68397afb3b1e4c2",
        "name":          "Bob Martinez",
        "role":          "customer",
        "kyc_level":     1,
        "risk_score":    42,
        "checking":      12500.00,
        "savings":       3250.00,
        "investments":   8100.00,
    },
    "carol": {
        "user_id":       "carol",
        "email":         "carol.williams@pinnaclebank.com",
        "password_hash": "97e6f4b42cac9beb56784d5c85db78ed:acb95bcbaa42bf7bab73dba9cc8ef3723d2a588f0177e1a7041c696912bcaf0b",
        "name":          "Carol Williams",
        "role":          "customer",
        "kyc_level":     3,
        "risk_score":    8,
        "checking":      250000.00,
        "savings":       92750.00,
        "investments":   184500.00,
    },
    "david": {
        "user_id":       "david",
        "email":         "david.chen@pinnaclebank.com",
        "password_hash": "3bdb0384afeb39d5b1e3454aabbc5dea:0642228dc80b4ebc8f2136a86df566161aabc74d028d37f2992c592ed6174bd1",
        "name":          "David Chen",
        "role":          "customer",
        "kyc_level":     1,
        "risk_score":    67,
        "checking":      8750.00,
        "savings":       1200.00,
        "investments":   3000.00,
    },
    "eve": {
        "user_id":       "eve",
        "email":         "eve.thompson@pinnaclebank.com",
        "password_hash": "00ca30b1e136a8587070836f82d44fd4:29320429da4209f137c71e15705ba0c471821b5249c586700a59593db8ee7d48",
        "name":          "Eve Thompson",
        "role":          "customer",
        "kyc_level":     3,
        "risk_score":    12,
        "checking":      125000.00,
        "savings":       45000.00,
        "investments":   89000.00,
    },
    "admin": {
        "user_id":       "admin",
        "email":         "admin@pinnaclebank.com",
        "password_hash": "097467b4a33e03954e0abf77244f6f43:3755f84e8dc7872d8f576cb47bcead5593c174bed46c74ee19725a711f900011",
        "name":          "System Administrator",
        "role":          "admin",
        "kyc_level":     99,
        "risk_score":    0,
        "checking":      0.0,
        "savings":       0.0,
        "investments":   0.0,
    },
}

# Email → user_id index
_EMAIL_INDEX: dict[str, str] = {v["email"]: k for k, v in _USER_STORE.items()}

# ---------------------------------------------------------------------------
# Refresh token store — 30-day expiry, single-use rotation on redemption.
# ---------------------------------------------------------------------------
_REFRESH_TOKENS: dict[str, dict] = {}  # token_value → {"user_id": str, "expires_at": float}


# ---------------------------------------------------------------------------
# JWT utilities — manual HS256 + alg:none implementation
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign_hs256(header_b64: str, payload_b64: str, secret: str) -> str:
    msg = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return _b64url_encode(sig)


def create_access_token(user_id: str) -> str:
    """Create a signed HS256 JWT containing only the subject and role."""
    user = _USER_STORE.get(user_id, {})
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub":  user_id,
        "iat":  now,
        "exp":  now + 3600,
        "role": user.get("role", "customer"),
    }
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = _sign_hs256(h, p, _JWT_SECRET)
    return f"{h}.{p}.{sig}"


def create_refresh_token(user_id: str) -> str:
    """Issue and persist a refresh token with a 30-day expiry."""
    token = str(uuid.uuid4())
    _REFRESH_TOKENS[token] = {
        "user_id":    user_id,
        "expires_at": time.time() + 30 * 86400,
    }
    return token


def validate_refresh_token(token: str) -> str | None:
    """Validate a refresh token, consuming it (single-use rotation).

    Returns the user_id if valid, or None if the token is missing or expired.
    """
    entry = _REFRESH_TOKENS.get(token)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        del _REFRESH_TOKENS[token]
        return None
    # Consume the token — caller must issue a new one
    del _REFRESH_TOKENS[token]
    return entry["user_id"]


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a JWT.

    VULN-AUTH-01: If the JWT header declares alg='none' (or 'NONE', 'None', etc.)
    the signature segment is ignored completely.  An attacker can forge a token
    with any payload — including role='admin' or a different 'sub' — and it will
    be accepted as valid.

    Forge example (Python):
        import base64, json
        def b64u(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()
        header  = b64u({"alg": "none", "typ": "JWT"})
        payload = b64u({"sub": "admin", "role": "admin", "exp": 9999999999})
        forged  = header + "." + payload + "."
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header  = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
        alg = header.get("alg", "")

        # Only accept HS256 — reject alg:none and any other algorithm.
        if alg.lower() != "hs256":
            logger.warning(
                "JWT with unsupported algorithm '%s' rejected for sub=%s",
                alg, payload.get("sub"),
            )
            return None

        # HS256 path — verify signature with constant-time compare
        expected = _sign_hs256(parts[0], parts[1], _JWT_SECRET)
        if not hmac.compare_digest(expected, parts[2]):
            logger.warning("JWT signature mismatch for sub=%s", payload.get("sub"))
            return None

        # Expiry check
        if payload.get("exp", 0) < time.time():
            logger.debug("JWT expired for sub=%s", payload.get("sub"))
            return None

        return payload

    except Exception as exc:
        logger.debug("JWT decode error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# User lookup helpers
# ---------------------------------------------------------------------------

def lookup_by_email(email: str) -> dict | None:
    uid = _EMAIL_INDEX.get(email.lower())
    return _USER_STORE.get(uid) if uid else None


def lookup_by_id(user_id: str) -> dict | None:
    return _USER_STORE.get(user_id)


def verify_password(user: dict, password: str) -> bool:
    """Verify a password against the stored PBKDF2-HMAC-SHA256 hash."""
    stored = user.get("password_hash", "")
    if ":" not in stored:
        return False
    salt, expected_hash = stored.split(":", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return hmac.compare_digest(expected_hash, dk.hex())
