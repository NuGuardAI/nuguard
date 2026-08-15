---
applyTo: "backend/**,frontend/src/services/**,frontend/src/hooks/**,backend/tests/**"
description: "NuGuard backend microservice endpoint reference — correct URLs, route prefixes, auth headers, and request/response shapes for all 6 services. Use this when writing code that calls or tests any NuGuard API endpoint."
---

# NuGuard Backend Service Contracts

## Service URL Reference

| Service | Direct port | Gateway prefix | Notes |
|---------|-------------|---------------|-------|
| `data_service` | `http://localhost:8000` | `/api/data/` | Strips prefix in middleware |
| `ai_asset_service` | `http://localhost:8004` | `/api/assets/` | Strips prefix in middleware |
| `assessment_service` | `http://localhost:8005` | `/api/compliance/` | Keeps prefix (service mounts at `/api/compliance`) |
| `red_team_service` | `http://localhost:8002` | `/api/redteam/` | Strips prefix in middleware |
| `ai_chatbot_service` | `http://localhost:8006` | `/api/chat/` | Keeps prefix internally |
| `sn_integration_service` | `http://localhost:8007` | `/api/sn/` | Strips prefix in middleware |
| `gateway` | `http://localhost:8080` | — | Reverse proxy for all of the above |

> **Rule for tests/scripts**: Use direct ports for non-gateway services. For `ai_asset_service`, prefer gateway (`http://localhost:8080/api/assets/...`) so the `/api/assets` prefix is preserved correctly.

```python
BASE_URL       = "http://localhost:8000"   # data_service
COMPLIANCE_URL = "http://localhost:8005"   # assessment_service
ASSETS_URL     = "http://localhost:8080"   # via gateway (keeps /api/assets prefix)
```

## Authentication

All endpoints (except `/auth/*` and `/health`) require `Authorization: Bearer <token>`.

```python
headers = {"Authorization": f"Bearer {token}"}
```

Obtain a token via `POST /auth/login` on `data_service`:
```json
{ "email": "admin@nuguard.ai", "password": "admin123" }
```
Response: `{ "access_token": "...", "refresh_token": "...", "token_type": "bearer" }`

Refresh via `POST /auth/refresh` with `{ "refresh_token": "..." }`.

---

## data_service (port 8000) — Core CRUD

### Auth (`/auth`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/signup` | Create account |
| `GET` | `/auth/verify-email` | Email verification |
| `POST` | `/auth/login` | Get access + refresh tokens |
| `POST` | `/auth/refresh` | Rotate tokens |
| `GET` | `/auth/me` | Current user profile |
| `POST` | `/auth/logout` | Invalidate session |
| `GET` | `/auth/oauth/github/authorize` | Start GitHub OAuth |
| `GET` | `/auth/oauth/github/callback` | GitHub OAuth callback |
| `GET` | `/auth/oauth/google/authorize` | Start Google OAuth |
| `GET` | `/auth/oauth/microsoft/authorize` | Start Microsoft OAuth |

### Applications (`/applications`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/applications` | List tenant applications |
| `GET` | `/applications/{app_id}` | Get single application |
| `POST` | `/applications` | Create application |
| `PUT` | `/applications/ensure` | Upsert by name |
| `DELETE` | `/applications/{app_id}` | Delete application |

### Reports (`/reports`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports` | List reports |
| `POST` | `/reports` | Generate report |
| `GET` | `/reports/{report_id}` | Get report |
| `GET` | `/reports/{report_id}/download` | Download PDF |
| `DELETE` | `/reports/{report_id}` | Delete report |

### Risk (`/risk`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/risk/calculate` | Calculate risk from query params |
| `POST` | `/risk/calculate` | Calculate risk from body |
| `GET` | `/risk/scan/{scan_id}` | Full risk for a scan |
| `PUT` | `/risk/scan/{scan_id}/use-case` | Update use-case classification |
| `GET` | `/risk/heatmap` | Tenant-wide risk heatmap |
| `GET` | `/risk/applications` | Risk summary per application |

