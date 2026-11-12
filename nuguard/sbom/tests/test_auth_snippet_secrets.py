"""Go and TypeScript AUTH evidence must not carry raw credential literals (issue #733).

Mirrors ``test_csharp_auth_adapter.py`` (#722) and ``test_prompt_redaction.py`` (#731):
adapter-level tests check the detection fields directly, and one pipeline test per language
proves a constructed secret never reaches ``AiSbomDocument.model_dump_json()``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go.auth import GoJWTAdapter, GoOAuth2Adapter
from nuguard.sbom.adapters.typescript.auth_detector import NestJSAuthTSAdapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.types import ComponentType

REDACTED = "<redacted>"


def _auth(detections: list[ComponentDetection]) -> list[ComponentDetection]:
    return [d for d in detections if d.component_type == ComponentType.AUTH]


def _serialized(detections: list[ComponentDetection]) -> str:
    """Every field of every detection (snippet, metadata, canonical name, ...)."""
    return json.dumps([dataclasses.asdict(d) for d in detections], default=str)


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

_GO_OAUTH2 = """package main

import (
    "golang.org/x/oauth2"
    "golang.org/x/oauth2/google"
)

var conf = &oauth2.Config{
    ClientID:     "GoClientIdValue",
    ClientSecret: "GoOAuthSecretValue", // rotate: GoCommentSecret
    RedirectURL:  "http://localhost:8080/callback",
    Scopes:       []string{"email"},
    Endpoint:     google.Endpoint,
}
"""

_GO_OAUTH2_NEW_CLIENT = """package main

import (
    "context"
    "net/http"

    "golang.org/x/oauth2"
)

func client(ctx context.Context) *http.Client {
    return oauth2.NewClient(ctx, oauth2.StaticTokenSource(
        &oauth2.Token{AccessToken: "GoStaticAccessToken"},
    ))
}
"""

_GO_JWT_INTERPRETED = """package main

import "github.com/golang-jwt/jwt/v5"

func parse(tokenString string) (*jwt.Token, error) {
    return jwt.Parse(tokenString, func(t *jwt.Token) (interface{}, error) {
        return []byte("GoJwtKeyValue"), nil
    })
}
"""

_GO_JWT_RAW = (
    "package main\n\n"
    'import "github.com/golang-jwt/jwt/v5"\n\n'
    "func parse(tokenString string) (*jwt.Token, error) {\n"
    "    return jwt.Parse(tokenString, func(t *jwt.Token) (interface{}, error) {\n"
    "        return []byte(`GoRawBacktickKey`), nil\n"
    "    })\n"
    "}\n"
)


def _go(adapter: GoJWTAdapter | GoOAuth2Adapter, source: str) -> list[ComponentDetection]:
    return _auth(adapter.extract(source, "main.go", parse_go(source, "main.go")))


def test_go_oauth2_config_literals_are_redacted() -> None:
    detections = _go(GoOAuth2Adapter(), _GO_OAUTH2)

    assert detections, "expected an AUTH detection for the oauth2.Config literal"
    dumped = _serialized(detections)
    for secret in ("GoClientIdValue", "GoOAuthSecretValue", "GoCommentSecret"):
        assert secret not in dumped
    snippet = detections[0].snippet
    assert "oauth2.Config{" in snippet
    assert f'ClientSecret: "{REDACTED}"' in snippet


def test_go_oauth2_new_client_literals_are_redacted() -> None:
    detections = _go(GoOAuth2Adapter(), _GO_OAUTH2_NEW_CLIENT)

    assert detections, "expected an AUTH detection for oauth2.NewClient"
    assert "GoStaticAccessToken" not in _serialized(detections)
    assert "oauth2.NewClient(" in detections[0].snippet


@pytest.mark.parametrize(
    ("source", "secret"),
    [(_GO_JWT_INTERPRETED, "GoJwtKeyValue"), (_GO_JWT_RAW, "GoRawBacktickKey")],
)
def test_go_jwt_callback_key_literals_are_redacted(source: str, secret: str) -> None:
    detections = _go(GoJWTAdapter(), source)

    assert detections, "expected an AUTH detection for jwt.Parse"
    assert secret not in _serialized(detections)
    assert f'[]byte("{REDACTED}")' in detections[0].snippet


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------

_TS_OAUTH2 = (
    "import { OAuth2Client } from 'google-auth-library';\n\n"
    "const client = new OAuth2Client('TsClientIdValue', 'TsOAuthSecretValue', "
    "'http://localhost/cb');\n"
)

_TS_JWKS = (
    "import jwksClient from 'jwks-rsa';\n\n"
    "const jwks = jwksClient({ jwksUri: "
    "'https://example.com/.well-known/jwks.json?api_key=TsJwksApiKeyValue' });\n"
)

_TS_JWKS_APPLE = (
    "import jwksClient from 'jwks-rsa';\n\n"
    "const jwks = jwksClient({ jwksUri: 'https://appleid.apple.com/auth/keys' });\n"
)

_TS_JWT = """import { Injectable } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';

