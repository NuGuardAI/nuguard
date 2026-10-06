# Documentation Changelog

Major product and documentation changes for NuGuard users. See the linked guides for setup and configuration details.

## Unreleased - 2026-10-04

### Added
- **Red-team campaign mode (opt-in):** `redteam.mode: campaign` (or `nuguard redteam --mode campaign`) reuses conversations across related attacks instead of opening a fresh session per scenario, schedules one representative attempt per control before expanding techniques, cools down `429`/5xx retries outside the request path, and reproduces each finding in a brand-new conversation (`confirmed`, `not_reproduced`, `blocked`, `not_attempted`). Objectives that need a second account, a callback server, or a declared dry-run/sandbox fixture are reported as blocked instead of running. Reports add coverage-quality and efficiency sections and mark a no-findings run with untested controls as inconclusive. `concurrent` and `progressive` are unchanged. See the [red-team guide](redteam-guide.md#campaign-mode-opt-in).
- **Red-team API endpoint scoping:** For apps with many API endpoints the redteam LLM now picks the relevant endpoints for each direct-HTTP probe (JWT tampering, reflected XSS, injection, IDOR, and so on), and the `ci` profile spot-checks three endpoints. Rate-limit tests are classified as destructive and the `ci` profile no longer runs destructive scenarios. Tune with `redteam.api_endpoint_threshold` and `redteam.ci_api_spot_checks`.
- **Container and deployment layer (AI-SBOM schema 1.7.0):** The SBOM now records what an attacker or analyst needs to know about how the application runs. Each Dockerfile produces an app-image node with its base image, OS name and version, entrypoint, ports, and the packages installed by `RUN` instructions. Each deployed service gets a workload with replicas and min/max autoscaling, ports and how far each is exposed, CPU and memory, identity, and environment variable and secret *names* (never values). Sources are docker-compose, Kubernetes (Services, autoscalers, Ingress), Helm values, Kustomize, Bicep with `azure.yaml`, Terraform, CloudFormation/SAM, Cloud Run YAML, and ECS task definitions. See [SBOM schema](sbom-schema.md) and [supported technologies](supported-technologies.md).
- **Workload relationships:** New `RUNS`, `BUILT_FROM`, `HOSTS`, `EXPOSES`, `ROUTES_TO`, and `DEPENDS_ON` edges link workloads to images, the code in their build context, gateways, and each other. Endpoints, agents, and MCP servers gain `hosted_by` and `network_exposure` (`public`, `internal`, or `cluster`).
- **Image scanning:** `nuguard sbom generate --scan-images` runs `syft` on pulled base images to record the real OS and package list. It is off by default and needs `syft` and registry access.

### Changed
- **Red-team OWASP mappings:** Scenario catalog references now use the OWASP LLM Top 10 **2026** numbering. The catalog previously carried 2025-era numbers, so for example destructive tool actions were cited as `LLM06` and are now `LLM03` (Excessive Agency), hidden-context probes are `LLM08`, and vector/RAG scenarios are `LLM09`. Agentic references were corrected the same way (for example destructive actions map to `ASI02`, not `ASI06`). The per-scenario references set by the individual attack builders were also normalized to the same `LLM02:2026` / `ASI03` format and 2026 meanings (they previously mixed 2023, 2025 and 2026 numbers and free-text labels), so findings from legacy-builder scenarios now cite the same IDs as catalog scenarios. Reports and findings produced from catalog scenarios cite the new IDs; historical reports keep the 2025 numbers they were generated with.

### Improved
- **Static scanner reliability:** Checkov and Semgrep now have configurable per-process and total deadlines, worker cleanup, path-level timings and heartbeat logs. Failed or incomplete scans report errors while retaining partial findings; overlapping source paths are scanned once.
- **Guided campaign confirmation:** Successful guided attacks now replay their recorded attacker turns in a fresh conversation and use the same success criteria to judge reproduction. Confirmation removes unnecessary turns where possible; without a configured red-team LLM judge, it reports `blocked` with `no_judge_available`.
- **Campaign checkpoint security:** Stronger credential fingerprints make offline guessing more expensive while keeping raw credentials out of checkpoints. Credential rotation invalidates resume.
- **Red-team setup guidance:** The guide now starts with source discovery, configuration, and target verification, and clarifies profiles, non-destructive defaults, campaign budgets, and coverage results. The documentation landing page adds visual security workflows.
- **CloudFormation:** YAML templates that use short-form tags such as `!Ref` and `!Sub` are now scanned instead of being skipped.
- **Large compose and Bicep files:** Every service is kept; previously only the first three were retained.
- **Dockerfile and nginx detection:** `Dockerfile.*` variants are scanned, and `proxy_pass` on the same line as a `location` block is detected.

### Fixed
- **HTTP chat discovery (#627):** Shared reply extraction recognizes outputs-only and nested text replies while rejecting empty, error-only, and metadata-only bodies. Blind discovery checks field omission rather than confirming a guessed message field from a default greeting. Ambiguous candidates cannot create validated endpoint caches; existing endpoint confirmations are revalidated. API-only targets with optional message fields can provide explicit payload configuration.
- **Red-team regression runtime:** Defence-regression probes log their names, variants, durations, and outcomes, with configurable pre-pass and probe deadlines. Ambiguous fallback errors receive one short retry, gateway errors receive capped retries, and request slots are released during backoff. Structured provider policy blocks are non-retryable; failed probes remain inconclusive.
- **Dockerfile scan performance:** Malformed package-install flags no longer cause excessive regex backtracking, preventing scan stalls on crafted Dockerfiles.

### Compatibility
- Schema changes are additive. Code that switches exhaustively on edge `relationship_type` must handle the six new values.
- Authenticated campaign checkpoints created with the older SHA-256 credential fingerprint cannot be resumed after the fingerprint upgrade. Start a new campaign without `--resume`; incompatible checkpoints raise `CheckpointMismatchError` before state is restored.

## v0.9.14 - 2026-10-01

### Improved
- **Endpoint resolution:** Behavior, red-team, and target verification now retain the endpoint confirmed by live probing instead of re-resolving it to a different SBOM candidate. Known-payload probes no longer treat a rejected 4xx response as endpoint confirmation.
- **Endpoint coverage:** Behavior endpoint-coverage scenarios now send requests to the SBOM endpoint they report testing, using its discovered payload shape. Unsupported GET and unresolved path-parameter routes are skipped.
- **Tool-family probes:** Reachability probes reuse endpoint-preflight rotation and path-parameter bindings.

## v0.9.13 - 2026-10-01

### Improved
- **Chat endpoint discovery:** Target-session resolution now validates SBOM endpoint candidates, probes alternatives with configured authentication and payload extras, and uses browser UI sniffing as a last resort when HTTP discovery cannot confirm an endpoint.
- **WebSocket discovery:** An unset endpoint remains unset until SBOM discovery selects the route, allowing the correct WebSocket client to be constructed.
- **Discovery failures:** Runs now report a clear endpoint-not-found result instead of silently probing the generic `/chat` fallback when automatic discovery fails.

## v0.9.12 - 2026-09-30

### Improved
- **Partial repo scans:** `nuguard sbom generate --from-repo` accepts GitHub subfolder URLs, so large repositories can be scanned by just the AI-relevant subfolder. The cloned path is exposed on the generated AI-SBOM.

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