### Dashboard (`/dashboard`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/dashboard` | Aggregated tenant metrics |
| `GET` | `/policies/{policy_id}/categories` | Category breakdown for a policy |

### Users (`/users`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/users` | List tenant users |
| `POST` | `/users` | Invite user |

### Tenant (`/tenant`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenant` | Current tenant info |
| `PATCH` | `/tenant` | Update tenant settings |

### Platform Admin (`/admin`)  — ADMIN role required
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/admin/tenants` | Create tenant |
| `GET` | `/admin/tenants` | List all tenants |
| `GET` | `/admin/tenants/{tenant_id}` | Get tenant |
| `PATCH` | `/admin/tenants/{tenant_id}` | Update tenant |
| `DELETE` | `/admin/tenants/{tenant_id}` | Delete tenant |
| `GET` | `/admin/tenants/{tenant_id}/users` | List users in tenant |
| `PATCH` | `/admin/tenants/{tenant_id}/users/{user_id}` | Update user role |
| `DELETE` | `/admin/tenants/{tenant_id}/users/{user_id}` | Remove user |

### Evidence / Exports (`/evidence`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/evidence/export/scan` | Create scan evidence export |
| `GET` | `/evidence/exports` | List exports |
| `GET` | `/evidence/export/{export_id}/manifest` | Export manifest |
| `GET` | `/evidence/download/{export_id}` | Download export zip |
| `DELETE` | `/evidence/export/{export_id}` | Delete export |

### Integrations (`/integrations`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/integrations/servicenow` | Get SN integration config |
| `PATCH` | `/integrations/servicenow/enabled` | Toggle SN integration |
| `PATCH` | `/integrations/servicenow/instance-url` | Set SN instance URL |
| `PATCH` | `/integrations/servicenow/oauth-token` | Set SN OAuth token |
| `POST` | `/integrations/servicenow/test` | Test SN connection |
| `POST` | `/integrations/servicenow/export-aibom` | Push AIBOM to ServiceNow |

### Red Team (proxy stub in data_service — `/redteam`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/redteam/tools` | List available tools → `{ "tools": ["PYRIT","GARAK","PROMPTFOO","CUSTOM"] }` |

---

## ai_asset_service (port 8004 / gateway /api/assets) — AIBOM

> Gateway strips `/api/assets` prefix; service mounts at `/`.

### Scans
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/scans/github` | Start GitHub scan — body: `{ "repo_url", "branch"?, "tenant_id", "application_id"?, "policy_ids"? }` |
| `POST` | `/scans` | Start generic AIBOM extraction |
| `POST` | `/scans/upload` | Extract from uploaded files |
| `GET` | `/scans/{scan_id}` | Scan status + summary |
| `GET` | `/scans` | List scans (query: `application_id`, `tenant_id`) |

### AIBOM Graph Reads (all scoped to `scan_id`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/scans/{scan_id}/summary` | App-level summary (endpoints, use-case, stats) |
| `GET` | `/scans/{scan_id}/dependencies` | Package dependency map |
| `GET` | `/scans/{scan_id}/nodes` | Paginated node list (query: `page`, `page_size`, `type`) |
| `GET` | `/scans/{scan_id}/nodes/types` | Node type counts → `[{ "type", "count" }]` |
| `GET` | `/scans/{scan_id}/nodes/{node_id}` | Single node |
| `PATCH` | `/scans/{scan_id}/nodes/{node_id}` | Update node |
| `GET` | `/scans/{scan_id}/edges` | Paginated edges |
| `GET` | `/scans/{scan_id}/edges/node/{node_id}` | Edges for a node |
| `GET` | `/scans/{scan_id}/evidence` | All evidence |
| `GET` | `/scans/{scan_id}/evidence/node/{node_id}` | Evidence for a node |
| `GET` | `/scans/{scan_id}/evidence/edge/{edge_id}` | Evidence for an edge |
| `GET` | `/scans/{scan_id}/graph` | Full AIBOM graph (nodes + edges + evidence) |

