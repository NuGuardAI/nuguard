# NuGuard Remote Scan API: Implementation Spec

_Status: ready to implement. Written 2026-11-02 against `develop` (v0.9.15)._
_Audience: Claude Code, implementing this inside the NuGuard repository._
_Related: [`GATEWAY-DESIGN.md`](../../GATEWAY-DESIGN.md) (the broader gateway proposal). This spec builds a narrower first slice of it and says where it differs._

---

## 0. How to use this spec

- Build in the phase order of §12. Each phase has acceptance criteria. Don't start a phase until the previous phase's criteria pass.
- Follow `CLAUDE.md` and `.github/skills/pydantic-interface/SKILL.md` for every public model you add or change.
- Where this spec and the code disagree, the code wins for *facts* (file names, signatures). Note the mismatch in the PR description and keep the spec's *intent*.
- Wherever this spec says **MUST**, a test must prove it.

---

## 1. Goal

Give an external program an HTTP API that does three things:

1. Accept a **GitHub repository URL**, an optional **ref**, and a **GitHub token**.
2. Clone the repo, generate the AI-SBOM, and run NuGuard static analysis.
3. Return **machine-readable scan results** the caller can store, diff and fingerprint.

The first consumer is a Go scan orchestrator (`vulnframework`). It treats each scanner as an adapter with `Run` and `Normalize` steps. It needs results that are:

- **Honest about coverage.** A scanner that didn't run must never look like a clean result.
- **Stable across runs.** The same defect at the same commit must get the same finding identity.
- **Traceable.** Every result records exactly which tool and rule versions produced it.

### 1.1 Non-goals (v1)

- No red-team, behavior, pentest, `validate`, `target` or `enrich_sbom`. The service **never sends traffic to a running application**.
- No multi-tenant SaaS features: RBAC, billing, dashboards.
- No LLM use by default. LLM enrichment is an operator opt-in; callers can't choose models or endpoints.
- No source-archive upload. That's planned for v1.1 (§13).
- No write access to repositories. The service only reads.

---

## 2. Summary of the design

```
caller ──HTTPS──▶ API process (FastAPI + uvicorn)
                    │ Bearer API key → caller identity
                    │ validate → insert job row (no secrets) → hold token in memory
                    │ 202 + Location
                    ▼
               Job store (SQLite default, WAL; Postgres-ready interface)
                    ▲
                    │ in-process dispatcher claims queued jobs
               Worker pool: one child process per job (spawn), hard timeout
                    │ fresh scratch dir; token handed over a pipe, never argv/env-file/disk
                    ├─ git clone (https only, host allowlist, depth 1, size cap)
                    ├─ nuguard.sbom.public_api.generate_sbom
                    ├─ nuguard.analysis.public_api.run_analysis
                    └─ build ScanResult → store JSON → delete scratch dir
```

Key decisions:

| Decision | Choice | Why |
|---|---|---|
| Sync vs async | Async jobs (`202` and polling), with optional `Prefer: wait=N` | Scans take seconds to minutes. Analyzers start subprocesses. |
| Where it lives | New package `nuguard/service/`, optional extra `nuguard[service]` | Reuses the public APIs. Doesn't bloat the base install. |
| Token handling | Accepted per request, **held only in memory**, never persisted | The caller wants to send the token in the request (stated requirement). See §7.2 for how that differs from `GATEWAY-DESIGN.md`. |
| Isolation | One OS process per job, `spawn` start method | Extraction is CPU-bound sync code inside `async def`. Process isolation contains crashes, memory use and env leaks. |
| Queue | Job table in SQLite. `JobStore` protocol allows Postgres later | No extra infrastructure for a single deployment. |
| Result shape | NuGuard-native findings **plus** a normalized projection (`fingerprint`, `rule_id`, `category`, `locations`) | Consumers shouldn't have to reverse-engineer NuGuard internals to build stable identities. |

---

## 3. Prerequisite changes in NuGuard core

These land **before** the service (phase 1). Without them the API would return misleading results. Each one follows the pydantic-interface skill.

### P1. No false-clean scanner status (blocking)

**Problem:** in `nuguard/analysis/static_analyzer.py` (around L270–330), OSV and Grype set `{"status": "ok", "findings": "0"}` even when the client failed, for example with no network. Trivy behaves the same when its DB update fails.

**Change:**

- Use a closed set of statuses: `ok | failed | skipped | disabled | not_applicable | timeout`.
  - `ok`: the tool ran and finished.
  - `failed`: the tool was attempted and errored, or couldn't reach its data source.
  - `skipped`: a precondition was missing (binary not installed, offline mode).
  - `disabled`: the request turned it off.
  - `not_applicable`: nothing for the tool to look at (for example, no IaC files for Checkov).
  - `timeout`: the tool hit its deadline.
- `_run_osv`, `_run_grype` and the Trivy, Checkov and Semgrep wrappers MUST return or raise enough to tell `ok` apart from `failed`. Don't swallow exceptions into an empty list.
- Add a `reason` key (short, sanitized, no stderr dump) for anything other than `ok` or `disabled`.
- Keep `tool_status: dict[str, dict[str, str]]` for backward compatibility. Add a typed `AnalysisRunResult.tool_runs: list[ToolRun]`:

```python
class ToolRunStatus(str, Enum):
    OK = "ok"; FAILED = "failed"; SKIPPED = "skipped"
    DISABLED = "disabled"; NOT_APPLICABLE = "not_applicable"; TIMEOUT = "timeout"

class ToolRun(BaseModel):
    tool: str                    # "osv", "grype", "trivy", "checkov", "semgrep", "nga-rules", "atlas", "supply-chain"
    version: str | None = None   # binary or rule-pack version actually used
    status: ToolRunStatus
    findings: int = 0
    duration_ms: int | None = None
    reason: str | None = None    # sanitized; required unless status in {ok, disabled}
```

