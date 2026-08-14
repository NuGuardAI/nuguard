---
applyTo: "**"
description: "Use PostgreSQL-specific best practices when creating new databases/instances, schemas, migrations, or SQL. Covers JSONB, arrays, custom types, indexing, performance, and RLS/security."
---

# PostgreSQL Database Creation Guidelines

When creating or modifying PostgreSQL databases/instances, schemas, or migrations, follow these guidelines:

## PostgreSQL-Specific Best Practices

### Data Types and Schema Design
- Prefer PostgreSQL-native types (JSONB, CITEXT, ARRAY, ENUM, domains) over generic VARCHAR/TEXT where appropriate.
- Use TIMESTAMPTZ instead of TIMESTAMP for time data.
- Add CHECK constraints for value validation (status enums, email format, etc.).

### Indexing Strategy
- Use GIN/GiST indexes for JSONB, arrays, and ranges.
- Use containment operators for JSONB (@>, ?, ?|) and arrays (@>) to leverage indexes.
- Avoid unindexed JSONB path lookups with ->> when it can be replaced by containment queries.

### JSONB and Array Usage
- Structure JSONB with stable keys and apply constraints where possible.
- Avoid deep JSONB nesting without a clear query/index strategy.
- Avoid inefficient array operations; prefer indexed @> queries when possible.

### Custom Types and Domains
- Use ENUMs for constrained values (status, severity, type).
- Use domains to enforce reusable constraints (positive_amount, email, etc.).

### Performance and Functions
- Prefer set-based operations over row-by-row loops.
- Use PL/pgSQL functions judiciously; avoid heavy trigger logic.
- For triggers, include WHEN clauses to avoid unnecessary updates.

### Security and RLS
- Use least-privilege grants; avoid GRANT ALL.
- Implement Row Level Security (RLS) where multi-tenant isolation requires it.
- Avoid storing secrets in plaintext; use pgcrypto or external secrets stores.

## Review Checklist (Use During DB Creation)
- Appropriate Postgres data types used
- Index types match query patterns
- JSONB and arrays are structured and indexable
- Constraints and enums applied where applicable
- Security and RLS considered
