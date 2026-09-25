# Implementation Plan: GitHub Subfolder Support for `nuguard sbom generate --from-repo`

## Context

`nuguard sbom generate --from-repo <url>` clones a repo root and scans it. If
a user points it at a subfolder — e.g.
`https://github.com/NuGuardAI/openai-cs-agents-demo/python-backend` or the
GitHub web-UI form `https://github.com/org/repo/tree/main/python-backend` —
today it fails with a raw error, because the "subfolder" is passed straight
to `git clone` as if it were the repo URL itself:

```
RuntimeError: git clone failed for 'https://github.com/NuGuardAI/openai-cs-agents-demo/python-backend' @ None:
Cloning into '/var/folders/.../repo/python-backend'...
remote: Not Found
fatal: repository 'https://github.com/NuGuardAI/openai-cs-agents-demo/python-backend/' not found
```

NuGuard-app (the SaaS platform, sibling repo `../NuGuard-app`, `develop`
branch) already solved this for its own backend using a GitHub REST API
tree-manifest + `git sparse-checkout` approach. The goal here is to port that
capability into the `nuguard` OSS package itself, so that NuGuard-app can
eventually delete its own copy of this logic and depend on nuguard's
implementation instead.

This must be **purely additive**: every existing repo-root clone path (CLI
`sbom generate`, `nuguard analyze`'s remote re-clone, the MCP tool,
`SbomGenerator.from_repo`) must behave byte-for-byte as it does today when no
subfolder is present in the URL. If the subfolder doesn't exist in the repo,
nuguard must exit with an error the same way it does today for "repo not
found" — no new UX/exit-code path.

## Decisions made

1. **URL formats supported — both:**
   - GitHub's own web-UI copy/paste format: `.../org/repo/tree/<ref>/<subpath...>`
     (ref embedded in the URL).
   - Bare shorthand: `.../org/repo/<subpath...>` (no `/tree/`; ref comes from
     `--ref`/`nuguard.yaml`/default branch, exactly as it does today for
     non-subfolder URLs).
2. **Clone mechanism — mirrors NuGuard-app closely:** GitHub REST API
   tree-manifest fetch + non-cone `git sparse-checkout --stdin`, adapted to a
   synchronous CLI tool and reusing nuguard's existing token
   resolution/config (no new auth config knob). This was chosen over a
   simpler "shallow clone + scan subdir" approach specifically because it
   minimizes what's fetched from the remote repo, at the cost of being
   GitHub-only and depending on the GitHub REST API (see Limitations).
3. **No AI-SBOM schema change.** `AiSbomDocument.target` keeps holding the
   exact URL the user passed — now possibly including `/tree/<ref>/<path>` or
   a bare subfolder suffix. This is a **documented behavior change**:
   `target` is no longer guaranteed to be a literal, directly re-clonable
   repo URL when a subfolder was scanned.

## Current architecture (as verified in the live codebase)

- `nuguard/sbom/extractor/core.py`:
  - `AiSbomExtractor._clone_repo(url, ref, dest)` (~lines 3314-3357): the
    single low-level clone primitive. Validates the URL (`_SAFE_URL_RE`,
    ~3308-3312) and ref (`_SAFE_REF_RE`, ~3291) against argument-injection
    regexes, builds `["git", "clone", "--depth", "1", ("--branch", ref)?,
    "--", url, str(dest)]`, runs via `subprocess.run(..., check=True,
    capture_output=True)`. On `CalledProcessError`, raises `RuntimeError(f"git
    clone failed for {display_url!r} @ {ref!r}: {stderr}")` — this is the
    exact error the user hit.
  - `AiSbomExtractor.extract_from_repo(url, ref, config, cache_dir=None,
    source_ref=None)` (~lines 2235-2296): computes `display_url =
    sanitize_repository_url(source_ref or url)`, derives `app_name` from the
    URL's last path segment, clones into `cache_dir/repo/<app_name>` (or a
    temp dir), then calls `self.extract_from_path(repo_dir, config,
    source_ref=display_url, branch=ref)`.
  - **Verified:** there is no top-level exception handler in
    `nuguard/cli/main.py` beyond `sbom.py`'s own `except SbomError` (which
    maps to exit code 3). An uncaught `RuntimeError`/`ValueError` from
    `_clone_repo` today propagates as a raw Click/Typer traceback at exit
    code 1. Any new error path introduced by this feature must propagate the
    same way (not wrapped in `SbomError`) to match today's behavior.
  - **Verified:** `.git` is already excluded from the file walk
    (`core.py:500`), so the new clone path doesn't need to delete `.git`
    after checkout, unlike NuGuard-app's implementation.
  - No existing GitHub URL parser exists anywhere in the codebase — `app_name`
    is derived ad hoc from the last URL path segment.
