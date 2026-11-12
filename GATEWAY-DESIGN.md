# NuGuard Gateway: Design Proposal

_Status: proposal, no code written. Investigated 2026-11-02 against `develop` @ `ad7873ee` (v0.9.15)._

_Implemented first slice: the remote scan API in `documentation/developer-specs/remote-scan-api.md` (`nuguard serve`, `nuguard/service/`)._

## Summary

This proposes a FastAPI service that runs four NuGuard operations as jobs: SBOM generation,
static analysis, policy validation and remediation. Callers authenticate with service tokens
that carry scopes. Behavior testing, red teaming, pentesting and SBOM enrichment (which probes a
target) are deliberately left out, so the gateway never sends traffic to an application under
test.

The investigation found these things the gateway has to deal with:

1. **There is no `remediation/public_api.py`.** The remediation entry point is
   `RemediationSynthesizer`, which takes untyped dicts. Policy linting has no public API either.
   Both need a typed public contract before a gateway can depend on them.
2. **OSV and Grype failures are reported as success.** When either one fails (for example with
   no network), the result says `status: "ok", findings: "0"`. A gateway would return a
   false-clean result.
3. **Both reported rule bugs in `ai-security.yaml` still exist**, and a third rule has the same
   defect as the eval rule. All three rules can never fire.
4. **Some library inputs are unsafe to expose directly.** `generate_sbom` accepts any server
   path and any git URL scheme. `AiSbomConfig` reads the server's `NUGUARD_LLM_*` variables as
   defaults, so a client-supplied config could send the server's LLM key to a URL of the
   client's choosing.

The "Prerequisite changes" section lists these fixes. The rest of the document is the API
design.

---

## Part 1: Findings, checked against the code

### 1.1 What the public APIs offer

Every model is Pydantic and every entry point is `async` unless noted.

**`nuguard/sbom/public_api.py`**

