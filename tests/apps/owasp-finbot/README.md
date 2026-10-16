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
   tests/apps/owasp-finbot/finbot-redteam.sh
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
