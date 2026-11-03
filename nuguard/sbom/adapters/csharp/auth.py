"""C# auth adapter — detects JWT, OAuth2, and IdentityServer structural patterns.

Covers:
- JWT Bearer authentication (Microsoft.AspNetCore.Authentication.JwtBearer)
- OAuth2 / OpenID Connect (Microsoft.Identity.Web, IdentityServer4, Duende.IdentityServer)
- ASP.NET Core [Authorize] / [AllowAnonymous] attributes
- ASP.NET Core authentication middleware (AddJwtBearer, AddOAuth, etc.)

Detection is namespace-import driven with attribute-level structural detection
for authorization decorators.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._csharp_base import CSharpFrameworkAdapter
from ._source import _TOKEN_RE, CSharpCall, find_calls, mask_non_code, statement_tail

# ---------------------------------------------------------------------------
# Namespace → (provider, display_name)
# ---------------------------------------------------------------------------

_NAMESPACE_MAP: dict[str, tuple[str, str]] = {
    "Microsoft.AspNetCore.Authentication.JwtBearer": ("jwt_bearer", "JWT Bearer Auth"),
    "Microsoft.AspNetCore.Authentication": ("aspnet_auth", "ASP.NET Core Auth"),
    "Microsoft.AspNetCore.Authorization": ("aspnet_authz", "ASP.NET Core Authorization"),
    "Microsoft.Identity.Web": ("ms_identity_web", "Microsoft.Identity.Web"),
    "Microsoft.Identity.Client": ("msal", "MSAL"),
    "IdentityServer4": ("identityserver4", "IdentityServer4"),
    "Duende.IdentityServer": ("duende_identityserver", "Duende IdentityServer"),
    "System.IdentityModel.Tokens.Jwt": ("jwt_system", "System.IdentityModel.Tokens.Jwt"),
    "Microsoft.AspNetCore.Authentication.Cookies": ("cookie_auth", "Cookie Auth"),
    "Microsoft.AspNetCore.Authentication.Google": ("oauth_google", "Google OAuth"),
    "Microsoft.AspNetCore.Authentication.Facebook": ("oauth_facebook", "Facebook OAuth"),
    "Microsoft.AspNetCore.Authentication.MicrosoftAccount": ("oauth_microsoft", "Microsoft OAuth"),
    "Microsoft.AspNetCore.Authentication.Twitter": ("oauth_twitter", "Twitter OAuth"),
}

# Registration-call patterns: method_name → (provider, auth_kind)
_REGISTRATION_CALLS: dict[str, tuple[str, str]] = {
    "AddJwtBearer": ("jwt_bearer", "jwt"),
    "AddOAuth": ("oauth", "oauth"),
    "AddOpenIdConnect": ("oidc", "oidc"),
    "AddGoogle": ("oauth_google", "oauth"),
    "AddFacebook": ("oauth_facebook", "oauth"),
    "AddMicrosoftAccount": ("oauth_microsoft", "oauth"),
    "AddTwitter": ("oauth_twitter", "oauth"),
    "AddCookie": ("cookie_auth", "cookie"),
    "AddIdentity": ("identity", "identity"),
    "AddIdentityCore": ("identity", "identity"),
    "AddAuthentication": ("aspnet_auth", "middleware"),
    "AddAuthorization": ("aspnet_authz", "middleware"),
}

# Class-instantiation patterns (constructor calls)
_CLASS_MAP: dict[str, tuple[str, str]] = {
    "JwtBearerOptions": ("jwt_bearer", "jwt"),
    "JwtSecurityToken": ("jwt_system", "jwt"),
    "JwtSecurityTokenHandler": ("jwt_system", "jwt"),
}

# auth_kind -> SBOM auth_type. JWT nodes share the ``auth:jwt`` canonical name of
# the ``auth_jwt`` regex fallback so both evidence kinds merge into one node.
_AUTH_TYPES = {"jwt": "jwt", "oauth": "oauth2", "oidc": "oauth2"}
_SCHEME_CHAIN_RE = re.compile(
    r"\s*\.\s*(?P<name>"
    + "|".join(name for name, (_, kind) in _REGISTRATION_CALLS.items() if kind != "middleware")
    + r")\s*\("
)
# The Add* auth extension methods live in Microsoft.Extensions.DependencyInjection,
# which Web SDK implicit usings supply; a provider-namespace import is not required.
_HOST_NAMESPACES = ("Microsoft.Extensions.DependencyInjection", "Microsoft.AspNetCore.Builder")
_SAFE_LITERALS = frozenset(
    f'"{scheme}"'
    for scheme in (
        "Bearer",
        "Cookies",
        "OpenIdConnect",
        "oidc",
        "Google",
        "Facebook",
        "MicrosoftAccount",
        "Twitter",
        "Identity.Application",
    )
)
_SNIPPET_TOKEN_RE = re.compile(
    "|".join(re.escape(t) for t in ('"<redacted>"', "'?'", *sorted(_SAFE_LITERALS)))
)

# Attribute patterns for structural authorization detection
_AUTHORIZE_ATTR_RE = re.compile(
    r"\[(?:[^\]]*\b)(Authorize|AllowAnonymous)(?:\([^\]]*\))?(?:[^\]]*)\]"
)


class CSharpAuthAdapter(CSharpFrameworkAdapter):
    """Detect authentication and authorization patterns in C# source files."""

    name = "csharp_auth"
    priority = 42  # After framework adapters, before generic regex
    handles_namespaces = [*_NAMESPACE_MAP, *_HOST_NAMESPACES]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(content, file_path, parse_result)
        code = mask_non_code(content)

        # Collect imported providers — sort by length descending so more-specific
        # namespaces (e.g. Authentication.Google) match before their parents.
        imported_providers: set[str] = set()
        provider_lines: dict[str, int] = {}
        sorted_ns = sorted(_NAMESPACE_MAP, key=len, reverse=True)
        for directive in result.using_directives:
            ns = directive.namespace.removeprefix("global::")
            for prefix in sorted_ns:
                provider, _ = _NAMESPACE_MAP[prefix]
                if ns == prefix or ns.startswith(prefix + "."):
                    imported_providers.add(provider)
                    provider_lines.setdefault(provider, directive.line)
                    break

        host_imported = bool(imported_providers & {"aspnet_auth", "aspnet_authz"}) or any(
            directive.namespace.removeprefix("global::") in _HOST_NAMESPACES
            for directive in result.using_directives
        )

        def registration_allowed(provider: str) -> bool:
            return (
                host_imported
                or provider in imported_providers
                or provider in ("aspnet_auth", "aspnet_authz", "oauth", "oidc")
            )

        detections: list[ComponentDetection] = []
        seen_providers: set[str] = set()

        # ---- Registration-call detection (high confidence) ----
        calls = find_calls(content, set(_REGISTRATION_CALLS) | set(_CLASS_MAP))

        for call in calls:
            # Check registration calls
            reg_mapping = _REGISTRATION_CALLS.get(call.name)
            if reg_mapping:
                provider, auth_kind = reg_mapping
                if not registration_allowed(provider):
                    continue

                # AddAuthentication(...).AddJwtBearer(...) is reported once, as the scheme.
                chained = (
                    _SCHEME_CHAIN_RE.match(mask_non_code(statement_tail(content, call.end)))
                    if call.name == "AddAuthentication"
                    else None
                )
                if chained and registration_allowed(_REGISTRATION_CALLS[chained["name"]][0]):
                    seen_providers.add(provider)
                    continue

                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name=_canonical(provider, auth_kind),
                        display_name=_provider_display(provider, call.name),
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata=_metadata(provider, auth_kind),
                        file_path=file_path,
                        line=call.line,
                        snippet=_redacted_snippet(content, call),
                        evidence_kind="ast_call",
                    )
                )
                seen_providers.add(provider)
                continue

            # Check class instantiation calls
            cls_mapping = _CLASS_MAP.get(call.name)
            if cls_mapping:
                provider, auth_kind = cls_mapping
                if provider not in imported_providers:
                    continue

                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.AUTH,
                        canonical_name=_canonical(provider, auth_kind),
                        display_name=_provider_display(provider, call.name),
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata=_metadata(provider, auth_kind),
                        file_path=file_path,
                        line=call.line,
                        snippet=_redacted_snippet(content, call),
                        evidence_kind="ast_call",
                    )
                )
                seen_providers.add(provider)

        # ---- [Authorize] / [AllowAnonymous] attribute detection ----
        for match in _AUTHORIZE_ATTR_RE.finditer(code):
            attr_name = match.group(1)
            canonical = canonicalize_text("auth:aspnet_authz_attribute")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    canonical_name=canonical,
                    display_name=f"[{attr_name}]",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.88,
                    metadata={
                        "auth_kind": "attribute",
                        "attribute": attr_name,
                        "provider": "aspnet_authz",
                        "framework": "csharp_auth",
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=0,
                    snippet=f"[{attr_name}]",
                    evidence_kind="ast_attribute",
                )
            )
            seen_providers.add("aspnet_authz")

        # ---- Import-only fallback (low confidence) ----
        for provider in imported_providers:
            # MSAL acquires tokens for outbound calls; an import alone says nothing
            # about how the app authenticates its own callers.
            if provider in seen_providers or provider == "msal":
                continue
            display = _provider_display(provider, provider)
            canonical = canonicalize_text(f"auth:{provider}")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.AUTH,
                    canonical_name=canonical,
                    display_name=display,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.70,
                    metadata={
                        "auth_kind": "import",
                        "provider": provider,
                        "framework": "csharp_auth",
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=provider_lines.get(provider, 0),
                    snippet=f"using {_provider_namespace(provider)}",
                    evidence_kind="ast_import",
                )
            )

        return _dedupe(detections)