| Function | Input | Output | Notes |
|---|---|---|---|
| `generate_sbom` ([L214](nuguard/sbom/public_api.py#L214)) | `SbomGenerateRequest`: exactly one of `source_path` / `repo_url`, plus `repo_ref` (default `"main"`) and `config: AiSbomConfig` | `SbomGenerateResult`: `sbom: AiSbomDocument`, `source_ref`, `node_count`, `edge_count` | `repo_url` runs `git clone --depth 1`. The CPU-bound work runs synchronously inside the coroutine. |
| `parse_sbom_json` ([L235](nuguard/sbom/public_api.py#L235)) | `SbomParseRequest`: `raw_json` | `SbomParseResult`: `sbom`, counts | Offline |
| `render_sbom` ([L241](nuguard/sbom/public_api.py#L241)) | `SbomRenderRequest`: `format` ∈ `json`, `cyclonedx`, `cyclonedx-ext`, `markdown`; `options` (e.g. `spec_version`); plus keyword `sbom=` | `SbomRenderResult`: `content`, `media_type`, `filename_hint` | Offline |
| `export_sbom` ([L292](nuguard/sbom/public_api.py#L292)) | `SbomExportRequest`: `format`, `output_path`, `options`; plus `sbom=` | `SbomExportResult`: path, bytes written | **Writes to an arbitrary server path** |
| `enrich_sbom` ([L155](nuguard/sbom/public_api.py#L155)) | `SbomEnrichmentRequest`: `sbom`, `target_url`, `llm_config`, `probe_auth`, `output_path` | `SbomEnrichmentResult`: `sbom`, confidence before and after, `probe_requests`, `cache_fingerprint` | **Probes a live target**, optionally uses an LLM, can write a file |

**`nuguard/analysis/public_api.py`**

| Function | Input | Output |
|---|---|---|
| `run_analysis` ([L121](nuguard/analysis/public_api.py#L121)) | `AnalysisRunRequest` ([L35](nuguard/analysis/public_api.py#L35)): `enable_{atlas,osv,grype,checkov,trivy,semgrep,supply_chain}` (all default `True`), `source_path`, `atlas_config`, `min_severity` (default `LOW`), per-tool timeouts, `supply_chain_profile`, `supply_chain_verify_artifacts` (`off`/`warn`/`fail`), `supply_chain_threat_intel_feeds`. Keyword arguments: `sbom` (required), `policy`, `llm_client` | `AnalysisRunResult`: `findings: list[Finding]`, `tool_status: dict[str, dict[str, str]]`, `nga_audit`, `sc_audit`, `token_usage`, `remediation_plan: list[RemediationArtefact]` |

`run_analysis` does not catch errors from the analysis pass itself. It does synthesize
remediation for the analysis findings ([L89](nuguard/analysis/public_api.py#L89)), and a
synthesis failure propagates as an error.

**`nuguard/remediation/` (no `public_api.py`)**

| Callable | Input | Output |
|---|---|---|
| `RemediationSynthesizer(sbom=None, policy=None, llm_client=None, intent_purpose="")` ([synthesizer.py:669](nuguard/remediation/synthesizer.py#L669)) | (constructor) | |
| `.synthesize_findings_async(findings: list[dict])` ([L805](nuguard/remediation/synthesizer.py#L805)) | Dicts with `finding_id`, `title`, `description`, `severity`, `affected_component`; optionally `scenario_type` and `goal_type` | `list[RemediationArtefact]`. With an LLM client, any per-finding LLM failure propagates. |
| `.synthesize_findings(findings)` ([L959](nuguard/remediation/synthesizer.py#L959)) | Same | Synchronous, templates only |
| `.synthesize_pentest_findings[_async](Sequence[PentestRemediationFinding])` ([L702](nuguard/remediation/synthesizer.py#L702)) | Typed, frozen model ([pentest.py:113](nuguard/remediation/pentest.py#L113)): `finding_id`, `rule_id`, `title`, `description`, `severity`, `target`, `matched_at`, `remediation`, `cwe_ids`, `cve_ids`, `references`, `tags`, `matcher_name`. Normalized and length-bounded. | Artefacts with `runtime: RuntimeRemediationContext` |
| `backfill_finding_remediation(findings, plan)` ([backfill.py:66](nuguard/remediation/backfill.py#L66)) | Findings (models or dicts) plus a plan | Mutates findings in place |
| `resolve_remediation_llm_client(cfg: NuGuardConfig)` ([llm.py:16](nuguard/remediation/llm.py#L16)) | Full config | `LLMClient`, or `None` when no model is set |

`RemediationArtefact` ([models.py:46](nuguard/remediation/models.py#L46)) has `finding_ids`,
`component`, `component_type`, an artefact type (`system_prompt_patch`, `input_guardrail`,
`output_guardrail`, `architectural_change`) and the patch or guidance text.

**`nuguard/policy/` (for `policy validate`)**

- `parse_cognitive_policy(CognitivePolicyParseRequest{markdown})` →
  `CognitivePolicyParseResult{success, policy, errors[], warnings[]}`
  ([public_api.py:36](nuguard/policy/public_api.py#L36)). It is **synchronous**, and its errors
  are sanitized so the source text never appears in them.
- The CLI's `nuguard policy validate` does more than parse: it also calls
  `lint_policy(policy) -> list[LintIssue]`
  ([validator.py:26](nuguard/policy/validator.py#L26)). `LintIssue` is a plain dataclass, not a
  public Pydantic contract, and `parse_cognitive_policy` never returns lint results. **No public
  API matches what the CLI's validate command does.**

### 1.2 Which steps need the network or a model

| Step | Network | Model | Local binaries | Source |
|---|---|---|---|---|
| SBOM from `source_path` | No | Only if `config.enable_llm` | none | |
| SBOM from `repo_url` | Yes (git clone) | Only if `config.enable_llm` | `git` | [core.py:3622](nuguard/sbom/extractor/core.py#L3622) |
| `AiSbomConfig` defaults | n/a | `enable_llm` comes from `NUGUARD_LLM` / `AISBOM_ENABLE_LLM` (default off). The model defaults to `gpt-4o-mini`, and the key and base come from env vars. | | [config.py:62-80](nuguard/sbom/config.py#L62-L80) |
| `enrich_sbom` | **Yes (probes target)** | Optional | | excluded from the gateway |
| Analysis: NGA rules | No | No | none | |
| Analysis: OSV | **Yes**, `api.osv.dev` | No | none | [osv_client.py:28](nuguard/analysis/osv_client.py#L28) |
| Analysis: Grype | Yes, vulnerability DB updates and container image pulls | No | `grype` | |
| Analysis: Trivy | Yes, DB download (no `--skip-db-update` or `--offline-scan` flag is passed) | No | `trivy` | [trivy_scanner.py:211](nuguard/analysis/plugins/trivy_scanner.py#L211) |
| Analysis: Checkov | No (in the invocation used) | No | `checkov` | |
| Analysis: Semgrep | No. Bundled local rule files only, `--metrics off --disable-version-check`. | No | `semgrep` | [semgrep_scanner.py:152](nuguard/analysis/plugins/semgrep_scanner.py#L152) |
| Analysis: ATLAS | No | Only if `atlas_config={"llm": True}` | none | [atlas_annotator.py:21](nuguard/analysis/plugins/atlas_annotator.py#L21) |
| Analysis: supply chain | Only if `supply_chain_verify_artifacts != "off"` (PyPI and npm registries). Threat-intel feeds are bundled YAML. | No | none | [artifact_verifier.py:24](nuguard/analysis/artifact_verifier.py#L24) |
| Analysis: remediation plan | No | Only if `llm_client` is passed | | |
| Policy parse and lint | No | No | none | |
| Remediation | No | Optional. Falls back to deterministic templates when `llm_client is None` | none | [synthesizer.py:728](nuguard/remediation/synthesizer.py#L728) |

Semgrep, Checkov, Trivy and the Dockerfile and IaC checks only see files when `source_path` is
set. Without source code, analysis covers just the SBOM-native checks: NGA rules, OSV, Grype,
ATLAS and the SBOM-native supply-chain checks.

### 1.3 How a skipped analyzer is reported

`StaticAnalyzer.analyze` pre-fills `tool_status` with eight keys in display order
([static_analyzer.py:232](nuguard/analysis/static_analyzer.py#L232)): `nga-rules`, `osv`,
`grype`, `checkov`, `trivy`, `semgrep`, `atlas`, `supply-chain`. Each value is a string map:

- `{"status": "disabled", "findings": "0"}` when turned off in the request.
- `{"status": "skipped", "findings": "0", "reason": "..."}` when the plugin bails out. Examples
  are a missing binary (`"grype not installed"`, [L291](nuguard/analysis/static_analyzer.py#L291))
  and a plugin result with `status == "skipped"` ([L479](nuguard/analysis/static_analyzer.py#L479)).
- `{"status": "error", "findings": "N", "reason": "..."}` on a plugin error or an unexpected
  exception. Partial findings are kept.
- `{"status": "ok", "findings": "N"}` otherwise.

A skip never raises, never appears in `findings`, and does not change any top-level flag.
`AnalysisRunResult` has no field such as "complete" or "partial", so callers have to inspect
`tool_status` themselves.

**Defect: OSV and Grype report failures as ok.** `_run_osv` and `_run_grype` catch every
exception and return `[]`
([L407-L424](nuguard/analysis/static_analyzer.py#L407-L424),
[L426-L450](nuguard/analysis/static_analyzer.py#L426-L450)). The step wrappers then write
`status: "ok"` unconditionally
([L282](nuguard/analysis/static_analyzer.py#L282),
[L297](nuguard/analysis/static_analyzer.py#L297)). With no network, an OSV timeout, or a Grype
DB failure, the result reads "ok, 0 findings": a false all-clear. The M1 tools (Checkov, Trivy,
Semgrep) and supply chain do not have this problem, because they set `skipped` or `error`
themselves.

### 1.4 Does remediation accept findings from other tools?

**Yes, structurally, but quality depends on the route.**

- **Generic route** (`synthesize_findings[_async]`). Any dict with the five keys is accepted and
  the source tool is not checked. Classification ([`_classify_finding`,
  synthesizer.py:481](nuguard/remediation/synthesizer.py#L481)) uses `scenario_type`, then
  `goal_type`, then keyword regexes and NuGuard behavior IDs (`BA-00x`). A finding from Bandit,
  Snyk or a SAST tool without NuGuard vocabulary will mostly classify as `generic` and get
  template guidance. Remediation is tied to SBOM nodes through `affected_component`, so foreign
  findings only get node-specific artefacts if the caller maps them to SBOM node names.
- **Pentest route** (`synthesize_pentest_findings[_async]` with `PentestRemediationFinding`).
  This is the typed, hardened path for DAST and scanner-style findings: rule, CWE/CVE, target
  and matched location. It needs no SBOM, and it works without a model (the async variant only
  rewrites `runtime.implementation` when an LLM is configured). Nuclei-, ZAP- or Burp-style
  findings fit this model well once mapped.
- There is no input for SARIF. The gateway would have to map SARIF `result` objects to one of
  the two routes.

### 1.5 The two `ai-security.yaml` rule bugs

File: [nuguard/analysis/plugins/semgrep_rules/ai-security.yaml](nuguard/analysis/plugins/semgrep_rules/ai-security.yaml).
Both bugs **still exist**.

1. **`nuguard-llm-prompt-injection-fstring`** ([L7-L24](nuguard/analysis/plugins/semgrep_rules/ai-security.yaml#L7-L24)).
   The `pattern-not` is identical to the `pattern`, so every match is excluded and the rule
   never fires. The inline comment ("allow if input is validated above — but we flag the raw
   pattern") shows the intended exclusion was never written.
2. **`nuguard-eval-on-llm-output`** ([L78-L92](nuguard/analysis/plugins/semgrep_rules/ai-security.yaml#L78-L92)).
   `pattern: eval(...)` and `pattern: exec(...)` sit under `patterns:`, which means AND. The
   same expression would have to be both `eval(x)` and `exec(x)`, which is impossible, so the
   rule never fires. It needs `pattern-either:`.
3. **Additional finding: `nuguard-hardcoded-api-key`** ([L41-L57](nuguard/analysis/plugins/semgrep_rules/ai-security.yaml#L41-L57))
   has the same AND-of-alternatives defect across four patterns, so it never fires. The
   neighbouring `nuguard-openai-key-in-code` still covers part of the case.

As a result, NGA-002 and NGA-003 Python coverage from Semgrep is weaker than the rule list
suggests. I did not check the Go, C#, Java, generic and supply-chain rule files for the same
pattern.

### 1.6 Licences of bundled rules, templates and data

| Bundled content | Origin per file | Licence |
|---|---|---|
| Semgrep rules: `ai-security.yaml`, `generic-security.yaml`, `java-ai-security.yaml`, `supply-chain.yaml` | Headers say "NuGuard ... Semgrep Rules". No per-file licence or copyright. | Covered by the repository's Apache-2.0 `LICENSE`. Not derived from the Semgrep Registry as far as headers show. |
| Nuclei templates: 27 files in `nuguard/pentest/templates/` | All `author: nuguard`, `nuguard-generic-*` IDs. Signed locally at runtime and not shipped signed ([bundled_templates.py](nuguard/pentest/bundled_templates.py)). | Apache-2.0 (repo). Not used by this gateway. |
| Threat-intel feed `miasma_2026_06.yaml` | IOC list citing Microsoft and Wiz reporting | Apache-2.0 (repo). The facts are factual data, but the feed should cite its sources. |
| `policy/compliance/data/owasp_llm_top10.json` | Paraphrased short descriptions plus links to owasp.org | OWASP Top 10 for LLM is CC BY-SA 4.0. The text is paraphrased rather than copied, but this should get a licence review before redistribution. |
| `eu_ai_act.json`, `nist_ai_rmf.json` | Empty skeletons (`controls: []`) | No third-party content |
| `common/control_mappings/atlas.py` | MITRE ATLAS v2 technique and mitigation IDs and names, sourced from atlas.mitre.org | Third-party data with no attribution or licence note in the file. MITRE publishes ATLAS data under its own terms, which should be confirmed. |
| `documentation/developer-specs/*.pdf` (OWASP) | Third-party PDFs | CC BY-SA 4.0. Excluded from the wheel and sdist (the sdist includes only `/nuguard`), but present in the git repo. |

The repository has **no `NOTICE` file** and no third-party attribution list. External scanners
are invoked as subprocesses and not bundled. From general knowledge, not verified in this repo:
Semgrep CE is LGPL-2.1, Trivy, Grype and Checkov are Apache-2.0, and OSV.dev data is largely
CC-BY 4.0 with per-source variation. Confirm these before shipping a container image that
includes them.

---

## Part 2: Gateway design

### 2.1 Scope

**In scope**
- SBOM generation from an uploaded source archive or an allowlisted HTTPS git repo
- Static analysis of an SBOM, optionally with the source archive
- Policy validation: parse plus lint
- Remediation for NuGuard findings, pentest-shaped findings and generic findings

**Out of scope (not reachable through the gateway)**
- `behavior`, `redteam`, `pentest`, `validate`, `target`
- `enrich_sbom` (it probes targets)
- `export_sbom` and any other server-path writes
- Raw `source_path`
- Client-chosen LLM endpoints

The gateway never contacts an application under test. Its only outbound traffic goes to git
hosts, vulnerability databases, package registries and the operator's LLM endpoint, all
allowlisted.

### 2.2 Architecture

```
client ──HTTPS──▶ API (FastAPI, stateless)
                    │  auth: service token → (tenant, scopes)
                    │  validate request → write job row → 202
                    ▼
                 Job store (Postgres; SQLite for dev)  ◀── artifact store (object storage / local dir)
                    ▲
                    │  claim: SELECT … FOR UPDATE SKIP LOCKED
                 Workers (separate processes/containers, N per host)
                    │  run one job per process in a scratch dir with resource limits
                    └─▶ nuguard public APIs  ─▶ git / semgrep / checkov / trivy / grype / OSV / LLM
```

- **Workers run outside the API process.** SBOM extraction is CPU-bound synchronous code inside
  `async def`, and the analyzers start subprocesses. Running them in the web process would block
  the event loop and mix tenants' files.
- **The queue is the job table.** A Postgres `SKIP LOCKED` queue needs no extra infrastructure,
  and a `JobQueue` interface keeps Redis/arq possible later.
- **Each job gets a fresh scratch directory**, deleted when the job ends. Per-tenant caches are
  not shared across tenants.
- **Worker image:** pinned `semgrep==1.172.0` (matching CI), `checkov`, `trivy`, `grype` with a
  baked-in DB and `git`. Rebuild the image to refresh the Trivy and Grype DBs so jobs can run
  offline (`--skip-db-update`, `GRYPE_DB_AUTO_UPDATE=false`) and give reproducible results.
  This needs a small NuGuard change (see section 3).

### 2.3 Resources and endpoints

All routes are under `/v1`. Request and response bodies reuse NuGuard's public Pydantic models
where they exist, wrapped in gateway envelopes. The gateway's own OpenAPI schema is the contract.

**Artifacts** are inputs that clients upload and jobs reference by ID. There are no server paths.

| Method and path | Scope | Purpose |
|---|---|---|
| `POST /v1/artifacts` (multipart; `kind` = `source-archive`, `sbom`, `policy` or `findings`) | `artifacts:write` | Upload. Returns `{artifact_id, kind, sha256, size}`. Source archives are `.tar.gz` or `.zip`, size-capped. |
| `GET /v1/artifacts/{id}` | `artifacts:read` | Metadata only |
| `DELETE /v1/artifacts/{id}` | `artifacts:write` | |

**Jobs**

| Method and path | Scope | Body |
|---|---|---|
| `POST /v1/jobs/sbom` | `sbom:generate` | `{source: {artifact_id} \| {repo_url, ref}, options: SbomOptions}` |
| `POST /v1/jobs/analyze` | `analyze:run` (+ `analyze:network` for OSV, Grype, Trivy DB or artifact verification) | `{sbom: {job_id} \| {artifact_id} \| inline, source_artifact_id?, policy_artifact_id?, options: AnalyzeOptions}` |
| `POST /v1/jobs/policy-validate` | `policy:validate` | `{policy: {artifact_id} \| {markdown}}` |
| `POST /v1/jobs/remediation` | `remediation:run` (+ `remediation:llm` to request a model) | `{findings: FindingsInput, sbom?: ref, policy_artifact_id?, use_llm: bool}` |
| `GET /v1/jobs/{id}` | `jobs:read` | Status envelope |
| `GET /v1/jobs/{id}/result` | `jobs:read` | Typed result. `409` while not finished. |
| `GET /v1/jobs/{id}/result/sbom?format=json\|cyclonedx\|cyclonedx-ext\|markdown` | `jobs:read` | SBOM jobs only, through `render_sbom` |
| `POST /v1/jobs/{id}:cancel` | `jobs:write` | Best-effort cancel |
| `GET /v1/jobs?type=&status=&cursor=` | `jobs:read` | Lists the caller's tenant only |

Every `POST /v1/jobs/*` returns `202` with the job envelope and `Location`. It also accepts:

- **`Idempotency-Key`** (same key and same body returns the same job; same key with a different
  body returns `409`).
- **`Prefer: wait=N`** (N ≤ 30 s). The API holds the response and returns `200` with the result
  if the job finishes in time. This keeps policy validation (milliseconds) a job, as requested,
  without forcing clients to poll.

**Job envelope**

```json
{
  "job_id": "job_01J…",
  "type": "analyze",
  "status": "queued | running | succeeded | failed | cancelled | expired",
  "created_at": "…", "started_at": "…", "finished_at": "…",
  "request_sha256": "…",
  "nuguard_version": "0.9.15",
  "completeness": "complete | partial | null",
  "error": { "code": "analyzer_unavailable", "message": "sanitized text" },
  "links": { "self": "…", "result": "…" }
}
```

**Option models.** These are whitelists. NuGuard's own request models are never passed through
from the client.

- `SbomOptions`: `include_tests`, path include and exclude globs, `use_llm: bool`. Model, key
  and base come from server config only, and `use_llm` requires the `sbom:llm` scope.
- `AnalyzeOptions`: the `enable_*` booleans, `min_severity`, `supply_chain_profile`,
  `supply_chain_verify_artifacts`, `threat_intel_feeds` (validated against `load_feeds()` IDs).
  Timeouts are clamped to server maximums. `atlas_llm` requires `analyze:llm`.
- `FindingsInput` is one of:
  - `{format: "nuguard", findings: [Finding]}`, routed through `synthesize_findings_async`
  - `{format: "pentest", findings: [PentestRemediationFinding]}`, routed through
    `synthesize_pentest_findings_async`
  - `{format: "generic", findings: [{finding_id, title, description, severity, affected_component}]}`
  - `{format: "sarif", artifact_id}`. The gateway maps SARIF results to the pentest route when
    a result has a URL location and to the generic route otherwise.

  Caps: maximum findings per job, maximum text length (reusing the pentest model's bounds).

**Results**

- **sbom** → `SbomGenerateResult`, with `source_ref` sanitized and no local clone path
  (`doc.local_cloned_path` must be stripped).
- **analyze** → `AnalysisRunResult`, plus a gateway-computed
  `completeness: {state, analyzers: {name: {status, reason?}}}`.
  - `state = "complete"` only if every analyzer the job enabled is `ok`.
  - Any enabled analyzer that is `skipped` or `error` makes it `partial`.
  - Analyzers that need source are reported as `not_applicable` (not `ok`) when no source
    artifact was given.
  - A clean-looking `partial` result is never presented as clean.
- **policy-validate** → `{parse: CognitivePolicyParseResult, lint: [LintIssueModel], valid: bool}`.
  `valid` means the parse succeeded and there are no lint errors.
- **remediation** → `{artefacts: [RemediationArtefact], unmatched_components: [str], llm_used: bool, token_usage}`.

### 2.4 Authentication and scopes

- **Service tokens.** Opaque `ngw_<id>_<secret>` with 256-bit secret entropy. The server stores
  only `SHA-256(secret)` together with the token ID, `tenant_id`, `scopes[]`, `expires_at`,
  `created_by` and `last_used_at`. Clients send `Authorization: Bearer`. Comparison is
  constant-time.
- **Token management** happens in v1 through an operator CLI (`nuguard-gateway tokens
  create|list|revoke|rotate`), not through HTTP. That keeps the admin surface off the network.
  Rotation runs two tokens side by side, with an optional grace expiry for the old one.
- **Scopes:**

| Scope | Grants |
|---|---|
| `artifacts:write`, `artifacts:read` | Upload and read artifact metadata |
| `sbom:generate` | SBOM jobs from an uploaded archive |
| `sbom:repo` | SBOM jobs from `repo_url` (outbound git) |
| `sbom:llm` | LLM-assisted extraction |
| `analyze:run` | Offline analyzers (NGA, ATLAS without LLM, Checkov, Semgrep, offline supply chain) |
| `analyze:network` | OSV, Grype/Trivy DB access, artifact verification |
| `analyze:llm` | ATLAS LLM enrichment, LLM remediation inside analysis |
| `policy:validate` | Policy validation jobs |
| `remediation:run` | Template remediation |
| `remediation:llm` | LLM-authored remediation |
| `jobs:read`, `jobs:write` | Read and cancel jobs in the caller's tenant |

  A request that asks for a capability without the matching scope gets `403` with
  `error.code = "scope_required"` and the missing scope named. It is never silently downgraded.

- **Tenant isolation.** Every artifact, job and result row carries `tenant_id`, and every query
  filters on it. A cross-tenant ID returns `404`, not `403`.
- **Rate and concurrency limits** apply per token and per tenant: queued jobs, running jobs, and
  upload bytes per day.

### 2.5 Security controls

1. **No server paths.** `source_path` is always a gateway-made scratch directory. `output_path`
   and `export_sbom` are never reachable.
2. **Git URLs.** Only `https://`, with a host allowlist from config (for example `github.com`).
   No userinfo in the URL. Private-repo credentials come from a per-tenant secret reference,
   never the request body. Clone with `GIT_ALLOW_PROTOCOL=https`, `GIT_TERMINAL_PROMPT=0`,
   `GIT_CONFIG_NOSYSTEM=1` and `HOME` set to the scratch directory. Cap clone time and size.
   NuGuard's `_clone_repo` already passes `--` before the URL, but it has **no scheme
   restriction** ([core.py:3622](nuguard/sbom/extractor/core.py#L3622)).
3. **Server env isolation.** Workers build `AiSbomConfig` explicitly from `SbomOptions` plus
   server config, and run with `NUGUARD_LLM*` / `AISBOM_*` unset. That way no env default (such
   as the operator's LLM key) attaches to a request with a client-influenced base URL. LLM
   model, key and base are server-side only.
4. **Archive handling.** Reject absolute paths, `..`, symlinks, hard links and device files.
   Cap total size, file count and per-file size. Extract only into the scratch directory.
5. **Worker sandbox.** A container per worker with a read-only root, a scratch tmpfs, no host
   mounts, CPU, memory and pid limits, and an egress allowlist covering git hosts, `api.osv.dev`,
   the PyPI and npm registries (only when verification is enabled), and the LLM endpoint.
   Analyzers parse untrusted repositories, so the sandbox matters more than the API layer.
6. **Errors.** Return codes plus sanitized messages, following the policy public API's pattern.
   Never echo source text, tracebacks or scanner stderr. Full detail goes to server logs with
   `SecretStr` redaction.
7. **Retention.** Artifacts and results expire after a configurable time (default 7 days).
   Jobs then move to `expired` and their result is deleted.
8. **Audit log.** One entry per job create, cancel and token use: token ID, tenant, job type,
   scopes exercised, `request_sha256`. Request bodies are not logged.

### 2.6 Job execution per type

| Job | Worker steps |
|---|---|
| sbom | Materialize the source (extract the archive or clone). Build `AiSbomConfig`. Run `generate_sbom(SbomGenerateRequest(source_path=scratch, config=…))`. Strip local paths. Store the result. |
| analyze | Load the SBOM (`parse_sbom_json` or a prior job's result). Optionally extract the source and parse the policy. Run `run_analysis(AnalysisRunRequest(...), sbom=…, policy=…, llm_client=…)`. Compute `completeness`. Store the result. |
| policy-validate | `parse_cognitive_policy`, then `lint_policy` if the parse succeeded. Convert `LintIssue` to the gateway model (or the new public model, see section 3). |
| remediation | Normalize `FindingsInput` (Pydantic validation and caps). Build `RemediationSynthesizer(sbom, policy, llm_client if use_llm)`. Route to the generic or pentest method. Store the artefacts and the list of components not found in the SBOM. |

The job timeout covers the whole run (default 15 minutes for analyze, 10 for sbom). A cancelled
job kills the worker's process group, which takes the subprocesses with it.

---

## Part 3: Prerequisite changes in the NuGuard fork

These should land before or with the gateway. Changes to public interfaces follow
`.github/skills/pydantic-interface/SKILL.md`.

| # | Change | Why |
|---|---|---|
| P1 | Make `_osv_step` and `_grype_step` report `error` with a reason when the client failed (have `_run_osv` / `_run_grype` set status instead of swallowing errors) | Removes the false-clean result (section 1.3) |
| P2 | Fix the three Semgrep rules: a real exclusion (or none) for the f-string rule, and `pattern-either` for the eval/exec and hardcoded-key rules. Add positive and negative fixtures. | Section 1.5 |
| P3 | Add `nuguard/remediation/public_api.py` with `RemediationRunRequest` (findings union, `use_llm`) and `RemediationRunResult` | The gateway, and other integrators, need a typed contract instead of `list[dict]` |
| P4 | Add a public policy lint API: `lint_cognitive_policy(request) -> PolicyLintResult` with a Pydantic `PolicyLintIssue` | `policy validate` has no public equivalent today |
| P5 | Add an `offline: bool` analysis option that passes `--skip-db-update`/`--offline-scan` to Trivy and disables Grype DB auto-update | Reproducible, egress-free analysis |
| P6 | Optional: let `AiSbomConfig` be built without env defaults (for example `AiSbomConfig.model_construct_isolated(...)` or an `env_defaults=False` switch) | Removes the reliance on scrubbing worker env |
| P7 | Add a `NOTICE` / third-party attribution file covering MITRE ATLAS data, OWASP-derived text and threat-intel sources | Section 1.6 |

## Part 4: Delivery plan

1. **Prerequisites P1, P2 and P4.** These are small changes in the existing code, and they make
   gateway results trustworthy.
2. **Gateway skeleton.** Tokens and scopes, the job table, the artifact store, a single worker,
   and the `policy-validate` job end-to-end with `Prefer: wait`.
3. **SBOM jobs (archive only) and analyze jobs (offline scopes).** Worker sandbox and
   completeness reporting.
4. **Network features.** `sbom:repo`, `analyze:network`, egress allowlist, P5.
5. **Remediation jobs.** P3, all four `FindingsInput` formats, LLM scopes.
6. **Hardening.** Rate limits, retention, audit log, load test with representative repos from
   `tests/apps/`.

## Open decisions

- **Hosting model.** Single-tenant per deployment, or multi-tenant? The design supports both,
  but multi-tenant raises the bar on the worker sandbox.
- **Which LLM provider and model** the operator funds, and whether tenants may bring their own
  key through a secret reference. The default proposed here: the operator's key only.
- **Postgres queue or Redis/arq.** The proposal is Postgres unless throughput needs exceed
  roughly tens of jobs per second.
- **Whether `render_sbom` formats beyond JSON are needed in v1.** They are cheap, so they are
  included.
