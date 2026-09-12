# NuGuard Static Analysis Guide

`nuguard analyze` works entirely from an AI-SBOM document — no running application, no network
access required for the core pass. It looks at your application from two angles at once:

1. **The AI stack itself** — agents, models, tools, prompts, guardrails, and the data/privilege
   paths between them — using 30 built-in structural rules purpose-built for AI/agentic systems.
2. **The rest of the software infrastructure around it** — CI/CD pipelines, dependencies, IaC,
   containers, secrets, and supply-chain integrity — using 27 more structural rules plus five
   optional third-party scanners (OSV, Grype, Checkov, Trivy, Semgrep).

Both dimensions produce the same normalized `Finding` model, mapped to OWASP and MITRE ATLAS, and
rendered as Markdown, SARIF, JSON, or CSV.

---

## Quick Start

Static analysis only needs an AI-SBOM — no live target required.

<img src="assets/quickstart-2-analyze.svg" alt="nuguard sbom generate --source <path-to-your-app> --output app.sbom.json. nuguard analyze --sbom app.sbom.json --format markdown." width="760">

**Common changes:**

- `--source` — path to the repo to scan (or `--from-repo <url> --ref <branch>` for a remote repo); enables the supply-chain rules and third-party scanners, which need repo access beyond the SBOM alone
- `--format` — `markdown` for human review, `json` for automation, `sarif` for code scanning, `csv` for spreadsheets
- `--min-severity` — raise to `high`/`critical` to cut noise once the codebase is triaged
- `--fail-on` — exit code `1` when any finding meets this severity (default `high`)

```bash
nuguard analyze --sbom app.sbom.json --source . --format markdown
```

---

## What Gets Analyzed

### 1. The AI stack

30 structural rules (`NGA-001`–`NGA-030`) run against the AI-SBOM graph itself — no source code
re-parsing, just reasoning over the nodes and edges NuGuard already extracted. This is where
NuGuard differs from generic code scanners: it can flag *combinations* across component
boundaries, like "this agent has write access to a HIPAA-classified datastore via a tool with no
guardrail node in the graph," which a single-file scanner can't see.

| Theme | What it looks for | Example rules |
|---|---|---|
| Sensitive-data handling | PII/PHI flowing to external LLM providers or unencrypted datastores, unguarded write paths, credentials embedded in prompts | `NGA-001`, `NGA-005`, `NGA-019`, `NGA-023`, `NGA-025` |
| Guardrails & output validation | Agents/tools with no guardrail coverage on model output, internet-facing exposure, or delegation | `NGA-002` |
| Authentication & access control | Missing auth on AI endpoints, overly permissive IAM, unauthenticated inter-agent delegation, IDOR-prone endpoints, permissive CORS, weak JWT verification | `NGA-006`, `NGA-007`, `NGA-018`, `NGA-021`, `NGA-022`, `NGA-024`, `NGA-028`, `NGA-030` |
| Secrets & credential hygiene | Secrets as plain env vars, no secret store, debug flags exposing tokens | `NGA-003`, `NGA-014`, `NGA-025` |
| Human oversight & agent autonomy | High-risk/irreversible tool actions with no HITL approval, unguarded delegation chains | `NGA-012`, `NGA-020` |
| Model & tool provenance | Model weights loaded from untrusted registries, tools sourced from untrusted MCP servers | `NGA-008`, `NGA-022` |
| Audit & observability | No audit logging on the AI application | `NGA-009` |
| API hardening | Missing rate limiting, missing security headers, leaked stack traces on error | `NGA-026`, `NGA-027`, `NGA-029` |
| Deployment hardening | Containers running as root, no NetworkPolicy, no resource limits, `latest` image tags, missing health checks | `NGA-004`, `NGA-013`, `NGA-015`, `NGA-016`, `NGA-017` |
| CI/CD injection into the AI pipeline | `pull_request_target` / `GITHUB_ENV` injection reaching AI workflow files | `NGA-010`, `NGA-011` |

**Example finding** (`NGA-006`, rendered Markdown):

```
#### 🟠 NGA-006 — Missing authentication on API endpoint '/api/health'

**Summary:** Endpoint '/api/health' has no AUTH node.
**Remediation:** Add authentication middleware (API key, JWT, OAuth 2.0) to '/api/health'.
Use an API gateway to centralise auth enforcement.
**Framework Mapping:** OWASP LLM Top 10: LLM06:2026 · OWASP Agentic Top 10: ASI02 ·
MITRE ATLAS: AML.T0040, AML.T0000, AML.T0016
```

### 2. The rest of the software infrastructure

27 more structural rules (`NGA-SC-001`–`NGA-SC-027`) — plus five optional third-party scanners —
cover everything *around* the AI stack: the CI/CD pipeline that ships it, its dependencies, its
IaC, and the containers it runs in. This is where a compromised build pipeline or a malicious
package gets caught before it ever reaches the AI components above.

