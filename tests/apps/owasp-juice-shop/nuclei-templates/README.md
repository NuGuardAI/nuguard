# Custom Nuclei templates — OWASP Juice Shop

Two hand-authored templates for `nuguard pentest --templates-dir`:

- `juicebox-scoreboard.yaml` — detects the exposed `/rest/continue-code` scoreboard endpoint.
- `juiceshop-sqli.yaml` — exploits the `/rest/user/login` form via SQL injection auth bypass.

NuGuard's pentest engine always runs Nuclei with `-disable-unsigned-templates`, so these
must be cryptographically signed before Nuclei will load them (each `# digest:` line at
the bottom of the file). That signature is tied to whatever ECDSA key signed it — it does
**not** travel with the repo, so on a machine without a matching key Nuclei will treat
these as unsigned and skip them, same as running without `--templates-dir` at all pointed
here.

To (re-)sign on a new machine:

```bash
nuclei -sign -t tests/apps/owasp-juice-shop/nuclei-templates
```

The first run generates a local key pair (interactive: prompts for an identifier and an
optional passphrase) under `~/.config/nuclei/keys/` and signs both templates; re-running
re-signs with the existing key. Never commit the generated private key
(`nuclei-user-private-key.pem`) — it stays local to whoever signs.

Usage:

```bash
uv run nuguard pentest \
  --config tests/apps/owasp-juice-shop/nuguard.yaml \
  --acknowledge-authorization \
  --allow-dynamic-auth \
  --templates-dir tests/apps/owasp-juice-shop/nuclei-templates \
  --rate-limit 100 --concurrency 25 \
  --format markdown \
  --output tests/apps/owasp-juice-shop/reports/juiceshop-custom-templates.md
```

Note: `--templates-dir` replaces Nuclei's default template corpus for the run, it does not
add to it — run without `--templates-dir` (or add `--allow-active-fuzzing`) separately to
also get the full standard-corpus / DAST coverage.
