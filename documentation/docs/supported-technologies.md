# Supported Technologies

NuGuard discovers AI application components from source code, workflow exports, package manifests, and infrastructure configuration. This reference shows what the current repository recognizes and how deeply each technology is analyzed.

## How to read this guide

| Support level | Meaning |
|---|---|
| **Framework-aware** | A dedicated parser or adapter recognizes framework concepts such as agents, models, tools, prompts, endpoints, auth, guardrails, and datastores. |
| **Structured** | NuGuard parses a known export, manifest, or IaC shape and maps it into typed AI-SBOM nodes and relationships. |
| **Signal detection** | Generic patterns identify likely components when no dedicated framework adapter exists. Confirm these lower-context findings from their source evidence. |

```text
Application estate
├── Code                 Python · TypeScript/JavaScript · Go · C# · Java
├── Agentic stack        frameworks · model SDKs · MCP · guardrails · Prompts · Tools
├── Visual workflows     n8n · Langflow · Flowise · Copilot Studio · Sparkflows
├── Runtime platform     AWS · Azure · GCP · Kubernetes · containers
└── Data layer           SQL · NoSQL · vector · object storage · warehouses
                         ↓
              One evidence-backed AI-SBOM graph
```

## Languages and source formats

| Language or format | Support | What NuGuard extracts |
|---|---|---|
| Python (`.py`, `.pyw`) | Framework-aware | Agents, models, tools, prompts, guardrails, API endpoints, auth, datastores, and data classifications |
| Jupyter (`.ipynb`) | Framework-aware | Python code cells are inspected with the Python framework adapters |
| TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`) | Framework-aware | Agent and model SDKs, tools, prompts, NestJS endpoints/auth, datastores, and guardrails |
| Go (`.go`) | Framework-aware | AI SDKs, MCP, HTTP/GraphQL endpoints, auth, guardrails, and datastores |
| C# (`.cs`) | Framework-aware | Semantic Kernel, model clients, ASP.NET Core endpoints/auth, ML.NET, prompts, and datastores |
| Java (`.java`) | Framework-aware | AI/model SDKs, tools, prompts, guardrails, datastores, and Spring/JAX-RS endpoints |
| SQL (`.sql`) | Structured | Schemas, tables, and sensitive-field classifications such as PII, PHI, and PFI |
| JSON/YAML/TOML/CFG | Structured or signal detection | Workflow exports, framework configuration, package metadata, deployment settings, and generic component signals |
| Rust, Ruby, shell, and Markdown | Signal detection | Model names, credentials, prompts, endpoints, and other generic security signals; no language-specific AST adapter yet |

## AI and agentic frameworks

| Language | Frameworks and agent SDKs with dedicated or framework-aware coverage |
|---|---|
| Python | LangGraph, OpenAI Agents SDK, AutoGen, CrewAI, LlamaIndex, Agno, Azure AI Agent Service, AWS Bedrock AgentCore, Google ADK, Semantic Kernel, Claude Agent SDK, Vercel AI SDK, FastMCP and low-level MCP servers/clients |
| TypeScript/JavaScript | LangGraph.js, OpenAI Agents SDK, Claude Agent SDK, Google ADK, AWS Bedrock Agents, Azure AI Agents, Agno, Vercel AI SDK, NestJS, and agent registry/orchestrator patterns |
| Go | LangChainGo, Eino, Genkit, MCP servers/clients, OpenAI, Anthropic, Google GenAI, Ollama, and direct HTTP model clients |
| C# | Semantic Kernel, ML.NET, Azure OpenAI, OpenAI, Anthropic, and ASP.NET Core |
| Java | Spring AI, LangChain4j, Quarkus LangChain4j, OpenAI Java, Azure OpenAI Java, AWS Bedrock, Google Gen AI, Google Vertex AI, Spring MVC/WebFlux, JAX-RS, and Quarkus |

LangChain and LangChain.js imports and model classes are also recognized through framework and model-client signals. Dedicated LangGraph adapters provide deeper graph, agent, and tool relationships.

### Model providers and SDKs

Provider detection includes native SDKs and OpenAI-compatible clients where the configured base URL identifies the provider.

| Provider group | Recognized examples |
|---|---|
| OpenAI-compatible | OpenAI, Azure OpenAI, Groq, Together AI, DeepSeek, OpenRouter, Cerebras, Fireworks AI, and Perplexity |
| Anthropic | Anthropic Python, TypeScript, Go, and C# clients; Claude Agent SDK |
| Google | Google Gen AI, Gemini, Vertex AI, and Google ADK |
| AWS | Bedrock model clients, Bedrock Agents, and Bedrock AgentCore |
| Other model runtimes | Mistral, Cohere, Ollama, and Hugging Face model references |

### Guardrails and AI-security controls

NuGuard recognizes framework-native controls and dedicated security products, including Guardrails AI, OpenAI Agents guardrails, LangChain moderation, Claude hooks and tool approval, AWS Bedrock Guardrails, Azure AI Content Safety and Prompt Shields, GCP Model Armor, Palo Alto Prisma AIRS, Protect AI Guardian, Microsoft Presidio, llm-guard, Rebuff, NeMo Guardrails, and Lakera Guard.

## Low-code and no-code platforms

NuGuard scans exported workflow files; it does not connect to or execute the hosted platform.

| Platform | Accepted export | Extracted concepts |
|---|---|---|
| n8n | Workflow JSON | Agents, models, prompts, tools/connectors, webhooks, datastores, guardrails, and workflow edges |
| Langflow | Flow JSON | Components, prompts, models, tools, datastores, and graph connections |
| Flowise | Chatflow or agentflow JSON | Agents, chains, models, tools, memory/datastores, endpoints, and connections |
| Microsoft Copilot Studio | `.mcs.yml` topic exports | Topics, generative answers, actions/connectors, prompts, variables, and request triggers |
| Sparkflows | Exported project JSON tree | Projects, agents, prompts, model references, ETL workflows, datasets, analytics apps, JDBC/Salesforce connections, and H2O models |

Credentials and secret-bearing values are redacted before workflow evidence is written. See [Workflow Export Scanning](workflow-export-scanning.md) for export boundaries and examples.

## Cloud and delivery platforms

| Platform | AI/runtime coverage | Infrastructure and security coverage |
|---|---|---|
| AWS | Bedrock, Bedrock Agents, AgentCore, Bedrock Guardrails | Terraform, CloudFormation, IAM, regions/AZs, Secrets Manager, KMS/encryption signals, S3, containers, and GitHub Actions deployment signals |
| Azure | Azure OpenAI, Azure AI Agents, Content Safety, Prompt Shields, Semantic Kernel | Terraform, Bicep, managed identities/RBAC, regions, Key Vault, Storage Blob, Azure SQL/Cosmos DB signals, and deployment workflow signals |
| Google Cloud | Google Gen AI, Gemini, Vertex AI, ADK, Model Armor | Terraform, Deployment Manager, service accounts/IAM, regions, Secret Manager, Cloud Storage, BigQuery/Firestore signals, and GKE-related configuration |
| Provider-neutral | OpenAI-compatible endpoints, MCP, containers | Dockerfiles, Nginx, GitHub Actions, HashiCorp Vault, environment configuration, and package manifests |

Cloud support means NuGuard extracts declared configuration and source evidence. It does not inventory resources directly from a live cloud account.

## Kubernetes and containers

| Area | Coverage |
|---|---|
| Workloads | `Deployment`, `StatefulSet`, `DaemonSet`, `Job`, and `CronJob` manifests |
| Helm | `Chart.yaml` metadata and resolvable resources under `templates/` |
| Identity and RBAC | `ServiceAccount`, `Role`, `ClusterRole`, `RoleBinding`, and `ClusterRoleBinding` |
| Network isolation | `NetworkPolicy` coverage by namespace |
| Pod hardening | Root/non-root execution, liveness/readiness probes, resource limits, replicas, affinity, and topology spread |
| Secrets | Kubernetes Secret references plus Vault, AWS Secrets Manager, and Azure Key Vault annotations |
| Containers | Docker/OCI image, tag/digest, registry, base image, health check, root user, and resource-limit evidence |

NuGuard statically analyzes manifests and Helm source. It does not query a live Kubernetes API server.

## Datastores and data services

Dedicated language adapters detect concrete client construction. A broader signal layer recognizes additional datastore names in configuration and IaC.

| Category | Recognized technologies |
|---|---|
| Relational and SQL | PostgreSQL, MySQL, MariaDB, SQLite, SQL Server, Oracle, CockroachDB, TiDB, Amazon Redshift, ClickHouse, and Snowflake |
| ORMs and data frameworks | SQLAlchemy, Django models, Pydantic models, Entity Framework Core, Prisma, TypeORM, Sequelize, Drizzle, Knex, and Go `database/sql` |
| Document and key-value | MongoDB, Redis, Valkey, Upstash Redis, Memcached, DynamoDB, Firestore, Cosmos DB, Cassandra, and Couchbase |
| Vector and retrieval | Pinecone, Chroma, Qdrant, Weaviate, Milvus, FAISS, LanceDB, Elasticsearch, OpenSearch, Neo4j, and Amazon Kendra |
| Managed data platforms | Supabase, Neon, BigQuery, Snowflake, Azure SQL, and Dataverse workflow references |
| Object and file storage | Amazon S3, Google Cloud Storage, Azure Blob Storage, MinIO, and local/file-store signals |

Where schema or model definitions are available, NuGuard also records classified tables and fields, access direction (`read`, `write`, or `readwrite`), encryption signals, and relationships from agents/tools to datastores.

## Package manifests

Dependency discovery covers Python (`pyproject.toml`, `requirements*.txt`, `setup.cfg`), npm (`package.json`), .NET/NuGet (`*.csproj`, `packages.config`, `Directory.Packages.props`), Java/Maven/Gradle (`pom.xml`, `build.gradle`, `build.gradle.kts`, version catalogs), and Go (`go.mod`, `go.sum`). See the [AI-SBOM Schema](sbom-schema.md#supported-dependency-manifests) for the normalized dependency shape.

## Confirm support in a scan

Run a source-only scan and inspect the evidence attached to each AI-SBOM node:

```bash
nuguard sbom generate --source . --output app.sbom.json
nuguard analyze --sbom app.sbom.json --format markdown
```

If a technology is detected only by the generic signal layer, treat it as a lead rather than proof. Use the evidence path and line number to confirm the component, then open a feature request with a minimal example when deeper extraction is needed.
