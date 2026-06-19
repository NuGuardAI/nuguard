# Documentation Changelog

Track user-facing documentation updates here, especially changes to CLI behavior, workflows, and troubleshooting guidance.

## v0.8.0 - 2026-06-18

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
