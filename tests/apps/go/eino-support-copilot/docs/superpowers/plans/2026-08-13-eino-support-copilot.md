# Eino Support Copilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个可公开运行的 AI 客服与工单协作平台，用 Go、Eino、MCP、混合 RAG、人工审核、运行轨迹和离线评测展示 AI 全栈开发能力。

**Architecture:** 单仓库包含 Next.js Web、Go API、Go MCP Server、Eval CLI 和 PostgreSQL/pgvector。应用层通过 `agent.Engine` 隔离 Mock 与 Eino 实现；Eino `ChatModelAgent` 由 `adk.Runner` 驱动，业务工具通过独立的只读 MCP Server 提供，代码门禁负责证据、风险和人工审核。

**Tech Stack:** Go 1.25.7、Eino v0.9.13、eino-ext MCP v0.0.9、mcp-go v0.43.0、pgx v5.10.0、pgvector-go v0.4.1、Next.js 16.3.0、React 19.2.8、TypeScript、Tailwind CSS 4.3.3、TanStack Query 5.101.4、PostgreSQL 17 + pgvector、Vitest 4.1.10、Playwright 1.62.1、Docker Compose。

## Global Constraints

- 仓库名固定为 `eino-support-copilot`，Go module 固定为 `github.com/guiyangyuan/eino-support-copilot`。
- 只使用生成的 SaaS 数据和自编知识文档；不得使用公司代码、截图、接口、配置、数据或内部流程。
- Mock 模式是默认模式，不能要求模型 API Key；真实模型只通过环境变量配置 OpenAI-compatible API。
- 所有 MCP 业务工具只读，单次调用超时 2 秒，临时错误最多重试一次。
- 不记录或展示模型隐藏思维过程；只持久化节点状态、输入摘要、工具参数、结果摘要、引用、耗时和错误。
- Embedding 维度固定为 1536；维度不匹配时返回配置错误，不写入数据库。
- 上传文件只支持 `.md`、`.txt` 和文本型 `.pdf`，最大 10 MB；不执行宏、脚本或附件。
- P0/P1、低置信度、证据不足或工具部分失败必须进入人工审核。
- README 只展示由评测命令实际产生的数据；没有结果时明确标记“尚未测量”。
- 每个任务先写失败测试，再实现最小行为，最后运行相关测试并提交。

---

## File Map

### 根目录与运行

- `.gitignore`：忽略密钥、构建产物、本地数据和设计临时目录。
- `.env.example`：公开可提交的配置变量名和安全默认值。
- `Makefile`：统一开发、测试、迁移、seed、评测和 E2E 命令。
- `docker-compose.yml`：编排 Web、API、MCP Server 与 PostgreSQL。
- `go.mod`、`go.sum`：锁定 Go 依赖。

### Go 后端

- `cmd/api/main.go`：API 进程组合根。
- `cmd/mcp-server/main.go`：MCP 进程组合根。
- `cmd/eval/main.go`：离线评测 CLI。
- `internal/config/config.go`：环境变量解析和验证。
- `internal/platform/database/`：pgx 连接、迁移和测试容器。
- `internal/domain/`：Conversation、Ticket、Knowledge、Agent Run 与 Evaluation 类型。
- `internal/conversation/`：会话与消息用例、仓储。
- `internal/ticket/`：工单、审核与转人工用例、仓储。
- `internal/knowledge/`：文档上传、解析、索引状态和仓储。
- `internal/retrieval/`：切分、embedding、全文/向量召回与 RRF。
- `internal/mcpserver/`：三个只读业务工具。
- `internal/agent/`：Engine 接口、Mock Engine、Eino Engine、门禁和提示词。
- `internal/runtrace/`：事件、运行轨迹和重放关联。
- `internal/evaluation/`：数据集运行、评分和结果聚合。
- `internal/transport/httpapi/`：HTTP 路由、JSON 错误和流式事件。

### Next.js 前端

- `apps/web/app/`：客户、客服、知识库、Runs 与 Evaluation 页面。
- `apps/web/components/`：页面级组件与共享 UI。
- `apps/web/lib/api/`：类型化 API Client 和流解析器。
- `apps/web/lib/state/`：会话流式 reducer。
- `apps/web/tests/`：组件和 reducer 测试。
- `apps/web/e2e/`：Playwright 关键路径。

### 数据与文档

- `migrations/`：数据库迁移。
- `seed/`：模拟账户、支付、服务状态、知识文档和 Golden Dataset。
- `docs/architecture.md`：系统架构与设计权衡。
- `docs/demo-script.md`：三分钟面试演示脚本。
- `README.md`：项目首屏、运行、截图、评测和安全边界。

---

### Task 1: 可运行的仓库骨架与健康检查

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `go.mod`
- Create: `internal/config/config.go`
- Create: `internal/config/config_test.go`
- Create: `internal/transport/httpapi/errors.go`
- Create: `internal/transport/httpapi/middleware.go`
- Create: `internal/transport/httpapi/server.go`
- Create: `internal/transport/httpapi/server_test.go`
- Create: `cmd/api/main.go`
- Create: `apps/web/package.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/vitest.config.ts`
- Create: `apps/web/tests/setup.ts`
- Create: `apps/web/app/layout.tsx`
- Create: `apps/web/app/page.tsx`
- Create: `apps/web/app/globals.css`
- Create: `Makefile`

**Interfaces:**
- Produces: `config.Load() (config.Config, error)`。
- Produces: `httpapi.NewServer(httpapi.Dependencies) http.Handler`。
- Produces: `GET /healthz -> {"status":"ok","mode":"mock"}`。

- [x] **Step 1: 初始化锁定版本的 Go module 和 Web package**

```bash
go mod init github.com/guiyangyuan/eino-support-copilot
go mod edit -go=1.25.7
go get github.com/cloudwego/eino@v0.9.13
go get github.com/cloudwego/eino-ext/components/tool/mcp@v0.0.9
go get github.com/jackc/pgx/v5@v5.10.0
go get github.com/pgvector/pgvector-go@v0.4.1
go get github.com/stretchr/testify@v1.11.1
mkdir -p apps/web
cd apps/web
npm init -y
npm install --save-exact next@16.3.0 react@19.2.8 react-dom@19.2.8 @tanstack/react-query@5.101.4 zod@4.4.3
npm install --save-dev --save-exact typescript@7.0.2 @types/node@26.2.0 @types/react@19.2.18 @types/react-dom@19.2.4 tailwindcss@4.3.3 vitest@4.1.10 jsdom@30.0.1 @testing-library/react@16.3.2 @testing-library/jest-dom@7.0.1 @testing-library/user-event@14.6.4 @playwright/test@1.62.1
npm pkg set type=module scripts.dev="next dev" scripts.build="next build" scripts.start="next start" scripts.test="vitest run" scripts.test:watch="vitest"
```

`.gitignore` 内容固定包含：

```gitignore
.env
.env.local
node_modules/
.next/
coverage/
playwright-report/
test-results/
.data/
.volumes/
.superpowers/
work/
outputs/
```

- [x] **Step 2: 写配置默认值失败测试**

```go
func TestLoadUsesSafeDefaults(t *testing.T) {
    t.Setenv("APP_MODE", "")
    t.Setenv("HTTP_ADDR", "")
    t.Setenv("MODEL_API_KEY", "")
    cfg, err := Load()
    require.NoError(t, err)
    assert.Equal(t, "mock", cfg.AppMode)
    assert.Equal(t, ":8080", cfg.HTTPAddr)
    assert.Empty(t, cfg.ModelAPIKey)
}
```

- [x] **Step 3: 运行测试并确认因 `Load` 不存在而失败**

Run: `go test ./internal/config -run TestLoadUsesSafeDefaults -v`
Expected: FAIL，提示 `undefined: Load`。

- [x] **Step 4: 实现最小配置与健康检查**