**Tests:** run with the network disabled (monkeypatch the OSV client to raise; point Grype at a nonexistent DB). Assert `status == "failed"` with a reason, never `ok`.

### P2. Fix the Semgrep AI rules that can never fire (blocking)

In `nuguard/analysis/plugins/semgrep_rules/ai-security.yaml`:

- `nuguard-llm-prompt-injection-fstring`: its `pattern-not` is identical to its `pattern`, so it never matches. Replace it with a real exclusion, or remove the exclusion. Widen the call shapes beyond `.chat(...)`: OpenAI `chat.completions.create`, Anthropic `messages.create`, and LangChain `invoke`/`ainvoke`.
- `nuguard-eval-on-llm-output`: it requires `eval` **and** `exec` at the same time. Switch to `pattern-either`.
- The hardcoded-key rule has the same defect, per `GATEWAY-DESIGN.md` §1.5. Fix it the same way.
- Add a positive and a negative fixture for each rule under `tests/analysis/fixtures/semgrep/`. Add a test that runs Semgrep on them, skipped if `semgrep` isn't on PATH.

### P3. Token support in the public SBOM API (blocking)

`SbomGenerateRequest` has no credential field. The CLI's `--token` path (`nuguard/cli/commands/sbom.py`, `_inject_token`) embeds the token into the clone URL. The service must not do that, because it would put the token in process argv and in git's error output.

**Change:**

```python
class SbomGenerateRequest(BaseModel):
    source_path: str | None = None
    repo_url: str | None = None
    repo_ref: str = "main"
    repo_token: SecretStr | None = None          # NEW: never serialized in results
    config: AiSbomConfig = Field(default_factory=AiSbomConfig)
```

- Thread `repo_token` through `generator.from_repo` → `AiSbomExtractor.extract_from_repo` → `_clone_repo` and `resolve_and_clone`/`clone_github_subfolder`.
- In `_clone_repo`, authenticate with an **ephemeral credential helper**, not the URL. Pass `-c credential.helper=` (clear any helpers) and `-c credential.helper=!f() { echo username=x-access-token; echo "password=$NUGUARD_GIT_TOKEN"; }; f`. Set `NUGUARD_GIT_TOKEN` only in that subprocess's env, along with `GIT_TERMINAL_PROMPT=0`.
  - Alternatively, use `GIT_ASKPASS` pointing at a tiny script in the scratch dir that echoes the env var. Pick one approach and document it. Either way, the token MUST NOT appear in argv, the URL, logs or exception text.
- `resolve_and_clone` already accepts `token=`. Pass `repo_token.get_secret_value()` there for the GitHub API tree calls.
- `SbomGenerateResult.source_ref` stays sanitized. Add `resolved_commit: str | None`: the SHA actually checked out (`git rev-parse HEAD` after clone).
- Update `tests/contracts/public_api.schema.json`.

**Tests:**

- The token never appears in `model_dump_json()`, logs captured with `caplog`, exception messages, or the `subprocess.run` argv (mock and inspect it).
- Cloning a private fixture works through a local `git daemon` or an http-backend fixture with basic auth.

### P4. Clone safety (blocking)

`validate_url` currently has no scheme or host restriction (`core.py` around L3617).

- Add `AiSbomConfig.allowed_git_hosts: list[str] | None = None`. `None` keeps today's library behavior; the service always sets it.
- Add `AiSbomConfig.clone_timeout_s` (default 120) and `max_repo_bytes` (default 500 MB, measured after clone with a `du`-equivalent walk; abort when exceeded).
- When `allowed_git_hosts` is set:
  - Reject non-`https` schemes.
  - Reject userinfo in the URL.
  - Reject hosts not on the list, including IP literals and `localhost`.
  - Set `GIT_ALLOW_PROTOCOL=https`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `HOME=<scratch>`.
  - Disable submodules and LFS smudge (`GIT_LFS_SKIP_SMUDGE=1`, no `--recurse-submodules`).

### P5. Finding identity and location projection (blocking for the consumer)

`nuguard.models.finding.Finding` has no `rule_id`, `file` or `line`. Finding IDs and titles are built per run, and some include model-name strings (NGA-002 fires once per model name). Consumers can't build stable fingerprints from that.

**Add** to `Finding`, all optional and additive:

```python
rule_id: str | None = None        # "NGA-012", "nuguard-eval-on-llm-output", "CVE-2024-1234", "CKV_AWS_20"
source_tool: str | None = None    # matches ToolRun.tool
locations: list[FindingLocation] = Field(default_factory=list)
kind: Literal["code", "posture", "dependency", "iac", "container", "supply_chain"] | None = None

class FindingLocation(BaseModel):
    path: str                       # repo-relative, POSIX separators, never absolute
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None       # enclosing function/class if known
```

Populate them at the source:

| Producer | rule_id | locations | kind |
|---|---|---|---|
| Semgrep | the rule `check_id` | the result's `path`, `start.line`, `end.line` | `code` |
| NGA rules | `NGA-xxx` | the `Evidence.location` of the offending SBOM node(s) (`nuguard/sbom/models.py` `SourceLocation`) | `posture`, or `code` when the rule is anchored to a specific call |
| ATLAS | its technique/rule id | same as NGA | `posture` |
| OSV / Grype | the advisory id (prefer the GHSA/CVE alias) | the lockfile/manifest path | `dependency` |
| Checkov | `check_id` | `file_path`, `file_line_range` | `iac` |
| Trivy | the vulnerability/misconfig id | the target path | `container` or `iac` |
| Supply chain | its rule id | the script/workflow path | `supply_chain` |

