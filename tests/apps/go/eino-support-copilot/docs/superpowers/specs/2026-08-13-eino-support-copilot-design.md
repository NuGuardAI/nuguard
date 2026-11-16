# Eino Support Copilot 设计规格

日期：2026-08-13  
状态：已通过
目标仓库：`guiyangyuan/eino-support-copilot`

## 1. 项目目标

构建一个可公开运行、可用于简历和面试演示的 AI 客服与工单协作平台。项目必须同时证明以下能力：

- 使用 Go 与 Eino 构建可控的 Agent 应用；
- 使用 RAG、只读 MCP 工具和结构化输出解决真实业务流程；
- 使用 Next.js 完成用户端、客服端和可观测性界面；
- 通过人工审核、失败兜底、运行轨迹和离线评测证明工程完整性；
- 在不配置模型密钥时也能通过 Mock 模式完整演示。

项目只使用生成的 SaaS 业务数据和自编示例文档，不使用任何公司代码、截图、配置、接口、数据或内部流程。

## 2. 面向的简历叙事

项目对外描述为：

> 面向 SaaS 客服场景的 AI 工单协作平台。基于 Go、Eino、MCP 与混合检索构建受控 Agent，串联意图分诊、知识检索、实时状态查询、回复草稿、人工审核和离线评测，并通过 SSE 展示执行轨迹。

面试演示采用一条完整链路：

1. 用户询问“Pro 订阅付款成功后为什么仍显示 Free”；
2. 系统识别为订阅问题并判断需要知识与实时状态；
3. 检索订阅激活文档，并调用账户、支付状态工具；
4. 生成带引用的结构化回复草稿；
5. P1 工单进入人工审核，客服确认后完成回复；
6. 在 Agent Runs 页面查看节点、耗时、工具参数、证据和错误；
7. 在 Evaluation 页面查看该用例所属测试集的评测结果。

## 3. 用户与核心场景

### 3.1 用户角色

- 客户：提交问题、查看流式处理进度、阅读带引用的回答、评价结果或创建工单。
- 客服人员：查看工单队列、分诊信息、证据和 AI 草稿，编辑、批准或转人工。
- 项目演示者：管理示例知识、查看 Agent Run 和离线评测结果。

MVP 不实现真实账号体系、SSO 或复杂 RBAC。界面通过固定 Demo 身份切换角色，服务端仍对管理接口做角色校验，防止前端直接越权。

### 3.2 核心业务场景

- 订阅付款后未激活；
- 无法邀请工作区成员；
- 发票下载或账单金额问题；
- 服务异常与状态查询；
- 知识不足、工具失败或低置信度时转人工。

## 4. MVP 范围

### 4.1 必须完成

- 客户对话页：会话列表、流式回答、节点进度、引用、解决反馈和转工单；
- 客服工作台：工单队列、优先级、意图、上下文、证据、工具结果、AI 草稿和人工审核；
- 知识库：上传 `.md`、`.txt`、文本型 `.pdf`，完成解析、切分、索引和状态展示；
- 混合检索：PostgreSQL 全文检索、pgvector 向量检索、RRF 融合和引用溯源；
- 三个只读 MCP 工具：账户状态、支付状态和服务状态；
- Eino Agent：工具选择、信息补充、带证据草稿和结构化结果；
- SSE/流式 Fetch：文本增量与节点事件；
- Agent Runs：节点、工具调用、延迟、Token、错误和失败重放；
- Evaluation：20–30 条固定测试用例、批量运行和结果看板；
- Docker Compose：Web、API、MCP Server 和 PostgreSQL 一键启动；
- Mock 模式：不配置外部模型密钥也能完成完整演示链路。

### 4.2 明确不做

- 真实支付、CRM、邮件、工单系统或企业内部接口；
- 自动修改账户、支付或生产配置等写操作；
- 多租户计费、企业 SSO、完整权限后台；
- 多 Agent 协作、拖拽式编排器或通用 Agent 平台；
- 扫描 PDF 的 OCR、复杂表格还原；
- Kubernetes 和生产级大规模并发设计。

## 5. 系统架构

采用单仓库、四个可独立运行单元：

```text
Next.js Web
    │ HTTP + streaming response
    ▼
Go API ───────────────► PostgreSQL + pgvector
    │                        │
    │ Eino ADK Runner        ├─ business data
    │ + application guards   ├─ knowledge chunks
    │                        └─ runs/evaluations
    ▼
Mock Business MCP Server
    ├─ get_account_status
    ├─ get_payment_status
    └─ get_service_status
```

