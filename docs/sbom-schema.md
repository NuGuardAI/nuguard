# NuGuard SBOM Schema

This document describes the canonical AI-SBOM document shape used by NuGuard.

The source of truth for the schema is:

- [nuguard/sbom/models.py](/workspaces/nuguard-oss/nuguard/sbom/models.py)
- [nuguard/sbom/schemas/aibom.schema.json](/workspaces/nuguard-oss/nuguard/sbom/schemas/aibom.schema.json)

## Overview

NuGuard uses an AI Bill of Materials document with these top-level fields:

- `schema_version`
- `generated_at`
- `generator`
- `target`
- `nodes`
- `edges`
- `deps`
- `summary`

The current schema version in the model is `1.4.0`.

## Top-Level Object

Example shape:

```json
{
  "schema_version": "1.4.0",
  "generated_at": "2026-03-23T00:00:00Z",
  "generator": "xelo",
  "target": "https://github.com/NuGuardAI/nuguard-oss",
  "nodes": [],
  "edges": [],
  "deps": [],
  "summary": null
}
```

## Nodes

Each entry in `nodes` represents a discovered AI-related component.

Required fields:

- `name`
- `component_type`
- `confidence`

Common fields:

- `id`: UUID
- `metadata`: typed but extensible metadata
- `evidence`: detection evidence entries

### Supported `component_type` values

- `AGENT`
- `GUARDRAIL`
- `FRAMEWORK`
- `MODEL`
- `TOOL`
- `DATASTORE`
- `AUTH`
- `PRIVILEGE`
- `API_ENDPOINT`
- `DEPLOYMENT`
- `PROMPT`
- `CONTAINER_IMAGE`
- `IAM`

### Example node

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "name": "support-agent",
  "component_type": "AGENT",
  "confidence": 0.98,
  "metadata": {
    "framework": "openai-agents",
    "system_prompt_excerpt": "You are a customer support assistant.",
    "extras": {
      "adapter": "openai_agents"
    }
  },
  "evidence": [
    {
      "kind": "ast",
      "confidence": 0.98,
      "detail": "openai_agents: agent instantiation",
      "location": {
        "path": "app/agents.py",
        "line": 12
      }
    }
  ]
}
```

## Edges

Each entry in `edges` represents a directed relationship between two nodes.

Required fields:

- `source`
- `target`
- `relationship_type`

Optional fields:

- `access_type`

### Supported `relationship_type` values

- `USES`
- `CALLS`
- `ACCESSES`
- `PROTECTS`
- `DEPLOYS`

### Supported `access_type` values

- `read`
- `write`
- `readwrite`

### Example edge

```json
{
  "source": "11111111-1111-1111-1111-111111111111",
  "target": "22222222-2222-2222-2222-222222222222",
  "relationship_type": "CALLS"
}
```

## Dependencies

`deps` contains package dependencies discovered from manifests.

Common fields:

- `name`
- `version`
- `version_spec`
- `purl`
- `ecosystem`
- `source_file`

Example:

```json
{
  "name": "openai",
  "version": "1.70.0",
  "version_spec": ">=1.70,<2.0",
  "purl": "pkg:pypi/openai@1.70.0",
  "ecosystem": "pypi",
  "source_file": "pyproject.toml"
}
```

## Summary

`summary` is scan-level metadata derived during extraction.

Common fields include:

- `use_case`
- `frameworks`
- `modalities`
- `api_endpoints`
- `deployment_platforms`
- `regions`
- `environments`
- `deployment_urls`
- `node_counts`
- `data_classification`
- `classified_tables`
- `startup_commands`
- `env_files`
- `env_var_keys`
- `local_url`
- `staging_urls`
- `production_urls`
- `log_paths`

Example:

```json
{
  "use_case": "Customer support assistant with tool access",
  "frameworks": ["openai-agents"],
  "modalities": ["TEXT"],
  "api_endpoints": ["/chat", "/health"],
  "node_counts": {
    "AGENT": 1,
    "TOOL": 1,
    "API_ENDPOINT": 1
  },
  "startup_commands": [
    {
      "command": "uv run python -m app.main",
      "source": "pyproject.toml",
      "label": "dev"
    }
  ],
  "local_url": "http://localhost:8000"
}
```

## Metadata Notes

`Node.metadata` is intentionally broad. Different node types populate different fields.

Common examples:

- AGENT:
  - `framework`
  - `system_prompt_excerpt`
  - `injection_risk_score`
- TOOL:
  - `description`
  - `parameters`
  - `no_auth_required`
  - `high_privilege`
  - `sql_injectable`
  - `ssrf_possible`
- API endpoint:
  - `endpoint`
  - `method`
  - `auth_required`
  - `accepts_user_input`
  - `returns_sensitive_data`
  - `chat_payload_key`
- DATASTORE:
  - `data_classification`
  - `classified_tables`
  - `classified_fields`
  - `pii_fields`
  - `phi_fields`

Unknown adapter-specific values go in `metadata.extras`.

## Validation

To validate an SBOM file with the CLI:

```bash
nuguard sbom validate --file ./docs/sample-sbom.json
```

To inspect the generated JSON schema:

```bash
nuguard sbom schema
```

## Sample File

A small example document lives at:

- [docs/sample-sbom.json](/workspaces/nuguard-oss/docs/sample-sbom.json)