### Exports
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/scans/{scan_id}/export/aibom` | Export AIBOM JSON |
| `GET` | `/scans/{scan_id}/export/cyclonedx` | Export CycloneDX SBOM |
| `GET` | `/scans/{scan_id}/export/nuguard` | Full AiSbomDocument (raw NuGuard SBOM schema) |
| `GET` | `/scans/{scan_id}/export/nuguard/cyclonedx` | NuGuard SBOM-native CycloneDX 1.6 |
| `GET` | `/schema/nuguard` | Canonical NuGuard AIBOM JSON schema |

### Applications (`/applications`)
Same CRUD as data_service applications but scoped to ai_asset_service's DB view.

---

## assessment_service (port 8005) — Compliance

> Service mounts at `/api/compliance` — **prefix is kept, do not strip it**.

### Policies (`/api/compliance/policies`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/compliance/policies` | List policies (query: `tenant_id`, `framework`, `category`) |
| `GET` | `/api/compliance/policies/{policy_id}` | Get policy |
| `POST` | `/api/compliance/policies` | Create custom policy |
| `PATCH` | `/api/compliance/policies/{policy_id}` | Update policy |
| `DELETE` | `/api/compliance/policies/{policy_id}` | Delete policy |
| `POST` | `/api/compliance/policies/{policy_id}/enable` | Enable policy for tenant |
| `POST` | `/api/compliance/policies/{policy_id}/disable` | Disable policy |
| `GET` | `/api/compliance/policies/{policy_id}/controls` | List controls for policy |
| `PATCH` | `/api/compliance/policies/{policy_id}/controls/{control_id}` | Update control |
| `DELETE` | `/api/compliance/policies/{policy_id}/controls/{control_id}` | Remove control |

### Controls (`/api/compliance/controls`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/compliance/controls/search` | Search controls |
| `POST` | `/api/compliance/controls/batch` | Batch get controls |
| `POST` | `/api/compliance/controls/seed` | Seed control library |
| `GET` | `/api/compliance/controls` | List controls |
| `GET` | `/api/compliance/controls/library` | Full control library |
| `GET` | `/api/compliance/controls/frameworks` | Available frameworks |
| `GET` | `/api/compliance/controls/categories` | Control categories |
| `GET` | `/api/compliance/controls/{control_id}` | Get control |
| `PATCH` | `/api/compliance/controls/{control_id}` | Update control |
| `DELETE` | `/api/compliance/controls/{control_id}` | Delete control |
| `POST` | `/api/compliance/controls/{control_id}/assess` | Assess single control |

### Assessments (`/api/compliance/assessments`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/compliance/assessments` | Create assessment |
| `GET` | `/api/compliance/assessments` | List assessments |
| `GET` | `/api/compliance/assessments/{assessment_id}` | Get assessment |
| `GET` | `/api/compliance/assessments/{assessment_id}/results` | Assessment results |
| `POST` | `/api/compliance/assessments/{assessment_id}/run` | Run assessment |
| `POST` | `/api/compliance/assessments/{assessment_id}/complete` | Mark complete |
| `DELETE` | `/api/compliance/assessments/{assessment_id}` | Delete assessment |

### Attestations (`/api/compliance/attestations`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/compliance/attestations` | Create attestation record |
| `GET` | `/api/compliance/attestations` | List attestations |
| `GET` | `/api/compliance/attestations/{attestation_id}` | Get attestation |
| `POST` | `/api/compliance/attestations/batch` | Batch create |
| `POST` | `/api/compliance/attestations/{attestation_id}/approve` | Approve |
| `GET` | `/api/compliance/attestations/pending` | List pending attestations |

### Exports (`/api/compliance/exports`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/compliance/exports` | Create export |
| `GET` | `/api/compliance/exports` | List exports |
| `GET` | `/api/compliance/exports/{export_id}` | Get export |
| `GET` | `/api/compliance/exports/{export_id}/download` | Download export |