### 5.1 仓库结构

```text
eino-support-copilot/
├── apps/
│   └── web/                    # Next.js + TypeScript
├── cmd/
│   ├── api/                    # Go API 入口
│   ├── mcp-server/             # 模拟业务 MCP 服务
│   └── eval/                   # 离线评测 CLI
├── internal/
│   ├── agent/                  # Eino Agent、Runner、提示词和门禁
│   ├── application/            # 用例编排
│   ├── domain/                 # conversation/ticket/run/knowledge
│   ├── infrastructure/         # 数据库、模型、embedding、MCP 客户端
│   ├── retrieval/              # 切分、索引、混合召回、RRF
│   ├── transport/http/         # REST 与流式接口
│   └── observability/          # 事件、指标、追踪持久化
├── migrations/                 # SQL 迁移
├── seed/                       # 生成的业务数据、知识文档和评测集
├── docs/                       # 架构、演示脚本和截图
├── deployments/               # Docker 配置
├── Makefile
├── docker-compose.yml
└── README.md
```

### 5.2 技术选型

- 后端：Go、标准 `net/http` 路由或轻量路由层、Eino ADK、Eino Tools；
- Agent：`ChatModelAgent` + `Runner`，不直接调用 Agent 的底层 Run；
- 工具：独立 Go MCP Server，API 通过 Eino MCP Tool 适配器消费；
- 前端：Next.js、TypeScript、Tailwind CSS、TanStack Query；
- 流式交互：基于 `fetch` 的 SSE 格式响应，以支持 POST 请求和中止；
- 数据：PostgreSQL、pgvector、PostgreSQL 全文检索；
- 部署：Docker Compose；
- 测试：Go testing、前端组件测试、Playwright 和 Golden Dataset 评测。

Eino 与 eino-ext 的具体版本在实现时锁定到相互兼容的稳定版本，并写入 `go.mod`，不使用 alpha API 作为 MVP 的必要依赖。

## 6. Agent 执行设计

### 6.1 执行阶段

1. `receive`：校验输入，创建 conversation/message/agent_run；
2. `classify`：输出意图、优先级、风险、实体和缺失字段；
3. `agent_loop`：Eino Runner 驱动模型在白名单工具中选择；
4. `retrieve`：知识工具执行混合检索并返回可追溯引用；
5. `business_tool`：按需调用 MCP 只读工具；
6. `draft`：生成符合 JSON Schema 的回复草稿；
7. `verify`：代码校验 Schema、证据、风险、置信度与工具状态；
8. `deliver`：安全场景直答，P1/P0、低置信度或失败场景进入人工审核；
9. `complete`：持久化使用量、延迟、最终状态和用户反馈。

### 6.2 模型可自主决定的事项

- 是否需要检索知识或查询业务状态；
- 从白名单中选择工具并构造参数；
- 在信息不足时提出明确追问；
- 根据已获得的事实和引用组织草稿。

### 6.3 代码强制的边界

- 所有业务工具只读；
- 工具参数必须通过 JSON Schema 校验；
- 业务事实必须来自工具结果，规范性结论必须有知识引用；
- P0/P1、低置信度、证据不足或工具部分失败必须人工审核；
- 不允许把模型的自然语言当成已执行的业务操作；
- 日志与 UI 不展示隐藏思维过程，只展示节点、输入摘要、工具参数、结果摘要和证据。

### 6.4 结构化草稿

```json
{
  "intent": "billing.subscription_activation",
  "priority": "P1",
  "answer": "付款已完成，订阅同步任务仍在处理中。",
  "citations": [
    {"chunk_id": "...", "title": "订阅激活说明", "locator": "§2"}
  ],
  "facts": [
    {"source": "get_payment_status", "field": "status", "value": "paid"}
  ],
  "confidence": 0.94,
  "requires_review": true,
  "follow_up_question": null
}
```

## 7. RAG 设计

### 7.1 索引流程

1. 校验文件类型、大小和内容；
2. 提取文本并保留标题、章节或 PDF 页码；
3. 按标题、段落和列表边界切分，目标 400–700 tokens，重叠不超过 15%；
4. 生成 1536 维 embedding；
5. 写入原文、`tsvector`、vector 和 metadata；
6. 标记文档索引状态和失败原因。

