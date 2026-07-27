Here’s the design I’d add after reviewing the current Miasma reporting and the `NuGuardAI/nuguard` repo structure.

## What NuGuard needs to detect

The latest Miasma activity has two distinct supply-chain paths NuGuard should cover.

First, the classic package-registry path: compromised npm packages under the `@redhat-cloud-services` scope were modified across many releases; Microsoft reported the malware downloading Bun, running a second-stage payload, stealing credentials from developer and CI/CD environments, scraping GitHub Actions runner memory, and attempting package propagation with forged provenance. ([microsoft.com][1]) Wiz similarly reported that published package artifacts did not match their corresponding source repositories, which is exactly the kind of artifact/source drift NuGuard should validate. ([wiz.io][2])

Second, the newer repo/AI-agent path: Miasma-style activity has moved beyond `npm install` triggers into repository files that auto-execute or influence developer tools, including Claude Code, Gemini CLI, Cursor, VS Code config, and npm scripts. SafeDep described a variant that planted a large dropper in GitHub repos and wired it into AI coding-agent and editor configuration rather than publishing an npm package. ([SafeDep][3]) Recent reporting also links Miasma/Hades activity to more than 100 npm and PyPI packages as of June 2026, so NuGuard should treat this as an active, evolving threat class rather than a one-off IOC check. ([SecurityWeek][4])

## What the NuGuard repo already has

`NuGuardAI/nuguard` is a good base for this. The repo describes NuGuard as an open-source AI app security toolkit that generates AI-SBOMs, analyzes SBOMs for AI and supply-chain risks, proposes policy, runs static/runtime tests, performs red-team tests, and exports findings. ([GitHub][5]) The CLI already has `sbom`, `analyze`, `scan`, `policy`, `behavior`, and `redteam` commands. ([GitHub][5]) This also aligns with the NuGuard SaaS PRD goal of helping developers identify and mitigate risks in AI applications before production deployment. 

The architecture already has the right extension points: `nuguard/analysis` contains plugin infrastructure, OSV/Grype clients, Semgrep integration, and a `StaticAnalyzer`; `nuguard/sbom` contains extraction, dependency, schema, and adapter code. ([GitHub][6]) The static analyzer already runs structural NuGuard checks plus OSV, Grype, Checkov, Trivy, Semgrep, and MITRE ATLAS when enabled. ([GitHub][7])

The current gap is that the dependency scanner is intentionally shallow: it reads declared Python and JavaScript manifests such as `pyproject.toml`, `requirements*.txt`, `setup.cfg`, and `package.json`, but it does not build a full transitive lockfile SBOM. ([GitHub][8]) Also, the extractor currently skips `.claude`, `CLAUDE.md`, `AGENTS.md`, and most `.github` content except workflows, which is a problem for Miasma-style repo poisoning because those are now precisely the files attackers may target. ([GitHub][9]) The bundled Semgrep rules are focused on Python AI/LLM app risks, not npm lifecycle scripts, AI-agent configs, GitHub Actions publishing paths, or malicious shell/JS droppers. ([GitHub][10])

## Proposed feature: NuGuard “Miasma Coverage Pack”

Add a first-class supply-chain threat pack rather than a few one-off rules. I’d call it something like `supply_chain_threats` or `miasma_style_supply_chain`.

### 1. New analysis plugin

Add:

```text
nuguard/analysis/plugins/supply_chain_threats.py
nuguard/analysis/plugins/rules/supply_chain/*.yaml
nuguard/threat_intel/miasma_2026_06.yaml
```

Wire it into `StaticAnalyzer` as a native plugin alongside OSV, Grype, Checkov, Trivy, and Semgrep. The existing plugin model already returns structured `AnalysisResult` objects with findings and status, so this can fit cleanly into the current architecture. ([GitHub][11])

Add CLI flags:

```text
nuguard analyze --supply-chain-threats
nuguard scan --supply-chain-threats
nuguard scan --supply-chain-profile ci|standard|full
```

Default recommendation: enable `standard` in normal scans and `full` in release/publish workflows.

### 2. Treat AI-agent and developer-tool config as executable attack surface

NuGuard should add a special “developer tool config” scan path that overrides normal SBOM skip rules. Do not necessarily include these files in the normal AI-SBOM semantic extraction path, but always include them in the supply-chain threat pass.

