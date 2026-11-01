# OWASP VulnerableApp test target

This fixture targets [SasanLabs/OWASP VulnerableApp](https://github.com/SasanLabs/VulnerableApp),
a deliberately vulnerable web application for scanner testing and benchmarking.

## Authentication

The application has no site-wide login. Its home page, vulnerability routes,
`/scanner/dast`, and `/scanner/benchmark` are accessible without credentials.
Some individual vulnerability exercises contain username/password forms; those
credentials are test data for the exercise and must not be configured as
NuGuard target authentication.

Accordingly, [`nuguard.yaml`](nuguard.yaml) sets `target.auth.type: none`.

## Workflows

- `./vulnerableapp-pentest.sh` runs the deployed-target pentest and writes a
  Markdown report.
- `./vulnerableapp-benchmark.sh` runs a JSON pentest and submits the findings to
  VulnerableApp's built-in benchmark comparator.
- `./vulnerableapp-test.sh` generates the SBOM, performs static analysis, then
  runs the pentest benchmark workflow.
- `./deploy-local.sh` runs the standalone upstream image at
  `http://127.0.0.1:9090/VulnerableApp` for manual testing.

The pentest scripts default to the authorized Azure demo target. Override it
with `VULNERABLEAPP_URL` when testing another authorized non-loopback target.
NuGuard intentionally blocks loopback addresses in active pentest scope, so the
local deployment is not used automatically by these scripts.

Every pentest invocation explicitly supplies `--acknowledge-authorization` and
`--allow-active-fuzzing`; do not point these scripts at systems you do not own
or have permission to test.

## Benchmark key and bundled checks

The [upstream benchmark](https://github.com/SasanLabs/VulnerableApp/blob/master/benchmarks/README.md)
grades DAST results against the live `GET /scanner/dast` key. On 2026-09-26 the
demo key listed 160 route rows across 38 vulnerability types: 142 `UNSECURE`
and 18 `SECURE`. Secure rows are negative controls, not detections to claim.

NuGuard's bundled `vulnerableapp-*.yaml` templates target these nine classes
using only GET requests. The checks were validated with Nuclei 3.11.1 against
the demo deployment and produced 25 findings at 25 distinct routes that occur
as `UNSECURE` rows in that key:

| Class | Confirmed routes | Proof |
| --- | ---: | --- |
| Error-based SQL injection | 2 | H2 syntax exception from a quote |
| Blind SQL injection | 2 | True/false predicate pair |
| UNION SQL injection | 2 | Synthetic canary row |
| Command injection | 5 | `printf` output marker |
| LDAP injection | 2 | Wildcard returns users; nonexistent name does not |
| JWT cookie protection | 2 | JWT cookie lacks the `Secure` flag |
| Open redirect | 4 | Cross-host `Location` header |
| Path traversal | 2 | Public benchmark CSV read outside the resource directory |
| SSRF | 4 | Read-only `/scanner/dast` fetched over loopback |

These 25 matches are Nuclei detections checked against the downloaded key;
they are not a claim of full benchmark coverage. Other key entries require
authentication, persisted uploads or XML, cache changes, token lifecycle
checks, or browser execution and are outside these default read-only probes.
