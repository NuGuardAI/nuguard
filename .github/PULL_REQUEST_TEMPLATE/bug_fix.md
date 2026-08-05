## What

What changed at a high level?

Example:
- Fixed...
- Corrected...
- Patched...

## Why

Why was this a bug? What was the user-visible or system impact?

**Example:**
- Reported in issue #...
- Caused incorrect output when...
- Crashed / hung when...

## Root Cause

What was actually causing the bug?

**Example:**
- An off-by-one error in...
- A race condition between...
- Missing null check on...

## How

How did you fix it?

Example:
- Paired with <Teammate Name> where we did A, B, C
- Tried another approach but it didn't work because X, Y, Z
- Used these code APIs/SDKs: <Name>, <Name>, <Name>

## Test Steps

What are all the steps to verify the fix (and confirm no regression)?

**Example:**
- [ ] Reproduce the bug on `main` using these steps: ...
- [ ] Confirm the bug no longer occurs on this branch
- [ ] Added a regression test covering this case

## Checks

- [ ] `make test` passes (`uv run pytest tests/ -v`)
- [ ] `make lint` passes (`ruff check` + `mypy`)
- [ ] `make fmt` applied, no diff
- [ ] Added/updated a regression test for this bug

## Other Notes

What, if anything, hasn't been addressed in these code changes but should be in future changes?

**Example:**
- ABC wasn't working as expected...
- XYZ needs more research...
- A fast-follow PR is already planned for addressing 1, 2, 3...

<!-- pr-type: bug -->
