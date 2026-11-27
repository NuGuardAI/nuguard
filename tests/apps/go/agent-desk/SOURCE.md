# Source and assessment boundary

- Upstream: https://github.com/huabeitech/agent-desk
- Pinned commit: `0dde5e036d34e039a0c078a58737323d85d94669`
- Copy date: 2026-11-26.
- License: Apache-2.0; unchanged upstream LICENSE retained.
- The first fixture commit (`fb137f21`) is an unchanged copy of `cmd/`, `internal/`,
  `config/`, `go.mod`, `go.sum`, LICENSE, both READMEs and `.env.example`.
- Scope: 596 Go files. `web/` and `flowgram-editor/` are excluded, along with upstream
  CI, screenshots, deployment scripts and repository metadata.
- Upstream backend imports `agent-desk/web` for static assets. A backend-only build
  needs an explicitly marked adapter; this is a packaging issue, not a vulnerability.

## Local changes

1. Added SOURCE, EXPECTED_SBOM, DISCREPANCIES and FINDINGS assessment records.
2. Added `web/embed.go`: an empty filesystem adapter solely to satisfy the existing
   backend's static-asset import without vendoring either frontend. No HTTP API,
   authentication, ownership or confirmation behavior is changed.
3. Added focused graph regression tests, marked `NuGuard fixture:`.
4. Added a frozen first-scan SBOM, exact deterministic test output, and an unposted
   maintainer review request. The backend build and graph package tests passed locally.

No authentication or confirmation checks are intentionally weakened. No seeded
vulnerability is presented as an upstream flaw. No paid model endpoint is used.

5. Added a dedicated no-model Go-control/build workflow outside the existing e2e matrix.
