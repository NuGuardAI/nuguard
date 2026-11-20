# Remote Scan API

The remote scan API lets another program ask NuGuard to scan a GitHub repository over HTTP.
You send a repository URL, an optional ref and an optional read-only token. NuGuard clones the
repository, builds the AI-SBOM, runs static analysis, and returns a machine-readable result you
can store, diff and fingerprint.

The service never sends traffic to a running application: it does not run behavior, red-team or
pentest jobs. It only reads the repository.

## Quick start

```bash
pip install "nuguard[service]"

# 1. Create an API key for each calling program. The key is printed once.
nuguard serve keygen --id vulnframework-prod

# 2. Put the printed sha256 in nuguard.yaml (see "Configuration" below), then start the service.
nuguard serve --config nuguard.yaml
```

The service listens on `127.0.0.1:8080` by default. It refuses to start when:

- `service.api_keys` is empty, or two keys share an `id`;
- `service.git.allowed_hosts` is empty;
- `service.jobs.max_concurrent` is below 1;
- it binds to a non-loopback address without either `service.tls` (`cert_file` and `key_file`)
  or `service.trust_proxy: true` (TLS terminated at a reverse proxy).

## Configuration

Everything lives under `service:` in `nuguard.yaml`. Values support `${ENV}` interpolation, and
`NUGUARD_SERVICE_*` environment variables override the file, with `__` for nesting
(`NUGUARD_SERVICE_JOBS__MAX_CONCURRENT=2`).

```yaml
service:
  bind: 0.0.0.0
  port: 8080
  trust_proxy: true                  # TLS terminates at your proxy
  public_base_url: https://nuguard.internal.example.com   # used in Location headers and links
  api_keys:                          # SHA-256 of each key; the key itself is never stored
    - id: vulnframework-prod
      sha256: ${NUGUARD_SERVICE_KEY_VF_SHA256}
      max_concurrent_scans: 4
      max_queued_scans: 50
  git:
    allowed_hosts: [github.com]
    clone_timeout_s: 120
    max_repo_bytes: 524288000        # 500 MB working tree
  jobs:
    max_concurrent: 4                # worker processes
    job_timeout_s: 900               # the whole worker process tree is killed after this
    queue_limit: 200
    result_ttl_hours: 168            # results and SBOMs are deleted after this
    scratch_dir: /var/lib/nuguard/scratch
    db_path: /var/lib/nuguard/service.db
  analysis_defaults:                 # callers can narrow these, never widen them
    min_severity: low
    network: true                    # OSV / Grype / Trivy lookups
    tools: [nga-rules, atlas, semgrep, osv, grype, checkov, trivy, supply-chain]
  llm:
    enabled: false                   # callers can request use_llm only when true
    model: ${NUGUARD_LLM_MODEL}
    api_key: ${LITELLM_API_KEY}
  webhooks:
    allowed_hosts: []                # empty disables completion callbacks
    secret: ${NUGUARD_SERVICE_WEBHOOK_SECRET}
  metrics: false                     # true exposes GET /v1/metrics (Prometheus text)
```

## Authentication

Send `Authorization: Bearer <api key>` on every request except `/v1/healthz` and
`/v1/readyz`. The caller identity is the key's `id`. A caller only sees its own scans; another
caller's scan id returns `404`, not `403`.

A missing or wrong key returns `401` with `WWW-Authenticate: Bearer`. After 10 failed attempts
in a minute from one address, every request from that address gets `429` for the rest of the
minute.

## Endpoints

All routes are under `/v1`. The OpenAPI document is served at `/v1/openapi.json`; a copy is
committed at `nuguard/service/openapi.json`.

| Method and path | Purpose | Success |
|---|---|---|
| `POST /v1/scans` | Submit a scan | `202` + job + `Location`. With `Prefer: wait=N` (N ≤ 120) the response is `200` + the finished job when it completes in time. |
| `GET /v1/scans/{id}` | Job status | `200` job |
| `GET /v1/scans/{id}/result` | Full result | `200`; `409` while running or when the job failed; `410` once expired |
| `GET /v1/scans/{id}/result?format=sarif` | SARIF 2.1.0 | `200` `application/sarif+json` |
| `GET /v1/scans/{id}/sbom?format=json\|cyclonedx\|cyclonedx-ext\|markdown` | The AI-SBOM | `200` |
| `POST /v1/scans/{id}/cancel` | Cancel (queued: immediate; running: the worker is killed) | `202` job |
| `GET /v1/scans?status=&repo=&cursor=&limit=` | List your scans, newest first | `200` `{items, next_cursor}` |
| `GET /v1/capabilities` | Tool versions, allowed tools, limits, LLM availability | `200` |
| `GET /v1/healthz` | Liveness (no auth) | `200` |
| `GET /v1/readyz` | Readiness (no auth): database, scratch directory, `git`, scanners | `200` or `503` with the failing checks |

