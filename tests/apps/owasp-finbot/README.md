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

The wrappers obtain a temporary anonymous session from `/vendor/`, extract its
CSRF token, and validate the pair without invoking the AI. They then target
`/vendor/api/v1/chat` with consistent session fingerprint headers. CSRF protection
stays enabled. Credentials remain in process memory and are refreshed each run.
Reports and logs are written under `reports/`.

For direct CLI verification, load your assessment credentials, then source the
session helper in the same shell:

```bash
SCRIPT_DIR="$PWD/tests/apps/owasp-finbot"
source "$SCRIPT_DIR/prepare-session.sh"
uv run nuguard target verify --config "$SCRIPT_DIR/nuguard.yaml" --skip-discovery
```

The temporary identity does not represent a verified email or privileged account.
Long runs may need a new session if FinBot expires or rotates its cookie.

## Conventional pentest

NuGuard blocks loopback addresses for conventional pentest, including when
`--allow-private` is set. Use the running FinBot container's private address:

```bash
tests/apps/owasp-finbot/finbot-redteam.sh --pentest-only
```

The wrapper discovers `finbot-app`'s private Docker IPv4 address and uses
`--allow-private`. If that address is unreachable from the scanner host, set
`FINBOT_PENTEST_TARGET` to FinBot's authorized private/public URL. The URL is
validated before session preparation. Chat testing keeps its configured target.

For custom pentest options, invoke the stage directly:

```bash
tests/apps/owasp-finbot/finbot-pentest.sh --scan-timeout 300
```

DAST fuzzing and headless testing are enabled by default. Set
`FINBOT_PENTEST_ACTIVE_FUZZING=0` or `FINBOT_PENTEST_HEADLESS=0` to disable
those engines. The wrapper writes the session cookie, CSRF token, and fingerprint
headers to an owner-only, exact-host-scoped Nuclei credential file, then removes
it when the stage exits. This differs from the chat wrappers' memory-only flow.
The session is temporary; a scanner warning that no identity endpoint could
verify it does not establish privileged or verified-email coverage.

With no stage flag, `finbot-redteam.sh` runs pentest followed by redteam and
obtains a fresh chat session between stages. Pentest setup/execution failures
stop the script and do not announce a completed report. Findings and coverage
gate failures retain the generated report at `reports/finbot-pentest.md`.