def _redacted_snippet(content: str, call: CSharpCall) -> str:
    """Return the call snippet with literals and comments redacted.

    Falls back to a shape-only ``Name(…)`` snippet when the tokenizer may have
    lost sync (raw/interpolated literals it cannot model), so no literal text leaks.
    """
    desync = False

    def redact(match: re.Match[str]) -> str:
        nonlocal desync
        token = match.group(0)
        if match["line_comment"] or match["block_comment"]:
            return " "
        if match["char"]:
            return "'?'"
        if match["raw"]:
            body = token.lstrip("$")
            nxt = match.string[match.end() : match.end() + 1]
            desync |= len(body) - len(body.lstrip('"')) != 3 or nxt == '"'
        if token.startswith("$"):
            holes = token.replace("{{", "").replace("}}", "")
            desync |= holes.count("{") != holes.count("}")
        return token if token in _SAFE_LITERALS else '"<redacted>"'

    text = _TOKEN_RE.sub(redact, content[call.start : call.end])
    residue = _SNIPPET_TOKEN_RE.sub("\0", text)
    if desync or "'" in residue or '"' in residue or re.search(r"\w\0|\0[\w\0]", residue):
        return f"{call.name}(…)"
    return " ".join(text.split())[:240]


def _provider_display(provider: str, default: str) -> str:
    return next((d for _, (p, d) in _NAMESPACE_MAP.items() if p == provider), default)


def _canonical(provider: str, auth_kind: str) -> str:
    return "auth:jwt" if auth_kind == "jwt" else canonicalize_text(f"auth:{provider}")


def _metadata(provider: str, auth_kind: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "auth_kind": auth_kind,
        "provider": provider,
        "framework": "csharp_auth",
        "language": "csharp",
    }

    if auth_kind in _AUTH_TYPES:
        metadata["auth_type"] = _AUTH_TYPES[auth_kind]

    return metadata


def _provider_namespace(provider: str) -> str:
    """Return the primary namespace for a provider key."""
    for ns, (p, _) in _NAMESPACE_MAP.items():
        if p == provider:
            return ns
    return provider


def _dedupe(detections: list[ComponentDetection]) -> list[ComponentDetection]:
    seen: set[tuple[ComponentType, str]] = set()
    result: list[ComponentDetection] = []
    for detection in detections:
        key = (detection.component_type, detection.canonical_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(detection)
    return result


__all__ = ["CSharpAuthAdapter"]