```go
type Config struct {
    AppMode       string
    HTTPAddr      string
    DatabaseURL   string
    MCPURL        string
    ModelBaseURL  string
    ModelAPIKey   string
    ModelName     string
    EmbeddingName string
}

func Load() (Config, error) {
    cfg := Config{
        AppMode:       envOr("APP_MODE", "mock"),
        HTTPAddr:      envOr("HTTP_ADDR", ":8080"),
        DatabaseURL:   envOr("DATABASE_URL", "postgres://copilot:copilot@localhost:5432/copilot?sslmode=disable"),
        MCPURL:        envOr("MCP_URL", "http://localhost:8090/mcp"),
        ModelBaseURL:  os.Getenv("MODEL_BASE_URL"),
        ModelAPIKey:   os.Getenv("MODEL_API_KEY"),
        ModelName:     envOr("MODEL_NAME", "gpt-4.1-mini"),
        EmbeddingName: envOr("EMBEDDING_MODEL", "text-embedding-3-small"),
    }
    if cfg.AppMode != "mock" && cfg.AppMode != "openai-compatible" {
        return Config{}, fmt.Errorf("APP_MODE must be mock or openai-compatible")
    }
    return cfg, nil
}
```

`NewServer` 使用 Go 1.22+ `http.ServeMux` 注册 `GET /healthz`。`middleware.go` 为每个请求生成或透传 `X-Request-ID`；`errors.go` 统一输出 `{"code","message","retryable","request_id"}` 且不返回堆栈。`main.go` 只负责加载配置、创建依赖、启动 `http.Server` 和处理优雅关闭。

- [x] **Step 5: 创建最小 Next.js 首页并验证构建**

首页必须显示 `Eino Support Copilot`、一句产品价值、`Customer Portal` 与 `Agent Workspace` 两个入口。`next.config.ts` 将浏览器同源的 `/api/:path*` rewrite 到 `API_ORIGIN`（本地默认 `http://localhost:8080`），前端 API Client 禁止硬编码跨域地址。`vitest.config.ts` 固定 `environment: 'jsdom'` 并加载 `tests/setup.ts` 中的 `@testing-library/jest-dom/vitest`。运行：

```bash
cd apps/web
npm run build
```

Expected: Next.js production build succeeds。

- [x] **Step 6: 运行后端测试与静态检查**

Run: `go test ./internal/config ./internal/transport/httpapi -v`
Expected: PASS，健康检查返回 200 与 mock mode。

- [x] **Step 7: 提交骨架**

```bash
git add .gitignore .env.example go.mod go.sum internal cmd apps/web Makefile
git commit -m "chore: bootstrap support copilot"
```

---

### Task 2: PostgreSQL、pgvector 与完整数据迁移

**Files:**
- Create: `migrations/00001_init.sql`
- Create: `internal/platform/database/database.go`
- Create: `internal/platform/database/migrate.go`
- Create: `internal/platform/database/database_test.go`
- Modify: `go.mod`

**Interfaces:**
- Produces: `database.Open(ctx context.Context, url string) (*pgxpool.Pool, error)`。
- Produces: `database.Migrate(ctx context.Context, pool *pgxpool.Pool, dir string) error`。
- Produces tables listed in the design spec with UUID primary keys and UTC timestamps。

- [x] **Step 1: 添加迁移与测试容器依赖**

```bash
go get github.com/pressly/goose/v3@v3.27.3
go get github.com/testcontainers/testcontainers-go@v0.44.0
go get github.com/testcontainers/testcontainers-go/modules/postgres@v0.44.0
```

- [x] **Step 2: 写迁移失败测试**

```go
func TestMigrateCreatesCoreSchema(t *testing.T) {
    ctx := context.Background()
    pool := startPostgres(t, ctx)
    require.NoError(t, Migrate(ctx, pool, "../../../migrations"))

    var vectorInstalled bool
    require.NoError(t, pool.QueryRow(ctx,
        `select exists(select 1 from pg_extension where extname='vector')`,
    ).Scan(&vectorInstalled))
    assert.True(t, vectorInstalled)

    for _, table := range []string{"conversations", "messages", "tickets", "knowledge_chunks", "agent_runs", "eval_results"} {
        var name string
        require.NoError(t, pool.QueryRow(ctx, `select to_regclass($1)`, "public."+table).Scan(&name))
        assert.Equal(t, table, name)
    }
}
```

`startPostgres` 必须启动 `pgvector/pgvector:pg17`，等待 `5432/tcp` 可用并通过 `t.Cleanup` 终止容器；不得用不含 vector 扩展的普通 postgres image。

- [x] **Step 3: 运行测试并确认迁移文件缺失**

Run: `go test ./internal/platform/database -run TestMigrateCreatesCoreSchema -v`
Expected: FAIL，提示迁移目录或表不存在。

- [x] **Step 4: 创建 `00001_init.sql`**

迁移必须包含：

```sql
-- +goose Up
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE demo_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  role text NOT NULL CHECK (role IN ('customer','agent','admin')),
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id uuid NOT NULL REFERENCES demo_users(id),
  title text NOT NULL DEFAULT 'New conversation',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id),
  role text NOT NULL CHECK (role IN ('user','assistant','agent')),
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tickets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id),
  status text NOT NULL CHECK (status IN ('open','triaged','waiting_review','resolved','escalated')),
  priority text NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
  intent text NOT NULL,
  summary text NOT NULL,
  draft jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ticket_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id uuid NOT NULL REFERENCES tickets(id),
  reviewer_id uuid NOT NULL REFERENCES demo_users(id),
  action text NOT NULL CHECK (action IN ('approve','edit_and_approve','escalate')),
  final_answer text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  filename text NOT NULL,
  media_type text NOT NULL,
  sha256 text NOT NULL UNIQUE,
  status text NOT NULL CHECK (status IN ('uploaded','indexing','ready','failed')),
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES knowledge_documents(id),
  ordinal integer NOT NULL,
  content text NOT NULL,
  locator text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  embedding vector(1536) NOT NULL,
  UNIQUE(document_id, ordinal)
);
CREATE INDEX knowledge_chunks_fts_idx ON knowledge_chunks USING gin(search_vector);
CREATE INDEX knowledge_chunks_vector_idx ON knowledge_chunks USING hnsw(embedding vector_cosine_ops);

CREATE TABLE agent_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id),
  message_id uuid REFERENCES messages(id),
  ticket_id uuid REFERENCES tickets(id),
  status text NOT NULL CHECK (status IN ('queued','running','waiting_review','completed','failed','cancelled')),
  mode text NOT NULL CHECK (mode IN ('mock','openai-compatible')),
  model_name text,
  input_summary text NOT NULL,
  output_summary text,
  usage jsonb NOT NULL DEFAULT '{}',
  error_code text,
  error_message text,
  replayed_from_run_id uuid REFERENCES agent_runs(id),
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_runs_conversation_idx ON agent_runs(conversation_id, created_at DESC);
CREATE INDEX agent_runs_status_idx ON agent_runs(status, created_at DESC);

CREATE TABLE agent_run_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  sequence integer NOT NULL CHECK (sequence >= 0),
  event_type text NOT NULL,
  stage text,
  input_summary jsonb NOT NULL DEFAULT '{}',
  tool_name text,
  tool_arguments jsonb NOT NULL DEFAULT '{}',
  result_summary jsonb NOT NULL DEFAULT '{}',
  citations jsonb NOT NULL DEFAULT '[]',
  duration_ms bigint CHECK (duration_ms IS NULL OR duration_ms >= 0),
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id, sequence)
);

CREATE TABLE eval_datasets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  version text NOT NULL,
  dataset_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(name, version),
  UNIQUE(dataset_hash)
);

CREATE TABLE eval_cases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES eval_datasets(id) ON DELETE CASCADE,
  case_key text NOT NULL,
  input text NOT NULL,
  expected_intent text NOT NULL,
  expected_priority text NOT NULL CHECK (expected_priority IN ('P0','P1','P2','P3')),
  expected_tools jsonb NOT NULL DEFAULT '[]',
  expected_citations jsonb NOT NULL DEFAULT '[]',
  expected_requires_review boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(dataset_id, case_key)
);

CREATE TABLE eval_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES eval_datasets(id),
  status text NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
  mode text NOT NULL CHECK (mode IN ('mock','openai-compatible')),
  commit_sha text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}',
  metrics jsonb NOT NULL DEFAULT '{}',
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  eval_run_id uuid NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  eval_case_id uuid NOT NULL REFERENCES eval_cases(id),
  passed boolean NOT NULL,
  scores jsonb NOT NULL DEFAULT '{}',
  actual jsonb NOT NULL DEFAULT '{}',
  latency_ms bigint NOT NULL CHECK (latency_ms >= 0),
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(eval_run_id, eval_case_id)
);

CREATE TABLE mock_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id text NOT NULL UNIQUE,
  plan text NOT NULL,
  account_status text NOT NULL CHECK (account_status IN ('active','past_due','suspended','closed')),
  subscription_sync_status text NOT NULL CHECK (subscription_sync_status IN ('pending','processing','active','failed')),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mock_payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id text NOT NULL UNIQUE,
  account_id text NOT NULL REFERENCES mock_accounts(account_id),
  status text NOT NULL CHECK (status IN ('pending','paid','failed','refunded')),
  amount_cents bigint NOT NULL CHECK (amount_cents >= 0),
  currency text NOT NULL CHECK (char_length(currency) = 3),
  paid_at timestamptz,
  linked_subscription text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mock_payments_account_idx ON mock_payments(account_id, created_at DESC);

CREATE TABLE mock_service_incidents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  service text NOT NULL,
  status text NOT NULL CHECK (status IN ('operational','degraded','outage')),
  impact_scope text NOT NULL,
  summary text NOT NULL,
  started_at timestamptz NOT NULL,
  resolved_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mock_service_incidents_service_idx ON mock_service_incidents(service, updated_at DESC);

-- +goose Down
DROP TABLE IF EXISTS mock_service_incidents;
DROP TABLE IF EXISTS mock_payments;
DROP TABLE IF EXISTS mock_accounts;
DROP TABLE IF EXISTS eval_results;
DROP TABLE IF EXISTS eval_runs;
DROP TABLE IF EXISTS eval_cases;
DROP TABLE IF EXISTS eval_datasets;
DROP TABLE IF EXISTS agent_run_steps;
DROP TABLE IF EXISTS agent_runs;
DROP TABLE IF EXISTS knowledge_chunks;
DROP TABLE IF EXISTS knowledge_documents;
DROP TABLE IF EXISTS ticket_reviews;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS demo_users;
```

