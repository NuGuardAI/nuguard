"""Spring Security HTTP rules are applied to Spring endpoints (NuGuardAI/nuguard#654)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.adapters.java import JavaWebAdapter
from nuguard.sbom.adapters.java._http_security import (
    HTTP_SECURITY_AUTH_TYPE,
    ant_match,
    ant_regex,
    parse_chain,
    source_module,
)
from nuguard.sbom.core.java_parser import parse_java
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType

_CONTROLLER = """package demo;

import org.springframework.web.bind.annotation.*;

@RestController
public class ApiController {
    @GetMapping("/public/health")
    public String health() { return "ok"; }

    @PostMapping("/admin/users")
    public String createUser() { return "created"; }

    @GetMapping("/admin/stats")
    public String stats() { return "stats"; }

    @GetMapping("/api/orders/{id}")
    public String order(@PathVariable String id) { return id; }
}
"""

_LEGACY_CONTROLLER = """package demo;

import org.springframework.web.bind.annotation.*;

@RestController
public class LegacyController {
    @RequestMapping("/legacy/ping")
    public String ping() { return "pong"; }
}
"""

_LOGIN_CONTROLLER = """package demo;

import org.springframework.web.bind.annotation.*;

@RestController
public class LoginController {
    @GetMapping("/login")
    public String login() { return "login"; }

    @GetMapping("/signin")
    public String signin() { return "signin"; }

    @GetMapping("/bye")
    public String bye() { return "bye"; }
}
"""

_SECURED_CONTROLLER = """package demo;

import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
public class SecuredController {
    @PreAuthorize("hasRole('ADMIN')")
    @GetMapping("/public/secret")
    public String secret() { return "secret"; }
}
"""

_CONFIG = """package demo;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
public class SecurityConfig {
    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http.authorizeHttpRequests(auth -> auth
RULES);
        return http.build();
    }
}
"""

_TWO_CHAINS = """package demo;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
public class SecurityConfig {
    @Bean
    ORDER_REST
    public SecurityFilterChain restChain(HttpSecurity http) throws Exception {
        http.authorizeHttpRequests(auth -> auth.anyRequest().permitAll());
        return http.build();
    }

    @Bean
    ORDER_API
    public SecurityFilterChain apiChain(HttpSecurity http) throws Exception {
        http.securityMatcher("/api/**")
            .authorizeHttpRequests(auth -> auth.anyRequest().hasRole("USER"));
        return http.build();
    }
}
"""

_LEGACY_CONFIG = """package demo;

import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.*;