All paths MUST be relative to the clone root, with the scratch dir stripped. Add a test: no `locations[].path` starts with `/` or contains the scratch prefix.

**Also:** dedupe NGA-002, and any rule that fires once per model-name string, so it fires **once per SBOM node** with the model names in `evidence`. Gate this behind `AnalysisRunRequest.dedupe_posture: bool = True`. Add a regression test using a fixture with 30 model-name strings and one client.

### P6. Packaging

Base `pip install nuguard==0.9.15` fails at start-up with `No module named 'cryptography'`. Either make `cryptography` a base dependency, or make the import lazy inside `nuguard/common/secret_store.py`. Add a CI step that installs the wheel in a clean venv and runs `nuguard --help`.

---

## 4. Package layout

```
nuguard/service/
├── __init__.py
├── __main__.py          # `python -m nuguard.service` → uvicorn
├── app.py               # create_app(settings) -> FastAPI; routers, middleware, error handlers
├── settings.py          # ServiceSettings (pydantic-settings) loaded from nuguard.yaml `service:` + env
├── auth.py              # API-key verification, caller identity
├── models.py            # public request/response models (§6)
├── store/
│   ├── __init__.py      # JobStore Protocol
│   ├── sqlite.py        # SqliteJobStore (WAL, single writer)
│   └── migrations/001_jobs.sql
├── dispatcher.py        # claims queued jobs, enforces concurrency, owns the in-memory token vault
├── vault.py             # TokenVault: job_id -> SecretStr, in memory, wiped on terminal state
├── worker.py            # child-process entrypoint: run_scan_job(spec, token) -> ScanResult
├── pipeline.py          # pure orchestration: clone → sbom → analyze → build_result
├── fingerprint.py       # stable finding fingerprints (§6.5)
├── results.py           # ScanResult assembly, SARIF rendering via nuguard.output
├── webhooks.py          # optional completion callback (HMAC-signed)
└── tests/ → put tests in tests/service/ (repo convention)
```

- **Entry points** (`pyproject.toml`):
  - `[project.scripts]`: add `nuguard-service = "nuguard.service.__main__:main"`.
  - Add a CLI command `nuguard serve` in `nuguard/cli/commands/serve.py`, with lazy imports per `CLAUDE.md`.
- **Optional extra:** `service = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "nuguard[secrets]"]`.
- **Docker:** `deploy/service/Dockerfile`. Base it on `python:3.12-slim` and add `git`, plus pinned `semgrep`, `trivy`, `grype` (DB baked in), `checkov` and `osv-scanner` if used. Run as a non-root user. Write tool versions to `/etc/nuguard/tools.json` at build time; the service reads that file for provenance (§6.4).

---

## 5. Configuration

Per `CLAUDE.md`, everything configurable goes under a `service:` section of `nuguard.yaml`, with `${ENV}` interpolation. Env vars `NUGUARD_SERVICE_*` override the file.

```yaml
service:
  bind: 0.0.0.0
  port: 8080
  public_base_url: https://nuguard.internal.example.com   # used in Location headers / links
  api_keys:                       # SHA-256 hex of each key; plaintext never stored
    - id: vulnframework-prod
      sha256: ${NUGUARD_SERVICE_KEY_VF_SHA256}
      max_concurrent_scans: 4
      max_queued_scans: 50
  git:
    allowed_hosts: [github.com]
    clone_timeout_s: 120
    max_repo_bytes: 524288000
  jobs:
    max_concurrent: 4             # global worker processes
    job_timeout_s: 900            # hard wall clock per job; child is SIGKILLed after
    queue_limit: 200
    result_ttl_hours: 168         # results + SBOMs deleted after this
    scratch_dir: /var/lib/nuguard/scratch
    db_path: /var/lib/nuguard/service.db
  analysis_defaults:              # caller can narrow these, never widen them beyond `allow`
    min_severity: low
    network: true                 # OSV/Grype/Trivy DB lookups
    tools: [nga-rules, atlas, semgrep, osv, grype, checkov, trivy, supply-chain]
  llm:
    enabled: false                # operator opt-in; caller can request use_llm only if true
    model: ${NUGUARD_LLM_MODEL}
    api_key: ${LITELLM_API_KEY}
  webhooks:
    allowed_hosts: []             # empty = callbacks disabled
    secret: ${NUGUARD_SERVICE_WEBHOOK_SECRET}
  tls:                            # optional; usually terminate TLS at a proxy
    cert_file: null
    key_file: null
```

Validate at startup. Refuse to start if:

- `api_keys` is empty;
- `git.allowed_hosts` is empty;
- `jobs.max_concurrent < 1`;
- `bind` isn't loopback **and** no TLS or proxy setting (`service.trust_proxy: true`) is configured. Log a clear message saying why.

---

## 6. API contract

All routes are under `/v1`. Content type is JSON. All models live in `nuguard/service/models.py`, are JSON-safe (`model_dump(mode="json")`) and are registered in the contract schema test. The FastAPI-generated OpenAPI document at `/v1/openapi.json` is the published contract. Commit a snapshot to `nuguard/service/openapi.json` and test that it doesn't drift.

### 6.1 Authentication

- `Authorization: Bearer <api_key>`. Compare by SHA-256 using `hmac.compare_digest` against the configured hashes.
- The caller identity is the `api_keys[].id`. Every job row stores `owner_id`. Callers only see their own jobs: return `404`, not `403`, for other callers' jobs.
- Missing or invalid key → `401` with `WWW-Authenticate: Bearer`.
- Generate keys with `nuguard serve keygen`. It prints the key once and its SHA-256 for the config.