Mock 模式使用固定的 1536 维确定性 embedding，使结果可复现。真实模型模式使用可配置的 OpenAI-compatible embedding provider；维度不匹配时拒绝写入并返回配置错误。

### 7.2 检索流程

- 对查询做轻量规范化，不默认使用额外 LLM 改写；
- 全文检索召回 Top 20；
- 向量检索召回 Top 20；
- 使用 RRF 融合，默认 `k=60`；
- 去重后返回 Top 6；
- 通过最低相关性门槛过滤弱证据；
- 返回 chunk 原文、文档标题、章节/页码和分数。

首版不加入 Cross-Encoder Reranker，以控制工期。评测发现 Top 6 排序不足时，再将 Reranker 作为后续扩展，而不是预置复杂度。

## 8. MCP 工具设计

MCP Server 只访问 seed 生成的业务表，并提供：

### `get_account_status`

- 输入：`account_id`；
- 输出：套餐、账户状态、订阅同步状态、更新时间；
- 错误：`INVALID_ARGUMENT`、`NOT_FOUND`、`TEMPORARY_UNAVAILABLE`。

### `get_payment_status`

- 输入：`account_id`，可选 `payment_id`；
- 输出：支付状态、金额、币种、支付时间和关联订阅；
- 不返回完整支付凭证或敏感卡信息。

### `get_service_status`

- 输入：服务枚举；
- 输出：当前状态、影响范围、事件摘要和更新时间。

所有工具设置 2 秒超时，临时错误最多重试一次。工具结果保存结构化摘要；日志对可能出现的个人字段做遮罩。

## 9. 页面设计

### 9.1 客户对话页

- 左侧：历史会话；
- 中间：对话、流式文本、引用卡片、反馈和输入框；
- 右侧：用户可理解的处理进度，不展示隐藏推理；
- 未解决时一键创建工单，并继承会话与运行摘要。

### 9.2 客服工作台

- 左侧：按优先级和状态排序的工单队列；
- 中间：用户上下文、对话、分诊结果和可编辑回复草稿；
- 右侧：知识证据、工具事实和运行摘要；
- 支持批准、编辑后批准、转人工，所有动作留审计记录。

### 9.3 Agent Runs 与 Evaluation

- Run 详情展示阶段时间线、工具参数、结果摘要、Token、错误和关联工单；
- 支持使用原输入与当前配置重新运行，旧 Run 保留；
- Evaluation 展示通过率、引用正确率、工具调用准确率、平均延迟和逐用例差异。

## 10. HTTP 与流式接口

主要接口：

- `POST /api/v1/conversations`；
- `GET /api/v1/conversations`；
- `POST /api/v1/conversations/{id}/messages:stream`；
- `POST /api/v1/conversations/{id}/tickets`；
- `GET /api/v1/tickets`；
- `GET /api/v1/tickets/{id}`；
- `POST /api/v1/tickets/{id}/review`；
- `POST /api/v1/knowledge/documents`；
- `GET /api/v1/knowledge/documents`；
- `GET /api/v1/runs/{id}`；
- `POST /api/v1/runs/{id}:replay`；
- `POST /api/v1/evaluations`；
- `GET /api/v1/evaluations/{id}`。

流式事件类型：

- `run.started`；
- `stage.started`、`stage.completed`；
- `retrieval.completed`；
- `tool.started`、`tool.completed`、`tool.failed`；
- `draft.delta`；
- `citation`；
- `review.required`；
- `run.completed`、`run.failed`。

每个事件包含 `conversation_id`、`message_id`、`run_id`、`event_id`、时间戳和类型化 payload。客户端按 `event_id` 去重，取消请求时服务端传播 context cancellation。

## 11. 数据模型

核心表：

- `demo_users`：固定 Demo 身份和角色；
- `conversations`、`messages`；
- `tickets`、`ticket_reviews`；
- `knowledge_documents`、`knowledge_chunks`；
- `agent_runs`、`agent_run_steps`；
- `eval_datasets`、`eval_cases`、`eval_runs`、`eval_results`；
- `mock_accounts`、`mock_payments`、`mock_service_incidents`。

主要状态：

- Ticket：`open`、`triaged`、`waiting_review`、`resolved`、`escalated`；
- Run：`queued`、`running`、`waiting_review`、`completed`、`failed`、`cancelled`；
- Document：`uploaded`、`indexing`、`ready`、`failed`。

