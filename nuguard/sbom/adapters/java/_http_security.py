"""Spring Security HTTP authorization rules for the Java web adapter.

A ``SecurityFilterChain`` bean (or the legacy
``WebSecurityConfigurerAdapter.configure(HttpSecurity)``) declares which request paths need which
authority. This module parses those ordered rules into one ``http-security`` AUTH detection per
chain and evaluates them against endpoint paths, so the extractor can set ``auth_required``,
``no_auth_required`` and ``auth_scope`` on Spring endpoints (NuGuardAI/nuguard#654).

Only rules that can be read statically are applied: string-literal Ant patterns with an optional
``HttpMethod`` and one of ``permitAll``, ``authenticated``, ``hasRole``, ``hasAnyRole``,
``hasAuthority``, ``hasAnyAuthority`` or ``denyAll``. Anything else is recorded on the AUTH node
(``http_security_unsupported_rules``) and stops evaluation for the endpoints it could affect
instead of being guessed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ...core.java_parser import JavaMethodDeclaration, JavaTypeDeclaration
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection

HTTP_SECURITY_AUTH_TYPE = "http-security"
STYLE_BEAN = "SecurityFilterChain"
STYLE_LEGACY = "WebSecurityConfigurerAdapter"

_LOWEST_PRECEDENCE = 2**31 - 1
_HIGHEST_PRECEDENCE = -(2**31)

# Calls whose arguments hold the authorization rules; the scanner descends into them.
_DESCEND_CALLS = frozenset({"authorizeHttpRequests", "authorizeRequests"})
# Calls that limit which requests a chain handles.
_SCOPE_CALLS = frozenset({"securityMatcher", "antMatcher"})
_UNSUPPORTED_SCOPE_CALLS = frozenset({"securityMatchers", "requestMatcher"})
_MATCHER_CALLS = frozenset({"requestMatchers", "antMatchers", "mvcMatchers"})
_ACCESS_CALLS = frozenset(
    {
        "permitAll",
        "authenticated",
        "denyAll",
        "hasRole",
        "hasAnyRole",
        "hasAuthority",
        "hasAnyAuthority",
    }
)
_UNSUPPORTED_ACCESS_CALLS = frozenset(
    {"access", "hasIpAddress", "anonymous", "rememberMe", "fullyAuthenticated", "not"}
)
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"})
# formLogin()/logout() URL settings. permitAll() on those configurers makes the URLs public outside
# the authorization rules, so endpoints at these paths are never decided from the rules.
_RESERVED_URL_CALLS = frozenset(
    {"loginPage", "loginProcessingUrl", "failureUrl", "logoutUrl", "logoutSuccessUrl"}
)

_CALL_RE = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\(")
_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_HTTP_METHOD_ARG_RE = re.compile(r"(?:[\w.]+\.)?HttpMethod\.([A-Z]+)")
_REGEX_VARIABLE_RE = re.compile(r"\{[^}]*:")
_PATH_VARIABLE_RE = re.compile(r"\{[^{}]*\}")
_ORDER_RE = re.compile(r"@(?:[\w.]+\.)?Order\s*\((.*)\)\s*$", re.DOTALL)


# --------------------------------------------------------------------------------------------
# Source scanning
# --------------------------------------------------------------------------------------------


def _skip_quoted(text: str, start: int) -> int:
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return len(text)


def _skip_trivia(text: str, i: int) -> int | None:
    """Return the index after the string, char literal or comment starting at ``i``."""
    if text[i] in "\"'":
        return _skip_quoted(text, i)
    if text.startswith("//", i):
        end = text.find("\n", i)
        return len(text) if end < 0 else end + 1
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end < 0 else end + 2
    return None


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    i = open_index
    while i < len(text):
        skipped = _skip_trivia(text, i)
        if skipped is not None:
            i = skipped
            continue
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_args(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(text):
        skipped = _skip_trivia(text, i)
        if skipped is not None:
            i = skipped
            continue
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _flat_calls(text: str) -> list[tuple[str, str]]:
    """Return ``(name, arguments)`` for each ``.name(...)`` call, in source order.

    Arguments of ``authorizeHttpRequests`` / ``authorizeRequests`` are scanned too (that is where
    the rules live); the arguments of every other call are skipped so that nested configuration
    such as ``formLogin(login -> login.permitAll())`` is not mistaken for a rule.
    """
    calls: list[tuple[str, str]] = []
    i = 0
    while i < len(text):
        skipped = _skip_trivia(text, i)
        if skipped is not None:
            i = skipped
            continue
        if text[i] == ".":
            match = _CALL_RE.match(text, i)
            if match:
                open_index = match.end() - 1
                close = _matching_paren(text, open_index)
                if close < 0:
                    break
                name = match.group(1)
                calls.append((name, text[open_index + 1 : close]))
                i = open_index + 1 if name in _DESCEND_CALLS else close + 1
                continue
        i += 1
    return calls


def _all_calls(text: str) -> list[tuple[str, str]]:
    """Like ``_flat_calls`` but descends into the arguments of every call."""
    calls: list[tuple[str, str]] = []
    i = 0
    while i < len(text):
        skipped = _skip_trivia(text, i)
        if skipped is not None:
            i = skipped
            continue
        if text[i] == ".":
            match = _CALL_RE.match(text, i)
            if match:
                open_index = match.end() - 1
                close = _matching_paren(text, open_index)
                if close < 0:
                    break
                calls.append((match.group(1), text[open_index + 1 : close]))
                i = open_index + 1
                continue
        i += 1
    return calls


def _literal(arg: str) -> str | None:
    match = _STRING_RE.fullmatch(arg.strip())
    return match.group(1) if match else None


# --------------------------------------------------------------------------------------------
# Ant pattern matching
# --------------------------------------------------------------------------------------------


def _segment_regex(segment: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "{" and segment.find("}", i) > i:
            out.append("[^/]+")
            i = segment.find("}", i)
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def ant_regex(pattern: str) -> re.Pattern[str] | None:
    """Compile an Ant pattern (``**``, ``*``, ``?``, ``{var}``); None when it is not supported."""
    if _REGEX_VARIABLE_RE.search(pattern):
        return None
    segments = [segment for segment in pattern.split("/") if segment]
    if not segments:
        return re.compile("/")
    parts: list[str] = []
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        if segment == "**" or segment.startswith("{*"):
            parts.append("(?:/.*)?" if index == last else "(?:/[^/]+)*")
        else:
            parts.append("/" + _segment_regex(segment))
    return re.compile("".join(parts))


def _normalize_path(path: str) -> str:
    """Treat each ``{variable}`` as one ordinary segment and ignore trailing slashes."""
    cleaned = _PATH_VARIABLE_RE.sub("pathvar", path.strip())
    return "/" + "/".join(segment for segment in cleaned.split("/") if segment)


def _path_matches(pattern: str, normalized_path: str) -> bool:
    regex = ant_regex(pattern)
    return regex is not None and regex.fullmatch(normalized_path) is not None


def ant_match(pattern: str, path: str) -> bool:
    """True when the endpoint ``path`` matches the Ant ``pattern`` (False if unsupported)."""
    return _path_matches(pattern, _normalize_path(path))


# --------------------------------------------------------------------------------------------
# Rule parsing
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedChain:
    rules: list[dict[str, Any]]
    scope: list[str]
    scope_supported: bool
    unsupported: int
    reserved: list[str]


def _reserved_paths(body: str) -> list[str]:
    """URLs that ``formLogin()`` and ``logout()`` serve (explicit literals plus the defaults)."""
    calls = _all_calls(body)
    names = {name for name, _ in calls}
    paths: set[str] = set()
    if "formLogin" in names:
        paths.update({"/login", "/logout"})
    if "logout" in names:
        paths.add("/logout")
    for name, args in calls:
        if name not in _RESERVED_URL_CALLS:
            continue
        items = _split_args(args)
        value = _literal(items[0]) if items else None
        if value:
            path = value.split("?", 1)[0]
            if path.startswith("/"):
                paths.add(path)
    return sorted(paths)


def _parse_matcher_args(args: str) -> tuple[list[str], list[str], bool]:
    items = _split_args(args)
    methods: list[str] = []
    if items:
        method_match = _HTTP_METHOD_ARG_RE.fullmatch(items[0])
        if method_match and method_match.group(1) in _HTTP_METHODS:
            methods = [method_match.group(1)]
            items = items[1:]
    patterns: list[str] = []
    for item in items:
        value = _literal(item)
        if value is None or ant_regex(value) is None:
            return [], [], False
        patterns.append(value)
    if not patterns:
        if not methods:
            return [], [], False
        patterns = ["/**"]
    return methods, patterns, True


def _literal_patterns(args: str) -> list[str] | None:
    items = _split_args(args)
    patterns: list[str] = []
    for item in items:
        value = _literal(item)
        if value is None or ant_regex(value) is None:
            return None
        patterns.append(value)
    return patterns or None


def _new_pending(name: str, args: str) -> dict[str, Any]:
    if name == "anyRequest":
        return {
            "kind": "any_request",
            "methods": [],
            "patterns": ["/**"],
            "matcher_supported": True,
        }
    if name in _MATCHER_CALLS:
        methods, patterns, ok = _parse_matcher_args(args)
        return {
            "kind": "matchers",
            "methods": methods,
            "patterns": patterns,
            "matcher_supported": ok,
        }
    return {"kind": "matchers", "methods": [], "patterns": [], "matcher_supported": False}


def _parse_access(name: str, args: str) -> tuple[str, list[str], list[str], bool]:
    """Return ``(access, roles, authorities, supported)`` for a terminal call."""
    items = _split_args(args)
    literals = [_literal(item) for item in items]
    values = [value for value in literals if value is not None]
    all_literal = len(values) == len(items)
    if name == "permitAll" and not items:
        return "permit_all", [], [], True
    if name == "authenticated" and not items:
        return "authenticated", [], [], True
    if name == "denyAll" and not items:
        return "deny_all", [], [], True
    if name == "hasRole" and len(values) == 1 and all_literal:
        return "has_role", values, [], True
    if name == "hasAnyRole" and values and all_literal:
        return "has_any_role", values, [], True
    if name == "hasAuthority" and len(values) == 1 and all_literal:
        return "has_authority", [], values, True
    if name == "hasAnyAuthority" and values and all_literal:
        return "has_any_authority", [], values, True
    return "unsupported", [], [], False


def _finish(
    pending: dict[str, Any],
    call: str,
    access: str,
    roles: list[str],
    authorities: list[str],
    access_supported: bool,
) -> dict[str, Any]:
    return {
        "kind": pending["kind"],
        "methods": list(pending["methods"]),
        "patterns": list(pending["patterns"]),
        "matcher_supported": bool(pending["matcher_supported"]),
        "access": access,
        "roles": roles,
        "authorities": authorities,
        "supported": bool(pending["matcher_supported"]) and access_supported,
        "call": call,
    }


def _unfinished(pending: dict[str, Any]) -> dict[str, Any]:
    return _finish(pending, "", "unsupported", [], [], False)


def parse_chain(body: str) -> ParsedChain | None:
    """Parse the rules of one chain body; None when it configures no authorization at all."""
    calls = _flat_calls(body)
    if not any(name in _DESCEND_CALLS for name, _ in calls):
        return None
    rules: list[dict[str, Any]] = []
    scope: list[str] = []
    scope_supported = True
    pending: dict[str, Any] | None = None
    for name, args in calls:
        if name in _SCOPE_CALLS:
            patterns = _literal_patterns(args)
            if patterns is None:
                scope_supported = False
            else:
                scope.extend(patterns)
            continue
        if name in _UNSUPPORTED_SCOPE_CALLS:
            scope_supported = False
            continue
        if name in _MATCHER_CALLS or name == "anyRequest" or name.endswith(("Matchers", "Matcher")):
            if pending is not None:
                rules.append(_unfinished(pending))
            pending = _new_pending(name, args)
            continue
        if pending is None:
            continue
        if name in _ACCESS_CALLS or name in _UNSUPPORTED_ACCESS_CALLS:
            access, roles, authorities, ok = _parse_access(name, args)
            rules.append(_finish(pending, name, access, roles, authorities, ok))
            pending = None
    if pending is not None:
        rules.append(_unfinished(pending))
    if not rules:
        # authorizeHttpRequests(...) with an argument we could not read (a Customizer variable).
        rules.append(
            _finish(
                {"kind": "unknown", "methods": [], "patterns": [], "matcher_supported": False},
                "authorizeHttpRequests",
                "unsupported",
                [],
                [],
                False,
            )
        )
    unsupported = sum(1 for rule in rules if not rule["supported"])
    return ParsedChain(
        rules=rules,
        scope=scope,
        scope_supported=scope_supported,
        unsupported=unsupported,
        reserved=_reserved_paths(body),
    )


# --------------------------------------------------------------------------------------------
# AUTH detections
# --------------------------------------------------------------------------------------------


def _simple_type_name(name: str) -> str:
    return name.split("<", 1)[0].strip().rsplit(".", 1)[-1]


def _chain_style(method: JavaMethodDeclaration, owner: JavaTypeDeclaration | None) -> str | None:
    if _simple_type_name(method.return_type or "") == "SecurityFilterChain":
        return STYLE_BEAN
    if (
        method.name == "configure"
        and any("HttpSecurity" in parameter for parameter in method.parameters)
        and owner is not None
        and any(_simple_type_name(base) == STYLE_LEGACY for base in owner.base_types)
    ):
        return STYLE_LEGACY
    return None


def _order_value(annotations: Sequence[str]) -> int | None:
    for annotation in annotations:
        match = _ORDER_RE.match(annotation.strip())
        if not match:
            continue
        raw = match.group(1).strip()
        named = re.fullmatch(r"value\s*=\s*(.+)", raw)
        if named:
            raw = named.group(1).strip()
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        if raw.endswith("HIGHEST_PRECEDENCE"):
            return _HIGHEST_PRECEDENCE
        if raw.endswith("LOWEST_PRECEDENCE"):
            return _LOWEST_PRECEDENCE
    return None


def http_security_detections(
    *,
    methods: Sequence[JavaMethodDeclaration],
    types: Mapping[str, JavaTypeDeclaration],
    file_path: str,
    framework: str,
    adapter_name: str,
    priority: int,
) -> list[ComponentDetection]:
    """Return one ``http-security`` AUTH detection per chain declared in the file."""
    detections: list[ComponentDetection] = []
    for method in methods:
        owner = types.get(method.containing_type or "")
        style = _chain_style(method, owner)
        if style is None or not method.body:
            continue
        parsed = parse_chain(method.body)
        if parsed is None:
            continue
        if style == STYLE_BEAN:
            order = _order_value(method.annotations)
        else:
            order = _order_value(owner.annotations if owner is not None else ())
        owner_name = method.containing_type or PurePosixPath(file_path).stem
        canonical = canonicalize_text(f"java-http-security:{file_path}:{owner_name}:{method.name}")
        detections.append(
            ComponentDetection(
                component_type=ComponentType.AUTH,
                canonical_name=canonical,
                display_name=f"{owner_name} HTTP security",
                adapter_name=adapter_name,
                priority=priority,
                confidence=0.9,
                metadata={
                    "framework": framework,
                    "language": "java",
                    "auth_type": HTTP_SECURITY_AUTH_TYPE,
                    "http_security_style": style,
                    "http_security_rules": parsed.rules,
                    "http_security_unsupported_rules": parsed.unsupported,
                    "http_security_scope": parsed.scope,
                    "http_security_scope_supported": parsed.scope_supported,
                    "http_security_reserved_paths": parsed.reserved,
                    "http_security_order": order,
                    "http_security_file": file_path,
                    "http_security_line": method.line,
                },
                file_path=file_path,
                line=method.line,
                snippet=method.signature[:160],
                evidence_kind="ast_decorator",
            )
        )
    return detections


# --------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpSecurityChain:
    key: Any
    order: int | None
    file: str
    line: int
    scope: tuple[str, ...]
    scope_supported: bool
    rules: tuple[Mapping[str, Any], ...]
    reserved: tuple[str, ...] = ()


@dataclass(frozen=True)
class HttpSecurityDecision:
    key: Any
    rule_index: int
    access: str
    roles: tuple[str, ...]
    authorities: tuple[str, ...]


def chain_from_extras(key: Any, extras: Mapping[str, Any]) -> HttpSecurityChain | None:
    """Rebuild a chain from an AUTH node's extras; None for any other node."""
    if extras.get("auth_type") != HTTP_SECURITY_AUTH_TYPE:
        return None
    rules = extras.get("http_security_rules")
    if not isinstance(rules, list):
        return None
    scope = extras.get("http_security_scope")
    reserved = extras.get("http_security_reserved_paths")
    order = extras.get("http_security_order")
    line = extras.get("http_security_line")
    return HttpSecurityChain(
        key=key,
        order=order if isinstance(order, int) and not isinstance(order, bool) else None,
        file=str(extras.get("http_security_file") or ""),
        line=line if isinstance(line, int) and not isinstance(line, bool) else 0,
        scope=tuple(str(item) for item in scope) if isinstance(scope, list) else (),
        scope_supported=bool(extras.get("http_security_scope_supported", True)),
        rules=tuple(rule for rule in rules if isinstance(rule, dict)),
        reserved=tuple(str(item) for item in reserved) if isinstance(reserved, list) else (),
    )