### 6.2 Endpoints

| Method & path | Purpose | Success |
|---|---|---|
| `POST /v1/scans` | Submit a scan | `202` + `ScanJob` + `Location: /v1/scans/{id}`. With `Prefer: wait=N` (N ≤ 120), may return `200` + `ScanJob` with `status=succeeded` if it finishes in time. |
| `GET /v1/scans/{id}` | Job status | `200` `ScanJob` |
| `GET /v1/scans/{id}/result` | Full result | `200` `ScanResult`; `409` if not terminal; `410` if expired |
| `GET /v1/scans/{id}/result?format=sarif` | SARIF 2.1.0 | `200` `application/sarif+json` |
| `GET /v1/scans/{id}/sbom?format=json\|cyclonedx\|markdown` | AI-SBOM via `render_sbom` | `200` |
| `POST /v1/scans/{id}/cancel` | Best-effort cancel | `202` `ScanJob` |
| `GET /v1/scans?status=&repo=&cursor=&limit=` | List the caller's jobs | `200` `{items: [ScanJob], next_cursor}` |
| `GET /v1/healthz` | Liveness (no auth) | `200 {"status":"ok"}` |
| `GET /v1/readyz` | Readiness (no auth): DB writable, scratch writable, `git` present, tools present | `200` or `503` with failing checks listed (names only) |
| `GET /v1/capabilities` | Tool versions, enabled tools, limits, whether LLM is available | `200` `Capabilities` |

### 6.3 Request model

```python
class ScanOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[ToolName] | None = None          # subset of server analysis_defaults.tools; None = all allowed
    min_severity: Severity = Severity.LOW
    include_sbom: bool = False                   # embed full AI-SBOM in result (can be large)
    include_posture: bool = True                 # include kind=posture findings
    network: bool = True                         # False → OSV/Grype/Trivy-DB marked skipped, not ok
    use_llm: bool = False                        # rejected 422 unless server llm.enabled
    subpath: str | None = None                   # scan only this repo-relative directory

class ScanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_url: HttpUrl                            # https://github.com/org/repo (or .../tree/<ref>/<subpath>)
    ref: str | None = None                       # branch/tag/SHA; None = default branch
    github_token: SecretStr | None = None        # read-only token; optional for public repos
    options: ScanOptions = Field(default_factory=ScanOptions)
    client_reference: str | None = Field(default=None, max_length=200)  # caller's own id, echoed back
    callback_url: HttpUrl | None = None          # optional; host must be in webhooks.allowed_hosts
```

Validation, returned as `422` with a `ProblemDetail`:

- `repo_url`: https, host in `git.allowed_hosts`, no userinfo, no query or fragment, and the path matches `/<owner>/<repo>(.git)?(/tree/<ref>/<subpath>)?`. Reuse `nuguard/common/github_url.py`.
- `ref`: reuse `validate_ref`. No leading `-`, no `..`, no control characters, ≤ 255 characters.
- `github_token`: 20–255 characters, printable ASCII. **Don't** reject unknown prefixes. **Do** log (without the value) a warning on classic `ghp_` tokens, because fine-grained read-only tokens are recommended.
- `subpath`: relative, no `..`, no leading `/`.
- `options.tools`: every entry must be in the server's allowed set.

**Idempotency:** honor an `Idempotency-Key` header (≤ 128 characters), scoped by `owner_id`, for 24 h. A repeat with the same key and the same request hash returns the original job (`200`). The same key with a different body returns `409`. The request hash excludes `github_token`.

### 6.4 Response models

```python
class ScanStatus(str, Enum):
    QUEUED = "queued"; RUNNING = "running"; SUCCEEDED = "succeeded"
    PARTIAL = "partial"          # finished, but ≥1 requested tool did not reach `ok`/`not_applicable`
    FAILED = "failed"; CANCELLED = "cancelled"; EXPIRED = "expired"

class ScanPhase(str, Enum):
    QUEUED = "queued"; CLONING = "cloning"; SBOM = "sbom"; ANALYZING = "analyzing"; FINALIZING = "finalizing"

class ScanJob(BaseModel):
    id: str                       # "scn_" + 26-char ULID
    status: ScanStatus
    phase: ScanPhase | None
    repo_url: str                 # sanitized
    ref_requested: str | None
    commit_sha: str | None        # set once cloned
    client_reference: str | None
    created_at: datetime; started_at: datetime | None; finished_at: datetime | None
    error: ProblemDetail | None   # only for failed/cancelled
    links: dict[str, str]         # self, result, sarif, sbom

class Provenance(BaseModel):
    nuguard_version: str
    service_version: str
    image_digest: str | None      # from env NUGUARD_IMAGE_DIGEST, set at deploy
    rulepack_sha256: str          # hash of bundled semgrep rules + NGA rule table
    tools: list[ToolVersion]      # name + exact version, from /etc/nuguard/tools.json or `--version`
    options_effective: ScanOptions  # after server clamping
    llm_used: bool

class Coverage(BaseModel):
    tool_runs: list[ToolRun]      # from P1, one per requested tool, always present
    languages_detected: list[str]
    files_scanned: int
    files_skipped: int
    gaps: list[str]               # human-readable, e.g. "osv: failed (network unreachable)"

class ScanFinding(BaseModel):
    fingerprint: str              # §6.5, stable
    rule_id: str
    source_tool: str
    kind: Literal["code","posture","dependency","iac","container","supply_chain"]
    severity: Severity
    title: str
    description: str
    locations: list[FindingLocation]
    affected_component: str | None
    references: list[str]
    owasp_llm_ref: str | None
    owasp_asi_ref: str | None
    mitre_atlas_technique: str | None
    remediation: str | None
    raw: Finding                  # full NuGuard Finding, for consumers who want everything

class InventorySummary(BaseModel):
    node_counts: dict[str, int]   # by SBOM node type: AGENT, MODEL, TOOL, PROMPT, MCP_SERVER, ...
    edge_count: int
    frameworks: list[str]
    model_providers: list[str]

class ScanResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    job: ScanJob
    provenance: Provenance
    coverage: Coverage
    summary: dict[Severity, int]  # counts after min_severity / include_posture filtering
    findings: list[ScanFinding]   # sorted by (severity desc, rule_id, fingerprint) for determinism
    inventory: InventorySummary
    sbom: dict | None = None      # AiSbomDocument JSON when include_sbom
    result_sha256: str            # SHA-256 of canonical JSON of everything above except this field

class ProblemDetail(BaseModel):   # RFC 9457
    type: str                     # e.g. "https://nuguard.ai/problems/repo-not-found"
    title: str
    status: int
    code: str                     # stable machine code, see §6.6
    detail: str | None            # sanitized; never stderr, paths, tokens
```