所有主键使用 UUID；业务时间统一存 UTC；审计记录只追加，不覆盖历史事实。

## 12. 异常与降级

- 模型超时：重试一次；仍失败则终止 Run 并生成转人工摘要；
- 工具超时：记录工具错误；允许模型基于其余证据生成明确标注的不完整草稿，但强制审核；
- 检索无结果：不得生成带确定性规范结论的答案，转追问或人工；
- Schema 解析失败：使用修复提示重试一次，仍失败则标记 `OUTPUT_VALIDATION_FAILED`；
- 数据库失败：事务回滚，流返回稳定错误码和可重试标记；
- 客户端断开：取消模型与工具 context，Run 标记 cancelled；
- 重放：创建新 Run 并关联 `replayed_from_run_id`，不覆盖旧结果。

错误响应统一包含 `code`、`message`、`retryable`、`request_id`，不向前端暴露堆栈和密钥。

## 13. 安全与隐私

- `.env.example` 只提供变量名，不包含密钥；
- 密钥只从环境变量读取，日志自动遮罩；
- 上传限制为 10 MB，并校验 MIME、扩展名和实际内容；
- 文档解析不执行宏、脚本或附件；
- MCP 工具使用白名单和 JSON Schema；
- 输出 HTML 统一转义；
- Demo 数据标明为生成数据，README 不声称具有真实生产指标；
- 不展示模型隐藏推理，只展示可审计事件与事实来源。

## 14. 测试与验收

### 14.1 自动化测试

- Go 单元测试：领域规则、门禁、RRF、切分、Schema 和错误映射；
- Go 集成测试：PostgreSQL、MCP 工具、索引、流式接口和取消传播；
- 前端测试：流式事件 reducer、工单操作和错误状态；
- Playwright：核心演示链路、转人工、知识上传和 Run 查看；
- Eval CLI：对固定数据集运行 Mock 或真实模型配置。

### 14.2 评测指标

- 意图分类准确率；
- 工具选择与参数准确率；
- 引用正确率；
- 有证据回答率与无证据拒答率；
- 结构化输出通过率；
- 首 Token 时间、总延迟、P95 和错误率。

README 只展示实际由仓库评测命令生成的结果，并注明模型、数据集版本、日期和运行配置。没有运行结果时使用“尚未测量”，不写预设百分比。

### 14.3 完成标准

- 新环境按 README 可在 10 分钟内启动 Mock 模式；
- 核心演示链路无需外部密钥即可运行；
- 配置真实模型后可完成流式回答与工具调用；
- 所有必做页面和状态可访问；
- 核心自动化测试通过；
- 固定评测集可重复运行并保存结果；
- README 包含架构图、截图/GIF、快速开始、演示脚本、设计权衡和后续路线。

## 15. GitHub 包装

仓库名称：`eino-support-copilot`  
描述：`Production-minded AI support copilot built with Go, Eino, MCP, hybrid RAG, human review, tracing and evaluation.`

Topics：`go`、`eino`、`ai-agent`、`mcp`、`rag`、`nextjs`、`pgvector`、`llm-evaluation`。

README 首屏必须在 20 秒内回答：

1. 这是解决什么问题的产品；
2. 核心业务闭环是什么；
3. 为什么不是普通聊天机器人；
4. 如何立即运行；
5. 可以查看哪些截图、Demo 与评测结果。

仓库完成后设置为 GitHub 主页置顶项目。现有 `mode` 不进入自定义置顶列表；当至少一个新项目被自定义置顶后，主页不再使用当前的自动 Popular repositories 展示作为主视觉。

## 16. 开发节奏

- 第 1–3 天：仓库骨架、数据库、seed、Mock 模式和基础 UI；
- 第 4–7 天：知识索引、混合检索、MCP Server 和工具测试；
- 第 8–11 天：Eino Agent、流式事件、门禁和运行轨迹；
- 第 12–15 天：客服工作台、人工审核、评测 CLI 与看板；
- 第 16–18 天：端到端测试、Docker、README、截图/GIF 和 GitHub 设置；
- 第 19–21 天：缓冲、缺陷修复和面试讲解材料。

若进度落后，优先保留核心演示链路、Mock 模式、运行轨迹和 README；先删除 PDF 上传、Run 重放的高级筛选和评测图表装饰，不削弱可运行性与证据链。
