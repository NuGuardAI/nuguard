# Three-minute demo script

## 0:00–0:30 — Frame the problem

Open the landing page. Explain that a production support Agent needs more than a chat box: it must retrieve policy, inspect live business state, expose safe execution evidence, and stop before sending risky answers.

## 0:30–1:20 — Customer workflow

1. Open **Knowledge** and upload `seed/knowledge/subscription-activation.md`.
2. Open **Customer Portal**.
3. Ask: `My Pro payment succeeded but workspace is still Free`.
4. Point out the visible stages, three read-only tool calls, cited policy excerpt, and generated answer.
5. Show that the P1 guard does not send the draft. Create a Ticket.

## 1:20–2:05 — Human review

1. Open **Agent Workspace**.
2. Select the P1 Ticket.
3. Show intent, confidence, citations, and business facts.
4. Click **批准并发送** and explain the confirmation gate.
5. Confirm approval and show **已解决**.
6. Mention that review, final message, Ticket state, and Run state are one transaction.

## 2:05–2:35 — Observability and replay

Open a Run detail URL. Show ordered steps, durations, safe tool arguments, citations, and error codes. Explain recursive secret redaction and that replay creates a new linked Run without changing history.

## 2:35–3:00 — Evaluation and trade-offs

Open **Agent Evaluation** and run the Mock suite. State clearly that 24/24 is a deterministic contract check, not a model-quality benchmark. Highlight stored dataset hash, commit SHA, mode, timestamp, numerator/denominator metrics, and per-case failures.

## Common interview follow-ups

**Why Go and Eino?**

Go makes concurrency, typed boundaries, and small deployable services straightforward. Eino supplies a composable Agent runtime while the project keeps domain rules and observability outside model prompts.

**Why a separate MCP server?**

It proves protocol-level tool integration and keeps the business boundary independently testable. The tools are intentionally read-only.

**How do you reduce hallucination?**

Hybrid retrieval, typed business facts, citation requirements, output schema validation, confidence/risk gates, and human review for P0/P1 or weak evidence.

**What would change in production?**

Replace demo headers with OIDC, add tenant isolation and rate limiting, use a managed vector store/observability backend as scale requires, and establish real-model evaluation thresholds before automated sending.