**Status rule:** `succeeded` only if every requested tool ended `ok` or `not_applicable`. Any `failed`, `skipped` or `timeout` makes the job `partial`, and the reason goes into `coverage.gaps`. **A scan whose clone or SBOM step fails is `failed`, never `partial`.**

**Canonical JSON** for `result_sha256`: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` on `model_dump(mode="json")`, with `result_sha256` excluded. The stored result is immutable once written.

### 6.5 Fingerprint algorithm

Implement in `nuguard/service/fingerprint.py`. The fingerprint MUST NOT include line numbers, absolute paths, run ids, timestamps or titles.

```
fingerprint = "nf1_" + sha256_hex( "\x1f".join([
    normalized_repo,            # "github.com/owner/repo", lowercase
    kind,
    source_tool,
    rule_id,
    identity_key,               # per kind, below
]) )[:32]
```

| kind | identity_key |
|---|---|
| `code` | `path` + `"#"` + `symbol` if known, else `path` + `"#"` + sha256 of the matched source snippet with whitespace normalized |
| `posture` | the SBOM node's stable key: `node_type:canonical_name` (node ids are not stable across runs; use type + canonical name) |
| `dependency` | `ecosystem:package` + `"@"` + major version + `"#"` + the lockfile path |
| `iac` | `path` + `"#"` + resource address (Checkov resource / Trivy `CauseMetadata.Resource`) |
| `container` | image reference without tag digest + package name |
| `supply_chain` | `path` + `"#"` + rule-specific key (script name / workflow job id) |

**Test:** scan the same fixture repo twice, and once more after reformatting files (adding blank lines). All three runs MUST produce the same set of fingerprints.

### 6.6 Error codes

| HTTP | `code` | When |
|---|---|---|
| 401 | `unauthorized` | Bad or missing API key |
| 404 | `not_found` | Unknown job, or another caller's job |
| 409 | `not_finished` / `idempotency_conflict` | Result requested early / key reused with a different body |
| 410 | `expired` | Result past its TTL |
| 413 | `request_too_large` | Body > 16 KB |
| 422 | `invalid_request` / `host_not_allowed` / `llm_not_available` / `tool_not_allowed` | Validation |
| 429 | `rate_limited` / `queue_full` | Per-key or global limits; include `Retry-After` |
| 503 | `not_ready` | Readiness failing |

Job-level `error.code` values for `failed` jobs:

- `repo_not_found_or_no_access`: GitHub 404, or 403 with a token. Don't distinguish them, so private repos can't be probed.
- `auth_failed`
- `ref_not_found`
- `repo_too_large`
- `clone_timeout`
- `sbom_failed`
- `analysis_crashed`
- `job_timeout`
- `worker_lost`: the process died or the service restarted mid-job.
- `credential_unavailable`: the service restarted while the job was queued; the token was never persisted (§7.2).

### 6.7 Webhook (optional, off by default)

If `callback_url` is set and its host is in `webhooks.allowed_hosts`, then on terminal status the service POSTs the `ScanJob` (not the full result) with:

- `X-NuGuard-Signature: sha256=<hmac(secret, body)>`
- `X-NuGuard-Timestamp`

Retry three times with exponential backoff. Webhook failures never change the job status. Resolve the callback host's DNS, refuse private, loopback and link-local IPs unless explicitly allowlisted, and don't follow redirects.

---

## 7. Execution model

### 7.1 Job lifecycle

1. **Submit.** In one transaction, `POST /v1/scans`:
   - validates the request;
   - checks per-key and global limits;
   - writes the job row with status `queued` and the request JSON **minus the token**;
   - puts the token into `TokenVault[job_id]`.
2. **Dispatch.** The dispatcher, an asyncio task in the API process:
   - polls for `queued` jobs, oldest first, respecting `max_concurrent` and per-key `max_concurrent_scans`;
   - claims a job with a conditional update (`UPDATE … SET status='running' WHERE id=? AND status='queued'`);
   - starts the worker.
3. **Worker.** A `multiprocessing` context with `spawn`, so the child doesn't inherit open DB handles or other jobs' memory:
   - The parent passes a `JobSpec` (no secret) as an argument, and passes the token over a one-shot `multiprocessing.Pipe`.
   - The child's environment is **built explicitly**: `PATH`, `LANG`, `HOME=<scratch>/home`, `TMPDIR=<scratch>/tmp`, plus LLM vars only if `use_llm` is approved. Every `NUGUARD_*`, `AISBOM_*`, `LITELLM_*`, `GH_TOKEN` and `GITHUB_TOKEN` from the parent is dropped, so ambient credentials can't leak into a scan (see `GATEWAY-DESIGN.md` §2.5 item 3).
   - The child writes phase updates to the parent over the pipe. The parent persists them.
4. **Pipeline** (`pipeline.py`, pure and directly unit-testable):
   1. Create `<scratch_root>/<job_id>/` with mode `0700`.
   2. `generate_sbom(SbomGenerateRequest(repo_url=…, repo_ref=…, repo_token=…, config=AiSbomConfig(allowed_git_hosts=…, …)))`.
      - Capture `resolved_commit`.
      - The clone must be kept for analysis. Add a `keep_clone_dir: str | None` option to `AiSbomConfig`, or perform the clone in the service and call `generate_sbom(source_path=clone_dir)`. **Prefer the latter**: the service owns cloning through a shared helper extracted from `_clone_repo` with P3 and P4 applied, then passes the same directory to both steps. That avoids cloning twice.
   3. `run_analysis(AnalysisRunRequest(source_path=clone_dir, enable_*=…, min_severity=…, dedupe_posture=True), sbom=…)`, with `llm_client=None` unless approved.
   4. Build the `ScanResult`: project findings to `ScanFinding`, compute fingerprints, relativize paths, compute coverage and status, hash.
   5. Return the result over the pipe, or write it to `<scratch>/result.json` and return its path. The parent then moves it into the store.
5. **Finalize.** The parent stores the result JSON in the DB (or in `results/<id>.json` with a DB pointer when > 5 MB). It sets the terminal status, wipes `TokenVault[job_id]`, and deletes the scratch dir in a `finally` block, including on timeout and crash.
6. **Timeout.** The parent enforces `job_timeout_s`: SIGTERM, then SIGKILL after 5 s, then the whole process group (start the child with `os.setsid` so scanner grandchildren die too). Status `failed`, code `job_timeout`.
7. **Cancel.** A queued job becomes `cancelled` immediately. A running job gets the same kill sequence, then `cancelled`.

### 7.2 Token handling (differs from `GATEWAY-DESIGN.md`)

`GATEWAY-DESIGN.md` §2.5 says private-repo credentials should come from a per-tenant secret reference and never from the request body. The caller's requirement here is a token in the request. This spec allows that, with these mitigations, all of which are MUST:

- The token is `SecretStr` end to end. It is never written to the DB, logs, result, webhook, error detail or argv.
- It lives only in `TokenVault` (a process-memory dict) until the job ends, then it's deleted and the reference dropped.
- On restart, queued and running jobs whose token is gone are marked `failed` with `credential_unavailable` or `worker_lost`. The client resubmits; the idempotency key makes that safe.
- It's used only for `git clone` and the GitHub tree API calls in `resolve_and_clone`. No other outbound request carries it.
- Docs recommend a **fine-grained, read-only token scoped to the target repos** (`Contents: read`, `Metadata: read`).
- **Phase 2 extension (optional):** `github_token_ref: str` resolves against a server-side secret store. It's mutually exclusive with `github_token`. This lines up with the gateway design without breaking the v1 contract.

### 7.3 Concurrency and resource limits

- Global `max_concurrent` worker processes; per-key `max_concurrent_scans`; `queue_limit` → `429 queue_full`.
- On Linux, the child sets `resource.setrlimit`: `RLIMIT_AS` (default 4 GB, configurable), `RLIMIT_NPROC`, `RLIMIT_FSIZE` (1 GB).
- Docs state plainly: **for untrusted repos, run the service in a container** with a read-only root fs, a tmpfs or volume for scratch, no host mounts, CPU and memory limits, and an egress allowlist covering `github.com`, `api.github.com`, `codeload.github.com`, `api.osv.dev`, Grype/Trivy DB hosts if not baked in, and the LLM endpoint if enabled.

### 7.4 Retention

A background task runs every 15 minutes:

- Deletes results and SBOMs older than `result_ttl_hours` and sets status `expired`.
- Deletes orphaned scratch dirs older than `job_timeout_s` × 2.
- Vacuums SQLite weekly.

---

## 8. Storage schema (SQLite)

```sql
CREATE TABLE scan_jobs (
  id              TEXT PRIMARY KEY,
  owner_id        TEXT NOT NULL,
  status          TEXT NOT NULL,
  phase           TEXT,
  repo_url        TEXT NOT NULL,            -- sanitized
  ref_requested   TEXT,
  commit_sha      TEXT,
  client_reference TEXT,
  request_json    TEXT NOT NULL,            -- ScanCreateRequest minus github_token
  request_sha256  TEXT NOT NULL,            -- for idempotency (token excluded)
  idempotency_key TEXT,
  error_json      TEXT,
  result_ref      TEXT,                     -- inline marker or file path
  result_sha256   TEXT,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  expires_at      TEXT
);
CREATE INDEX ix_jobs_owner_created ON scan_jobs(owner_id, created_at DESC);
CREATE INDEX ix_jobs_status ON scan_jobs(status, created_at);
CREATE UNIQUE INDEX ux_jobs_idem ON scan_jobs(owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE scan_results (
  job_id     TEXT PRIMARY KEY REFERENCES scan_jobs(id) ON DELETE CASCADE,
  result_json TEXT NOT NULL,
  sbom_json   TEXT
);

CREATE TABLE audit_log (
  ts TEXT NOT NULL, owner_id TEXT, action TEXT NOT NULL,  -- create|cancel|read_result|auth_fail
  job_id TEXT, repo_url TEXT, remote_addr TEXT, request_sha256 TEXT
);
```

- Open with `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000`.
- Only the API process writes. Workers report back over the pipe.
- Define a `JobStore` Protocol so a `PostgresJobStore` (`SELECT … FOR UPDATE SKIP LOCKED`) can be added later without touching routes.

---

## 9. Security requirements checklist

Each item needs a test in `tests/service/test_security.py`:

1. The token never appears in: the DB file bytes, `caplog` output, the API response, the webhook body, `ProblemDetail`, child process argv (inspect with `psutil` in an integration test), or git stderr surfaced in errors.
2. Non-https, non-allowlisted host, IP literal, userinfo URL, or `file://` → `422`. Nothing gets cloned.
3. `ref="--upload-pack=touch /tmp/pwned"` → `422`.
4. A `subpath` with `..` → `422`.
5. Another caller's job id → `404`.
6. The result never contains absolute paths or the scratch prefix.
7. Ambient `GITHUB_TOKEN` or `LITELLM_API_KEY` in the server env is not visible inside the worker (the worker prints its env in a test hook).
8. A repo with a symlink pointing outside the clone (`ln -s /etc/passwd x`): scanners must not follow it out of the clone. At minimum, the result must not contain contents from outside the clone.
9. Request body > 16 KB → `413`.
10. A failed auth is rate-limited per remote address (10/min) → `429`.
11. `use_llm=true` with server LLM disabled → `422 llm_not_available`.
12. Error responses never include stack traces. In debug mode traces go to logs only, never to the response.

---

## 10. Observability

- Structured logs via `nuguard.common.logging` (structlog), JSON in service mode. Every line carries `job_id` and `owner_id`. Never log request bodies.
- `GET /v1/metrics` is Prometheus text, optional and behind `service.metrics: true`:
  - `nuguard_scans_total{status}`
  - `nuguard_scan_duration_seconds` (histogram, by phase)
  - `nuguard_queue_depth`
  - `nuguard_tool_runs_total{tool,status}`

---

## 11. Testing plan

| Layer | Location | What |
|---|---|---|
| Prereq unit | `tests/analysis/`, `tests/sbom/` | P1 statuses; P2 rule fixtures; P3 token threading and redaction; P4 URL/host policy; P5 locations/rule_id/kind and NGA-002 dedupe |
| Contract | `tests/contracts/` | New public models registered; schema regenerated; `nuguard/service/openapi.json` snapshot matches the app |
| Service unit | `tests/service/test_models.py`, `test_fingerprint.py`, `test_store.py`, `test_vault.py` | Validation matrix; fingerprint stability; idempotency; TTL; vault wipe |
| Pipeline | `tests/service/test_pipeline.py` | Run `pipeline.run()` on local fixture repos (`tests/apps/…`) by cloning from a local bare repo served over `git http-backend` with basic auth, to exercise the token path for real |
| API | `tests/service/test_api.py` | `httpx.AsyncClient` + `ASGITransport`. Submit → poll → result; SARIF; SBOM render; cancel; 401/404/409/410/422/429 |
| Process | `tests/service/test_worker.py` | Timeout kills the process group; crash → `worker_lost`; restart with a queued job → `credential_unavailable` |
| Security | `tests/service/test_security.py` | §9 |
| Offline honesty | `tests/service/test_offline.py` | With `network=false`, or the network blocked: the job is `partial`, OSV/Grype are `skipped`/`failed`, `coverage.gaps` is non-empty |
| E2E (opt-in) | marker `service_e2e`, env `NUGUARD_SERVICE_E2E=1` | Real GitHub public repo pinned to a SHA (for example `openai/openai-quickstart-python@ec8890d`). Two runs give identical fingerprints and `result_sha256` (excluding the timestamp fields that get hashed; put timestamps in `job`, and exclude `job.created_at/started_at/finished_at` from the hash) |

Run per `CLAUDE.md` before every commit:

- `uv run ruff check nuguard/ tests/`
- `uv run mypy nuguard/`
- `uv run pytest tests/contracts -q`
- `uv run pytest tests/service -q`
- then the full suite.

---

## 12. Delivery phases

**Phase 1: Core prerequisites (P1–P6).** Separate PRs are fine.
_Accept when:_
- Offline analysis never reports `ok` for OSV/Grype/Trivy.
- The three Semgrep rules fire on their positive fixtures and not on their negatives.
- `generate_sbom` clones a private fixture with `repo_token`, and a redaction test passes.
- Every analysis finding has `rule_id`, `source_tool` and `kind`, and code, IaC and dependency findings have `locations`.
- Clean-venv `nuguard --help` passes.

**Phase 2: Service skeleton.**
_Scope:_ `settings`, `auth`, `models`, SQLite store, `TokenVault`, dispatcher, worker with a stub pipeline, all endpoints except SBOM render and webhooks, `healthz`/`readyz`/`capabilities`, `nuguard serve` and `keygen`.
_Accept when:_
- API tests pass against the stub pipeline.
- Timeout, cancel and restart behaviors are proven.
- OpenAPI snapshot is committed.

**Phase 3: Real pipeline.**
_Scope:_ wire clone → SBOM → analyze → result; fingerprints; SARIF; SBOM render; coverage and `partial` logic.
_Accept when:_
- The pipeline test against the local http-backend private repo passes.
- Fingerprints stay stable across reformatting.
- `result_sha256` is reproducible.

**Phase 4: Hardening and packaging.**
_Scope:_ rlimits, retention task, audit log, rate limits, §9 security tests, Dockerfile with pinned tools and `/etc/nuguard/tools.json`, metrics, docs.
_Accept when:_
- All §9 tests pass.
- The container image builds; `readyz` passes inside it; the E2E test passes from inside the container.

**Phase 5 (optional):** webhooks, `github_token_ref`, a Postgres store, source-archive upload.

---

## 13. Documentation to add

- `documentation/docs/remote-api.md`: quick start (`pip install "nuguard[service]"`, keygen, config, `nuguard serve`), auth, an endpoint walkthrough with `curl`, result schema, fingerprint semantics, status semantics (`succeeded` vs `partial`), token guidance (fine-grained, read-only), the deployment recommendation (container plus egress allowlist), and limits.
- Add a short "Remote API" section to `documentation/docs/platform-public-api-integration.md` that links to it.
- `documentation/docs/CHANGELOG.md` entries for P1–P6 and the service.
- Leave `GATEWAY-DESIGN.md` unchanged, but add a line to its header pointing to this spec as the implemented first slice.

---

## 14. Example interaction

```bash
# Submit
curl -sS -X POST https://nuguard.internal/v1/scans \
  -H "Authorization: Bearer $NUGUARD_API_KEY" \
  -H "Idempotency-Key: vf-run-20261102-acme-agent" \
  -H "Content-Type: application/json" \
  -d '{
        "repo_url": "https://github.com/acme/support-agent",
        "ref": "main",
        "github_token": "github_pat_…",
        "options": {"min_severity": "low", "include_posture": false},
        "client_reference": "vf-run-8841"
      }'
# → 202
# {"id":"scn_01JC…","status":"queued","phase":"queued","repo_url":"https://github.com/acme/support-agent",
#  "links":{"self":"/v1/scans/scn_01JC…","result":"/v1/scans/scn_01JC…/result", …}, …}

# Poll
curl -sS https://nuguard.internal/v1/scans/scn_01JC… -H "Authorization: Bearer $NUGUARD_API_KEY"
# → {"status":"running","phase":"analyzing","commit_sha":"9ec32b9…", …}

# Result
curl -sS https://nuguard.internal/v1/scans/scn_01JC…/result -H "Authorization: Bearer $NUGUARD_API_KEY"
```

Abbreviated `ScanResult`:

```json
{
  "schema_version": "1.0",
  "job": {"id": "scn_01JC…", "status": "partial", "commit_sha": "9ec32b9…", "client_reference": "vf-run-8841"},
  "provenance": {
    "nuguard_version": "0.10.0", "service_version": "0.10.0",
    "image_digest": "sha256:…", "rulepack_sha256": "…",
    "tools": [{"name": "semgrep", "version": "1.177.0"}, {"name": "trivy", "version": "0.74.0"}],
    "llm_used": false
  },
  "coverage": {
    "tool_runs": [
      {"tool": "nga-rules", "status": "ok", "findings": 4},
      {"tool": "semgrep", "status": "ok", "findings": 2},
      {"tool": "osv", "status": "failed", "findings": 0, "reason": "network unreachable"},
      {"tool": "checkov", "status": "not_applicable", "findings": 0}
    ],
    "gaps": ["osv: failed (network unreachable)"]
  },
  "summary": {"critical": 0, "high": 2, "medium": 1, "low": 0, "info": 0},
  "findings": [
    {
      "fingerprint": "nf1_4f0c…",
      "rule_id": "nuguard-eval-on-llm-output",
      "source_tool": "semgrep",
      "kind": "code",
      "severity": "high",
      "title": "LLM output passed to eval()",
      "locations": [{"path": "app/tools/calc.py", "start_line": 42, "end_line": 42, "symbol": "run_calc"}],
      "owasp_llm_ref": "LLM05",
      "raw": {"finding_id": "…", "…": "…"}
    }
  ],
  "inventory": {"node_counts": {"AGENT": 2, "MODEL": 3, "TOOL": 7, "MCP_SERVER": 1}, "edge_count": 31,
                "frameworks": ["openai-agents"], "model_providers": ["openai"]},
  "result_sha256": "…"
}
```

### 14.1 Consumer adapter sketch (Go, for `vulnframework`)

For reference only; it isn't part of this repo. The client implements `scan.Adapter`:

- `Run` does POST, then polls with backoff until terminal, then GETs the result. It returns `RawOutput{Tool: "nuguard", ToolVersion: provenance.nuguard_version, Stdout: <result JSON>}`. On `partial` or `failed`, `ExitCode` is non-zero.
- `Normalize` is a pure function. It maps each `ScanFinding` to `model.RawFinding` (`Tool=source_tool`, `RuleID=rule_id`, `File=locations[0].path`, `Line=locations[0].start_line`, display only). It keeps NuGuard's `fingerprint` as an alias, not as the consumer's own fingerprint. Every `coverage.tool_runs` entry that isn't `ok` becomes a coverage-gap record, never a clean result.
- Fixture tests: capture real `ScanResult` JSON into `testdata/fixtures/nuguard/` and assert the normalized output.

---

## 15. Open questions for the owner

1. **Deployment:** single instance behind your existing reverse proxy (Caddy), or a separate host? This spec assumes a single instance with SQLite.
2. **Posture findings:** include `kind=posture` by default (`include_posture=true`), or default them off? The earlier evaluation found they dominate real repos.
3. **Network:** should dependency scanning (OSV/Grype) run here, or does your program already cover dependencies, so this service can run AI-only tools offline (`tools: [nga-rules, atlas, semgrep]`, `network: false`)?
4. **Token source:** keep per-request tokens (v1), or move to `github_token_ref` once a secret store exists?
