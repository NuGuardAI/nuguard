# Custom Nuclei templates — OWASP Juice Shop

NuGuard now ships app-agnostic forms of these vulnerability checks in
`nuguard/pentest/templates`; the bundled pass runs them by default. That suite
adds bounded generic checks for auth-bypass SQLi/NoSQLi, error-based SQLi,
command injection, reflected XSS, SSTI, CRLF injection, path traversal, open
redirect, credentialed CORS, sensitive files/configuration, directory listings,
stack traces, TRACE, and clickjacking headers.

The files in this directory remain deliberately Juice Shop-specific fixtures.
Use them only when reproducing the exact demo findings below (for example the
scoreboard endpoint and Juice Shop's allowlist bypass). A normal NuGuard
pentest no longer needs `--templates-dir` to receive generalized coverage.

Hand-authored templates for `nuguard pentest --templates-dir`, each verified against the
live `juice-shop-demo` deployment before being committed (not guessed from the challenge
list at https://github.com/refabr1k/owasp-juiceshop-solutions):

- `juicebox-scoreboard.yaml` — detects the exposed `/rest/continue-code` scoreboard endpoint.
- `juiceshop-sqli.yaml` — exploits the `/rest/user/login` form via SQL injection auth bypass.
- `juiceshop-search-sqli.yaml` — error-based SQL injection in `/rest/products/search?q=`
  (confirmed via a raw `SQLITE_ERROR` in the response to an unescaped quote).
- `juiceshop-exposed-directories.yaml` — public directory listings at `/ftp`,
  `/encryptionkeys`, and `/support/logs`.
- `juiceshop-exposed-admin-config.yaml` — unauthenticated `/rest/admin/application-configuration`
  disclosure.
- `juiceshop-open-redirect.yaml` — `/redirect` allowlist bypass via the `%2f@` userinfo
  trick (encodes a trusted prefix as URL userinfo, redirecting to an attacker host after
  the `@`).

Not covered here — these need multi-step state or semantic judgment a single-request Nuclei
template can't express (IDOR on baskets/feedback/reviews, password-reset flows, CAPTCHA
bypass, price/coupon manipulation, admin-registration role bypass, DOM XSS requiring real
JS execution): use NuGuard's `redteam`/`behavior` engines for those instead.

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
