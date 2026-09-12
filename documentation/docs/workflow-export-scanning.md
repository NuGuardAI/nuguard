# Workflow export scanning

NuGuard can statically scan exported workflows from n8n, Langflow, Flowise,
and Microsoft Copilot Studio. The export is normalized into the existing
AI-SBOM component graph, allowing existing discovery, policy, and analysis
logic to reason about low-code workflows.

## Supported files

| Platform | Export shape |
| --- | --- |
| n8n | Workflow JSON with `nodes` and `connections` |
| Langflow | Flow JSON with native node metadata and graph edges |
| Flowise | Chatflow or agentflow JSON, including encoded `flowData` |
| Copilot Studio | `AdaptiveDialog` topics in `.mcs.yml` or `.mcs.yaml` |

Recognition uses platform-specific structural fingerprints. A generic JSON
document containing `nodes` and `edges` is not sufficient on its own.

## Normalized components

The adapters can emit these existing AI-SBOM component types:

- `FRAMEWORK`
- `AGENT`
- `MODEL`
- `PROMPT`
- `TOOL`
- `DATASTORE`
- `GUARDRAIL`
- `AUTH`
- `API_ENDPOINT`

Native graph edges are converted into relationships such as:

- agent uses model
- agent uses prompt
- agent calls tool
- tool accesses datastore
- endpoint calls agent
- authentication protects a resource
- guardrail protects an agent, model, or endpoint

Tool metadata includes detected privilege scopes, write or side-effect
capability, dynamic outbound URL handling, and possible SSRF exposure. Human
approval nodes annotate the workflow with HITL metadata.

## Credential handling

Workflow exports can contain credential references and, in poorly sanitized
exports, literal secret values.

NuGuard preserves useful non-secret references such as credential type, name,
and ID. It does not copy raw credential values into AI-SBOM metadata or
evidence. The adapter redacts:

- bearer tokens
- common API-key formats
- password, token, secret, authorization, and connection-string fields
- sensitive URL query parameters
- URL user information
- suspicious high-entropy URL path segments

The original workflow file remains subject to NuGuard's normal static secret
scanning. Export files should still be treated as sensitive source artifacts.

## Runtime boundary

Static exports provide component, capability, guardrail, authentication, and
endpoint context.

Behavior and red-team execution still require a running deployment and a
configured base URL. The adapters emit an API endpoint only when the export
contains a concrete trigger path or URL. They do not invent deployment URLs
for Langflow, Flowise, or Copilot Studio.

The scanner does not launch workflow platforms, execute workflow nodes,
invoke connectors, or run exported code.