- `nuguard/cli/commands/analyze.py`: `_clone_remote_source_for_analysis()`
  (~lines 41-80) is a second, independent caller of `_clone_repo`, used by
  `nuguard analyze` to re-clone a remote `source:` for local-file scanners
  (Checkov/Trivy/Semgrep/supply-chain). It needs the same subfolder handling
  to stay consistent, but only needs files on disk, not a full SBOM.
- `nuguard/cli/commands/sbom.py`: `_do_generate` (~lines 259-370) resolves
  `effective_ref = ref if ref is not None else cfg.source_ref` and calls
  `extractor.extract_from_repo(clone_url, ref=effective_ref, config=config,
  source_ref=from_repo)` inside a `try: ... except SbomError:` block.
  `_inject_token`/`_resolve_token` (~223-231, ~355) handle embedding an auth
  token into the clone URL's userinfo for private-repo `git clone` — this
  pattern is reused for *resolving* the token but not for URL-embedding, since
  the new GitHub API calls authenticate via an `Authorization` header instead.
- `nuguard/sbom/generator.py`: `SbomGenerator.from_repo(url, ref="main",
  output=None)` — thin wrapper. Note its `ref` default is `"main"`, unlike
  `extract_from_repo`'s `ref: str | None` — see Limitations.
- `nuguard/sbom/public_api.py`: `SbomGenerateRequest.repo_url`/`repo_ref`
  (`repo_ref` also defaults to `"main"`); `generate_sbom()` delegates to
  `SbomGenerator.from_repo`, so no direct changes needed there.
- `nuguard/sbom/models.py`: `AiSbomDocument.target: str` (~line 1225) is the
  only field persisting the repo URL in the output JSON. `ScanSummary`
  (~line 953) has no `branch`/`source_ref`/subfolder field — `branch` is
  already silently dropped today in `postprocess.py:_make_scan_summary`
  (pre-existing, not something this feature needs to fix).
- `nuguard/mcp/server.py` (~lines 113-135): forwards `ref` (default `"main"`)
  as `--ref` to the CLI subprocess; `from_repo` passes through untouched, so
  a subfolder-bearing URL flows through with no MCP code changes required.
- `nuguard/common/url_sanitization.py`: `sanitize_repository_url`,
  `redact_repository_url_from_text` — already operate on full URL strings
  including path, so a `/tree/<ref>/<subpath>` URL passes through unchanged;
  no changes needed here.

## Reference implementation studied (NuGuard-app, not modified — just the model)

`backend/ai_asset_service/core/github_fetcher.py`:
- `_GITHUB_TREE_URL_RE` matches `.../org/repo(.git)?/tree/<ref>/<subpath>`.
- `parse_github_url(url) -> (owner, repo)`: validates `https://github.com`,
  requires exactly 2 path segments.
- `split_github_folder_url(url) -> (repo_root_url, ref|None, subpath|None)`:
  rejects `..` segments and `%2f`-encoded slashes.

`backend/ai_asset_service/core/github_clone.py`, `clone_github_repository`:
- `git init` + `remote add origin`, then `git fetch --depth 1
  --filter=blob:none origin <ref-or-HEAD>` with a `main`→`master` fallback.
- Resolves `commit_sha` via `git rev-parse FETCH_HEAD`.
- Fetches the full commit-pinned file tree via the **GitHub REST API**
  (`GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1`) to bound size
  before touching disk.
- Filters tree entries to the subpath prefix, excludes symlinks, enforces
  file-count/size caps, escapes git wildcard metacharacters, and materializes
  only the selected files via `git sparse-checkout set --no-cone --stdin`.