### Submitting a scan

```bash
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
```

Request fields:

| Field | Notes |
|---|---|
| `repo_url` | `https://<allowed host>/<owner>/<repo>[.git]`, optionally `/tree/<ref>/<subpath>`. No credentials, query or fragment. |
| `ref` | Branch, tag or full 40-character commit SHA. Omit for the default branch. When `repo_url` has `/tree/…` and the branch name contains `/`, pass `ref` explicitly. |
| `github_token` | Optional for public repositories. See "Tokens" below. |
| `options.tools` | A subset of the server's allowed tools. Omit for all of them. |
| `options.min_severity` | `critical`, `high`, `medium`, `low` (default) or `info`. |
| `options.include_posture` | `false` drops `kind=posture` findings (SBOM-structure findings). |
| `options.include_sbom` | `true` embeds the full AI-SBOM in the result. |
| `options.network` | `false` skips OSV, Grype and Trivy; they are reported `skipped`, so the job is `partial`. |
| `options.use_llm` | Rejected with `422 llm_not_available` unless the server enables LLM use. |
| `options.subpath` | Scan only this repository-relative directory. |
| `client_reference` | Your own id, echoed back on the job. |
| `callback_url` | Optional `https://` URL on a host listed in `service.webhooks.allowed_hosts`. |

Requests over 16 KB are rejected with `413`.

**Idempotency.** Send `Idempotency-Key` (up to 128 characters). Within 24 hours, the same key
with the same request returns the original job with `200`; the same key with a different request
returns `409 idempotency_conflict`. The token is not part of the comparison, so a resubmission
after a token rotation still matches.

**Limits.** Each key has `max_queued_scans` and `max_concurrent_scans`; the server has
`queue_limit` and `max_concurrent`. Exceeding a queue limit returns `429` (`rate_limited` or
`queue_full`) with `Retry-After`.

### Job status

```json
{"id": "scn_01JC…", "status": "running", "phase": "analyzing", "commit_sha": "9ec32b9…",
 "repo_url": "https://github.com/acme/support-agent", "client_reference": "vf-run-8841",
 "links": {"self": "…", "result": "…", "sarif": "…", "sbom": "…"}}
```

`status` is one of:

| Status | Meaning |
|---|---|
| `queued`, `running` | Not finished. `phase` is `cloning`, `sbom`, `analyzing` or `finalizing` while running. |
| `succeeded` | Every requested tool ended `ok` or `not_applicable`. |
| `partial` | The scan finished, but at least one requested tool `failed`, was `skipped` or hit a `timeout`. The result lists each gap. **Treat a partial result as incomplete coverage, not as clean.** |
| `failed` | The clone, SBOM or analysis step failed. There is no result; see `error.code`. |
| `cancelled` | Cancelled by the caller. |
| `expired` | The result passed its retention period and was deleted. |

Job error codes: `repo_not_found_or_no_access` (GitHub returns the same answer for a missing repo
and a private repo the token cannot see, so these are deliberately not distinguished),
`auth_failed`, `ref_not_found`, `repo_too_large`, `clone_timeout`, `clone_failed`,
`subpath_not_found`, `sbom_failed`, `analysis_crashed`, `job_timeout`, `worker_lost` (the worker
died or the service restarted mid-scan), `credential_unavailable` (the service restarted while a
job that needed a token was queued; see "Tokens").

Errors use RFC 9457 problem details (`application/problem+json`) with a stable `code`. Error
responses never contain stack traces, local paths, scanner output or tokens.

## The result

