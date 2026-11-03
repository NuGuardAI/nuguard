# Discrepancies: expected vs generated AI-SBOM

Compared `EXPECTED_SBOM.md` (committed before any NuGuard run) with the output of
`nuguard sbom generate --config nuguard.prepublish.yaml` (deterministic, no LLM) on this directory.
NuGuard 0.9.16, `develop` at `81ac4b93`, 2026-11-01.
Result: 53 nodes, 77 edges (1 AGENT, 1 API_ENDPOINT, 2 AUTH, 3 CONTAINER_IMAGE, 2 DATASTORE,
7 DEPLOYMENT, 5 FRAMEWORK, 3 IAM, 3 MODEL, 1 PRIVILEGE, 7 PROMPT, 18 TOOL); 15 NuGet dependencies.

## Matches
- Frameworks: ASP.NET Core and Semantic Kernel.
- All 15 NuGet `PackageReference`s from the `.csproj`, including `Microsoft.AspNetCore.Authentication.JwtBearer`.
- All 14 `[KernelFunction]` tools by name (`AddFunTransferRequest`, `GetTransactionHistory`,
  `CheckPendingServiceRequests`, `RegisterAccount`, ...), with `Microsoft Semantic Kernel CALLS` each.
- Model `gpt-4o` from Bicep and the Azure OpenAI chat completion registration
  (`AddAzureOpenAIChatCompletion`); Cosmos DB as a datastore; Azure deployment, managed identity
  and role assignments from Bicep; Dockerfile base images.
- A `PRIVILEGE` node `db_write`.
- `analyze` found the real advisory `GHSA-2WW3-72RP-WPP4` (Semantic Kernel arbitrary file write
  via agent function calling) for the pinned `Microsoft.SemanticKernel.Core 1.47.0`.

## Discrepancies

| # | Expected | Found | Evidence | Class |
|---|----------|-------|----------|-------|
| D1 | Many minimal-API endpoints (`MapGet/MapPost/MapPut/MapDelete` in `ChatEndpoints.cs`) plus `POST /auth/token` and `POST /chat` | 1 endpoint: `POST /tenant/{tenantId}/user/{userId}/sessions/{sessionId}/completion` | `summary.api_endpoints` | NuGuard detection gap |
| D2 | Endpoints require JWT (`RequireAuthorization()` on the route group) | `auth_required: false` on the one endpoint, although two AUTH nodes have `PROTECTS` edges to it | endpoint metadata vs edges | NuGuard detection gap |
| D3 | AUTH: JWT bearer (`AddJwtBearer`, HS256) | `Apikey` ("auth_apikey: bearer" in `DemoAuth.cs`) and `Generic` (the word `Authorization` in `assignroles.bicep`) | node evidence | NuGuard detection gap (misclassified) |
| D4 | 4 agents (`Coordinator`, `CustomerSupport`, `Sales`, `Transactions`) in an `AgentGroupChat` with LLM selection/termination strategies | 1 synthesized agent "Banking Multi Agent Copilot Assistant" with no evidence and no edges to tools, prompts or model | AGENT node, edge list | NuGuard detection gap |
| D5 | Each agent's plugin tools only | `Microsoft Semantic Kernel CALLS` all 18 tools; no agent-to-tool or endpoint-to-agent path | edge list | NuGuard detection gap |
| D6 | 4 plugin objects registered with `Plugins.AddFromObject(...)` | Reported as 4 extra TOOL nodes `Plugin 100/104/108/112` (named by line number) | `AgentFactory.cs` lines 100-112 | NuGuard detection gap (noise) |
| D7 | Write tools (`AddFunTransferRequest`, `RegisterAccount`, `UpdateExistingServiceRequest`, ...) marked high privilege | `high_privilege: false` on all 18 tools | tool metadata | NuGuard detection gap |
| D8 | Prompts named after their files (`Coordinator.prompty`, ...) | 7 PROMPT nodes named `Prompt 29`, `Prompt 33`, `Instructions`, `Promptfile`, ... (by line) | node names | Cosmetic |
| D9 | One model per deployment setting | 3 MODEL nodes: `gpt-4o` (Bicep), `gpt-5.4-mini` (from a comment in `scripts/serve.sh`) and `_skSettings.AzureOpenAISettings.CompletionsDeployment` (the C# expression) | MODEL evidence | NuGuard detection gap (expression used as a name) |
| D10 | Embeddings model `text-embedding-3-small` (`AzureOpenAITextEmbeddingGenerationService`) | Not reported | | NuGuard detection gap |
| D11 | Cosmos vector store (`AzureCosmosDBNoSQLVectorStoreRecordCollection`) | Cosmos found only from Bicep, not from code; no vector store | DATASTORE evidence `assignroles.bicep:18` | NuGuard detection gap |
| D12 | No Redis | DATASTORE `Redis` | `infra/abbreviations.json` (Azure naming-abbreviation table) | False positive |
| D13 | Frameworks: ASP.NET Core, Semantic Kernel | Also `OpenAI .NET SDK` (from `using OpenAI.Chat` for a response-format type), `Builder` and ` Semantickernel` (from `Kernel.CreateBuilder()` / `builder.Build()`) | FRAMEWORK evidence | False positive / noise |
| D14 | In-memory fixture store | Not reported | `Services/InMemory/` | Expected: plain C# collections |

## Behavior and red-team notes

- `behavior` needed `scenario_timeout: 600`. Each turn runs the 4-agent group chat (about 20-36 s), so
  the 180 s default timed out mid-scenario and the run reported `scenarios_executed: 0`.
- `prepublish-sanity.sh` does not parse under macOS bash 3.2 (heredoc inside `$(...)`); it is unchanged
  on `develop` and runs under bash 5 in CI. Local runs used the same three commands directly.