- [x] **Step 5: 实现连接与 goose 迁移适配**

`Open` 使用 `pgxpool.ParseConfig`，设置 `MaxConns=10`、`MinConns=1`、`MaxConnLifetime=30m`，并执行 `Ping`。`Migrate` 通过 `stdlib.OpenDBFromPool(pool)` 调用 `goose.UpContext`。

- [x] **Step 6: 运行数据库测试**

Run: `go test ./internal/platform/database -v`
Expected: PASS，迁移可重复执行且 vector 扩展存在。

- [x] **Step 7: 提交数据库基础**

```bash
git add go.mod go.sum migrations internal/platform/database
git commit -m "feat: add postgres schema and migrations"
```

---

### Task 3: 领域类型与会话、工单、运行仓储

**Files:**
- Create: `internal/domain/types.go`
- Create: `internal/conversation/repository.go`
- Create: `internal/conversation/repository_test.go`
- Create: `internal/ticket/repository.go`
- Create: `internal/ticket/repository_test.go`
- Create: `internal/runtrace/repository.go`
- Create: `internal/runtrace/repository_test.go`

**Interfaces:**
- Produces: `domain.Draft`, `domain.Citation`, `domain.Fact`, `domain.AgentEvent`。
- Produces: `conversation.Repository` with `Create`, `AppendMessage`, `Get`, `List`。
- Produces: `ticket.Repository` with `Create`, `List`, `Get`, `Review`。
- Produces: `runtrace.Repository` with `Start`, `AppendStep`, `Complete`, `Get`。

- [x] **Step 1: 写领域类型**

```go
type Priority string

const (
    PriorityP0 Priority = "P0"
    PriorityP1 Priority = "P1"
    PriorityP2 Priority = "P2"
    PriorityP3 Priority = "P3"
)

type Citation struct {
    ChunkID uuid.UUID `json:"chunk_id"`
    Title   string    `json:"title"`
    Locator string    `json:"locator"`
    Content string    `json:"content"`
}

type Fact struct {
    Source string `json:"source"`
    Field  string `json:"field"`
    Value  any    `json:"value"`
}

type EventType string

const (
    EventRunStarted         EventType = "run.started"
    EventStageStarted       EventType = "stage.started"
    EventStageCompleted     EventType = "stage.completed"
    EventRetrievalCompleted EventType = "retrieval.completed"
    EventToolStarted        EventType = "tool.started"
    EventToolCompleted      EventType = "tool.completed"
    EventToolFailed         EventType = "tool.failed"
    EventDraftDelta         EventType = "draft.delta"
    EventCitation           EventType = "citation"
    EventReviewRequired     EventType = "review.required"
    EventRunCompleted       EventType = "run.completed"
    EventRunFailed          EventType = "run.failed"
)

type Draft struct {
    Intent           string     `json:"intent"`
    Priority         Priority   `json:"priority"`
    Answer           string     `json:"answer"`
    Citations        []Citation `json:"citations"`
    Facts            []Fact     `json:"facts"`
    Confidence       float64    `json:"confidence"`
    RequiresReview   bool       `json:"requires_review"`
    ReviewReasons    []string   `json:"review_reasons"`
    FollowUpQuestion *string    `json:"follow_up_question"`
}

type AgentEvent struct {
    EventID        uuid.UUID       `json:"event_id"`
    ConversationID uuid.UUID       `json:"conversation_id"`
    MessageID      uuid.UUID       `json:"message_id"`
    RunID          uuid.UUID       `json:"run_id"`
    Type           EventType       `json:"type"`
    Timestamp      time.Time       `json:"timestamp"`
    Payload        json.RawMessage `json:"payload"`
}
```

- [x] **Step 2: 写仓储失败测试**

```go
func TestConversationRepositoryRoundTrip(t *testing.T) {
    repo, ctx, customerID := newConversationRepo(t)
    c, err := repo.Create(ctx, customerID, "Subscription issue")
    require.NoError(t, err)
    msg, err := repo.AppendMessage(ctx, c.ID, domain.RoleUser, "Payment succeeded")
    require.NoError(t, err)
    got, err := repo.Get(ctx, c.ID)
    require.NoError(t, err)
    assert.Equal(t, msg.ID, got.Messages[0].ID)
}
```

为 Ticket 增加测试：`Review` 在 `waiting_review` 状态下写入审计记录并将 approve 变为 resolved；在 resolved 状态重复审核返回 `ErrInvalidTransition`。为 Run 增加测试：step 只追加，Complete 不删除历史 step。

- [x] **Step 3: 运行测试并确认仓储未实现**

Run: `go test ./internal/conversation ./internal/ticket ./internal/runtrace -v`
Expected: FAIL，提示 repository constructors 不存在。

- [x] **Step 4: 使用 pgx 实现仓储与事务**

每个仓储接收最小 `DBTX` 接口（由 `*pgxpool.Pool` 和 `pgx.Tx` 同时满足），方便 Task 12 将多个写入组合到一个事务。`ticket.Review` 使用 `SELECT ... FOR UPDATE`、状态校验、插入 review、更新 ticket；数据库错误通过 `%w` 保留错误链。`runtrace.AppendStep` 将 `AgentEvent.EventID` 写入 `agent_run_steps.id`，并在事务中用 `max(sequence)+1`/行锁生成连续 sequence。

- [x] **Step 5: 运行仓储测试**

Run: `go test ./internal/conversation ./internal/ticket ./internal/runtrace -v`
Expected: PASS。

- [x] **Step 6: 提交领域与仓储**

```bash
git add internal/domain internal/conversation internal/ticket internal/runtrace
git commit -m "feat: add core domain repositories"
```

---

### Task 4: 生成数据与只读 MCP Server

**Files:**
- Create: `seed/business.sql`
- Create: `internal/mcpserver/tools.go`
- Create: `internal/mcpserver/tools_test.go`
- Create: `internal/mcpserver/server.go`
- Create: `cmd/mcp-server/main.go`
- Modify: `go.mod`

**Interfaces:**
- Produces MCP tools `get_account_status`, `get_payment_status`, `get_service_status`。
- Consumes `mock_accounts`、`mock_payments`、`mock_service_incidents` tables。

- [x] **Step 1: 锁定 MCP Server 依赖并创建 seed**