```json
{
  "schema_version": "1.0",
  "job": {"id": "scn_…", "status": "partial", "commit_sha": "9ec32b9…", "…": "…"},
  "provenance": {
    "nuguard_version": "0.9.15", "service_version": "0.9.15", "image_digest": "sha256:…",
    "rulepack_sha256": "…",
    "tools": [{"name": "semgrep", "version": "1.172.0"}, {"name": "trivy", "version": "0.76.1"}],
    "options_effective": {"…": "…"}, "llm_used": false
  },
  "coverage": {
    "tool_runs": [
      {"tool": "nga-rules", "status": "ok", "findings": 4, "version": "nuguard 0.9.15", "duration_ms": 120},
      {"tool": "osv", "status": "failed", "findings": 0, "reason": "network unreachable"},
      {"tool": "checkov", "status": "not_applicable", "findings": 0, "reason": "no IaC paths found in SBOM — checkov scan skipped"}
    ],
    "languages_detected": ["python"], "files_scanned": 42, "files_skipped": 3,
    "gaps": ["osv: failed (network unreachable)"]
  },
  "summary": {"critical": 0, "high": 2, "medium": 1, "low": 0, "info": 0},
  "findings": [
    {
      "fingerprint": "nf1_4f0c…", "rule_id": "nuguard-eval-on-llm-output",
      "source_tool": "semgrep", "kind": "code", "severity": "high",
      "title": "…", "description": "…",
      "locations": [{"path": "app/tools/calc.py", "start_line": 42, "end_line": 42, "symbol": null}],
      "owasp_llm_ref": "LLM02: Insecure Output Handling", "raw": {"…": "full NuGuard Finding"}
    }
  ],
  "inventory": {"node_counts": {"AGENT": 2, "MODEL": 3}, "edge_count": 31,
                "frameworks": ["openai-agents"], "model_providers": ["openai"]},
  "result_sha256": "…"
}
```

- **`coverage.tool_runs`** has one entry per requested tool. Its `status` is one of `ok`,
  `failed`, `skipped` (a precondition was missing: binary not installed, offline mode),
  `disabled`, `not_applicable` (nothing for the tool to look at, such as no IaC files for
  Checkov) or `timeout`. A tool that did not finish never reports `ok`.
- **`kind`** is `code`, `posture` (SBOM-structure findings such as an LLM without a guardrail),
  `dependency`, `iac`, `container` or `supply_chain`.
- **`locations[].path`** is always relative to the scanned directory, with `/` separators.
  It is never absolute. For IaC findings `symbol` holds the resource address.
- **`findings`** are sorted by severity, then `rule_id`, then `fingerprint`.
- **`result_sha256`** is the SHA-256 of the canonical JSON of the result
  (`sort_keys`, compact separators, UTF-8) with the fields that legitimately differ between two
  runs removed: the job's id, timestamps, links and `client_reference`; tool `duration_ms`; each
  raw finding's `finding_id`; and the embedded `sbom` (its node ids are random). Two scans of the
  same commit with the same tool versions and options produce the same value. The stored result
  never changes after it is written.

### Fingerprints

A fingerprint identifies the same defect in the same repository across runs:

```
"nf1_" + sha256("\x1f".join([repo, kind, source_tool, rule_id, identity_key]))[:32]
```

`repo` is `host/owner/repo` in lower case. The fingerprint never includes line numbers, absolute
paths, run ids, timestamps or titles, so reformatting a file does not change it.

| kind | identity_key |
|---|---|
| `code` | `path#symbol` (the enclosing Python function or class), otherwise `path#` + a hash of the matched source with whitespace normalized |
| `posture` | the SBOM node's `NODE_TYPE:canonical name` |
| `dependency` | `ecosystem:package@<major version>#<manifest path>` |
| `iac` | `path#<resource address>` |
| `container` | the image without tag or digest, then `#` and the package name |
| `supply_chain` | `path` |

When two findings share an identity (the same rule twice in one function), the first keeps the
fingerprint and the others get a deterministic suffix in source order.

Use the fingerprint as an alias when you correlate across runs; if your system has its own
fingerprint scheme, keep yours as the primary key.

## Tokens

Send a **fine-grained, read-only token scoped to the repositories you scan**
(`Contents: read`, `Metadata: read`). Classic `ghp_` tokens work but are logged as a warning
(without the value).

How the service handles the token:

- It is accepted in the request body and held only in process memory until the job finishes.
  It is never written to the database, logs, results, webhooks, error messages, or any process's
  command line.
- It is handed to the worker over a pipe and used only by `git clone` (through an ephemeral
  credential helper, never embedded in the URL) and the GitHub tree API for subfolder URLs.
- If the service restarts while a token job is queued, the token is gone: the job fails with
  `credential_unavailable` (a job that was running fails with `worker_lost`). Jobs that fail for
  these service-side reasons release their `Idempotency-Key`, so resubmitting with the same key
  creates a new job.

## Deployment

**Run the service in a container when you scan repositories you do not trust.** Scanners parse
attacker-controlled files. Recommended settings:

- read-only root filesystem, a volume for `/var/lib/nuguard`, a tmpfs for `/tmp`;
- no host mounts; CPU and memory limits;
- an egress allowlist: `github.com`, `api.github.com`, `codeload.github.com`, `api.osv.dev`, and
  your LLM endpoint if enabled. The image bakes in the Grype and Trivy databases, so their
  download hosts are only needed when you rebuild it.

Build the image from the repository root:

