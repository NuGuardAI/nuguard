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
