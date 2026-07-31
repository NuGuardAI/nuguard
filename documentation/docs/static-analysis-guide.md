# NuGuard Static Analysis Guide

NuGuard static analysis works from an AI-SBOM document and does not require a running target application. It produces normalized `Finding` objects that render as Markdown, JSON, or SARIF.

---

## Quick Start

Static analysis only needs an AI-SBOM — no live target required.

<img src="assets/quickstart-2-analyze.svg" alt="nuguard sbom generate --source <path-to-your-app> --output app.sbom.json. nuguard analyze --sbom app.sbom.json --format markdown." width="760">

**Common changes:**

- `--source` — path to the repo to scan (or `--from-repo <url> --ref <branch>` for a remote repo)
- `--format` — `markdown` for human review, `json` for automation, `sarif` for code scanning
- `--min-severity` — raise to `high`/`critical` to cut noise once the codebase is triaged

### 📖 Need every flag?

[![Read the CLI Reference](https://img.shields.io/badge/→_Read_the_CLI_Reference-111111?style=for-the-badge)](cli-reference.md#nuguard-analyze)

### 🔀 Want to run a different test?

[![Back to Quick Start](https://img.shields.io/badge/←_Back_to_Quick_Start-111111?style=for-the-badge)](quick-start.md)