Scan at least:

```text
.claude/**
CLAUDE.md
AGENTS.md
.cursor/**
.vscode/**
.gemini/**
.codex/**
.mcp.json
gemini-extension.json
openclaw.plugin.json
smithery.yaml
commands/**
skills/**
.claude-plugin/**
package.json scripts
```

This is directly relevant to `NuGuardAI/nuguard` because the repo itself contains `.claude`, `.claude-plugin`, `.codex`, `.mcp.json`, `gemini-extension.json`, `commands`, `skills`, `.github`, `npm`, `package.json`, `pyproject.toml`, `smithery.yaml`, and `uv.lock`. ([GitHub][5])

A concrete finding NuGuard would likely raise in its own repo is `.claude/settings.json`, which currently allows broad `Bash(*:*)`, `Read`, `Edit`, and `Write`, while only denying `Bash(rm:*)`. That may be intentional for contributors, but it is exactly the kind of repo-controlled AI-agent permission grant a Miasma-style scan should flag. ([GitHub][12]) The repo also has a Claude plugin referencing `.mcp.json` and a Gemini extension referencing `CLAUDE.md`, so those should become first-class scan targets. ([GitHub][13])

### 3. Add GitHub Actions publishing-path analysis

NuGuard already has GitHub Actions-related extraction and also uses Checkov/Trivy/Semgrep as optional analyzers. ([GitHub][9]) For Miasma, it needs a purpose-built workflow risk model focused on secret exposure, publishing privileges, provenance, and untrusted code execution.

Add rules for:

| Rule         | Detects                                                                                                           |                                                                         Severity |      |
| ------------ | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------: | ---- |
| `NGA-SC-001` | Publish workflow uses OIDC or package tokens and unpinned third-party actions                                     |                                                                             High |      |
| `NGA-SC-002` | `workflow_dispatch`, `pull_request_target`, or `workflow_run` can reach publish/secrets path                      |                                                                    High/Critical |      |
| `NGA-SC-003` | Job has `id-token: write`, `contents: write`, `packages: write`, or secrets and checks out mutable/untrusted code |                                                                             High |      |
| `NGA-SC-004` | `npm install -g`, `curl                                                                                           | bash`, `pip install`, `npx`, `bun`, or shell download in CI without pinning/hash | High |
| `NGA-SC-005` | Commands read `/proc/*/environ`, `printenv`, cloud credential files, npm tokens, GitHub tokens, or runner memory  |                                                                         Critical |      |
| `NGA-SC-006` | Build/publish workflow uses package provenance but source artifact cannot be tied to expected repo/ref/SHA        |                                                                         Critical |      |

For the current NuGuard repo, the publish workflows are especially important: `publish-pypi.yml` uses PyPI trusted publishing with `id-token: write`, publishes to npm using `NPM_TOKEN`, and publishes to Smithery using `SMITHERY_API_KEY`; it also runs `npm install -g smithery`. ([GitHub][14]) `publish-testpypi.yml` similarly uses OIDC trusted publishing. ([GitHub][15]) Those are reasonable release patterns, but the Miasma pack should flag them as high-value paths requiring stricter controls.

Recommended hardening for NuGuard’s own workflows:

```text
- Pin all GitHub Actions by full commit SHA, especially publish workflows.
- Require protected environments for PyPI, npm, Smithery, and docs publishing.
- Split publish jobs so npm/Smithery secrets are unavailable to Python build/test jobs.
- Replace long-lived npm token publishing with npm trusted publishing where available.
- Avoid unpinned global installs like npm install -g smithery; pin version and verify integrity.
- Add explicit least-privilege permissions per job, not only per workflow.
- Add Dependabot coverage for npm ecosystems in / and /npm, not only pip and github-actions.
```

The repo’s Dependabot config currently covers pip and GitHub Actions, which is helpful, but NuGuard should also monitor npm paths because the repo contains npm/package metadata. ([GitHub][16])

### 4. Expand package and manifest scanning beyond declared dependencies

Current `DependencyScanner` reads manifests and declared dependencies, but skips workspace/file/git refs and does not provide complete lockfile/transitive coverage. ([GitHub][8]) For Miasma coverage, extend it into a `ManifestAndPackageScanner` with these capabilities:

