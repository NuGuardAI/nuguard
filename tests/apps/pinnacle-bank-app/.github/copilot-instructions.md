# NuGuard AI — Copilot Instructions

> **Read [`/CLAUDE.md`](../CLAUDE.md) first.** It is the canonical source for project overview, architecture, commands, env vars, security/multi-tenancy rules, and AIBOM conventions. This file only adds Copilot-specific details not in CLAUDE.md.

## Gateway prefixes (per service)

| Service | Port | Gateway prefix |
|---|---|---|
| `data_service` | 8000 | `/api/data/` |
| `red_team_service` | 8002 | `/api/redteam/` |
| `ai_asset_service` | 8004 | `/api/assets/` |
| `assessment_service` | 8005 | `/api/compliance/` |
| `ai_chatbot_service` | 8006 | `/api/chat/` |
| `sn_integration_service` | 8007 | `/api/sn/` |

## Vite dev proxy (no gateway needed locally)

```
/api/data       → :8000   (no rewrite)
/api/assets     → :8004   (strips /api/assets)
/api/compliance → :8005   (no rewrite)
/api/redteam    → :8002   (strips /api/redteam)
/api/chat       → :8006   (no rewrite)
/api/sn         → :8007   (strips /api/sn)
```

## Frontend patterns

- One component per file, named export, functional + hooks, async/await with `try/catch`.
- Tailwind, dark mode default (`dark:bg-gray-900`), `clsx` for conditional classes.
- Services exported as singletons: `export const db = { getPolicies: async (tenantId) => {...} }`.
- Components receive callbacks, never services: `<Scanner onScanStart={...} policies={...} />`.
- Components load their own data in `useEffect` keyed on user/tenant.
- Audit logs via the `logger` singleton, not `console.*`.
- Permission gates: `authService.hasPermission(user, requiredRole)` before mutations.

### Key enums (in `src/schemas.ts`)
`Severity`, `Role`, `AssetType`, `GapType`, `ScanPhase`, `ScanStatus`.
`AssetType`: `AGENT | MODEL | TOOL | PROMPT | DATASTORE | AUTH | PRIVILEGE | GUARDRAIL | FRAMEWORK | EVAL_SYSTEM | MCP_PROVIDER`.

### Hooks (`frontend/src/hooks/`)

| Hook | Purpose |
|---|---|
| `useAIBOM` | AIBOM graph (nodes, edges, evidence) |
| `useAssessment` | Compliance assessment lifecycle |
| `useAuth` | Auth state |
| `useDashboard` | Aggregated metrics, 5-min client cache |
| `useGenAI` | Gemini integration |
| `useGovernance` | Risk + policy mgmt |
| `useInventory` | Apps + assets |
| `useNotifications` | In-app notifications |
| `useRedTeaming` | Red-team attacks |
| `useReports` | Report generation/export |
| `useScans` | Scan ops |
| `useTheme` | Dark/light |

## Backend patterns

- Python 3.12 + FastAPI; `async def` endpoints; Pydantic for I/O validation.
- Errors → `HTTPException`; no bare `except`.
- Routes / services / models stay in separate modules.
- Every tenant-scoped query filters by `tenant_id` from JWT (see CLAUDE.md).

## Key data flows

1. **GitHub scan**: `Scanner.tsx` → `api.scans.createGithub()` → `POST /api/assets/scans/github` → AIBOM extract + Gemini → `assessment_service` compliance → PG.
2. **Policies**: `GET /api/compliance/policies` hits `assessment_service`, **not** `data_service`.
3. **AIBOM hydration**: nodes carry `nuguard_node` JSONB (raw SBOM) + `properties` JSONB (flattened for assessment).
4. **System policies**: `tenant_id = NULL`; user policies always scoped.

## Mock modes

- `VITE_USE_MOCK_MODE=true` — localStorage only, no backend (quick demos).
- `MOCK_E2E=true` — Playwright intercepts HTTP; real React code runs. Different from above.