- Empty selection (subfolder doesn't exist, or exists but has nothing
  eligible) → `ValueError("... does not contain eligible files")` — the same
  error for both cases.
- No separate `repo_url`/`subfolder` fields persisted — the full original URL
  is kept as-is in a single string field.
- Tests: `tests/test_github_clone.py` (materializes-only-subtree,
  rejects-empty-subtree, wildcard-escaping, main→master fallback).

## Proposed implementation

### 1. New URL parser — `nuguard/common/github_url.py` (new file)

```python
@dataclass(frozen=True)
class GitHubRepoRef:
    repo_root_url: str        # https://github.com/org/repo (no trailing slash/.git)
    url_ref: str | None       # ref from /tree/<ref>/, else None
    subpath: str | None       # e.g. "python-backend" or "a/b/c", else None

def parse_github_repo_and_subfolder(url: str) -> GitHubRepoRef: ...

def try_parse_github_subfolder(url: str) -> GitHubRepoRef | None:
    """None when host isn't github.com, or the URL is a plain repo-root URL
    (subpath=None) — the caller should treat None as "use today's unmodified
    clone path."""
```

- Only triggers for `github.com`/`www.github.com` hosts; every other host
  (GitLab, Bitbucket, self-hosted git, or a plain repo-root GitHub URL)
  returns `None`, so all existing callers fall through unchanged.
- Match order: `/tree/<ref>/<subpath>` regex first (nested paths via greedy
  capture), then exact `owner/repo` (→ `subpath=None`), then the bare
  shorthand `owner/repo/<subpath...>`.
- Reject `..` segments and `%2f`/`%2F`-encoded slashes in the subpath →
  `ValueError`.
- **Bare-shorthand ambiguity is resolved empirically, not by guessing.** The
  parser does **not** maintain a reserved-keyword denylist (`releases`,
  `wiki`, etc.) to guess whether a bare-shorthand 3+-segment URL is a
  subfolder or some other GitHub UI page. Instead, `try_parse_github_subfolder`
  only classifies the URL *shape* (does it look like `owner/repo` or
  `owner/repo/<extra...>`); the caller (see §3) resolves the actual ambiguity
  by attempting today's plain clone first and only reinterpreting the extra
  path segments as a subfolder if that plain clone fails with a definitive
  "not found" — see the "Try-first, fallback-on-not-found" logic in §3. This
  replaces the denylist approach and removes its false-positive/false-negative
  risk entirely, at the cost of one extra failed-clone attempt for genuine
  subfolder URLs (see Limitations).
