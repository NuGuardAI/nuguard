# Contributing to Xelo

Thanks for contributing. This project accepts issues and pull requests from the community.

## Before You Start

- Read the [Code of Conduct](./CODE_OF_CONDUCT.md).
- For security-sensitive reports, use [SECURITY.md](./SECURITY.md) instead of public issues.
- Check existing issues and pull requests to avoid duplicate work.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Pull Request Guidelines

- Keep PRs focused and small enough to review.
- Add or update tests for behavior changes.
- Update docs when user-facing behavior changes.
- Do not include secrets, credentials, or private data.

Use the PR template and include:

- What changed
- Why it changed
- How you validated it

## Commit Guidance

- Use clear, descriptive commit messages.
- Prefer one logical change per commit.

## Release Notes

If your change affects users, include a short note maintainers can reuse in release notes.
