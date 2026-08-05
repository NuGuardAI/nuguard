## PR Type

<!--
Check exactly ONE box below. This is the single source of truth automation
reads to classify the PR (e.g. a GitHub Action looking for "[x] Bug fix" vs
"[x] Feature") — do not remove or rephrase these two lines.
-->
- [ ] Bug fix
- [ ] Feature

## What

What changed at a high level?

Example:
- Added...
- Updated...
- Refactored...
- Fixed...

## Why

Why are these changes helpful or necessary?

**Example:**
- New feature requested...
- Reported in issue #...
- Addressing design feedback...
- Fast-follow to previous PR...

## Root Cause

<!-- Bug fixes only — delete this section for feature PRs. -->

What was actually causing the bug?

**Example:**
- An off-by-one error in...
- A race condition between...
- Missing null check on...

## How

How did you go about making these changes?

Example:
- Paired with <Teammate Name> where we did A, B, C
- Tried another approach but it didn't work because X, Y, Z
- I followed this resource by <Author Name>: <Resource Link>
- Used these code APIs/SDKs: <Name>, <Name>, <Name>

## Test Steps

What are all the steps to testing your code changes?

**Example:**
- [ ] Reproduce the original issue on `main` using these steps: ...
- [ ] Confirm the issue no longer occurs on this branch
- [ ] Enable `feature_flag`
- [ ] Go to this page: /a-test-page
- [ ] Etc...

## Checks

- [ ] `make test` passes (`uv run pytest tests/ -v`)
- [ ] `make lint` passes (`ruff check` + `mypy`)
- [ ] `make fmt` applied, no diff
- [ ] Added/updated tests covering this change (regression test for bugs, new coverage for features)

## Other Notes

What, if anything, hasn't been addressed in these code changes but should be in future changes?

**Example:**
- ABC wasn't working as expected...
- XYZ needs more research...
- A fast-follow PR is already planned for addressing 1, 2, 3...
