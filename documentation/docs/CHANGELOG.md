# Documentation Changelog

Major product and documentation changes for NuGuard users. See the linked guides for setup and configuration details.

## v0.9.11 - 2026-09-29

### Improved
- **Cloned source location:** When a repository is scanned with a cache directory, the returned AI-SBOM document exposes `local_cloned_path`, the on-disk root its relative file paths resolve against (including GitHub subfolder URLs). It is available on the returned object only and is not written to serialized output, so read it before serializing.
- **Safer red-team scans:** Runs now default to non-destructive scenarios. Mutating attacks require an explicit opt-in, and write-capable API checks use a configured disposable canary identity when available. Reports explain the selected default and any credential fallback.
- **Target verification:** Previously verified chat endpoints can be reused from the enriched AI-SBOM, with the source identified in reports.

## v0.9.10 - 2026-09-28

### Improved
- **Target verification:** Failures caused by an exhausted usage quota or plan limit are now classified separately from auth failures, so scans stop with a clear "raise your quota" message instead of a misleading credential error.
- **Endpoint discovery:** Chat-endpoint discovery now works against more two-step (create-conversation-then-post-message) and SPA-fronted applications.
- **AI-SBOM accuracy:** Repeated API endpoints get concise, distinct names instead of collapsing into duplicates; prompts and datastores are now correctly connected in the generated SBOM graph.

### Changed
- The `ai-security-review` and `sbom-analysis` Claude Code plugin skills were simplified and clarified for AI developers, and the security-review pipeline now includes explicit target-verification and behavior-validation steps ahead of dynamic and red-team testing.
- Cloud-pentesting documentation wording simplified and landing-page assets refreshed.

## v0.9.9 - 2026-09-26

### Improved
- **API endpoint detection:** Pentests now combine routes found in source code, live API documentation, and a bounded crawl of the target. This helps find more testable endpoints and request parameters, including routes under application-specific URL paths.
- **Authentication:** Pentests can discover common login forms, check whether credentials actually change access, and retry temporary login failures. Reports show which authentication was used.
- **Pentest coverage:** Broader built-in web checks and smarter endpoint selection exercise more of an authorized application's HTTP surface. Reports distinguish verified findings from unverified results and show when scan coverage was incomplete.
- **Reports:** Pentest Markdown reports now include a clearer risk summary and an optional AI-generated executive summary. Behavior reports more accurately describe which agents and tools were tested.

## v0.9.8 - 2026-09-23

### Added
- **Authenticated web pentesting:** `nuguard pentest` can use configured target credentials and built-in web checks. Its quick start and [cloud pentesting guide](./cloud-pentesting.md) explain how to run authorized scans.
- **Broader source scanning:** AI-SBOM generation now covers Java applications and exported n8n, Langflow, Flowise, and Copilot Studio workflows. See [Supported Technologies](./supported-technologies.md).
- A source-first [Getting Started guide](./quick-start.md) covering static scans, target verification, runtime testing, and CI.

### Changed
- The Quick Start and AI Developer Guide are combined into the Getting Started guide. The [AI-SBOM schema guide](./sbom-schema.md) and coding-agent setup now reflect the expanded framework and language support.

## v0.8.9–v0.9.7 highlights - 2026-08-10 to 2026-09-12

### Added
- **Go and C# scanning:** AI-SBOM generation recognizes AI SDKs, web endpoints, authentication, tools, and dependencies in more Go and C# applications.
- **Cloud web pentesting:** `nuguard pentest` adds bounded checks for conventional HTTP vulnerabilities on explicitly authorized targets.
- **WebSocket support:** Target discovery and runtime testing can work with WebSocket chat endpoints.
- **Resumable scans:** Behavior and red-team runs can save progress and resume after a timeout or failure.

### Improved
- **Endpoint detection:** NuGuard can use API schemas, browser discovery, streaming responses, and application feedback to find working chat endpoints and request formats more reliably.
- **Authentication setup:** Browser-based login discovery helps configure applications that use interactive sign-in; target verification handles session cookies and custom headers.
- **Security findings:** Static analysis and red-team testing cover more web and AI security risks, with clearer evidence and fewer duplicate or misleading findings.

## v0.8.8 - 2026-07-22

### Fixed
- Redteam remediation advice is now classified by the specific `scenario_type` of the attack (e.g. Approval State Forgery, Agent Impersonation, Memory Poisoning) instead of only the coarse `goal_type`, so distinct attack techniques that previously collapsed into the same generic or mismatched-domain advice now get specific, correctly-scoped guidance.
- `nuguard redteam --scenarios <SCENARIO_TYPE>` (a specific attack-technique filter, e.g. `APPROVAL_STATE_FORGERY`) no longer silently drops the resulting finding and its remediation from the final report — the post-run filter now matches on `scenario_type` and `title`, not just `goal_type`.

## v0.7.9 - 2026-06-22

### Changed
- Auth bootstrap now swaps in the fallback `AuthConfig` (basic/none) on the session directly, so every caller's `headers()`/`refresh_if_needed()` reflects the fallback without needing to merge `fallback_headers` separately.
- `CredentialCheckResult.auth_type` now reports the auth actually sent for the fallback probe instead of the originally configured `login_flow`.

### Fixed
- Auth bootstrap no longer crashes when a broken `login_flow` endpoint falls back to static headers with no session; it now falls back to the target endpoint directly and stops retrying a proven-dead login endpoint.
- `login_error` no longer surfaces response body content (status codes and response keys only), preventing sensitive data or token values from leaking into CLI output; full body remains available at debug-level logs.
- Suppressed a noisy "called before initialize()" warning once `login_flow` fallback is active and expected.

## v0.7.8 - 2026-06-20

### Added
- Prepublish sanity workflow profiles and runner guidance for release readiness.

### Changed
- Endpoint resolution precedence now keeps explicit endpoint configuration authoritative.
- Verbose-mode behavior is aligned across commands with stable findings and richer bounded diagnostics.

### Fixed
- Regression coverage now validates explicit vs fallback endpoint paths and endpoint/source metadata consistency.


## Release Template

Use this format when cutting a release:

```md
## vX.Y.Z - YYYY-MM-DD

### Added
- ...

### Changed
- ...

### Fixed
- ...

### Removed
- ...
```

## Update Checklist

1. Update this file for any user-visible docs change.
2. Ensure [CLI Reference](./cli-reference.md) matches current argparse flags/defaults.
3. Ensure [Quick Start](./quick-start.md) commands still run as documented.
4. Ensure troubleshooting entries still match real error messages.