| Theme | What it looks for | Example rules |
|---|---|---|
| CI/CD pipeline & trigger security | Dangerous triggers (`pull_request_target`) reaching a publish step, unpinned OIDC/action refs, deploy jobs with no environment protection gate | `NGA-SC-001`, `NGA-SC-002`, `NGA-SC-003`, `NGA-SC-026` |
| Dependency & install integrity | Unpinned global installs in CI, mutable git/tarball dependency references, missing lockfiles | `NGA-SC-004`, `NGA-SC-023`, `NGA-SC-024` |
| Credential exposure in CI | Workflow steps reading credential paths (cloud creds, npm/vault tokens, `/proc/environ`) | `NGA-SC-005`, `NGA-SC-014` |
| Malicious / obfuscated code detection | Install hooks making network calls, lifecycle scripts piping into a shell, `eval`/obfuscation patterns, packages matching known-malicious IOC feeds | `NGA-SC-011`, `NGA-SC-012`, `NGA-SC-013`, `NGA-SC-015`, `NGA-SC-016`, `NGA-SC-025` |
| Provenance & artifact integrity | Publish workflows with no repo/ref/SHA-bound provenance attestation, large or high-entropy blobs in tool directories, suspiciously minified JS | `NGA-SC-006`, `NGA-SC-017`, `NGA-SC-018`, `NGA-SC-019` |
| AI-agent tool config risk | Unrestricted shell access in agent config, repo-controlled agent config, untrusted MCP server references, auto-run/auto-approve enabled | `NGA-SC-007`, `NGA-SC-008`, `NGA-SC-009`, `NGA-SC-010` |
| Git-history / commit integrity (full profile only) | Commit messages that don't match the files they touch, `[skip ci]` on security-sensitive changes, workflow edits with no matching lockfile change | `NGA-SC-020`, `NGA-SC-021`, `NGA-SC-022` |
| Pipeline governance | Security-relevant CI steps configured to silently swallow failures | `NGA-SC-027` |

Rule coverage scales with `--supply-chain-profile`: `ci` (12 fast rules) → `standard` (24 rules,
default) → `full` (all 27, adds the git-history heuristics above — slower since it walks commit
history).

**Third-party scanners**, run concurrently alongside the structural rules and independently
toggleable:

| Scanner | Covers | Requires |
|---|---|---|
| OSV | Python/JS/Go dependency CVEs via [osv.dev](https://osv.dev) | Network access only, no binary |
| Grype | Package + container image CVEs (NVD, GHSA, distro advisories) | `grype` binary on `PATH` |
| Checkov | IaC misconfigurations — Terraform, CloudFormation, Bicep, Kubernetes | `checkov` binary |
| Trivy | Container image / filesystem secrets and CVEs | `trivy` binary |
| Semgrep | AI-security source patterns and code smells, via NuGuard's bundled rule pack | `semgrep` binary |

Every scanner that needs a binary is **skipped, not failed**, when that binary isn't installed —
`nuguard analyze` always completes and the report's Tool Coverage table shows exactly what ran.

**Example finding** (`NGA-SC-004`, rendered Markdown):

```
#### 🟠 NGA-SC-004 — CI step uses unpinned global install

**Summary:** Workflow '.github/workflows/e2e-tests.yml' contains a step that runs an unpinned
global npm install or npx without a pinned version. This allows attackers who compromise a
package to inject malicious code into the CI environment.
**Remediation:** Pin global installs to exact versions (e.g. `npm install -g tool@1.2.3`) or use
a lockfile-based approach. Avoid unpinned npx calls in CI.
**Framework Mapping:** OWASP LLM Top 10: LLM04:2026 · OWASP Agentic Top 10: ASI04
```

---

## MITRE ATLAS enrichment

Every NGA and NGA-SC finding is automatically annotated with its MITRE ATLAS technique ID(s),
tactic, and confidence — no separate scan, just an enrichment pass over findings the structural
rules already produced. On top of that, four ATLAS-native checks (`ATLAS-NC-001`–`004`) look for
patterns no NGA rule directly covers: unpinned/unhashed external models, writable datastores
reachable with no guardrail in the path, model↔deployment paths with no auth, and agents/tools
with outbound-external capability. Disable with `--no-atlas`; add `--llm` to also get CVE
correlation (cross-referencing OSV/Grype hits against ATLAS techniques) and an LLM-authored
narrative summary per finding.

## Compliance mappings

Every finding carries pre-computed references — no separate compliance pass to run:

- **OWASP LLM Top 10** and **OWASP Agentic Top 10 (ASI)** — attached to both NGA and NGA-SC
  findings.
- **OWASP Top 10 CI/CD Security Risks** — attached to NGA-SC findings only, since those are the
  rules that actually reason about the CI/CD pipeline.
- **MITRE ATLAS** — see above.

These are the same references NuGuard's [Policy Engine](policy-engine-guide.md) compliance
assessment rolls up into a framework-level score.

## Findings, output, and exit codes

Every finding — from NGA, NGA-SC, OSV, Grype, Checkov, Trivy, or Semgrep — normalizes into the
same `Finding` model: `finding_id`, `title`, `severity`, `description`, `affected_component`,
`remediation`, `references`, plus the OWASP/ATLAS fields above. Output formats: `markdown`
(default, human review), `json` (automation), `sarif` (GitHub Code Scanning), `csv` (spreadsheets).

Exit codes: `0` clean, `1` findings at or above `--fail-on` (default `high`), `2` analysis/SBOM
error.

### 📖 Need every flag?

[![Read the CLI Reference](https://img.shields.io/badge/→_Read_the_CLI_Reference-111111?style=for-the-badge)](cli-reference.md#nuguard-analyze)

### 🔀 Want to run a different test?

[![Back to Quick Start](https://img.shields.io/badge/←_Back_to_Quick_Start-111111?style=for-the-badge)](quick-start.md)