```text
package.json:
  - dependencies/devDependencies/optionalDependencies/peerDependencies
  - scripts
  - lifecycle scripts: preinstall, install, postinstall, prepare, prepack, postpack
  - gypfile, binary, files, exports, bin
  - packageManager and workspaces

lockfiles:
  - package-lock.json
  - npm-shrinkwrap.json
  - pnpm-lock.yaml
  - yarn.lock
  - uv.lock
  - poetry.lock
  - Pipfile.lock

Python packaging:
  - pyproject.toml build-system
  - setup.py
  - setup.cfg
  - MANIFEST.in
  - hatch/build hooks
  - console scripts
```

Rules should flag lifecycle scripts that invoke shell, network fetches, interpreters, Bun, obfuscated JS, `node-gyp`, `chmod`, temp directories, or credential paths. They should also flag package source references that are mutable or hard to audit, such as unpinned Git dependencies, tarball URLs, and local `file:` references in release contexts.

### 5. Add source-vs-artifact verification

One of the important Miasma lessons is that the published artifact can differ from the GitHub source. Wiz reported malicious package releases where npm artifacts did not match their corresponding source repos. ([wiz.io][2]) NuGuard should add an optional SaaS/full-profile verification step:

```text
For each package in the repo:
  1. Identify package name and version from pyproject.toml/package.json.
  2. Fetch the registry artifact metadata without installing it.
  3. Download the tarball/wheel/sdist into an isolated temp directory.
  4. Compare file list, lifecycle scripts, hashes, and provenance claims to the Git tag/SHA.
  5. Report drift:
     - source contains no lifecycle script, artifact does
     - artifact contains large/minified/obfuscated dropper not in repo
     - provenance repo/ref/workflow does not match expected owner/repo/workflow
     - artifact includes unexpected files outside declared package include list
```

This should run in “read-only, no install, no script execution” mode by default.

### 6. Detect large droppers and obfuscation

SafeDep reported a large dropper being added to repos for AI-agent/editor execution. ([SafeDep][3]) NuGuard’s current default file-size cap is useful for normal SBOM extraction, but threat scanning should have a separate oversized-file pre-scan so attackers cannot evade by adding a 4 MB minified payload. ([GitHub][17])

Add `LargePayloadScanner` rules:

```text
- Large JS/JSON/YAML/MD/config file in hidden tool directories
- Minified single-line JavaScript above threshold
- Long base64 blobs
- atob/eval/Function/child_process combinations
- shell that downloads and executes remote code
- references to Bun, npm token files, cloud credential paths, kubeconfig, Vault tokens
- newly added binary or high-entropy blob in a “dependency update” commit
```

Do not skip these files just because they exceed normal SBOM extraction limits. Instead, compute cheap metadata first: size, entropy, line count, MIME guess, extensions, suspicious token counts, and path context.

### 7. Add Git commit and PR-diff heuristics

Miasma-style repo poisoning often hides in plausible maintenance commits, such as “dependency update” changes that do not actually update dependency manifests. Add an optional Git-aware scanner:

```text
NGA-SC-020: commit message says dependency/update/chore but changes AI-agent config, workflow, package script, or large blob
NGA-SC-021: [skip ci] on security-sensitive file changes
NGA-SC-022: workflow or package publish config changed without manifest/lockfile changes
NGA-SC-023: maintainer account changed package publishing path or provenance workflow
NGA-SC-024: direct-to-main change in release/publish files
```

This would be very useful in SaaS PR scans and aligns with NuGuard’s broader PRD direction around dashboards, logging, and alerts for risk visibility. 

## Concrete implementation map for `NuGuardAI/nuguard`

### A. SBOM/config extraction changes

Modify `nuguard/sbom/config.py` and `nuguard/sbom/extractor.py` so supply-chain scanning has a separate include policy:

```yaml
analyze:
  supply_chain_threats: true
  supply_chain:
    scan_developer_tool_configs: true
    scan_large_payloads: true
    verify_package_artifacts: warn
    profile: standard
    threat_intel_feeds:
      - builtin:miasma-2026-06
```

Do not simply remove all skip rules globally. Keep the existing skip behavior for normal AI-SBOM extraction, but add a second pass:

```text
Normal AI-SBOM pass:
  honor current skip rules and size limits

Supply-chain threat pass:
  include .claude, CLAUDE.md, AGENTS.md, .cursor, .vscode, .mcp.json, package scripts, workflows
  apply larger or metadata-only size threshold
  never execute repo code
```