@Injectable()
export class AuthService {
  constructor(private readonly jwtService: JwtService) {}

  async generateTokens(userId: string) {
    const accessToken = this.jwtService.sign({ sub: userId }, { secret: 'TsJwtSigningSecret' });
    return { accessToken };
  }
}
"""


def _ts(source: str) -> list[ComponentDetection]:
    return _auth(NestJSAuthTSAdapter().extract(source, "auth.service.ts", None))


def test_ts_oauth2_client_literals_are_redacted() -> None:
    detections = _ts(_TS_OAUTH2)

    assert detections, "expected an AUTH detection for new OAuth2Client"
    dumped = _serialized(detections)
    assert "TsClientIdValue" not in dumped
    assert "TsOAuthSecretValue" not in dumped
    assert "new OAuth2Client(" in detections[0].snippet
    assert REDACTED in detections[0].snippet


def test_ts_jwks_uri_credentials_are_redacted_everywhere() -> None:
    detections = _ts(_TS_JWKS)

    assert len(detections) == 1
    detection = detections[0]
    safe_uri = f"https://example.com/.well-known/jwks.json?api_key={REDACTED}"
    assert "TsJwksApiKeyValue" not in _serialized(detections)
    assert detection.canonical_name == f"auth:jwks:{safe_uri}"
    assert detection.metadata["auth_detail"]["jwks_uri"] == safe_uri
    assert "jwksClient(" in detection.snippet


def test_ts_jwks_without_credentials_keeps_its_identity() -> None:
    detections = _ts(_TS_JWKS_APPLE)

    assert len(detections) == 1
    assert detections[0].canonical_name == "auth:apple-sign-in-jwks"
    jwks_uri = detections[0].metadata["auth_detail"]["jwks_uri"]
    assert jwks_uri == "https://appleid.apple.com/auth/keys"


def test_ts_jwt_sign_literals_are_redacted() -> None:
    detections = _ts(_TS_JWT)

    assert detections, "expected an AUTH detection for the jwtService.sign call"
    assert "TsJwtSigningSecret" not in _serialized(detections)
    assert f'secret: "{REDACTED}"' in detections[0].snippet


# ---------------------------------------------------------------------------
# Pipeline: nothing leaks into the serialized SBOM
# ---------------------------------------------------------------------------

_PROJECTS: dict[str, dict[str, str]] = {
    "go": {
        "go.mod": (
            "module example.com/app\n\ngo 1.22\n\n"
            "require (\n    golang.org/x/oauth2 v0.21.0\n"
            "    github.com/golang-jwt/jwt/v5 v5.2.1\n)\n"
        ),
        "oauth.go": _GO_OAUTH2,
        "jwt.go": _GO_JWT_INTERPRETED,
    },
    "typescript": {
        "package.json": json.dumps(
            {
                "name": "app",
                "dependencies": {
                    "google-auth-library": "^9.0.0",
                    "jwks-rsa": "^3.0.0",
                    "@nestjs/jwt": "^10.0.0",
                },
            }
        ),
        "oauth.ts": _TS_OAUTH2,
        "jwks.ts": _TS_JWKS,
        "auth.service.ts": _TS_JWT,
    },
}

_SECRETS: dict[str, tuple[str, ...]] = {
    "go": ("GoClientIdValue", "GoOAuthSecretValue", "GoCommentSecret", "GoJwtKeyValue"),
    "typescript": (
        "TsClientIdValue",
        "TsOAuthSecretValue",
        "TsJwksApiKeyValue",
        "TsJwtSigningSecret",
    ),
}


@pytest.mark.parametrize("language", ["go", "typescript"])
def test_pipeline_auth_secrets_absent_from_sbom_json(language: str, tmp_path: Path) -> None:
    for name, source in _PROJECTS[language].items():
        (tmp_path / name).write_text(source, encoding="utf-8")

    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(enable_llm=False))

    assert any(n.component_type == ComponentType.AUTH for n in doc.nodes), (
        f"{language}: expected at least one AUTH node"
    )
    dumped = doc.model_dump_json()
    for secret in _SECRETS[language]:
        assert secret not in dumped, f"{language}: {secret!r} leaked into the SBOM"
    assert REDACTED in dumped
