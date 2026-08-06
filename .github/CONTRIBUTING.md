# Contributing to NuGuard

Thanks for contributing. This project accepts issues and pull requests from the community.

## Before You Start

- Read the [Code of Conduct](./CODE_OF_CONDUCT.md).
- For security-sensitive reports, use [SECURITY.md](./SECURITY.md) instead of public issues.
- Check existing issues and pull requests to avoid duplicate work.
- When opening an issue, pick the **Bug Report** or **Feature Request** template
  (`.github/ISSUE_TEMPLATE/`) — each auto-applies the matching `bug`/`enhancement`
  label and ends with a hidden `<!-- issue-type: bug -->` / `<!-- issue-type: feature -->`
  marker for automation; leave it in place.

## Development Setup

```bash
make dev
```

Runs `uv sync --dev` and installs the project with its dev dependencies into a local `.venv`.

## Running Tests, Lint, and Format

```bash
make test    # uv run pytest tests/ -v
make lint    # ruff check + mypy over nuguard/
make fmt     # ruff format over nuguard/ and tests/
```

Install the pre-commit hooks once so lint/format run automatically on commit:

```bash
make precommit-install
```

Run all pre-commit hooks on demand (e.g. before opening a PR):

```bash
make precommit-run
```

All four checks (`test`, `lint`, `fmt` check, and pre-commit) should pass locally before you open a PR — the [PR Tests](workflows/pr-tests.yml) workflow re-runs lint and type checks on every pull request.

## Branch Naming

Branch names are enforced server-side by a repository ruleset — a push that doesn't match is rejected outright, not just flagged in review.

- Format: `feat/<short-description>` or `bug/<short-description>` — lowercase, hyphenated (e.g. `feat/add-pr-templates`, `bug/fix-null-check`).
- `main` and `develop` are exempt from the rule.
- Only applies going forward: existing branches that predate the rule aren't affected, and it only blocks *creating* a new non-conforming branch, not pushing further commits to one you already have.

## Pull Request Guidelines

- Keep PRs focused and small enough to review.
- Add or update tests for behavior changes.
- Update docs when user-facing behavior changes.
- Do not include secrets, credentials, or private data.
- Run `make test` and `make lint` locally before opening the PR.
- Opening a PR auto-fills the description from `.github/pull_request_template.md`.
  Check exactly one box under **PR Type** (`Bug fix` or `Feature`) — that checkbox is
  what automation reads to classify the PR, so leave the two lines in place even if
  you trim other sections. Delete the **Root Cause** section for feature PRs.
- Ensure that all `feat/*` branches are set up from develop and open PRs for develop
- Ensure that all `bug/*` branches are set up from main and open PRs for main

Include in the PR description:

- **What** changed
- **Why** it changed
- **How** you validated it (tests run, manual verification steps)

## Commit Guidance

- Use clear, descriptive commit messages.
- Prefer one logical change per commit.

## Release Notes

If your change affects users, include a short note maintainers can reuse in release notes.