def _evaluate_rules(
    chain: HttpSecurityChain, method: str | None, path: str
) -> HttpSecurityDecision | None:
    for index, rule in enumerate(chain.rules):
        if not rule.get("matcher_supported"):
            # We cannot tell whether this rule would catch the endpoint, so a later rule would be
            # a guess.
            return None
        patterns = [str(pattern) for pattern in rule.get("patterns") or []]
        if not any(_path_matches(pattern, path) for pattern in patterns):
            continue
        rule_methods = [str(item).upper() for item in rule.get("methods") or []]
        if rule_methods:
            endpoint_method = (method or "").upper()
            if endpoint_method not in _HTTP_METHODS:
                return None  # e.g. @RequestMapping without a method: do not guess
            if endpoint_method not in rule_methods:
                continue
        if not rule.get("supported"):
            return None  # the rule matches but we cannot read what it requires
        return HttpSecurityDecision(
            key=chain.key,
            rule_index=index,
            access=str(rule.get("access")),
            roles=tuple(str(item) for item in rule.get("roles") or []),
            authorities=tuple(str(item) for item in rule.get("authorities") or []),
        )
    return None


def evaluate_http_security(
    chains: Sequence[HttpSecurityChain], method: str | None, path: str | None
) -> HttpSecurityDecision | None:
    """Decide the access rule for one endpoint, or None when it cannot be decided.

    Chains are tried in ``@Order`` order (explicit values first), then file path and line. The
    first chain whose ``securityMatcher`` covers the path is the only one consulted, and inside it
    the first matching rule wins, as in Spring Security.
    """
    if not path:
        return None
    normalized = _normalize_path(path)
    ordered = sorted(
        chains,
        key=lambda chain: (
            chain.order if chain.order is not None else _LOWEST_PRECEDENCE,
            chain.file,
            chain.line,
        ),
    )
    for chain in ordered:
        if not chain.scope_supported:
            return None
        if chain.scope and not any(_path_matches(pattern, normalized) for pattern in chain.scope):
            continue
        if any(_path_matches(pattern, normalized) for pattern in chain.reserved):
            return None  # served by the formLogin()/logout() filters; may be public via permitAll()
        return _evaluate_rules(chain, method, normalized)
    return None