### B. Dependency scanner changes

Rename or extend `nuguard/sbom/deps.py` into a broader manifest scanner:

```text
DependencyScanner
  -> ManifestScanner
      parse_declared_dependencies()
      parse_lockfiles()
      parse_lifecycle_scripts()
      parse_package_publish_metadata()
      parse_python_build_hooks()
```

Add findings directly into the analysis phase, and also enrich the SBOM with new node types:

```text
PACKAGE
PACKAGE_VERSION
LOCKFILE_ENTRY
LIFECYCLE_SCRIPT
GITHUB_WORKFLOW
GITHUB_ACTION
PUBLISHING_IDENTITY
PROVENANCE_ATTESTATION
DEVELOPER_TOOL_CONFIG
MCP_SERVER
AI_AGENT_PERMISSION
```

### C. Static analyzer plugin

Add a native plugin that runs before or alongside Semgrep:

```text
nuguard/analysis/plugins/supply_chain_threats.py
```

It should consume:

```text
- source_path
- parsed manifests
- parsed workflow model
- parsed developer-tool config model
- optional Git diff metadata
- optional registry artifact metadata
- threat-intel YAML feed
```

Output findings in existing NuGuard formats, including JSON, Markdown, and SARIF, since the current scan command already supports report-style output. ([GitHub][18])

### D. Semgrep rules

Add a second built-in Semgrep ruleset, not replacing the current AI security one:

```text
nuguard/analysis/plugins/semgrep_rules/supply-chain.yaml
```

Target languages:

```text
yaml
json
javascript
typescript
bash
python
```

Rule themes:

```text
- npm lifecycle shell/network execution
- child_process + network + env access
- obfuscated JS execution
- GitHub Actions secrets exposed to mutable code
- curl/wget/bash/powershell execution chains
- /proc/environ and credential path reads
- broad AI-agent shell permissions
```

### E. Tests and fixtures

Add fixtures that model the campaign safely without live malware:

```text
tests/fixtures/miasma/package_postinstall_bun/
tests/fixtures/miasma/package_node_gyp_install/
tests/fixtures/miasma/github_publish_oidc_unpinned/
tests/fixtures/miasma/claude_bash_wildcard/
tests/fixtures/miasma/cursor_auto_run/
tests/fixtures/miasma/large_obfuscated_dropper/
tests/fixtures/miasma/source_artifact_mismatch/
```

Expected test assertions:

```text
- broad Claude Bash permission => high
- publish workflow with id-token + unpinned actions => high
- package postinstall downloading Bun/remote JS => critical
- artifact contains lifecycle script absent from source => critical
- large hidden config payload => high
- dependency-update commit touching only AI-agent config => medium/high
```

## Findings NuGuard would likely produce against its own repo

These are not claims of compromise; they are the sort of hardening findings the new pack should report.

1. **Broad Claude permissions**: `.claude/settings.json` allows `Bash(*:*)`, `Read`, `Edit`, and `Write`, denying only `rm`. That should become `NGA-SC-002: repository-controlled AI-agent config grants broad shell/file permissions`. ([GitHub][12])

2. **AI-agent config currently skipped by extraction**: existing extractor skip behavior excludes `.claude` and instruction files such as `CLAUDE.md`/`AGENTS.md`, which would miss an important Miasma-style repo vector. ([GitHub][9])

3. **Publish workflows need stricter supply-chain checks**: `publish-pypi.yml` uses OIDC for PyPI, `NPM_TOKEN` for npm, `SMITHERY_API_KEY` for Smithery, and an unpinned global `npm install -g smithery`; all should be reviewed under high-value release-path rules. ([GitHub][14])

4. **Declared-dependency scanning is not enough**: the current dependency scanner is shallow and does not fully analyze lockfiles/transitive closure, lifecycle scripts, or published artifact drift. ([GitHub][8])

5. **Existing Semgrep coverage is AI-app focused, not supply-chain focused**: the bundled rules cover Python LLM risks such as prompt injection patterns, hardcoded API keys, unsafe eval, SSRF, SQL, and unsafe model loading, but not GitHub Actions/package/AI-agent supply-chain vectors. ([GitHub][19])

## Recommended rollout

**Phase 1: offline high-signal scanner**