@Configuration
public class LegacySecurity extends WebSecurityConfigurerAdapter {
    @Override
    protected void configure(HttpSecurity http) throws Exception {
        http.authorizeRequests()
            .antMatchers("/public/**").permitAll()
            .antMatchers("/admin/**").hasRole("ADMIN")
            .anyRequest().authenticated()
            .and().formLogin().permitAll();
    }
}
"""

_REPRO_RULES = """            .requestMatchers("/public/**").permitAll()
            .requestMatchers(HttpMethod.POST, "/admin/**").hasRole("ADMIN")
            .anyRequest().authenticated()"""


def _config(rules: str) -> str:
    return _CONFIG.replace("RULES", rules)


def _scan(root: Path, files: dict[str, str]) -> AiSbomDocument:
    for name, source in files.items():
        path = root / "src" / "main" / "java" / "demo" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return AiSbomExtractor().extract_from_path(root, AiSbomConfig(enable_llm=False))


def _endpoints(document: AiSbomDocument) -> dict[str, Node]:
    return {
        node.name: node
        for node in document.nodes
        if node.component_type == ComponentType.API_ENDPOINT
    }


def _chains(document: AiSbomDocument) -> list[Node]:
    return [
        node
        for node in document.nodes
        if node.component_type == ComponentType.AUTH
        and node.metadata.extras.get("auth_type") == HTTP_SECURITY_AUTH_TYPE
    ]


def _protected(document: AiSbomDocument) -> set[str]:
    """Names of the endpoints that an http-security AUTH node PROTECTS."""
    chain_ids = {node.id for node in _chains(document)}
    names = {node.id: node.name for node in document.nodes}
    return {
        names[edge.target]
        for edge in document.edges
        if edge.source in chain_ids
        and str(getattr(edge.relationship_type, "value", edge.relationship_type)) == "PROTECTS"
    }


def _assert_unchanged(node: Node) -> None:
    assert node.metadata.auth_required is False
    assert node.metadata.no_auth_required is None
    assert node.metadata.auth_scope is None


@pytest.fixture(scope="module")
def repro(tmp_path_factory: pytest.TempPathFactory) -> AiSbomDocument:
    return _scan(
        tmp_path_factory.mktemp("repro"),
        {"SecurityConfig.java": _config(_REPRO_RULES), "ApiController.java": _CONTROLLER},
    )


def test_permit_all_marks_matching_endpoint_public(repro: AiSbomDocument) -> None:
    health = _endpoints(repro)["GET /public/health"]
    assert health.metadata.auth_required is False
    assert health.metadata.no_auth_required is True
    assert health.metadata.auth_scope is None
    assert health.metadata.extras["auth_source"] == HTTP_SECURITY_AUTH_TYPE
    assert "GET /public/health" not in _protected(repro)


def test_has_role_sets_scope_and_protects_edge(repro: AiSbomDocument) -> None:
    create = _endpoints(repro)["POST /admin/users"]
    assert create.metadata.auth_required is True
    assert create.metadata.no_auth_required is False
    assert create.metadata.auth_scope == "ADMIN"
    assert create.metadata.extras["auth_required"] is True
    assert "POST /admin/users" in _protected(repro)


def test_any_request_covers_endpoints_no_other_rule_matches(repro: AiSbomDocument) -> None:
    order = _endpoints(repro)["GET /api/orders/{id}"]
    assert order.metadata.auth_required is True
    assert order.metadata.no_auth_required is False
    assert order.metadata.auth_scope is None
    assert "GET /api/orders/{id}" in _protected(repro)


def test_method_qualified_rule_ignores_other_methods(repro: AiSbomDocument) -> None:
    stats = _endpoints(repro)["GET /admin/stats"]
    assert stats.metadata.auth_required is True
    assert stats.metadata.auth_scope is None  # the POST-only ADMIN rule does not apply to GET


def test_chain_node_records_rules_in_order(repro: AiSbomDocument) -> None:
    chains = _chains(repro)
    assert len(chains) == 1
    extras = chains[0].metadata.extras
    assert [(rule["kind"], rule["access"]) for rule in extras["http_security_rules"]] == [
        ("matchers", "permit_all"),
        ("matchers", "has_role"),
        ("any_request", "authenticated"),
    ]
    assert extras["http_security_rules"][1]["methods"] == ["POST"]
    assert extras["http_security_unsupported_rules"] == 0
    assert extras["http_security_style"] == "SecurityFilterChain"
    assert chains[0].metadata.auth_type == HTTP_SECURITY_AUTH_TYPE


def test_has_any_role_and_authority_scopes(tmp_path: Path) -> None:
    rules = """            .requestMatchers("/admin/**").hasAnyRole("ADMIN", "AUDITOR")
            .requestMatchers("/api/**").hasAuthority("SCOPE_orders")
            .anyRequest().permitAll()"""
    document = _scan(tmp_path, {"SecurityConfig.java": _config(rules), "A.java": _CONTROLLER})
    endpoints = _endpoints(document)
    assert endpoints["POST /admin/users"].metadata.auth_scope == "ADMIN, AUDITOR"
    assert endpoints["GET /api/orders/{id}"].metadata.auth_scope == "SCOPE_orders"
    assert endpoints["GET /public/health"].metadata.no_auth_required is True


@pytest.mark.parametrize(
    ("rules", "expected_required"),
    [
        (
            '.requestMatchers("/public/**").authenticated()'
            '.requestMatchers("/public/health").permitAll()',
            True,
        ),
        (
            '.requestMatchers("/public/health").permitAll()'
            '.requestMatchers("/public/**").authenticated()',
            False,
        ),
    ],
)
def test_first_matching_rule_wins(tmp_path: Path, rules: str, expected_required: bool) -> None:
    document = _scan(tmp_path, {"SecurityConfig.java": _config(rules), "A.java": _CONTROLLER})
    health = _endpoints(document)["GET /public/health"]
    assert health.metadata.auth_required is expected_required
    assert (health.metadata.no_auth_required is True) is (not expected_required)


@pytest.mark.parametrize("pattern", ["/api/orders/*", "/api/orders/{id}", "/api/**"])
def test_path_variable_counts_as_one_segment(tmp_path: Path, pattern: str) -> None:
    rules = f'            .requestMatchers("{pattern}").hasRole("USER").anyRequest().denyAll()'
    document = _scan(tmp_path, {"SecurityConfig.java": _config(rules), "A.java": _CONTROLLER})
    order = _endpoints(document)["GET /api/orders/{id}"]
    assert order.metadata.auth_required is True
    assert order.metadata.auth_scope == "USER"


def test_legacy_web_security_configurer_adapter(tmp_path: Path) -> None:
    document = _scan(
        tmp_path,
        {
            "LegacySecurity.java": _LEGACY_CONFIG,
            "A.java": _CONTROLLER,
            "L.java": _LOGIN_CONTROLLER,
        },
    )
    endpoints = _endpoints(document)
    assert endpoints["GET /public/health"].metadata.no_auth_required is True
    # formLogin() serves /login and permitAll() makes it public, so it is not claimed protected
    _assert_unchanged(endpoints["GET /login"])
    assert endpoints["POST /admin/users"].metadata.auth_scope == "ADMIN"
    assert endpoints["GET /api/orders/{id}"].metadata.auth_required is True
    chains = _chains(document)
    assert [c.metadata.extras["http_security_style"] for c in chains] == [
        "WebSecurityConfigurerAdapter"
    ]
    # `.formLogin().permitAll()` is not an authorization rule and must not be counted as one.
    assert chains[0].metadata.extras["http_security_unsupported_rules"] == 0


def test_form_login_and_logout_urls_are_left_unchanged(tmp_path: Path) -> None:
    config = _CONFIG.replace(
        "RULES);",
        "            .anyRequest().authenticated())\n"
        '            .formLogin(form -> form.loginPage("/signin").permitAll())\n'
        '            .logout(logout -> logout.logoutSuccessUrl("/bye"));',
    )
    document = _scan(
        tmp_path,
        {"SecurityConfig.java": config, "A.java": _CONTROLLER, "L.java": _LOGIN_CONTROLLER},
    )
    endpoints = _endpoints(document)
    for name in ("GET /signin", "GET /login", "GET /bye"):
        _assert_unchanged(endpoints[name])
    assert endpoints["GET /api/orders/{id}"].metadata.auth_required is True
    extras = _chains(document)[0].metadata.extras
    assert extras["http_security_reserved_paths"] == ["/bye", "/login", "/logout", "/signin"]
    assert _protected(document) == {
        "GET /public/health",
        "POST /admin/users",
        "GET /admin/stats",
        "GET /api/orders/{id}",
    }


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "http.authorizeHttpRequests(a -> a.anyRequest().authenticated())"
            '.formLogin(f -> f.loginPage("/signin").failureUrl("/signin?error").permitAll());',
            ["/login", "/logout", "/signin"],
        ),
        (
            "http.authorizeRequests().anyRequest().authenticated()"
            '.and().formLogin().loginProcessingUrl("/do-login")'
            '.and().logout().logoutUrl("/do-logout");',
            ["/do-login", "/do-logout", "/login", "/logout"],
        ),
        ("http.authorizeHttpRequests(a -> a.anyRequest().authenticated());", []),
    ],
)
def test_reserved_paths_come_from_form_login_and_logout(body: str, expected: list[str]) -> None:
    parsed = parse_chain(body)
    assert parsed is not None
    assert parsed.reserved == expected


def test_unsupported_rules_are_counted_and_never_guessed(tmp_path: Path) -> None:
    rules = """            .requestMatchers("/public/**").permitAll()
            .requestMatchers("/admin/**").access(customManager)
            .regexMatchers("/orders/.*").authenticated()
            .anyRequest().authenticated()"""
    document = _scan(tmp_path, {"SecurityConfig.java": _config(rules), "A.java": _CONTROLLER})
    endpoints = _endpoints(document)
    assert endpoints["GET /public/health"].metadata.no_auth_required is True
    # shadowed by an access(...) rule we cannot read
    _assert_unchanged(endpoints["POST /admin/users"])
    # an unsupported matcher stops evaluation, so anyRequest() is not reached
    _assert_unchanged(endpoints["GET /api/orders/{id}"])
    assert _chains(document)[0].metadata.extras["http_security_unsupported_rules"] == 2
    assert _protected(document) == set()


def test_unreadable_customizer_is_one_unsupported_rule(tmp_path: Path) -> None:
    config = _CONFIG.replace(
        "http.authorizeHttpRequests(auth -> auth\nRULES);", "http.authorizeHttpRequests(custom);"
    )
    document = _scan(tmp_path, {"SecurityConfig.java": config, "A.java": _CONTROLLER})
    assert _chains(document)[0].metadata.extras["http_security_unsupported_rules"] == 1
    for endpoint in _endpoints(document).values():
        _assert_unchanged(endpoint)


def test_endpoint_matching_no_supported_rule_keeps_previous_values(tmp_path: Path) -> None:
    rules = '            .requestMatchers("/admin/**").hasRole("ADMIN")'
    document = _scan(tmp_path, {"SecurityConfig.java": _config(rules), "A.java": _CONTROLLER})
    endpoints = _endpoints(document)
    _assert_unchanged(endpoints["GET /public/health"])
    _assert_unchanged(endpoints["GET /api/orders/{id}"])
    assert endpoints["POST /admin/users"].metadata.auth_scope == "ADMIN"


def test_permit_all_chain_creates_no_protects_edges(tmp_path: Path) -> None:
    rules = "            .anyRequest().permitAll()"
    document = _scan(tmp_path, {"SecurityConfig.java": _config(rules), "A.java": _CONTROLLER})
    assert len(_chains(document)) == 1
    assert _protected(document) == set()
    for endpoint in _endpoints(document).values():
        assert endpoint.metadata.no_auth_required is True


def test_method_level_security_is_never_downgraded(tmp_path: Path) -> None:
    rules = """            .requestMatchers("/public/**").permitAll()
            .anyRequest().authenticated()"""
    document = _scan(
        tmp_path, {"SecurityConfig.java": _config(rules), "S.java": _SECURED_CONTROLLER}
    )
    secret = _endpoints(document)["GET /public/secret"]
    assert secret.metadata.auth_required is True
    assert secret.metadata.no_auth_required is not True


def test_endpoint_without_http_method_is_not_guessed(tmp_path: Path) -> None:
    rules = """            .requestMatchers(HttpMethod.POST, "/legacy/**").hasRole("ADMIN")
            .anyRequest().permitAll()"""
    document = _scan(
        tmp_path, {"SecurityConfig.java": _config(rules), "L.java": _LEGACY_CONTROLLER}
    )
    ping = _endpoints(document)["ANY /legacy/ping"]
    _assert_unchanged(ping)


def test_security_matcher_and_order_select_the_chain(tmp_path: Path) -> None:
    source = _TWO_CHAINS.replace("ORDER_REST", "@Order(2)").replace("ORDER_API", "@Order(1)")
    document = _scan(tmp_path, {"SecurityConfig.java": source, "A.java": _CONTROLLER})
    endpoints = _endpoints(document)
    order = endpoints["GET /api/orders/{id}"]
    assert order.metadata.auth_required is True
    assert order.metadata.auth_scope == "USER"
    assert endpoints["GET /public/health"].metadata.no_auth_required is True
    assert endpoints["POST /admin/users"].metadata.no_auth_required is True
    assert sorted(c.metadata.extras["http_security_order"] for c in _chains(document)) == [1, 2]


def test_file_order_is_used_without_explicit_order(tmp_path: Path) -> None:
    source = _TWO_CHAINS.replace("ORDER_REST", "").replace("ORDER_API", "")
    document = _scan(tmp_path, {"SecurityConfig.java": source, "A.java": _CONTROLLER})
    # restChain comes first in the file and has no securityMatcher, so it matches everything and
    # apiChain is never consulted.
    order = _endpoints(document)["GET /api/orders/{id}"]
    assert order.metadata.no_auth_required is True
    assert order.metadata.auth_scope is None


def test_chain_without_authorization_config_emits_no_node(tmp_path: Path) -> None:
    config = _CONFIG.replace(
        "http.authorizeHttpRequests(auth -> auth\nRULES);", "http.csrf(csrf -> csrf.disable());"
    )
    document = _scan(tmp_path, {"SecurityConfig.java": config, "A.java": _CONTROLLER})
    assert _chains(document) == []
    for endpoint in _endpoints(document).values():
        _assert_unchanged(endpoint)


def test_config_without_controllers_still_emits_the_chain_node(tmp_path: Path) -> None:
    document = _scan(tmp_path, {"SecurityConfig.java": _config(_REPRO_RULES)})
    assert len(_chains(document)) == 1
    assert _endpoints(document) == {}


def test_adapter_detection_metadata() -> None:
    source = _TWO_CHAINS.replace("ORDER_REST", "@Order(2)").replace("ORDER_API", "@Order(1)")
    path = "src/main/java/demo/SecurityConfig.java"
    detections = JavaWebAdapter().extract(source, path, parse_java(source, path))
    chains: list[dict[str, Any]] = [
        dict(d.metadata)
        for d in detections
        if d.component_type == ComponentType.AUTH
        and d.metadata.get("auth_type") == HTTP_SECURITY_AUTH_TYPE
    ]
    assert sorted(c["http_security_order"] for c in chains) == [1, 2]
    scoped = [c for c in chains if c["http_security_scope"]]
    assert [c["http_security_scope"] for c in scoped] == [["/api/**"]]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/public/**", "/public/a/b", True),
        ("/public/**", "/public", True),
        ("/public/**", "/publicity", False),
        ("/a/*/c", "/a/b/c", True),
        ("/a/*/c", "/a/b/d/c", False),
        ("/a/**/c", "/a/c", True),
        ("/a/**/c", "/a/b/d/c", True),
        ("/a/?", "/a/b", True),
        ("/a/?", "/a/bc", False),
        ("/items/{id}", "/items/{id}", True),
        ("/items/*", "/items/{id}", True),
        ("/**", "/", True),
        ("/a/b", "/a/c", False),
        ("/a/{id:[0-9]+}", "/a/1", False),
    ],
)
def test_ant_match(pattern: str, path: str, expected: bool) -> None:
    assert ant_match(pattern, path) is expected


# --------------------------------------------------------------------------------------------
# NuGuardAI/nuguard#774: hostile patterns, unreadable @Order, several Spring modules
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/a/**/**/**/c", "/a/b/d/c", True),
        ("/a/**/**/**/c", "/a/c", True),
        ("/a/{*rest}/**", "/a/b/c", True),
        ("/x**y", "/xay", True),
    ],
)
def test_consecutive_wildcards_collapse(pattern: str, path: str, expected: bool) -> None:
    assert ant_regex(pattern) is not None
    assert ant_match(pattern, path) is expected


@pytest.mark.parametrize(
    "pattern",
    [
        "/a*b*c*d",  # three "*" runs in one segment
        "/**/a/**/b/**/c/**/d",  # four "**" segments
    ],
)
def test_patterns_past_the_wildcard_limits_are_unsupported(pattern: str) -> None:
    assert ant_regex(pattern) is None
    assert ant_match(pattern, "/a/b/c/d") is False


def test_worst_supported_pattern_matches_quickly() -> None:
    pattern = "/**/a*a/**/a*a/**/b"
    path = "/" + "/".join(["a" * 30] * 40) + "/c"
    started = time.perf_counter()
    assert ant_match(pattern, path) is False
    assert time.perf_counter() - started < 1.0


def test_hostile_pattern_rule_is_unsupported_and_not_guessed(tmp_path: Path) -> None:
    rules = """            .requestMatchers("/a*b*c*d").permitAll()
            .anyRequest().authenticated()"""
    document = _scan(tmp_path, {"SecurityConfig.java": _config(rules), "A.java": _CONTROLLER})
    (chain,) = _chains(document)
    assert chain.metadata.extras["http_security_unsupported_rules"] == 1
    for endpoint in _endpoints(document).values():
        _assert_unchanged(endpoint)


def test_unreadable_order_leaves_overlapping_endpoints_undecided(tmp_path: Path) -> None:
    source = _TWO_CHAINS.replace("ORDER_REST", "@Order(2)").replace(
        "ORDER_API", "@Order(SecurityOrders.API)"
    )
    document = _scan(tmp_path, {"SecurityConfig.java": source, "A.java": _CONTROLLER})
    unreadable = {
        c.metadata.extras["http_security_order"]: c.metadata.extras[
            "http_security_order_unreadable"
        ]
        for c in _chains(document)
    }
    assert unreadable == {2: False, None: True}
    endpoints = _endpoints(document)
    # Both chains cover /api/**, and the order between them cannot be read.
    _assert_unchanged(endpoints["GET /api/orders/{id}"])
    # Only restChain covers /public/**, so the unreadable order does not matter there.
    assert endpoints["GET /public/health"].metadata.no_auth_required is True


_REST_ONLY = _TWO_CHAINS.split("    @Bean\n    ORDER_API")[0].replace("ORDER_REST", "") + "}\n"
_API_ONLY = (
    _TWO_CHAINS.replace("SecurityConfig", "ApiSecurityConfig").split("    @Bean\n    ORDER_REST")[0]
    + "    @Bean"
    + _TWO_CHAINS.split("    @Bean")[2].replace("ORDER_API", "")
)


def test_unordered_chains_in_different_files_are_not_guessed(tmp_path: Path) -> None:
    document = _scan(
        tmp_path,
        {
            "SecurityConfig.java": _REST_ONLY,
            "ApiSecurityConfig.java": _API_ONLY,
            "A.java": _CONTROLLER,
        },
    )
    assert len(_chains(document)) == 2
    endpoints = _endpoints(document)
    # Spring orders the two beans by registration, which a static scan cannot see.
    _assert_unchanged(endpoints["GET /api/orders/{id}"])
    assert endpoints["GET /public/health"].metadata.no_auth_required is True


_B_CONTROLLER = """package demo;