```bash
go get github.com/mark3labs/mcp-go@v0.43.0
```

`seed/business.sql` 固定创建 `acct_demo_pro`、`pay_demo_paid` 和 `subscription-sync` incident，使核心演示输入可重复。

- [x] **Step 2: 写工具失败测试**

```go
func TestGetPaymentStatusReturnsSafeFields(t *testing.T) {
    svc := newSeededService(t)
    result, err := svc.GetPaymentStatus(context.Background(), PaymentInput{AccountID: "acct_demo_pro"})
    require.NoError(t, err)
    assert.Equal(t, "paid", result.Status)
    assert.Equal(t, "pro", result.LinkedSubscription)
    raw, _ := json.Marshal(result)
    assert.NotContains(t, string(raw), "card_number")
}
```

- [x] **Step 3: 运行测试并确认工具服务缺失**

Run: `go test ./internal/mcpserver -run TestGetPaymentStatusReturnsSafeFields -v`
Expected: FAIL。

- [x] **Step 4: 实现三个查询服务与稳定错误码**

```go
type ToolError struct {
    Code      string `json:"code"`
    Message   string `json:"message"`
    Retryable bool   `json:"retryable"`
}
```

空 ID 返回 `INVALID_ARGUMENT`，未命中返回 `NOT_FOUND`，context deadline 返回 `TEMPORARY_UNAVAILABLE` 且 `Retryable=true`。

- [x] **Step 5: 注册 Streamable HTTP MCP Server**

```go
mcpServer := server.NewMCPServer("support-copilot-business", "1.0.0")
mcpServer.AddTool(accountTool, accountHandler)
mcpServer.AddTool(paymentTool, paymentHandler)
mcpServer.AddTool(serviceTool, serviceHandler)
httpServer := server.NewStreamableHTTPServer(mcpServer, server.WithStateLess(true))
return httpServer.Start(":8090")
```

- [x] **Step 6: 添加 MCP 协议集成测试**

使用 `httptest.NewServer(server.NewStreamableHTTPServer(...))` 初始化 MCP Client，断言 `ListTools` 恰好包含三个工具，并实际调用 payment tool。

- [x] **Step 7: 运行测试并提交**

Run: `go test ./internal/mcpserver -v`
Expected: PASS。

```bash
git add go.mod go.sum seed/business.sql internal/mcpserver cmd/mcp-server
git commit -m "feat: add read-only business mcp server"
```

---

### Task 5: 文档解析、结构化切分与确定性 Mock Embedding

**Files:**
- Create: `internal/retrieval/document.go`
- Create: `internal/retrieval/chunker.go`
- Create: `internal/retrieval/chunker_test.go`
- Create: `internal/retrieval/embedder.go`
- Create: `internal/retrieval/embedder_test.go`
- Create: `internal/retrieval/openai_embedder.go`
- Create: `internal/retrieval/openai_embedder_test.go`
- Create: `seed/knowledge/subscription-activation.md`
- Create: `seed/knowledge/payment-troubleshooting.md`
- Create: `seed/knowledge/service-status.md`

**Interfaces:**
- Produces: `Chunker.Split(Document) ([]Chunk, error)`。
- Produces: `Embedder.Embed(ctx context.Context, texts []string) ([][]float32, error)`。
- Produces: stable 1536-dimensional Mock vectors。
- Produces: OpenAI-compatible `/embeddings` client with strict dimension validation。

- [x] **Step 1: 锁定文本型 PDF 解析依赖**

```bash
go get github.com/ledongthuc/pdf@v0.0.0-20250511090121-5959a4027728
```

- [x] **Step 2: 写切分失败测试**

```go
func TestChunkerPreservesHeadingLocator(t *testing.T) {
    doc := Document{Filename: "guide.md", Text: "# Billing\n\n## Activation\n\nPayment can take ten minutes."}
    chunks, err := NewChunker(500, 0.15).Split(doc)
    require.NoError(t, err)
    require.Len(t, chunks, 1)
    assert.Equal(t, "Billing / Activation", chunks[0].Locator)
    assert.Contains(t, chunks[0].Content, "ten minutes")
}
```

- [x] **Step 3: 写 embedding 维度与稳定性测试**

```go
func TestMockEmbedderIsStableAnd1536Dimensional(t *testing.T) {
    e := NewMockEmbedder(1536)
    first, _ := e.Embed(context.Background(), []string{"subscription active"})
    second, _ := e.Embed(context.Background(), []string{"subscription active"})
    assert.Len(t, first[0], 1536)
    assert.Equal(t, first[0], second[0])
}
```

另写 `TestOpenAIEmbedderRejectsWrongDimension`：`httptest.Server` 返回 3 维向量，配置期望 1536，断言返回 `ErrEmbeddingDimension` 且结果为空；测试不得访问外网。

- [x] **Step 4: 运行测试并确认失败**

Run: `go test ./internal/retrieval -run 'TestChunker|TestMockEmbedder' -v`
Expected: FAIL。

- [x] **Step 5: 实现解析与切分**

Markdown/Text 按标题、空行和列表边界组成 400–700 token 目标块；超过上限才按句子拆分。PDF 解析器只接受能提取非空文本的页面，并将 locator 设为 `Page N`；扫描 PDF 返回 `UNSUPPORTED_SCANNED_PDF`。

- [x] **Step 6: 实现 Mock 与 OpenAI-compatible Embedder**

Mock 对归一化 token 使用 SHA-256 映射到 1536 维索引和正负号，累加后做 L2 normalize；相同文本必须跨进程稳定。`OpenAIEmbedder` 使用标准库 `net/http` 向 `{MODEL_BASE_URL}/embeddings` 发送 `model` 与 `input`，设置 Bearer API Key、15 秒超时，按响应 `index` 恢复顺序；任一向量不是 1536 维时返回 `ErrEmbeddingDimension`，调用方不得写 chunk。

- [x] **Step 7: 运行测试并提交**

Run: `go test ./internal/retrieval -v`
Expected: PASS。

```bash
git add internal/retrieval seed/knowledge
git commit -m "feat: add document chunking and mock embeddings"
```

---

### Task 6: PostgreSQL 混合检索与 RRF

**Files:**
- Create: `internal/retrieval/rrf.go`
- Create: `internal/retrieval/rrf_test.go`
- Create: `internal/retrieval/store.go`
- Create: `internal/retrieval/store_test.go`
- Create: `internal/retrieval/searcher.go`

**Interfaces:**
- Consumes `Embedder` and `knowledge_chunks`。
- Produces: `Searcher.Search(ctx context.Context, query string, limit int) ([]domain.Citation, error)`。
- Produces: `FuseRRF(keywordIDs, vectorIDs []uuid.UUID, k int) []ScoredID`。

- [x] **Step 1: 写 RRF 排序失败测试**

```go
func TestFuseRRFRewardsAgreement(t *testing.T) {
    a, b, c := uuid.New(), uuid.New(), uuid.New()
    got := FuseRRF([]uuid.UUID{a, b}, []uuid.UUID{b, c}, 60)
    require.Len(t, got, 3)
    assert.Equal(t, b, got[0].ID)
}
```

- [x] **Step 2: 写混合检索集成测试**

Seed 两个 chunk：一个包含精确字段 `subscription_sync_status`，另一个仅语义相关。搜索 `subscription_sync_status processing`，断言精确字段文档进入 Top 2，所有 Citation 均含 Title、Locator、ChunkID 和 Content。

- [x] **Step 3: 运行测试并确认失败**

Run: `go test ./internal/retrieval -run 'TestFuseRRF|TestHybridSearch' -v`
Expected: FAIL。

- [x] **Step 4: 实现两路召回与融合**

全文 SQL 使用 `websearch_to_tsquery('simple',$1)` 和 `ts_rank_cd` 取 Top 20；向量 SQL 使用 `embedding <=> $1` 取 Top 20；RRF 使用 `1/(60+rank)` 相加并稳定按 UUID 打破平分，去重后返回 Top 6。

- [x] **Step 5: 加入最低证据门槛**

`Searcher` 若两路均未命中则返回空切片而非错误；RRF 分数低于配置门槛的 chunk 不返回，门槛通过测试 fixture 固定验证。

- [x] **Step 6: 运行测试并提交**

Run: `go test ./internal/retrieval -v`
Expected: PASS。

