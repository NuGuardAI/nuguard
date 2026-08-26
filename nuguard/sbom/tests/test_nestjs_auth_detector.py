"""Unit tests for NestJSAuthTSAdapter (docs/sbom-fix2.md #2).

Regression coverage: AUTH node evidence must point at the real
instantiation/signing/verification call site, never an `import` line or a
DTO field reference — and Apple Sign-In's JWKS mechanism must get its own
distinctly named node instead of having no counterpart at all.
"""

from __future__ import annotations

from nuguard.sbom.adapters.typescript.auth_detector import NestJSAuthTSAdapter
from nuguard.sbom.types import ComponentType

_ADAPTER = NestJSAuthTSAdapter()


def _auth_nodes(code: str, file_path: str = "auth.service.ts") -> list:
    dets = _ADAPTER.extract(code, file_path, None)
    return [d for d in dets if d.component_type == ComponentType.AUTH]


class TestOAuth2EvidencePrefersCallSiteOverImportOrDto:
    def test_evidence_line_is_verify_id_token_call_not_import_or_dto(self) -> None:
        code = (
            "import { OAuth2Client } from 'google-auth-library';\n"
            "\n"
            "interface LoginDto {\n"
            "  oauth2Token: string;\n"
            "}\n"
            "\n"
            "export class AuthService {\n"
            "  private client = new OAuth2Client(GOOGLE_CLIENT_ID);\n"
            "\n"
            "  async verifyGoogle(token: string) {\n"
            "    const ticket = await this.client.verifyIdToken({ idToken: token });\n"
            "    return ticket.getPayload();\n"
            "  }\n"
            "}\n"
        )
        nodes = _auth_nodes(code)
        oauth = next(n for n in nodes if n.metadata["auth_type"] == "oauth2")

        assert "import" not in oauth.snippet.lower()
        assert "oauth2Token" not in oauth.snippet
        assert "verifyIdToken" in oauth.snippet


class TestAppleSignInJwksGetsDistinctNode:
    def test_apple_jwks_uri_produces_apple_named_node(self) -> None:
        code = (
            "import jwksClient from 'jwks-rsa';\n"
            "const client = jwksClient({ jwksUri: 'https://appleid.apple.com/auth/keys' });\n"
        )
        nodes = _auth_nodes(code)
        assert any(n.canonical_name == "auth:apple-sign-in-jwks" for n in nodes), nodes

    def test_non_apple_jwks_uri_gets_generic_node(self) -> None:
        code = (
            "import jwksClient from 'jwks-rsa';\n"
            "const client = jwksClient({ jwksUri: 'https://example.com/.well-known/jwks.json' });\n"
        )
        nodes = _auth_nodes(code)
        assert not any(n.canonical_name == "auth:apple-sign-in-jwks" for n in nodes), nodes
        assert any(n.metadata["auth_type"] == "jwks" for n in nodes), nodes


class TestJwtSigningEvidence:
    def test_generate_tokens_method_with_sign_call_detected(self) -> None:
        code = (
            "import * as jwt from 'jsonwebtoken';\n"
            "export class AuthService {\n"
            "  generateTokens(userId: string) {\n"
            "    const accessToken = jwt.sign({ sub: userId }, this.secret);\n"
            "    return { accessToken };\n"
            "  }\n"
            "}\n"
        )
        nodes = _auth_nodes(code)
        jwt_nodes = [n for n in nodes if n.metadata["auth_type"] == "jwt"]
        assert len(jwt_nodes) == 1
        assert ".sign(" in jwt_nodes[0].snippet

    def test_no_signing_call_in_method_body_no_jwt_node(self) -> None:
        code = (
            "import * as jwt from 'jsonwebtoken';\n"
            "export class AuthService {\n"
            "  generateTokens(userId: string) {\n"
            "    return this.tokenStore.lookup(userId);\n"
            "  }\n"
            "}\n"
        )
        nodes = _auth_nodes(code)
        assert not any(n.metadata["auth_type"] == "jwt" for n in nodes), nodes

    def test_nestjs_jwt_service_sign_async_detected(self) -> None:
        """Real-world NestJS apps commonly use @nestjs/jwt's JwtService.signAsync(),
        not the bare jsonwebtoken package's jwt.sign() — must match too."""
        code = (
            "import { JwtService } from '@nestjs/jwt';\n"
            "export class AuthService {\n"
            "  private async generateTokens(userId: string) {\n"
            "    const accessToken = await this.jwtService.signAsync(payload, opts);\n"
            "    return { accessToken };\n"
            "  }\n"
            "}\n"
        )
        nodes = _auth_nodes(code)
        jwt_nodes = [n for n in nodes if n.metadata["auth_type"] == "jwt"]
        assert len(jwt_nodes) == 1
        assert "signAsync(" in jwt_nodes[0].snippet


class TestCanonicalNamesMergeWithGenericKeywordAdapters:
    """OAuth2/JWT nodes must share the same canonical_name as registry.py's
    generic `auth_oauth`/`auth_jwt` keyword RegexAdapters, so this detector's
    real-call-site evidence merges into (and, via higher confidence, ranks
    above) the generic adapter's first-keyword-match evidence instead of
    coexisting as a second, redundant AUTH node for the same mechanism."""

    def test_jwt_canonical_matches_generic_auth_jwt_adapter(self) -> None:
        from nuguard.sbom.adapters.base import RegexAdapter
        from nuguard.sbom.adapters.registry import default_registry

        generic = next(
            a
            for a in default_registry()
            if isinstance(a, RegexAdapter) and a.name == "auth_jwt"
        )
        code = (
            "import * as jwt from 'jsonwebtoken';\n"
            "export class AuthService {\n"
            "  generateTokens(userId: string) {\n"
            "    return jwt.sign({ sub: userId }, this.secret);\n"
            "  }\n"
            "}\n"
        )
        [jwt_node] = [n for n in _auth_nodes(code) if n.metadata["auth_type"] == "jwt"]
        assert jwt_node.canonical_name == generic.canonical_name

    def test_oauth2_canonical_matches_generic_auth_oauth_adapter(self) -> None:
        from nuguard.sbom.adapters.base import RegexAdapter
        from nuguard.sbom.adapters.registry import default_registry

        generic = next(
            a
            for a in default_registry()
            if isinstance(a, RegexAdapter) and a.name == "auth_oauth"
        )
        code = (
            "import { OAuth2Client } from 'google-auth-library';\n"
            "export class AuthService {\n"
            "  private client = new OAuth2Client(GOOGLE_CLIENT_ID);\n"
            "}\n"
        )
        [oauth_node] = [n for n in _auth_nodes(code) if n.metadata["auth_type"] == "oauth2"]
        assert oauth_node.canonical_name == generic.canonical_name