```bash
docker build -f deploy/service/Dockerfile -t nuguard-service .
docker run --read-only --tmpfs /tmp -v nuguard-data:/var/lib/nuguard \
  -v "$PWD/nuguard.yaml:/etc/nuguard/nuguard.yaml:ro" \
  -e NUGUARD_IMAGE_DIGEST=sha256:… -p 8080:8080 nuguard-service
```

The image pins Semgrep, Checkov, Trivy and Grype, verifies their downloads against the published
checksums, runs as a non-root user, and records exact tool versions in `/etc/nuguard/tools.json`
for `provenance.tools`. An example configuration is in `deploy/service/nuguard.yaml.example`.

Inside each job, the worker process runs with an environment built from scratch: `PATH`,
`LANG`, a private `HOME` and `TMPDIR`, and the variables listed in `service.jobs.passthrough_env`
(proxy and certificate settings, scanner DB locations). Credentials such as `GITHUB_TOKEN`,
`LITELLM_API_KEY` and any `NUGUARD_*` / `AISBOM_*` variable are never passed, even if listed.
On Linux the worker also sets `RLIMIT_FSIZE` (`jobs.rlimit_fsize_bytes`, default 1 GB).
`RLIMIT_AS` (`jobs.rlimit_as_bytes`) is off by default: it limits virtual address space, and
Semgrep and the Go-based scanners reserve far more than they use, so they fail under a cap such as
4 GB. Limit real memory with the container's memory limit (`docker run --memory`) instead.

Clones run with `GIT_ALLOW_PROTOCOL=https`, no system or global git config, a private `HOME`, no
Git LFS downloads, no submodules, and symlinks checked out as plain files.

### Behind a proxy

List the proxy and certificate variables in `service.jobs.passthrough_env` so they reach the
worker. From there, the clone keeps them too: under the host policy, git's environment is still
built from scratch, but it inherits `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` and `ALL_PROXY` (in
upper and lower case), `SSL_CERT_FILE`, `SSL_CERT_DIR`, `GIT_SSL_CAINFO` and `CURL_CA_BUNDLE`.
It still drops `GH_TOKEN`, `GITHUB_TOKEN` and every `NUGUARD_*` variable except the job's own
token. The GitHub tree API calls (subfolder scans) use the same proxy and CA variables.

### Running without the image

You can run the service from a source checkout or an installed package
(`pip install 'nuguard[service]'`, then `nuguard serve -c nuguard.yaml`). Two environment
variables carry what the image would otherwise record:

- `NUGUARD_TOOLS_JSON`: the path of a JSON object of tool name to version, in the image's
  format, for example `{"nuguard": "0.9.15", "semgrep": "1.172.0"}`. It feeds
  `provenance.tools` and `/v1/capabilities`. Without it, the service looks for
  `/etc/nuguard/tools.json`, then falls back to the versions each run reports. Semgrep does not
  report one, so its version is `null`.
- `NUGUARD_IMAGE_DIGEST`: copied into `provenance.image_digest`. There is no image to record, so
  set it to something that identifies the source instead, for example
  `git:<commit>`, or `git:<commit>-dirty` when the checkout has uncommitted changes. In that case
  the field holds a git commit, not an image digest.

Install Semgrep in its own virtual environment, as the image does, and put its `bin` folder on the
service's `PATH`. It conflicts with NuGuard's own dependencies (OpenTelemetry), so in the same
environment as NuGuard it fails on start-up and every scan reports `semgrep: failed`.

## Webhooks

When `callback_url` is set and its host is listed in `service.webhooks.allowed_hosts`, the service
POSTs the job (not the full result) when it reaches a terminal status, with:

- `X-NuGuard-Signature: sha256=<hex HMAC-SHA256 of the body with webhooks.secret>`
- `X-NuGuard-Timestamp: <unix seconds>`

Delivery is retried three times with exponential backoff and never changes the job status.
Redirects are not followed, and a host that resolves to a private, loopback or link-local address
is refused unless listed in `service.webhooks.allow_private_hosts`. The address is checked before
connecting; for strong protection against DNS rebinding, also restrict egress at the network
layer.

## Observability

Logs are JSON lines on stderr with `job_id` and `owner_id` on job-related lines. Request bodies
are never logged. With `service.metrics: true`, `GET /v1/metrics` serves Prometheus text:
`nuguard_scans_total{status}`, `nuguard_scan_duration_seconds`, `nuguard_queue_depth` and
`nuguard_tool_runs_total{tool,status}`.

## Retention

Every 15 minutes the service deletes results and SBOMs older than `jobs.result_ttl_hours` (the job
becomes `expired`), removes leftover scratch directories, and vacuums the database weekly.