```bash
git add internal/retrieval
git commit -m "feat: add hybrid postgres retrieval"
```

---

### Task 7: 知识上传与索引 API

**Files:**
- Create: `internal/knowledge/service.go`
- Create: `internal/knowledge/service_test.go`
- Create: `internal/knowledge/repository.go`
- Create: `internal/transport/httpapi/knowledge_handler.go`
- Create: `internal/transport/httpapi/knowledge_handler_test.go`
- Modify: `internal/transport/httpapi/server.go`

**Interfaces:**
- Produces: `knowledge.Service.Upload(ctx, Upload) (domain.KnowledgeDocument, error)`。
- Produces: `POST /api/v1/knowledge/documents` and `GET /api/v1/knowledge/documents`。

- [x] **Step 1: 写文件校验失败测试**

```go
func TestUploadRejectsExecutableAndOversizedFiles(t *testing.T) {
    svc := newKnowledgeService(t)
    _, err := svc.Upload(context.Background(), Upload{Filename: "run.sh", MediaType: "text/x-shellscript", Size: 20, Reader: strings.NewReader("echo hi")})
    assert.ErrorIs(t, err, ErrUnsupportedMediaType)
    _, err = svc.Upload(context.Background(), Upload{Filename: "large.md", MediaType: "text/markdown", Size: 10<<20 + 1, Reader: strings.NewReader("x")})
    assert.ErrorIs(t, err, ErrFileTooLarge)
}
```

同一测试加入伪装文件：`Filename="guide.pdf"`、`MediaType="application/pdf"` 但内容为 shell 文本，断言 `ErrContentTypeMismatch`。

- [x] **Step 2: 写成功索引测试**

上传示例 Markdown，断言状态依次持久化为 `uploaded -> indexing -> ready`，chunk 数大于 0；相同 SHA-256 再次上传返回 `ErrDuplicateDocument`。

- [x] **Step 3: 运行测试并确认失败**

Run: `go test ./internal/knowledge ./internal/transport/httpapi -run 'TestUpload' -v`
Expected: FAIL。

- [x] **Step 4: 实现事务化索引服务**

服务用 `io.LimitReader(reader, (10<<20)+1)` 限制实际读取 10 MB，同时计算 SHA-256；扩展名、声明 MIME 与 `http.DetectContentType`/PDF `%PDF-` magic 必须一致，再解析、切分、embedding。写 chunk 失败时将 document 标记 failed 并保存安全错误摘要；成功时在同一事务内替换 chunk 并标记 ready。

- [x] **Step 5: 实现 multipart HTTP Handler**

成功返回 201 与 document JSON；不支持类型返回 415；超限返回 413；重复文档返回 409。所有错误使用统一 `code/message/retryable/request_id`。

- [x] **Step 6: 运行测试并提交**

Run: `go test ./internal/knowledge ./internal/transport/httpapi -v`
Expected: PASS。

```bash
git add internal/knowledge internal/transport/httpapi
git commit -m "feat: add knowledge ingestion api"
```

---

### Task 8: 运行事件、SSE 编码与取消传播

**Files:**
- Create: `internal/runtrace/events.go`
- Create: `internal/runtrace/events_test.go`
- Create: `internal/transport/httpapi/stream.go`
- Create: `internal/transport/httpapi/stream_test.go`

**Interfaces:**
- Produces: `runtrace.Sink` interface with `Emit(context.Context, domain.AgentEvent) error`。
- Produces: `httpapi.WriteEvent(w io.Writer, event domain.AgentEvent) error`。

- [x] **Step 1: 写 SSE 编码失败测试**

