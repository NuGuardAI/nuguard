# NuGuard AI — Agent Context

AI Security SaaS: scans AI apps (GitHub URL) → AIBOM, vuln/policy/compliance analysis, behavior validation, red-teaming.

## Architecture

**Backend** (`backend/`, FastAPI microservices, shared code in `backend/shared/`):

| Service | Port | Role |
|---|---|---|
| `data_service` | 8000 | Auth, users, scans, reports, risk engine |
| `red_team_service` | 8002 | PyRIT / Garak / PromptFoo |
| `ai_asset_service` | 8004 | AIBOM extraction, GitHub scans |
| `assessment_service` | 8005 | Compliance, controls, policies |
| `ai_chatbot_service` | 8006 | OWASP AI Top 10 chatbot |
| `sn_integration_service` | 8007 | ServiceNow CMDB |
| `gateway` (nginx) | 8080 | Reverse proxy, TLS |
| `postgres` | 5432 | PG 16, Alembic |

**Frontend** (`frontend/`, Vite + React + TS + Tailwind, dev `:3000`):
- `src/schemas.ts` — Zod schemas (source of truth)
- `src/types.ts` — **always import types from here**, never `schemas.ts`
- `src/adapters/` — snake_case ↔ camelCase
- `src/services/api.ts` — JWT HTTP client
- `src/config.ts` — `CONFIG` with all service URLs

## Commands

```bash
# Frontend
npm run dev              # :3000
npm run build
npm run test:frontend    # lint + vitest

# Backend
docker compose up -d
docker compose run --rm migrate
cd backend && python -m pytest                 # excludes slow by default
python -m pytest -m slow --override-ini="addopts=-v --tb=short"

# E2E (no backend)
cd frontend && MOCK_E2E=true npx playwright test

# Migrations (from backend/)
alembic revision --autogenerate -m "desc"      # filename: NNN_snake_case.py
alembic current && alembic heads
```

## Critical Conventions

**Security & multi-tenancy**
- Every tenant-scoped query MUST filter by `tenant_id`. `tenant_id` comes from JWT claims — **never** the request body.
- No secrets in code, logs, or commits. Read from env / secret manager only.
- Validate all external input with Pydantic (backend) / Zod (frontend) at the boundary.
- Never disable auth, RBAC, or rate-limit middleware to "make tests pass" — fix the test.
- Parameterized queries only (SQLAlchemy ORM / `text()` with bindparams). No string SQL.

**Service URLs in tests/scripts**
```python
BASE_URL       = "http://localhost:8000"   # data_service
COMPLIANCE_URL = "http://localhost:8005"   # assessment_service
ASSETS_URL     = "http://localhost:8080"   # ai_asset_service via gateway
# e.g. POST {ASSETS_URL}/api/assets/scans/github  — NOT /api/data/...
```

**AIBOM**
- `canonical_id`: `type:namespace:name` (e.g. `model:openai:gpt-4`).
- `provider` lives in `node.metadata.extras.get("provider")` — NOT `node.metadata.provider`.

**IDs & timestamps**
- Frontend: `crypto.randomUUID()`, `Date.now()`
- Backend: `uuid.uuid4()`, `datetime.utcnow()` / `func.now()`

## Env (`.env.example` → `.env`)

```
GEMINI_API_KEY=...
GEMINI_MODEL_NAME=gemini-3.1-flash-lite
DATABASE_URL=postgresql+asyncpg://nuguard:nuguard@localhost:5432/nuguard
USE_LOCAL_POSTGRES=true
VITE_USE_MOCK_MODE=false
NUGUARD_TWO_PHASE_ANALYSIS=true
```

## Demo users (seeded)

`admin@nuguard.ai / admin123` · `editor@nuguard.ai / editor123` · `viewer@nuguard.ai / viewer123`

## Workflow

1. Before non-trivial changes, skim `.github/copilot-instructions.md`.
2. AIBOM work → `.github/skills/aibom-extraction/SKILL.md`.
3. API/tests → `.github/instructions/backend-service-contracts.instructions.md`.
4. Migrations/DB → `.github/instructions/postgresql-db-guidelines.instructions.md`.
5. Before handoff: run type checks + relevant tests. UI changes: verify in browser.
6. Significant features: keep a plan in `docs/New Direction/`.
