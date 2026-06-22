# Documentation Changelog

Track user-facing documentation updates here, especially changes to CLI behavior, workflows, and troubleshooting guidance.

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
3. Ensure [Getting Started](./getting-started.md) commands still run as documented.
4. Ensure troubleshooting entries still match real error messages.