### Tools (`/api/compliance/tools`) — LLM analysis helpers
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/compliance/tools/policies` | Fetch enriched policies for LLM |
| `POST` | `/api/compliance/tools/analyze` | AI-powered gap analysis |
| `POST` | `/api/compliance/tools/policies/{policy_id}/assess` | Full policy assessment |

---

## red_team_service (port 8002 / gateway /api/redteam)

> Gateway strips `/api/redteam` prefix; service mounts at `/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/run` | Start red-team test run — body: `{ "scan_id", "target_url", "objectives"?, "strategy"? }` |
| `GET` | `/tests` | List test runs |
| `GET` | `/test/{test_run_id}` | Test run status + results |
| `GET` | `/test/{test_run_id}/export` | Export results as JSON |
| `GET` | `/test/{test_run_id}/export/csv` | Export results as CSV |
| `DELETE` | `/test/{test_run_id}` | Delete test run |
| `GET` | `/objectives` | Available attack objective templates |
| `GET` | `/strategies` | Available attack strategy templates |

---

## ai_chatbot_service (port 8006 / gateway /api/chat)

> Gateway keeps `/api/chat` prefix; service normalises it internally.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/chat` | Single-turn chat — body: `{ "message", "conversation_id"?, "tenant_id"?, "context"? }` |
| `POST` | `/chat/stream` | Streaming chat (SSE) |
| `POST` | `/chat/context` | Set conversation context |
| `GET` | `/providers` | Available LLM providers |

---

## sn_integration_service (port 8007 / gateway /api/sn)

> Gateway strips `/api/sn` prefix; service mounts at `/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ci/table/{table_name}` | Query CMDB table |
| `GET` | `/ci/table/{table_name}/{sys_id}` | Get CMDB record |
| `POST` | `/ci/pipeline/{pipeline_name}` | Trigger SN pipeline |
| `POST` | `/ci/ai_system` | Create AI System CI |
| `POST` | `/ci/ai_system/dry_run` | Dry-run AI System CI |
| `POST` | `/ci/ai_model` | Create AI Model CI |
| `POST` | `/ci/ai_prompt` | Create AI Prompt CI |
| `POST` | `/ci/ai_dataset` | Create AI Dataset CI |
| `POST` | `/ci/ai_bom` | Push full AIBOM to ServiceNow |
| `POST` | `/ci/ai_bom/dry_run` | Dry-run AIBOM push |
| `POST` | `/ci/test_sn_connection` | Test ServiceNow connectivity |
| `POST` | `/ci/table/{table_name}` | Create CMDB record |
| `PUT` | `/ci/table/{table_name}/{sys_id}` | Update CMDB record |
| `DELETE` | `/ci/table/{table_name}/{sys_id}` | Delete CMDB record |

---

## Common Mistakes to Avoid

| Wrong | Correct |
|-------|---------|
| `GET /api/data/policies` (data_service) | `GET /api/compliance/policies` (assessment_service) |
| `POST /api/data/scans/github` | `POST /api/assets/scans/github` (via gateway) |
| `POST http://localhost:8004/api/assets/...` | `POST http://localhost:8080/api/assets/...` (gateway) |
| `GET /api/compliance/policies` with `http://localhost:8000` | Use `http://localhost:8005` |
| Calling `/api/compliance` without the full prefix | assessment_service is mounted at `/api/compliance` — always include it |

## Frontend `CONFIG` Object Mapping

```typescript
import { CONFIG } from '../config';
// CONFIG.DATA_SERVICE_URL      = '/api/data'
// CONFIG.ASSETS_SERVICE_URL    = '/api/assets'
// CONFIG.COMPLIANCE_SERVICE_URL= '/api/compliance'
// CONFIG.CHAT_SERVICE_URL      = '/api/chat'
// CONFIG.REDTEAM_SERVICE_URL   = '/api/redteam'
```

All relative URLs are proxied by Vite in dev mode. In production they pass through the nginx gateway.
