---
name: sbom-analysis
description: >
  This skill should be used when the user opens, mentions, or asks questions about a
  .sbom.json file, an AI Bill of Materials, or an aibom.json, or asks what AI components,
  tools, datastores, or MCP servers an application uses and how they connect.
version: 1.0.0
---

Read and interpret NuGuard AI-SBOM files to answer questions about an AI application's
components and attack surface.

## SBOM shape

```
{
  "schema_version": "1.4.0",
  "generated_at": "<ISO timestamp>",
  "target": "<repo or source path>",
  "nodes": [ ... ],   // AI components
  "edges": [ ... ],   // directed relationships between components
  "deps": [ ... ],    // Python/JS package dependencies
  "summary": { ... }  // high-level scan summary
}
```

## Node types

| Type | Check |
|---|---|
| `AGENT` | `system_prompt_excerpt`, `blocked_topics`, `injection_risk_score` |
| `MODEL` | `model_name`, `provider` |
| `TOOL` | `sql_injectable`, `ssrf_possible`, `no_auth_required`, `high_privilege` |
| `DATASTORE` | `data_classification` for PII/PHI, `auth_type` |
| `GUARDRAIL` | whether it appears in a `CALLS` edge from an `AGENT` |
| `MCP_SERVER` | `trust_level` — untrusted servers are toxic-flow targets |
| `API_ENDPOINT` | `no_auth_required`, `http_method` |
| `PROMPT` | injection surfaces in `system_prompt_excerpt` |

## Edge types

`CALLS` (agent → tool/model), `ACCESSES` (component → datastore, with `access_type`:
read/write/readwrite), `GUARDED_BY` (component filtered by a guardrail), `USES_AUTH`,
`EXPOSES` (service → endpoint).

A path `AGENT → CALLS → TOOL → ACCESSES → DATASTORE` with no `GUARDED_BY` edge on the TOOL
is a structural risk (NGA-001, or NGA-009 if the datastore holds PII/PHI).

## Answering common questions

- **"What AI components does this app use?"** List nodes grouped by `component_type`; for
  each `AGENT`, name its connected `TOOL`/`MODEL` nodes from `CALLS` edges.
- **"Is this app secure?"** Don't judge from the SBOM alone — run
  `nuguard analyze --sbom <path>` and explain that structural graph checks are more
  reliable than manual reading.
- **"What data does this app access?"** Find `DATASTORE` nodes; report
  `data_classification`, `access_type` from `ACCESSES` edges, and whether an auth node
  sits in the path.
- **"Does this app have guardrails?"** Find `GUARDRAIL` nodes and check whether `CALLS` or
  `GUARDED_BY` edges connect them to agents/tools.
- **"What frameworks does this app use?"** Check `summary.frameworks` and the `framework`
  field on `AGENT`/`PIPELINE`/`CHAIN` nodes.

## Risk signals to flag directly from the SBOM

Flag these even without running `nuguard analyze`:

- Any `TOOL` with `sql_injectable: true` or `ssrf_possible: true`
- Any `DATASTORE` with PII/PHI classification and no reachable auth node
- Any `MCP_SERVER` with `trust_level: untrusted`
- Any `AGENT` with `injection_risk_score > 0.7`
- Any `API_ENDPOINT` with `no_auth_required: true` and a write-capable HTTP method
- A `system_prompt_excerpt` containing instruction-like phrases fetched from an external
  source (indirect injection surface)
