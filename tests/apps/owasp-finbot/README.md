# Local OWASP FinBot

Run commands from the NuGuard repository root. Docker mode requires Docker Compose
2.24.4 or later; source mode requires uv and a separately running Redis instance.

1. Set `OPENAI_API_KEY` for FinBot's AI challenges, then start the app:

   ```bash
   tests/apps/owasp-finbot/deploy-local.sh --docker
   ```

   The default URL is `http://127.0.0.1:8000`. For a different port, pass `--port`
   and update `target.url` in `nuguard.yaml`. The deployment reuses its checkout in
   `tests/apps/owasp-finbot/tmp/finbot-ctf`; it does not pull or reset existing code.

2. Configure NuGuard's assessment models in `nuguard.yaml` and credentials in
   this directory's ignored `.env`. The supplied model configuration uses
   `AZURE_DEEPSEEK_MODEL_NAME`, `AZURE_DEEPSEEK_KEY`, `AZURE_DEEPSEEK_ENDPOINT`,
   `AZURE_OPENAI_KEY`, and `AZURE_API_BASE`. Export these variables before invoking
   NuGuard directly, or use the wrappers, which load `.env` automatically.

3. Generate an SBOM from the current FinBot source and compile the sample policy:

   ```bash
   uv run nuguard sbom generate --config tests/apps/owasp-finbot/nuguard.yaml \
     --format json -o tests/apps/owasp-finbot/finbot.sbom.json
   uv run nuguard policy compile --config tests/apps/owasp-finbot/nuguard.yaml
   ```

   Review the sample policy for your intended security tests before running scans.

4. Run behavior or redteam testing:

   ```bash
   tests/apps/owasp-finbot/finbot-behavior.sh
   tests/apps/owasp-finbot/finbot-redteam.sh --redteam-only
   ```

FinBot needs a temporary anonymous session cookie and a matching CSRF token.
NuGuard's pre-scan discovery recognises the CSRF/session rejection, fetches
`/vendor/` to obtain both, validates them, and pins the client fingerprint headers
(`User-Agent`, `Accept-Language`, `Accept-Encoding`) the session was issued to.
`behavior`, `redteam` and `pentest` (with `--allow-dynamic-auth`) all do this
automatically, and `behavior`/`redteam` refresh the session if FinBot expires it
mid-run. CSRF protection stays enabled and no helper script is required.
Reports and logs are written under `reports/`.

The temporary identity does not represent a verified email or privileged account.

## Conventional pentest

The wrappers use `nuguard-azure.yaml` by default. For the local deployment above,
set `FINBOT_CONFIG=tests/apps/owasp-finbot/nuguard.yaml`.

NuGuard blocks loopback addresses for conventional pentest, including when
`--allow-private` is set, so a local run needs FinBot's private address:

```bash
FINBOT_CONFIG=tests/apps/owasp-finbot/nuguard.yaml \
FINBOT_PENTEST_TARGET=docker \
  tests/apps/owasp-finbot/finbot-redteam.sh --pentest-only
```

`FINBOT_PENTEST_TARGET=docker` uses the running `finbot-app` container's private
Docker IPv4 address. If that address is unreachable from the scanner host, set
`FINBOT_PENTEST_TARGET` to FinBot's authorized private/public URL instead. The
target is scope-validated before any request is sent. Chat testing keeps its
configured target. With `FINBOT_PENTEST_TARGET` unset, pentest uses `target.url`
from the config.

For custom pentest options, invoke the stage directly:

```bash
tests/apps/owasp-finbot/finbot-pentest.sh --scan-timeout 300
```

DAST fuzzing and headless testing are enabled by default. Set
`FINBOT_PENTEST_ACTIVE_FUZZING=0` or `FINBOT_PENTEST_HEADLESS=0` to disable
those engines. Pentest acquires its temporary session through
`--allow-dynamic-auth`; a scanner warning that no identity endpoint could
verify it does not establish privileged or verified-email coverage.

With no stage flag, `finbot-redteam.sh` runs pentest followed by redteam. Pentest
setup/execution failures stop the script and do not announce a completed report.
Findings and coverage gate failures retain the generated report at
`reports/finbot-pentest.md`.
