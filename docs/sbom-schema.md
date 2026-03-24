# NuGuard AI SBOM Schema

This document describes the canonical AI-SBOM document shape used by NuGuard.

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
  "generator": "nuguard",
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

## Framework Coverage

NuGuard populates SBOM nodes by statically analysing source files with these framework adapters:

### Python adapters

| Adapter | Frameworks / libraries detected |
|---|---|
| `langchain` | LangChain chains, tools, memory, retrievers |
| `langgraph` | LangGraph graph nodes, agents, conditional edges |
| `openai_agents` | OpenAI Agents SDK — agents, tools, handoffs, prompts |
| `crewai` | CrewAI crews, agents, tasks |
| `autogen` | AutoGen agents, group chats |
| `agno` | Agno agents, tools, knowledge, teams |
| `llamaindex` | LlamaIndex query engines, data connectors, synthesizers |
| `semantic_kernel` | Semantic Kernel kernels, plugins, prompts |
| `azure_ai_agents` | Azure AI Foundry agents, tools, threads |
| `google_adk` | Google Agent Development Kit agents, tools, sessions |
| `bedrock_agentcore` | AWS Bedrock AgentCore runtimes, tools |
| `guardrails_ai` | GuardrailsAI validators |
| `mcp_server` | Model Context Protocol servers, tools, resources |
| `fastapi_adapter` | FastAPI routes, auth dependencies, middleware |
| `flask_adapter` | Flask routes, auth decorators |
| `llm_clients` | OpenAI / Anthropic / Gemini / Cohere / local model clients |

### TypeScript / JavaScript adapters

| Adapter | Frameworks / libraries detected |
|---|---|
| `langgraph` (TS) | LangGraph.js graph nodes, agents |
| `openai_agents` (TS) | OpenAI Agents SDK for Node — agents, tools, handoffs |
| `agno` (TS) | Agno agents, tools |
| `azure_ai_agents` (TS) | Azure AI Foundry agents, tools |
| `google_adk` (TS) | Google ADK agents, tools (TypeScript SDK) |
| `bedrock_agents` (TS) | AWS Bedrock Agents (JavaScript SDK) |
| `llm_clients` (TS) | OpenAI / Anthropic / Google / Vertex AI / Bedrock clients |
| `datastores` (TS) | PostgreSQL, MySQL, MongoDB, Redis, DynamoDB, Cosmos DB |
| `prompts` (TS) | Prompt templates and system messages |

### Infrastructure adapters

| Adapter | What is detected |
|---|---|
| `dockerfile` | Container images, run-as-root, EXPOSE ports, resource annotations |
| `iac` | Kubernetes manifests — RBAC, resource quotas, network policies |
| `iac` | Terraform / CloudFormation — IAM roles, storage encryption |
| `nginx` | Nginx reverse-proxy configs — auth headers, rate-limit rules |
| `yaml_adapters` | Model endpoints, environment variables, secrets in YAML configs |
| `data_classification` | PII/PHI field annotations in SQLAlchemy / Django / Pydantic models |
| `privilege` | Privileged scope indicators across all detected components |