```go
func TestWriteEventUsesEventIDTypeAndJSONData(t *testing.T) {
    var buf bytes.Buffer
    event := fixedEvent(domain.EventStageStarted, json.RawMessage(`{"stage":"retrieve"}`))
    require.NoError(t, WriteEvent(&buf, event))
    assert.Contains(t, buf.String(), "id: "+event.EventID.String()+"\n")
    assert.Contains(t, buf.String(), "event: stage.started\n")
    assert.Contains(t, buf.String(), `data: {"conversation_id"`)
    assert.True(t, strings.HasSuffix(buf.String(), "\n\n"))
}
```

- [x] **Step 2: 写取消传播测试**

使用 `httptest.NewRecorder` 和可取消 request context；Engine 阻塞等待 `ctx.Done()`。取消 request 后断言 Engine 收到 cancellation，Run 状态为 cancelled。

- [x] **Step 3: 运行测试并确认失败**

Run: `go test ./internal/runtrace ./internal/transport/httpapi -run 'TestWriteEvent|TestStreamCancellation' -v`
Expected: FAIL。

- [x] **Step 4: 实现事件工厂与持久化 Sink**

Event ID 使用 UUID，Timestamp 使用 UTC。Sink 先追加 `agent_run_steps` 再通知订阅者；持久化失败直接返回错误，不能向客户端宣称 stage completed。

- [x] **Step 5: 实现 SSE Writer**

设置 `Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`；每个事件写入后调用 `http.Flusher.Flush()`；request context 原样传给 Engine。

- [x] **Step 6: 运行测试并提交**

Run: `go test ./internal/runtrace ./internal/transport/httpapi -v`
Expected: PASS。

```bash
git add internal/runtrace internal/transport/httpapi
git commit -m "feat: add persisted agent event stream"
```

---

### Task 9: Agent 接口、Mock Engine 与代码门禁

**Files:**
- Create: `internal/agent/engine.go`
- Create: `internal/agent/mock_engine.go`
- Create: `internal/agent/mock_engine_test.go`
- Create: `internal/agent/guard.go`
- Create: `internal/agent/guard_test.go`
- Create: `internal/agent/errors.go`

**Interfaces:**
- Produces: `agent.Engine.Run(ctx context.Context, input agent.Input, sink runtrace.Sink) (domain.Draft, error)`。
- Produces: `agent.Guard.Verify(domain.Draft, agent.EvidenceState) (domain.Draft, error)`。

- [x] **Step 1: 定义稳定 Engine 接口**

```go
type Input struct {
    ConversationID uuid.UUID
    MessageID      uuid.UUID
    RunID          uuid.UUID
    CustomerID     uuid.UUID
    Text           string
}

type Engine interface {
    Run(context.Context, Input, runtrace.Sink) (domain.Draft, error)
}

type ToolFailure struct {
    Tool      string
    Code      string
    Retryable bool
}

type EvidenceState struct {
    CitationCount int
    FactCount     int
    ToolFailures  []ToolFailure
}
```

- [x] **Step 2: 写核心 Mock 演示失败测试**

输入 `My Pro payment succeeded but workspace is still Free`，断言事件顺序至少包含 `run.started`、`stage.started(classify)`、`retrieval.completed`、`tool.completed`、`review.required`；最终 Draft 为 P1、含 citation、含 payment fact、`RequiresReview=true`。

- [x] **Step 3: 写门禁失败测试**

```go
func TestGuardRequiresReviewWhenCitationMissing(t *testing.T) {
    draft := domain.Draft{Priority: domain.PriorityP2, Answer: "Activation always takes ten minutes", Confidence: .95}
    got, err := NewGuard(.70).Verify(draft, EvidenceState{})
    require.NoError(t, err)
    assert.True(t, got.RequiresReview)
    assert.Contains(t, got.Answer, "人工确认")
}
```

- [x] **Step 4: 运行测试并确认失败**

Run: `go test ./internal/agent -run 'TestMock|TestGuard' -v`
Expected: FAIL。

- [x] **Step 5: 实现确定性 Mock Engine**

Mock Engine 根据 fixture intent 匹配 seed knowledge 和 seed business records，不使用字符串硬编码最终答案；它仍经过真实 Searcher、工具服务和 Guard，以保证演示覆盖真实业务链路。

- [x] **Step 6: 实现门禁规则**

规则顺序固定：Schema 完整性 → priority → confidence `<0.70` → citation/fact 证据 → tool partial failure。任一条件触发时设置 `RequiresReview=true`，将稳定原因码追加到 `Draft.ReviewReasons`（如 `HIGH_PRIORITY`、`LOW_CONFIDENCE`、`MISSING_CITATION`、`MISSING_FACT`、`TOOL_PARTIAL_FAILURE`）；错误不修改原始 evidence。

- [x] **Step 7: 运行测试并提交**

Run: `go test ./internal/agent -v`
Expected: PASS。

```bash
git add internal/agent
git commit -m "feat: add mock agent and safety guard"
```

---

### Task 10: Eino ChatModelAgent、MCP 工具适配与真实模型模式

**Files:**
- Create: `internal/agent/eino_engine.go`
- Create: `internal/agent/eino_engine_test.go`
- Create: `internal/agent/prompt.go`
- Create: `internal/agent/mcp_tools.go`
- Create: `internal/agent/model.go`
- Modify: `internal/config/config.go`

**Interfaces:**
- Implements existing `agent.Engine` without changing HTTP or service layers。
- Produces: `NewEinoEngine(ctx, EinoDependencies) (*EinoEngine, error)`。

- [x] **Step 1: 锁定 OpenAI-compatible 模型适配器**

```bash
go get github.com/cloudwego/eino-ext/components/model/openai@v0.1.13
```

- [x] **Step 2: 写 fake-model 工具循环失败测试**

Fake ChatModel 第一轮返回 `get_payment_status` ToolCall，第二轮返回符合 Draft Schema 的最终消息。测试断言 Eino Engine 执行工具一次、将工具结果送回模型，并输出 `tool.started/tool.completed` 事件。

- [x] **Step 3: 写输出修复与失败测试**

Fake Model 第一次返回非法 JSON，第二次返回合法 Draft，断言总模型调用两次；连续两次非法时返回 `ErrOutputValidation`，Run 不得 completed。另用 fake model 首次返回 deadline/temporary error、第二次成功，断言仅重试一次；非临时错误不重试。

- [x] **Step 4: 运行测试并确认失败**

Run: `go test ./internal/agent -run 'TestEinoEngine' -v`
Expected: FAIL。

- [x] **Step 5: 创建 OpenAI-compatible ChatModel**

使用 eino-ext OpenAI model adapter，配置 `BaseURL`、`APIKey`、`Model` 和请求超时。`APP_MODE=openai-compatible` 且 API Key 为空时 `config.Load` 返回明确错误；mock mode 不初始化外部模型。

- [x] **Step 6: 通过 Streamable HTTP 装载 Eino MCP Tools**

使用与 Go 1.24 兼容的 mark3labs adapter；连接、初始化和装载代码固定为：

```go
cli, err := client.NewStreamableHttpClient(cfg.MCPURL)
if err != nil { return nil, fmt.Errorf("create MCP client: %w", err) }
if err := cli.Start(ctx); err != nil { return nil, fmt.Errorf("start MCP client: %w", err) }

req := mcp.InitializeRequest{}
req.Params.ProtocolVersion = mcp.LATEST_PROTOCOL_VERSION
req.Params.ClientInfo = mcp.Implementation{Name: "support-copilot-api", Version: "0.1.0"}
if _, err := cli.Initialize(ctx, req); err != nil {
    _ = cli.Close()
    return nil, fmt.Errorf("initialize MCP client: %w", err)
}

mcpTools, err := mcpp.GetTools(ctx, &mcpp.Config{
    Cli:          cli,
    ToolNameList: []string{"get_account_status", "get_payment_status", "get_service_status"},
})
```

`EinoEngine.Close` 负责关闭 client。每个装载后的工具由 `timeoutTool` 包装：单次 2 秒；仅当结构化错误码为 `TEMPORARY_UNAVAILABLE` 时重试一次，其他错误不重试。

- [x] **Step 7: 使用 Eino ADK Runner 执行 Agent**

```go
agentInstance, err := adk.NewChatModelAgent(ctx, &adk.ChatModelAgentConfig{
    Name:        "support-copilot",
    Description: "Resolve SaaS support questions with cited knowledge and read-only tools",
    Instruction: systemPrompt,
    Model:       chatModel,
    MaxIterations: 6,
    ModelRetryConfig: &adk.ModelRetryConfig{
        MaxRetries: 1,
        IsRetryAble: isTransientModelError,
    },
    ToolsConfig: adk.ToolsConfig{ToolsNodeConfig: compose.ToolsNodeConfig{Tools: tools}},
})
runner := adk.NewRunner(ctx, adk.RunnerConfig{Agent: agentInstance, EnableStreaming: true})
iter := runner.Query(ctx, prompt)
```

遍历 iterator 时只映射可公开 AgentEvent；不保存 reasoning content。最终消息解析为 Draft 并交给 Guard。

- [x] **Step 8: 运行 Agent 测试并提交**

Run: `go test ./internal/agent -v`
Expected: PASS，测试不访问外网。

```bash
git add go.mod go.sum internal/config internal/agent
git commit -m "feat: integrate eino agent runtime"
```

---

### Task 11: 会话用例与流式消息 API

**Files:**
- Create: `internal/conversation/service.go`
- Create: `internal/conversation/service_test.go`
- Create: `internal/transport/httpapi/conversation_handler.go`
- Create: `internal/transport/httpapi/conversation_handler_test.go`
- Modify: `internal/transport/httpapi/server.go`
- Modify: `cmd/api/main.go`

**Interfaces:**
- Produces: `POST /api/v1/conversations`、`GET /api/v1/conversations`。
- Produces: `POST /api/v1/conversations/{id}/messages:stream`。

- [x] **Step 1: 写服务失败测试**

创建 conversation 后发送消息，断言先保存 user message 和 running Run，再调用 Engine。安全 Draft 保存 assistant message 并将 Run 标记 completed；`RequiresReview=true` 时只保存 Draft/证据并将 Run 标记 waiting_review，不提前写入最终 assistant message；Engine 失败后保留 user message 并将 Run 标记 failed。

- [x] **Step 2: 写 HTTP 流式失败测试**

POST 合法安全问题后断言 status 200、content type SSE、首事件为 `run.started`、末事件为 `run.completed`；P1 fixture 的末事件为 `review.required` 且数据库 Run 为 waiting_review。空文本返回 422 `INVALID_ARGUMENT`，不存在 conversation 返回 404。

- [x] **Step 3: 运行测试并确认失败**

Run: `go test ./internal/conversation ./internal/transport/httpapi -run 'TestSend|TestConversationStream' -v`
Expected: FAIL。

- [x] **Step 4: 实现用例编排与事务边界**

消息与 Run 创建同事务；长时间模型调用不持有数据库事务。安全 Run 完成与 assistant message 写入使用短事务；待审核 Run 只更新 status/output summary，审核批准后才由 Ticket 服务写入最终消息。客户端取消时标记 cancelled。

- [x] **Step 5: 注册路由与 Demo 身份校验**

客户接口要求 `X-Demo-User` 对应 customer role。无 header 返回 401，角色错误返回 403。

- [x] **Step 6: 运行测试并提交**

Run: `go test ./internal/conversation ./internal/transport/httpapi -v`
Expected: PASS。

```bash
git add internal/conversation internal/transport/httpapi cmd/api
git commit -m "feat: add streaming conversation api"
```

---

### Task 12: 工单分诊、审核与转人工 API

**Files:**
- Create: `internal/ticket/service.go`
- Create: `internal/ticket/service_test.go`
- Create: `internal/transport/httpapi/ticket_handler.go`
- Create: `internal/transport/httpapi/ticket_handler_test.go`
- Modify: `internal/transport/httpapi/server.go`

**Interfaces:**
- Produces: `POST /api/v1/conversations/{id}/tickets`。
- Produces: `GET /api/v1/tickets`、`GET /api/v1/tickets/{id}`。
- Produces: `POST /api/v1/tickets/{id}/review`。

- [x] **Step 1: 写工单创建失败测试**

从未解决 conversation 创建 Ticket，断言复制最后一个 waiting_review Run 的 intent、priority、draft、facts 和 citations，并回填该 `agent_runs.ticket_id`；同一 conversation 存在 open/waiting_review ticket 时再次创建返回现有 ticket，不重复。

- [x] **Step 2: 写审核状态机失败测试**

`approve` 要求 agent role 且 final answer 非空；`edit_and_approve` 保存编辑后答案；两种批准动作都追加最终 agent message、将 Ticket 设为 resolved 并将关联 Run 从 waiting_review 设为 completed；`escalate` 将 Ticket 设为 escalated 且 Run 保持 waiting_review。客户身份访问 review 返回 403。

- [x] **Step 3: 运行测试并确认失败**

Run: `go test ./internal/ticket ./internal/transport/httpapi -run 'TestCreateTicket|TestReviewTicket' -v`
Expected: FAIL。

- [x] **Step 4: 实现服务与 Handler**

列表支持 `status`、`priority` 查询参数，默认按 priority 升序、created_at 升序；未知过滤值返回 422。详情包含 Conversation、latest Run、citations、facts 和 reviews。审核写 review、最终 message、Ticket 状态和 Run 状态时使用单一数据库事务，任一步失败全部回滚。

- [x] **Step 5: 运行测试并提交**

Run: `go test ./internal/ticket ./internal/transport/httpapi -v`
Expected: PASS。

```bash
git add internal/ticket internal/transport/httpapi
git commit -m "feat: add ticket triage and human review"
```

---

### Task 13: 客户智能客服页面

**Files:**
- Create: `apps/web/app/customer/page.tsx`
- Create: `apps/web/components/customer/chat-shell.tsx`
- Create: `apps/web/components/customer/message-list.tsx`
- Create: `apps/web/components/customer/process-panel.tsx`
- Create: `apps/web/components/customer/citation-card.tsx`
- Create: `apps/web/lib/api/client.ts`
- Create: `apps/web/lib/api/stream.ts`
- Create: `apps/web/lib/state/chat-reducer.ts`
- Create: `apps/web/tests/chat-reducer.test.ts`
- Create: `apps/web/tests/customer-chat.test.tsx`

**Interfaces:**
- Consumes conversation and streaming APIs from Task 11。
- Produces: typed `StreamEvent` union mirroring backend event names。

- [x] **Step 1: 写流式 reducer 失败测试**

```ts
it('deduplicates events and attaches deltas to the active message', () => {
  const started = event('run.started', 'e1', { run_id: 'r1' })
  const delta = event('draft.delta', 'e2', { text: 'Payment confirmed' })
  const state = reduceEvents(initialState, [started, delta, delta])
  expect(state.activeRunId).toBe('r1')
  expect(state.draftText).toBe('Payment confirmed')
  expect(state.seenEventIds.size).toBe(2)
})
```

- [x] **Step 2: 写客户页面失败测试**

Mock API stream 后断言页面显示用户消息、处理阶段、引用卡片和“创建工单”；工具事件只展示工具名称与安全摘要，不显示隐藏 prompt。

- [x] **Step 3: 运行测试并确认失败**

Run: `cd apps/web && npm test -- chat-reducer customer-chat`
Expected: FAIL。

- [x] **Step 4: 实现 fetch 流解析器**

解析 `id:`、`event:`、`data:` 行，空行提交事件；收到 AbortSignal 后停止 reader；非法 JSON 生成客户端 `stream.error` 状态而不是静默丢弃。

- [x] **Step 5: 实现三栏客户页面**

桌面端为会话/对话/过程三栏，移动端将会话与过程收进 drawer。输入发送时禁用重复提交，提供“停止生成”按钮取消请求；citation 卡片显示文档、locator 和片段。核心可见文案固定为输入占位符“描述你的问题…”、发送按钮“发送”、工单入口“创建工单”，供组件测试与 E2E 共用。

- [x] **Step 6: 运行测试与构建并提交**

```bash
cd apps/web
npm test
npm run build
```

Expected: PASS。

```bash
git add apps/web
git commit -m "feat: add customer support chat"
```

---

### Task 14: 客服工单工作台

**Files:**
- Create: `apps/web/app/workspace/page.tsx`
- Create: `apps/web/components/workspace/ticket-queue.tsx`
- Create: `apps/web/components/workspace/ticket-detail.tsx`
- Create: `apps/web/components/workspace/evidence-panel.tsx`
- Create: `apps/web/components/workspace/review-actions.tsx`
- Create: `apps/web/tests/ticket-workspace.test.tsx`

**Interfaces:**
- Consumes ticket APIs from Task 12。
- Produces approve、edit-and-approve、escalate interactions。

- [x] **Step 1: 写工作台失败测试**

Mock P1 waiting-review ticket，断言队列优先展示 P1，详情显示 intent/confidence/citations/facts，点击 approve 前必须显示确认对话框，提交成功后状态变 resolved。

- [x] **Step 2: 运行测试并确认失败**

Run: `cd apps/web && npm test -- ticket-workspace`
Expected: FAIL。

- [x] **Step 3: 实现队列与详情查询**

TanStack Query key 固定为 `['tickets', filters]` 和 `['ticket', id]`；审核成功 invalidate 两个 key。加载、空列表、403 和网络错误都有独立界面。

- [x] **Step 4: 实现人工审核动作**

Approve 发送当前草稿；Edit-and-approve 要求编辑内容非空；Escalate 要求选择原因。按钮 pending 时禁用，失败保留用户编辑内容。核心中文文案固定为“批准并发送”“编辑后批准”“转人工”“确认批准”，resolved 状态展示为“已解决”。

- [x] **Step 5: 运行测试与构建并提交**

Run: `cd apps/web && npm test && npm run build`
Expected: PASS。

```bash
git add apps/web
git commit -m "feat: add agent ticket workspace"
```

---

### Task 15: Runs 查询、重放与详情 API

**Files:**
- Create: `internal/runtrace/service.go`
- Create: `internal/runtrace/service_test.go`
- Create: `internal/transport/httpapi/run_handler.go`
- Create: `internal/transport/httpapi/run_handler_test.go`
- Modify: `internal/transport/httpapi/server.go`

**Interfaces:**
- Produces: `GET /api/v1/runs/{id}`。
- Produces: `POST /api/v1/runs/{id}:replay`。

- [x] **Step 1: 写 Run 详情失败测试**

断言详情 steps 按 `sequence` 稳定排序，工具参数中的 `api_key`、`token`、`password` 字段被替换为 `***`，错误只返回安全 code/message。

- [x] **Step 2: 写重放失败测试**

重放 completed Run 创建新 UUID，设置 `replayed_from_run_id`，复用原始 user input 与当前 Engine 配置，不修改旧 Run；running Run 不允许重放。

- [x] **Step 3: 运行测试并确认失败**

Run: `go test ./internal/runtrace ./internal/transport/httpapi -run 'TestRunDetail|TestReplay' -v`
Expected: FAIL。

- [x] **Step 4: 实现服务、遮罩和 Handler**

Replay 异步启动但立即返回 202 与新 Run ID。失败启动时将新 Run 标记 failed；admin/agent 可查看，customer 只能查看自己 conversation 的 Run。

- [x] **Step 5: 运行测试并提交**

Run: `go test ./internal/runtrace ./internal/transport/httpapi -v`
Expected: PASS。

```bash
git add internal/runtrace internal/transport/httpapi
git commit -m "feat: add run inspection and replay"
```

---

### Task 16: Golden Dataset、评测 CLI 与评测 API

**Files:**
- Create: `seed/eval/support-golden.jsonl`
- Create: `internal/evaluation/types.go`
- Create: `internal/evaluation/scorers.go`
- Create: `internal/evaluation/scorers_test.go`
- Create: `internal/evaluation/runner.go`
- Create: `internal/evaluation/runner_test.go`
- Create: `internal/transport/httpapi/evaluation_handler.go`
- Create: `cmd/eval/main.go`

**Interfaces:**
- Produces: `evaluation.Runner.Run(ctx, Dataset) (RunResult, error)`。
- Produces: `POST /api/v1/evaluations`、`GET /api/v1/evaluations/{id}`。
- Produces CLI `go run ./cmd/eval -dataset seed/eval/support-golden.jsonl -mode mock`。

- [x] **Step 1: 创建 24 条固定评测用例**

数据集覆盖 6 类：订阅、支付、成员权限、发票、服务状态、无证据转人工；每类 4 条。每行包含 `id`、`input`、`expected_intent`、`expected_priority`、`expected_tools`、`required_citations`（`title` + `locator`）和 `requires_review`，与 `eval_cases` 字段逐项对应。

- [x] **Step 2: 写评分器失败测试**

```go
func TestScoreToolCallsRequiresNameAndArguments(t *testing.T) {
    expected := []ExpectedTool{{Name: "get_payment_status", Arguments: map[string]string{"account_id": "acct_demo_pro"}}}
    actual := []ToolCall{{Name: "get_payment_status", Arguments: map[string]any{"account_id": "wrong"}}}
    score := ScoreToolCalls(expected, actual)
    assert.Equal(t, 0.5, score)
}
```

- [x] **Step 3: 写 Runner 可重复性测试**

Mock 模式连续运行同一 dataset 两次，断言逐例分数、聚合指标和结果顺序相同；运行配置包含 commit SHA、mode、dataset hash 和 UTC timestamp。

- [x] **Step 4: 运行测试并确认失败**

Run: `go test ./internal/evaluation -v`
Expected: FAIL。

- [x] **Step 5: 实现确定性评分与聚合**

计算 intent accuracy、tool name/argument accuracy、citation precision、evidence-grounded answer rate、unsupported refusal rate、schema pass rate、TTFT、total latency。聚合值使用分子/分母保存，避免只存百分比。

- [x] **Step 6: 实现 CLI 与 API**

CLI 退出码：全部基础断言通过为 0，数据集非法为 2，运行失败为 1。API 启动评测返回 202，查询返回进度与逐例结果。

- [x] **Step 7: 运行评测测试并提交**

Run: `go test ./internal/evaluation ./internal/transport/httpapi -v`
Expected: PASS。

```bash
git add seed/eval internal/evaluation internal/transport/httpapi cmd/eval
git commit -m "feat: add reproducible agent evaluation"
```

---

### Task 17: Agent Runs、Evaluation 与知识库界面

**Files:**
- Create: `apps/web/app/runs/[id]/page.tsx`
- Create: `apps/web/app/evaluations/page.tsx`
- Create: `apps/web/app/knowledge/page.tsx`
- Create: `apps/web/components/runs/run-timeline.tsx`
- Create: `apps/web/components/runs/tool-step.tsx`
- Create: `apps/web/components/evaluations/metric-card.tsx`
- Create: `apps/web/components/evaluations/case-table.tsx`
- Create: `apps/web/components/knowledge/upload-panel.tsx`
- Create: `apps/web/tests/run-timeline.test.tsx`
- Create: `apps/web/tests/evaluation-dashboard.test.tsx`

**Interfaces:**
- Consumes Tasks 7、15、16 APIs。

- [x] **Step 1: 写 Run 时间线失败测试**

给定成功、工具失败、人工审核三个 step，断言分别显示状态、耗时、工具安全参数、citation 和 retryable；页面中不存在 `reasoning_content` 文本。

- [x] **Step 2: 写评测看板失败测试**

给定分子/分母指标，断言卡片显示正确比例与样本数；未运行的指标显示“尚未测量”，不显示 0%。

- [x] **Step 3: 运行测试并确认失败**

Run: `cd apps/web && npm test -- run-timeline evaluation-dashboard`
Expected: FAIL。

- [x] **Step 4: 实现 Run 与 Evaluation 页面**

Run 页面提供 replay 按钮与前后 Run 链接；Evaluation 页面可启动 Mock 评测、轮询进度、按失败类型过滤用例；图表只使用实际响应数据。

- [x] **Step 5: 实现知识库上传页**

前端预检扩展名和 10 MB，大文件阻止上传；上传后显示 indexing/ready/failed 与安全错误原因；不提供批量删除以避免 MVP 的额外破坏性操作。

- [x] **Step 6: 运行测试与构建并提交**

Run: `cd apps/web && npm test && npm run build`
Expected: PASS。

```bash
git add apps/web
git commit -m "feat: add observability and evaluation ui"
```

---

### Task 18: Docker Compose、E2E、CI 与公开包装

**Files:**
- Create: `deployments/api.Dockerfile`
- Create: `deployments/web.Dockerfile`
- Create: `docker-compose.yml`
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/support-flow.spec.ts`
- Create: `.github/workflows/ci.yml`
- Create: `docs/architecture.md`
- Create: `docs/demo-script.md`
- Create: `README.md`
- Modify: `.env.example`
- Modify: `Makefile`

**Interfaces:**
- Produces: `docker compose up --build` complete Mock demo。
- Produces: CI jobs for Go tests, Web tests/build and Playwright。

- [x] **Step 1: 写核心 E2E 测试**

```ts
test('subscription issue reaches human-reviewed resolution', async ({ page }) => {
  await page.goto('/customer')
  await page.getByPlaceholder('描述你的问题…').fill('My Pro payment succeeded but workspace is still Free')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('订阅激活说明')).toBeVisible()
  await expect(page.getByText('需要人工审核')).toBeVisible()
  await page.getByRole('button', { name: '创建工单' }).click()
  await page.goto('/workspace')
  await page.getByText('订阅付款后未生效').click()
  await page.getByRole('button', { name: '批准并发送' }).click()
  await page.getByRole('button', { name: '确认批准' }).click()
  await expect(page.getByText('已解决')).toBeVisible()
})
```

- [x] **Step 2: 创建容器与健康检查**

PostgreSQL 使用 `pgvector/pgvector:pg17`；API 和 MCP build stage 固定 `golang:1.25.7`；Web build stage 固定 Node 24。Compose healthcheck 保证 DB ready 后再迁移和 seed，API ready 后 Web 才标记 healthy。

- [x] **Step 3: 运行 Compose 并确认 E2E 初次失败**

Run: `docker compose up --build -d && cd apps/web && npx playwright test e2e/support-flow.spec.ts`
Expected: 首次在未补齐 compose URL/seed 时 FAIL，失败点可定位。

- [x] **Step 4: 补齐环境与 Makefile 命令**

`make dev`、`make test`、`make integration-test`、`make eval`、`make e2e`、`make seed` 必须直接映射到文档中的命令；`.env.example` 包含 mock 默认值与真实模型可选变量。

- [x] **Step 5: 创建 CI**

CI 使用 PostgreSQL service + pgvector，运行 `go test ./...`、`npm test`、`npm run build`、Playwright 核心路径；任何一步失败阻止合并。

- [x] **Step 6: 编写 README 与演示资料**

README 顺序固定：产品截图/GIF → 业务问题 → 90 秒 Demo → 架构图 → 为什么不是普通聊天机器人 → Mock 快速开始 → 真实模型配置 → 评测结果 → 安全边界 → 路线图。`docs/demo-script.md` 提供三分钟逐步讲解和常见面试追问。

- [x] **Step 7: 执行全量验收**

```bash
make test
make integration-test
make eval
make e2e
docker compose down
```

Expected: 全部 PASS；Eval 生成带 commit SHA、dataset hash、mode 和日期的实际结果文件。

- [x] **Step 8: 提交发布候选版本**

```bash
git add deployments docker-compose.yml .github apps/web/e2e apps/web/playwright.config.ts docs README.md .env.example Makefile
git commit -m "docs: ship runnable support copilot demo"
git tag v0.1.0
```

---

## Final GitHub Actions

代码和文档验收通过后，再执行外部发布动作：

1. 在 GitHub 创建公开仓库 `guiyangyuan/eino-support-copilot`；
2. Push `main` 与 `v0.1.0`；
3. 设置描述和 Topics；
4. 上传实际产品截图/GIF；
5. 将 `eino-support-copilot` 设为主页自定义置顶；
6. 不置顶 `mode`，确认主页主展示区只出现新作品；
7. 将简历 GitHub 地址更新为 `https://github.com/guiyangyuan`。

这些动作会改变公开 GitHub 状态，执行前必须再次向用户展示目标仓库和将公开的内容。

## Plan Self-Review

- 设计规格中的 13 个 HTTP 接口均映射到明确任务；三类 MCP 工具、RAG、人工审核、Runs、Replay、Evaluation、Docker 与 GitHub 包装均有测试和验收步骤。
- 依赖图已在临时 Go module 中解析；`pgx v5.10.0` 与 `goose v3.27.3` 将最低 Go 版本提升到 1.25.7，因此计划和容器统一锁定 Go 1.25.7。
- MCP Server、mark3labs client 和 Eino MCP adapter 统一采用 Streamable HTTP `/mcp`，接口名已按锁定版本源码核对。
- 所有数据库表、状态、关键类型和核心 UI 文案已写明；计划不含未决占位项。