import org.springframework.web.bind.annotation.*;

@RestController
public class BController {
    @GetMapping("/b/items")
    public String items() { return "items"; }
}
"""


def _write(root: Path, files: dict[str, str]) -> AiSbomDocument:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return AiSbomExtractor().extract_from_path(root, AiSbomConfig(enable_llm=False))


def test_chains_apply_only_to_their_own_module(tmp_path: Path) -> None:
    java = "src/main/java/demo"
    document = _write(
        tmp_path,
        {
            f"app-a/{java}/SecurityConfig.java": _config("            .anyRequest().permitAll()"),
            f"app-a/{java}/A.java": _CONTROLLER,
            f"app-b/{java}/SecurityConfig.java": _config(
                '            .anyRequest().hasRole("ADMIN")'
            ),
            f"app-b/{java}/BController.java": _B_CONTROLLER,
        },
    )
    assert len(_chains(document)) == 2
    endpoints = _endpoints(document)
    assert endpoints["POST /admin/users"].metadata.no_auth_required is True
    items = endpoints["GET /b/items"]
    assert items.metadata.auth_required is True
    assert items.metadata.auth_scope == "ADMIN"
    assert _protected(document) == {"GET /b/items"}


def test_module_without_a_chain_is_left_unchanged(tmp_path: Path) -> None:
    java = "src/main/java/demo"
    document = _write(
        tmp_path,
        {
            f"app-a/{java}/SecurityConfig.java": _config("            .anyRequest().permitAll()"),
            f"app-b/{java}/SecurityConfig.java": _config(
                '            .anyRequest().hasRole("ADMIN")'
            ),
            f"app-c/{java}/BController.java": _B_CONTROLLER,
        },
    )
    _assert_unchanged(_endpoints(document)["GET /b/items"])


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/main/java/demo/A.java", ""),
        ("services/app-a/src/main/java/demo/A.java", "services/app-a"),
        ("lib/Helper.java", None),
    ],
)
def test_source_module(path: str, expected: str | None) -> None:
    assert source_module(path) == expected
