---
name: pydantic-interface
description: >
  Use when adding, changing, fixing, or removing NuGuard package functionality that has a
  public Python or platform-facing interface. Keeps public Pydantic request/result models,
  streaming events and reducers, exports, JSON Schema snapshots, documentation, redaction,
  and compatibility tests synchronized with implementation changes.
user-invocable: false
---

# Pydantic Interface Maintenance

Keep NuGuard's supported Pydantic boundary synchronized with functional changes.

## Workflow

1. Identify whether the changed behavior is callable by library, CLI, MCP, plugin, or platform
   consumers. Inspect the nearest `public_api.py`, existing exports, and contract tests before
   editing.
2. Reuse or extend public `BaseModel` request, result, configuration, event, and reducer types.
   Do not expose implementation dataclasses, private helpers, unresolved credentials, raw target
   responses, or mutable internal state.
3. Preserve existing public call signatures and import paths unless a breaking change is
   explicitly approved. Add normalization at the public boundary when an existing public model
   must interoperate with an internal model.
4. Ensure every public model is JSON-safe with `model_dump(mode="json")`, round-trips through
   validation, and has stable field names and constrained values. Use `SecretStr` for required
   credential inputs and omit secrets from results, events, cache metadata, logs, and errors.
5. For streaming changes, use `StreamEvent` and the public reducer. Preserve monotonic sequence
   and progress values, exactly one terminal event, settled cancellation, and sanitized stable
   failures.
6. Add focused tests for success, validation failure, serialization, backwards compatibility,
   side effects, and credential leakage. Public wrappers must not add filesystem or network side
   effects by default.
7. Register new public models in `tests/contracts/test_public_api_schema_contract.py`, regenerate
   `tests/contracts/public_api.schema.json`, and inspect the diff for accidental contract churn.
8. Update relevant library and platform-integration documentation, including cancellation,
   caching, persistence, authentication, and error behavior where applicable.

## Validation

Run the narrowest affected tests first, then complete these checks before finishing:

```powershell
uv run pytest tests/contracts/test_public_api_schema_contract.py -q
uv run ruff check nuguard/ tests/
uv run mypy nuguard/
uv run pytest tests/ -q
```

Search serialized results, events, logs, and public exception messages for fixture secrets when
the change handles repository URLs, API keys, authorization headers, cookies, or target output.