- Log at `INFO` (via the standard `logging` module, matching the rest of the
  package — no `print`) as each stage of URL resolution happens:
  `"Detected GitHub tree-URL form: repo=%s ref=%s subpath=%s"` or
  `"Detected possible GitHub subfolder shorthand: repo=%s candidate_subpath=%s"`
  when `try_parse_github_subfolder` matches, and nothing when it returns
  `None` (plain repo-root URL — today's silent path, unchanged).
- **Ref precedence** (to be documented in `--ref` help text and the README):
  `--ref` CLI flag > `ref:` in `nuguard.yaml` > URL-embedded `/tree/<ref>/` >
  default branch. `--ref` stays authoritative since it's today's single
  explicit-intent mechanism — silently letting the URL override an explicit
  flag would break today's mental model. Print an informational line when
  `--ref` overrides a differing URL-embedded ref, for transparency.

### 2. New sparse-checkout clone primitive — `nuguard/sbom/extractor/github_clone.py` (new file)

```python
def clone_github_subfolder(
    repo_root_url: str, ref: str | None, subpath: str, dest: Path, *,
    token: str | None = None,
    max_files: int = 5000, max_total_bytes: int = 200 * 1024 * 1024,
    timeout_seconds: float = 120.0,
) -> str:  # returns resolved commit SHA
```

Mirrors NuGuard-app's `clone_github_repository`, adapted for a synchronous
CLI:

1. Reuse `AiSbomExtractor._SAFE_URL_RE`/`_SAFE_REF_RE` for input validation —
   hoist both into a small shared `nuguard/sbom/extractor/git_safety.py`
   module so `_clone_repo` and this new function share one source of truth
   and can't drift apart.
2. `git init` + `remote add origin <repo_root_url>`.
3. `git fetch --depth 1 --filter=blob:none origin <ref-or-HEAD>`, with the
   `main`→`master` fallback **only when `ref is None`** — an explicit ref
   that fails to fetch is a genuine error and should propagate, matching
   today's `--branch` semantics for `_clone_repo`.
4. `git rev-parse FETCH_HEAD` → `commit_sha`.
5. `GET https://api.github.com/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1`
   via a **synchronous** `httpx.Client` (`nuguard/common/http.py` is
   async-only and built for redteam/live-probe call sites — don't force the
   sync CLI path async). Send `Authorization: Bearer <token>` when a token is
   available (reuse the same token resolution already used by
   `sbom.py:_resolve_token`/`--token`/`GH_TOKEN`/`GITHUB_TOKEN` — no new
   config field for auth). On 403/429 with no token, raise a clear
   `RuntimeError` explicitly naming the GitHub API rate limit and suggesting
   `--token`.
6. Filter tree entries to `f"{subpath.rstrip('/')}/"` prefix, strip the
   prefix, reject `..`/absolute paths, exclude symlinks (git mode
   `"120000"`), enforce `max_files`/`max_total_bytes` (raise `RuntimeError` —
   never silently truncate, since a truncated scan would silently produce an
   incomplete SBOM). **Zero matching entries → `ValueError("Requested
   repository folder ... not found")`** — deliberately the same error for
   "doesn't exist" and "exists but has nothing eligible," matching
   NuGuard-app and satisfying the requirement to fail the same way
   `_clone_repo` fails today (an uncaught exception with a clear message —
   see verified error-propagation note below).
   Log at `INFO`: `"Fetched tree manifest for %s@%s: %d entries, %d matched
   subpath %r"` after filtering, so a user can see at a glance how much was
   selected without needing `--verbose`/debug output.
7. Escape git glob metacharacters (`*?[]!\`) per selected path, pipe the
   patterns to `git sparse-checkout set --no-cone --stdin`, then `git
   checkout --detach FETCH_HEAD`.
8. Post-checkout guard: verify `(dest / subpath).is_dir()`, else the same
   `ValueError` as step 6 (belt-and-suspenders, matching NuGuard-app).
9. Do **not** delete `.git` afterward — verified `_clone_repo`'s existing
   plain clones also leave `.git` in place, and `extract_from_path` already
   excludes it (`core.py:500`), so this feature doesn't need to diverge.
10. On any exception, remove `dest` entirely — the caller (see §3) owns the
    `cache_dir` vs. temp-dir lifecycle decision, same as `extract_from_repo`
    today.

**Verified (not assumed):** `nuguard/cli/main.py` has no top-level exception
handler beyond `sbom.py`'s own `except SbomError` (confirmed by reading
`_do_generate`); an uncaught `RuntimeError` from `_clone_repo` already
propagates today as a raw Click/Typer traceback at exit code 1.
`clone_github_subfolder`'s errors must **not** be wrapped in `SbomError`, so
they propagate identically — this is what satisfies "exit with an error the
same way nuguard exits today."

**New git version floor**: `--filter=blob:none` + `sparse-checkout --no-cone
--stdin` require git ≥ 2.25. This is a new minimum for this feature only —
`_clone_repo`'s plain `git clone --depth 1` has no such requirement. Let
git's own stderr surface through the wrapped `RuntimeError` rather than
adding a separate version probe.

### 3. Wiring — an additive branch, not a change to existing functions

**Refinement: "try-first, fallback-on-not-found" for bare-shorthand URLs.**
To avoid guessing at URL shape (the reserved-keyword denylist idea from the
original draft of this plan — e.g. is `.../org/repo/releases` a subfolder
named `releases` or a GitHub UI page?), resolve the ambiguity empirically:

- **`/tree/<ref>/<subpath>` URLs are unambiguous.** That form can never be a
  valid plain git-clone target, so route straight to
  `extract_from_repo_subfolder`/`clone_github_subfolder` — no try-first
  needed, no wasted round-trip.
- **Bare shorthand (`owner/repo/<extra...>`, no `/tree/`) is ambiguous** —
  route through a try-first sequence instead of a denylist:
  1. Log `INFO`: `"Attempting direct clone of %s (candidate subfolder: %r) — verifying it isn't actually a valid repo path"`.
  2. Attempt today's unmodified `AiSbomExtractor._clone_repo(url, ref, dest)`
     with the **full** URL exactly as given (no reinterpretation). If it
     succeeds, log `INFO`: `"%s resolved directly — no subfolder detected"`
     and continue through today's unmodified `extract_from_repo` path.
     Nothing about this case changes from today's behavior.
  3. If it fails, classify the failure by inspecting the (redacted) stderr
     already captured by `_clone_repo`'s existing `RuntimeError` message:
     - **Definitive "not found"** (matches the same `remote: Not Found` /
       `fatal: repository ... not found` pattern the current bug report
       shows) → log `INFO`: `"%s not found directly; retrying as repo=%s
       subpath=%s"` and fall back to `extract_from_repo_subfolder`/
       `clone_github_subfolder` using the parsed `owner/repo` root and the
       remaining path as `subpath`.
     - **Any other failure** (auth/403, network/timeout, rate-limit, or
       anything not matching the "not found" pattern) → log `INFO`:
       `"%s clone failed for a reason other than 'not found'; not retrying as
       a subfolder"` and **re-raise the original error unchanged** — do not
       attempt the subfolder fallback. This prevents a transient or
       auth-related failure from being silently misreported as "maybe a
       subfolder," which would otherwise produce a confusing, unrelated
       second error.
  4. This "not found" classifier is a small, focused helper (e.g.
     `_is_repository_not_found_error(exc: RuntimeError) -> bool` next to
     `_clone_repo` in `core.py`, reusable by both `sbom.py` and
     `analyze.py`'s call sites) — it inspects the same stderr text
     `_clone_repo` already redacts and wraps, so no new subprocess calls or
     parsing surface beyond what's already captured today.
  5. If **both** the direct attempt and the subfolder-interpretation attempt
     fail (e.g. the owner/repo itself doesn't exist at all), the final error
     surfaced to the user is the subfolder-path's own "not found"-style
     error (`ValueError`/`RuntimeError`, uncaught, same exit-code behavior as
     today) — still satisfies "exit the same way nuguard exits today for
     repo not found."

Net effect: every existing repo-root URL (the overwhelming majority of
current usage) resolves on the very first attempt, through the exact
existing `_clone_repo` call, with zero new code on the success path. Only
URLs that actually fail a direct clone ever reach the new subfolder logic.
This is a strictly stronger version of "purely additive" than gating by URL
regex alone, and it removes the need for the reserved-keyword denylist
entirely — GitHub's own response is the source of truth, not a guess.

- `AiSbomExtractor.extract_from_repo` (`core.py:2235-2296`) is **not
  modified**. Add a new sibling method
  `extract_from_repo_subfolder(repo_root_url, ref, subpath, config,
  cache_dir=None, source_ref=None, token=None)` immediately after it, which
  calls `clone_github_subfolder(...)` into `repo_dir`, then the **same**
  `self.extract_from_path(repo_dir / subpath, config, source_ref=display_url,
  branch=ref)` used today — so all downstream scanning/postprocessing code is
  completely untouched. It replicates the `cache_dir` vs.
  `tempfile.TemporaryDirectory` branching from `extract_from_repo`, but
  derives `app_name` from **`repo_root_url`'s** last segment (the repo name),
  not the full URL's last segment — otherwise two different repos that both
  happen to have a same-named subfolder (e.g. both have `backend/`) would
  collide in `cache_dir/repo/<app_name>/`. Cache layout becomes
  `cache_dir/repo/<repo-name>/<subpath>/`.
- `nuguard/cli/commands/sbom.py`, `_do_generate`: before the existing
  `extractor.extract_from_repo(...)` call, gate on
  `try_parse_github_subfolder(from_repo)`:
  - `None` (non-GitHub host, or a plain repo-root URL) → today's unchanged
    `extractor.extract_from_repo(clone_url, ...)` call, no new code involved.
  - A `GitHubRepoRef` from the **`/tree/<ref>/<subpath>`** form → call
    `extractor.extract_from_repo_subfolder(...)` directly (unambiguous, no
    try-first needed).
  - A `GitHubRepoRef` from the **bare-shorthand** form → run the
    "try-first, fallback-on-not-found" sequence from §3 above: attempt
    `extract_from_repo` with the full URL as given; on a classified
    "not found" failure, retry via `extract_from_repo_subfolder` using the
    parsed `repo_root_url`/`subpath`; on any other failure, re-raise as-is.
  - In all subfolder-resolved cases, compute `effective_ref = ref if ref is
    not None else (gh.url_ref or cfg.source_ref)` and pass the raw resolved
    token (not `_inject_token`'s URL-embedded form, since this path
    authenticates GitHub API calls via an `Authorization` header rather than
    the git clone URL).
  This gate (plus the try-first sequence it delegates to) is the single
  place the new behavior is triggered from the CLI.
- `nuguard/cli/commands/analyze.py`, `_clone_remote_source_for_analysis`:
  the same three-way gate and try-first sequence at the top. On a confirmed
  subfolder, call `clone_github_subfolder` directly (not
  `extract_from_repo_subfolder`, since `analyze` only needs files on disk,
  not a full `AiSbomDocument` — mirrors today's asymmetry where `analyze.py`
  already calls the low-level `_clone_repo` rather than `extract_from_repo`)
  and return `str(repo_dir / subpath)`. Keep the existing best-effort
  `try/except Exception` → warn-and-skip wrapper unchanged around the whole
  sequence.
- `nuguard/sbom/generator.py`, `SbomGenerator.from_repo`: add the same gate.
  **Flagging for explicit sign-off**: its current default `ref="main"`
  (unlike `extract_from_repo`'s `ref: str | None`) means a URL-embedded ref
  would never win unless the caller overrides it. Recommend changing the
  default to `ref: str | None = None` — this only changes behavior for repos
  whose default branch isn't `main` (which already fail today), so it's a
  backward-compatible bug fix, but it's technically not "purely additive" and
  should get a nod before implementing.
- `nuguard/sbom/public_api.py`: no changes needed — `generate_sbom()`
  delegates to `SbomGenerator.from_repo`, so fixing the wrapper covers it.
  `SbomGenerateRequest.repo_ref` has the same `"main"`-default quirk;
  optional/lower-priority to touch.
- `nuguard/mcp/server.py`: no code change required — `from_repo` flows
  through to the CLI subprocess unchanged. Flagging a **pre-existing** (not
  new) quirk: the MCP tool's `ref` parameter defaults to `"main"` rather than
  `None`, so it always sends an explicit `--ref main`, which per the
  precedence rule would always beat a URL-embedded ref. Not a regression from
  this feature, but worth an optional follow-up (default `ref` to `None` in
  the MCP tool signature) — out of scope for this plan.

### 4. Config

No new required `nuguard.yaml` fields — GitHub API auth reuses the existing
`--token`/`GH_TOKEN`/`GITHUB_TOKEN` resolution already in `sbom.py`. Optional,
cuttable nice-to-have: `sbom_generation.subfolder_max_files` /
`subfolder_max_bytes` in `nuguard/config.py` (same `Field(alias=...)` pattern
as `source_path`/`source_ref`), threaded through to
`clone_github_subfolder`'s `max_files`/`max_total_bytes` kwargs.

### 5. JSON output — what changes and what doesn't

- **No schema change.** `AiSbomDocument.target` continues to be a plain
  string field.
- **Behavior change to document**: when `--from-repo` targets a subfolder,
  `target` stores the exact URL the user passed — including the
  `/tree/<ref>/<path>` suffix or bare subfolder suffix — rather than a
  literal, directly re-clonable repo URL. Any downstream tooling that does
  `git clone <target>` verbatim (outside nuguard) will need to learn to parse
  this new URL shape, or use `nuguard`'s own parser.
- `ScanSummary` still has no `branch`/`source_ref` field (pre-existing gap,
  unrelated to and not fixed by this work).
- This should be called out in the README/CHANGELOG when the feature ships.

## Critical files

**New:**
- `nuguard/common/github_url.py`
- `nuguard/sbom/extractor/github_clone.py`
- `nuguard/sbom/extractor/git_safety.py` (hoisted `_SAFE_URL_RE`/`_SAFE_REF_RE`)

**Modified (additive only):**
- `nuguard/sbom/extractor/core.py` — add `extract_from_repo_subfolder`;
  `extract_from_repo`/`_clone_repo` left untouched.
- `nuguard/cli/commands/sbom.py` — `_do_generate` subfolder gate.
- `nuguard/cli/commands/analyze.py` — `_clone_remote_source_for_analysis` gate.
- `nuguard/sbom/generator.py` — `from_repo` gate + `ref` default change.

**Reused as-is (no changes needed):**
- `nuguard/common/url_sanitization.py`

## Test plan

- New `tests/common/test_github_url.py`: repo-root URLs (with/without
  `.git`, trailing slash), `/tree/<ref>/<subpath>` (single/nested segments),
  bare shorthand (single/nested), non-GitHub host → `None`, `..`/`%2f`
  rejection. (No denylist test needed — that mitigation was replaced by the
  try-first-then-fallback logic tested below.)
- New test for `_is_repository_not_found_error` (the stderr classifier next
  to `_clone_repo`): matches on the exact "not found" pattern from the bug
  report; does **not** match on auth-failure/timeout/rate-limit-style stderr
  text.
- New CLI-level tests for the try-first sequence (in
  `tests/cli/test_sbom_generate_subfolder.py`, see below): (a) a bare
  shorthand URL where the direct clone **succeeds** → asserts
  `extract_from_repo_subfolder` is never called (no wasted work, existing
  path taken); (b) a bare shorthand URL where the direct clone fails with a
  "not found" stderr → asserts fallback to `extract_from_repo_subfolder`
  with the correctly split `repo_root_url`/`subpath`; (c) a bare shorthand
  URL where the direct clone fails with a non-"not found" error (e.g. auth
  failure) → asserts the original error propagates and
  `extract_from_repo_subfolder` is never called; (d) a `/tree/<ref>/<path>`
  URL → asserts it routes straight to `extract_from_repo_subfolder` with
  **no** direct-clone attempt first.
- Assert `INFO`-level log records are emitted at each stage listed in §1/§3
  (URL-shape detection, direct-attempt outcome, fallback decision,
  tree-manifest match counts) using `caplog`.
- New `tests/sbom/test_github_subfolder_clone.py`: use a local bare git repo
  fixture (`git init --bare` + push, served via `file://`) for the git
  operations, and mock `httpx.Client.get` for the tree-manifest call (no real
  network access in tests). Cover: happy path materializes only the
  subfolder's files; symlink exclusion; wildcard-escaped filenames;
  nonexistent/empty subfolder → `ValueError`; cap-exceeded → `RuntimeError`;
  `main`→`master` fallback only when `ref is None`; explicit-ref fetch
  failure propagates without falling back.
- New CLI-level test (`tests/cli/test_sbom_generate_subfolder.py`): mock
  `clone_github_subfolder`/`extract_from_repo_subfolder`, assert a
  `/tree/...` URL routes to the new path and a plain repo-root URL does
  **not** (this is the key regression guard for "purely additive, gated on
  subfolder detection").
- Extend `tests/cli/test_analyze_remote_source_clone.py` with a subfolder
  `source:` case, asserting the mocked clone call receives the right subpath
  and the returned path includes it.
- **Must keep passing unmodified** (no assertion changes):
  `tests/sbom/test_clone_repo_arg_injection.py` (pins `_clone_repo`'s exact
  contract, untouched by this work), `test_analyze_remote_source_clone.py`'s
  existing non-subfolder cases, `nuguard/sbom/tests/smoke/
  test_healthcare_voice_agent.py`, `tests/mcp/test_tools.py`.
- Run `uv run pytest tests/ -v`, `uv run ruff check nuguard/`,
  `uv run mypy nuguard/` before considering the feature done.
- Manual end-to-end verification:
  ```
  uv run nuguard sbom generate --from-repo \
    https://github.com/NuGuardAI/openai-cs-agents-demo/tree/main/python-backend \
    -o /tmp/out.json
  uv run nuguard sbom generate --from-repo \
    https://github.com/NuGuardAI/openai-cs-agents-demo/python-backend \
    -o /tmp/out2.json
  # And a known-nonexistent subfolder, to confirm the error/exit-code
  # matches today's "repo not found" UX.
  ```

## Complexity, Limitations, and Ambiguities

- **GitHub-only.** No GitLab/Bitbucket/self-hosted-git subfolder support
  (matches NuGuard-app's own scope restriction, whose upstream validation
  already limits non-GitHub hosts to repo-root only). Document in
  `--from-repo` help text and README.
- **Unauthenticated GitHub API rate limit**: 60 requests/hour/IP for the
  tree-manifest call — one call per subfolder invocation. This is a real
  practical limit for unauthenticated/CI/scripted use; `--token`/`GH_TOKEN`
  is the mitigation and must be documented prominently.
- **New git version floor** (≥ 2.25) for this feature only — the existing
  repo-root `_clone_repo` path has no such requirement, so this is a
  feature-specific minimum, not a package-wide one.
- **`--ref` vs. URL-embedded-ref precedence** is a judgment call (`--ref`
  wins) — reasonable people could argue the URL, being more specific, should
  win instead. Documented clearly, with a printed note when the two conflict.
- **`ref:`/`source_ref` config precedence**: sits between `--ref` and the
  URL-embedded ref, not above it. An alternative (config always beats the
  URL) is equally defensible — flagging this so it isn't silently assumed by
  future readers.
- **Bare-shorthand ambiguity is resolved via try-first-then-fallback**
  (§3), not a denylist: a 3+-segment `github.com/...` URL is first attempted
  as a direct, unmodified clone; only a definitive "not found" failure
  triggers reinterpretation as `owner/repo` + subfolder. This correctly
  handles GitHub UI pages like `.../releases` or `.../wiki` (they resolve
  directly, since they 404 the same way whether or not a real repo path
  matches — actually they don't exist as clonable paths either, so **this
  case still falls through to the subfolder interpretation**, same as a
  real subfolder would). In other words, try-first removes false positives
  against *real, clonable* nested paths, but it cannot distinguish "a
  subfolder named `wiki`" from "the URL for a repo's actual GitHub wiki UI
  page" — both look identical to git (neither is a valid clone target), so
  the tree-manifest lookup in `clone_github_subfolder` becomes the final
  arbiter: if `wiki` (or `releases`, etc.) exists as a real directory in the
  repo's tree, it's scanned as a subfolder; if not, the existing "not found"
  error surfaces. This is a smaller and more honest limitation than the
  denylist's silent misclassification risk, and needs no denylist at all.
  **Cost**: every genuine bare-shorthand subfolder URL now pays one extra
  failed-clone attempt (network round-trip + process spawn) before falling
  into the subfolder path — minor added latency, offset by the `INFO`-level
  logging making the two-step resolution visible rather than mysterious.
- **`SbomGenerator.from_repo`'s `ref="main"` default needs to change** to
  `None` for ref-precedence correctness through that wrapper. This is
  technically a small behavior change beyond pure-additive, though it only
  affects repos whose default branch isn't `main` (already broken today).
  Needs explicit sign-off before implementing.
- **`AiSbomDocument.target` semantics change** (accepted per decision above):
  no longer guaranteed to be a literal, directly re-clonable URL when a
  subfolder was scanned. No schema change, but this must be documented in
  README/CHANGELOG since external tooling could depend on the old guarantee.
- **MCP tool's pre-existing `ref="main"` default** interacts oddly with
  URL-embedded refs (always wins per precedence, since MCP always sends an
  explicit `--ref`). This is a pre-existing quirk, not a regression
  introduced here — flagged as an optional follow-up, out of scope for this
  plan.
- **Nested subfolders** are fully supported by both URL forms and the
  tree-filter logic (prefix matching handles arbitrary depth) — no special
  handling needed beyond an explicit test case.
