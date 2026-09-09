"""Unit tests for nuguard/sbom/core/frontend_evidence.py — the regex-only
frontend call-site scanner used to feed context into the auth/chat-schema
LLM inference fallback pass. File/line selection only, no extraction.
"""

from __future__ import annotations

from nuguard.sbom.core.frontend_evidence import find_frontend_call_sites


class TestFetchCallSites:
    def test_finds_fetch_with_matching_path(self) -> None:
        content = "async function login() {\n  const res = await fetch('/api/v1/auth/login', opts);\n}\n"
        matches = find_frontend_call_sites({"src/auth.ts": content}, "/api/v1/auth/login")
        assert len(matches) == 1
        assert matches[0].file_path == "src/auth.ts"
        assert matches[0].line == 2
        assert matches[0].matched_pattern == "fetch"

    def test_ignores_fetch_with_different_path(self) -> None:
        content = "fetch('/api/v1/other', opts);\n"
        matches = find_frontend_call_sites({"src/x.ts": content}, "/api/v1/auth/login")
        assert matches == []


class TestHttpClientCallSites:
    def test_finds_axios_post(self) -> None:
        content = "const { data } = await axios.post('/api/v1/auth/login', creds);\n"
        matches = find_frontend_call_sites({"src/auth.ts": content}, "/api/v1/auth/login")
        assert len(matches) == 1
        assert matches[0].matched_pattern == "http_client"

    def test_finds_api_client_variable(self) -> None:
        content = "return api.post('/api/v1/auth/login', body);\n"
        matches = find_frontend_call_sites({"src/auth.ts": content}, "/api/v1/auth/login")
        assert len(matches) == 1


class TestReactQueryHooks:
    def test_finds_use_mutation_with_nearby_url(self) -> None:
        content = (
            "const loginMutation = useMutation({\n"
            "  mutationFn: () => api.post('/api/v1/auth/login', creds),\n"
            "});\n"
        )
        matches = find_frontend_call_sites({"src/auth.ts": content}, "/api/v1/auth/login")
        # matches both the useMutation window AND the direct api.post call —
        # at least one match is required, exact count isn't the contract here.
        assert len(matches) >= 1


class TestExclusionAndFileTypeFiltering:
    def test_excludes_backend_evidence_paths(self) -> None:
        content = "fetch('/api/v1/auth/login');\n"
        matches = find_frontend_call_sites(
            {"src/auth.controller.ts": content},
            "/api/v1/auth/login",
            exclude_paths={"src/auth.controller.ts"},
        )
        assert matches == []

    def test_ignores_non_frontend_extensions(self) -> None:
        content = "fetch('/api/v1/auth/login');\n"
        matches = find_frontend_call_sites({"README.md": content}, "/api/v1/auth/login")
        assert matches == []

    def test_empty_endpoint_path_returns_no_matches(self) -> None:
        content = "fetch('/api/v1/auth/login');\n"
        matches = find_frontend_call_sites({"src/auth.ts": content}, "")
        assert matches == []