Ship a no-network plugin that scans repo files only:

```text
- package lifecycle scripts
- GitHub Actions publish/secrets paths
- AI-agent/editor config
- large suspicious payloads
- shell/network/credential-harvesting patterns
```

This should be fast enough for every `nuguard scan`.

**Phase 2: CI/release hardening mode**

Add a GitHub Actions workflow to NuGuard itself:

```text
.github/workflows/nuguard-supply-chain.yml

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  supply-chain:
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@<pinned-sha>
      - run: nuguard scan --supply-chain-threats --format sarif --output nuguard.sarif
      - upload SARIF
```

Fail on Critical by default; warn on High at first to avoid noisy rollout.

**Phase 3: artifact/provenance verification**

For release branches and SaaS scans, add read-only registry checks:

```text
- npm tarball/source diff
- PyPI wheel/sdist/source diff
- provenance repo/ref/workflow validation
- package version ownership and maintainer-change checks
```

This is the piece that most directly covers the published-artifact mismatch reported in Miasma. ([wiz.io][2])

**Phase 4: SaaS threat-intel and alerting**

In NuGuard SaaS, expose this as a “Miasma-style Supply Chain” scan family with dashboard filters:

```text
Attack surface:
  GitHub Actions
  Package manifests
  Lockfiles
  AI-agent configs
  Published artifacts
  Provenance

Severity:
  Critical: credential theft path, artifact/source mismatch, publish secrets exposed
  High: broad AI-agent shell permission, unpinned publish action, lifecycle downloader
  Medium: suspicious commit metadata, large hidden config file, unmonitored ecosystem
```

That fits the PRD’s emphasis on management dashboards, logging, and alerts for operational risk visibility. 

The highest-value immediate changes are: stop skipping AI-agent configs during threat scans, parse npm lifecycle scripts and lockfiles, add GitHub Actions publish-path rules, and add source-vs-registry artifact verification for release scans.

[1]: https://www.microsoft.com/en-us/security/blog/2026/06/02/preinstall-persistence-inside-red-hat-npm-miasma-credential-stealing-campaign/?utm_source=chatgpt.com "Preinstall to persistence: Inside the Red Hat npm Miasma credential ..."
[2]: https://www.wiz.io/blog/miasma-supply-chain-attack-targeting-redhat-npm-packages?utm_source=chatgpt.com "Miasma: Supply Chain Attack Targeting RedHat npm Packages"
[3]: https://safedep.io/miasma-worm-ai-coding-agent-config-injection/?utm_source=chatgpt.com "Miasma Worm Targets AI Coding Agents via GitHub Repos"
[4]: https://www.securityweek.com/over-100-npm-pypi-packages-hit-in-new-shai-hulud-supply-chain-attacks/?utm_source=chatgpt.com "Over 100 NPM, PyPI Packages Hit in New Shai-Hulud Supply Chain Attacks"
[5]: https://github.com/NuGuardAI/nuguard "GitHub - NuGuardAI/nuguard: opensource repo for NuGuard · GitHub"
[6]: https://github.com/NuGuardAI/nuguard/tree/main/nuguard/analysis "nuguard/nuguard/analysis at main · NuGuardAI/nuguard · GitHub"
[7]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/analysis/static_analyzer.py "raw.githubusercontent.com"
[8]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/sbom/deps.py "raw.githubusercontent.com"
[9]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/sbom/extractor.py "raw.githubusercontent.com"
[10]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/analysis/plugins/semgrep_scanner.py "raw.githubusercontent.com"
[11]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/analysis/plugin_base.py "raw.githubusercontent.com"
[12]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/.claude/settings.json "raw.githubusercontent.com"
[13]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/.claude-plugin/plugin.json "raw.githubusercontent.com"
[14]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/.github/workflows/publish-pypi.yml "raw.githubusercontent.com"
[15]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/.github/workflows/publish-testpypi.yml "raw.githubusercontent.com"
[16]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/.github/dependabot.yml "raw.githubusercontent.com"
[17]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/sbom/config.py "raw.githubusercontent.com"
[18]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/cli/commands/scan.py "raw.githubusercontent.com"
[19]: https://raw.githubusercontent.com/NuGuardAI/nuguard/main/nuguard/analysis/plugins/semgrep_rules/ai-security.yaml "raw.githubusercontent.com"
