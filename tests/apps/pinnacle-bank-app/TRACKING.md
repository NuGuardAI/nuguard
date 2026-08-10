# FinTech GOAT — Azure Monitor KQL Tracking Queries

This file documents the Application Insights / Log Analytics KQL queries used
to detect and investigate the deliberate vulnerabilities in the FinTech GOAT
application.  Use these with NuGuard's static `analyze` command output or
paste them directly into the Azure Portal Log Analytics workspace.

---

## Query 01 — Unauthorized Fund Transfers (VULN-01)

Detects all `transfer_funds` tool executions.  Because there is no ownership
validation, **any** execution is potentially unauthorized.

```kql
customEvents
| where name == "MCPToolExecutionAttempt"
| extend tool_name        = tostring(customDimensions["tool_name"])
| extend raw_args         = parse_json(tostring(customDimensions["tool_args"]))
| extend amount           = todouble(raw_args["amount"])
| extend target_account   = tostring(raw_args["target_account_id"])
| extend result_snippet   = tostring(customDimensions["result_snippet"])
| where tool_name == "transfer_funds"
| project
    timestamp,
    session_id    = tostring(customDimensions["session_id"]),
    amount,
    target_account,
    task_id       = extract('"task_id":\\s*"([^"]+)"', 1, result_snippet),
    client_ip     = client_IP
| order by timestamp desc
```

**Expected findings:**
- Transfers from `ACCT-GLOBAL-POOL` regardless of which account the user selected
- Multiple accounts debited in a single session (session_id grouping)
- Transfers to external / suspicious account IDs injected via chat

---

## Query 02 — SSRF Probe Attempts (VULN-02)

Detects `fetch_market_report` calls targeting private IP space, Azure IMDS,
or localhost — classic indicators of SSRF exploitation.

```kql
customEvents
| where name == "MCPToolExecutionAttempt"
| extend tool_name = tostring(customDimensions["tool_name"])
| extend raw_args  = parse_json(tostring(customDimensions["tool_args"]))
| extend url       = tostring(raw_args["url"])
| where tool_name == "fetch_market_report"
| extend is_ssrf = url matches regex
    @"(169\.254\.|10\.\d+\.\d+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|localhost|127\.0\.0\.1|::1|metadata\.azure\.com|management\.azure\.com|storage\.azure\.com)"
| where is_ssrf == true
| project
    timestamp,
    session_id  = tostring(customDimensions["session_id"]),
    suspect_url = url,
    status_code = extract('"status_code":\\s*(\\d+)', 1, tostring(customDimensions["result_snippet"])),
    client_ip   = client_IP
| order by timestamp desc
```

**Expected findings:**
- Calls to `http://169.254.169.254/metadata/instance` (Azure IMDS)
- HTTP 200 responses — confirming successful SSRF data exfiltration
- Internal `10.x.x.x` or `mcp-banking-server.internal` probe URLs

---

## Query 03 — Agent Routing Decisions (VULN-06)

Surfaces all triage routing decisions. Because `user_message` is logged raw,
this query can reveal injected payloads, PII, and conversation fragments.

```kql
customEvents
| where name == "AgentRoutingDecision"
| extend from_agent       = tostring(customDimensions["from_agent"])
| extend to_agent         = tostring(customDimensions["to_agent"])
| extend classified_intent = tostring(customDimensions["classified_intent"])
| extend user_message     = tostring(customDimensions["user_message"])
| extend session_id       = tostring(customDimensions["session_id"])
| project
    timestamp,
    session_id,
    from_agent,
    to_agent,
    classified_intent,
    user_message,          // ← full user input — may contain PII / injected payloads
    client_ip = client_IP
| order by timestamp desc
```

**NuGuard finding:** This query alone exposes VULN-06 (Sensitive Data in Logs).
The `user_message` column contains unredacted user inputs that could include:
account numbers, transfer targets, or attacker-injected instructions.

---

## Query 04 — Secret Extraction Attempts (VULN-04)

Detects conversations where the model was asked to repeat its system prompt
or disclose internal credentials.

```kql
customEvents
| where name == "AgentRoutingDecision"
| extend user_message = tostring(customDimensions["user_message"])
| where user_message matches regex
    @"(?i)(system prompt|instructions|api[_ -]?key|master key|token|password|secret|db uri|database|internal config|repeat your|ignore previous|forget previous)"
| project
    timestamp,
    session_id    = tostring(customDimensions["session_id"]),
    user_message,
    classified_intent = tostring(customDimensions["classified_intent"]),
    client_ip = client_IP
| order by timestamp desc
```

---

## Query 05 — High-Value Transfer Sessions

Identify sessions with large or repeated financial transfers (potential fraud).

```kql
customEvents
| where name == "MCPToolExecutionAttempt"
| extend tool_name = tostring(customDimensions["tool_name"])
| extend raw_args  = parse_json(tostring(customDimensions["tool_args"]))
| where tool_name == "transfer_funds"
| extend amount     = todouble(raw_args["amount"])
| extend session_id = tostring(customDimensions["session_id"])
| summarize
    transfer_count = count(),
    total_amount   = sum(amount),
    max_single     = max(amount),
    targets        = make_set(tostring(raw_args["target_account_id"]))
    by session_id
| where transfer_count > 2 or total_amount > 5000
| order by total_amount desc
```

---

## Query 06 — End-to-End Attack Chain Reconstruction

Join routing decisions with tool calls to reconstruct complete attack sessions.

```kql
let routing = customEvents
    | where name == "AgentRoutingDecision"
    | extend session_id = tostring(customDimensions["session_id"])
    | extend user_message = tostring(customDimensions["user_message"])
    | extend intent = tostring(customDimensions["classified_intent"])
    | project timestamp, session_id, user_message, intent, event = "routing";

let tool_calls = customEvents
    | where name == "MCPToolExecutionAttempt"
    | extend session_id = tostring(customDimensions["session_id"])
    | extend tool_name = tostring(customDimensions["tool_name"])
    | extend tool_args = tostring(customDimensions["tool_args"])
    | project timestamp, session_id, tool_name, tool_args, event = "tool_call";

routing
| join kind=leftouter tool_calls on session_id
| project
    timestamp,
    session_id,
    user_message,
    intent,
    tool_name,
    tool_args
| order by session_id asc, timestamp asc
```

---

## Query 07 — VULN-AI-08: Prompt-Injected Fund Transfer via Nova Agent

Reconstructs end-to-end attack chains where a chat session resulted in an injected
`transfer_funds` call — correlating the original user message with the Celery task dispatch.

```kql
let routing = customEvents
    | where name == "AgentRoutingDecision"
    | extend session_id   = tostring(customDimensions["session_id"])
    | extend user_message = tostring(customDimensions["user_message"])
    | extend intent       = tostring(customDimensions["classified_intent"])
    | where intent == "FUND_TRANSFER"
    | project timestamp, session_id, user_message, intent;

let transfers = customEvents
    | where name == "MCPToolExecutionAttempt"
    | extend session_id      = tostring(customDimensions["session_id"])
    | extend tool_name       = tostring(customDimensions["tool_name"])
    | extend raw_args        = parse_json(tostring(customDimensions["tool_args"]))
    | extend target_account  = tostring(raw_args["target_account_id"])
    | extend amount          = todouble(raw_args["amount"])
    | where tool_name == "transfer_funds"
    | project timestamp, session_id, tool_name, target_account, amount;

routing
| join kind=inner transfers on session_id
| extend suspicious = user_message matches regex
    @"(?i)(ignore|disregard|forget|override|bypass|admin|developer mode|no restriction)"
| project
    timestamp,
    session_id,
    user_message,
    target_account,
    amount,
    suspicious,
    client_ip = client_IP
| order by timestamp desc
```

**Expected findings:**
- Sessions where user_message contains injection keywords that also triggered `transfer_funds`
- Transfers to accounts not associated with the `user_id` in the request body
- High-value transfers (amount > 1000) from `ACCT-GLOBAL-POOL`

---

## Query 08 — VULN-AI-09: SSRF via `fetch_market_report` through WealthManager

Detects WealthManager-routed sessions where `fetch_market_report` was called with
private IP, IMDS, or internal service URLs — confirming the AI-to-SSRF chain.

```kql
customEvents
| where name == "MCPToolExecutionAttempt"
| extend tool_name = tostring(customDimensions["tool_name"])
| extend raw_args  = parse_json(tostring(customDimensions["tool_args"]))
| extend url       = tostring(raw_args["url"])
| where tool_name == "fetch_market_report"
| extend is_internal = url matches regex
    @"(169\.254\.|10\.\d+\.\d+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|localhost|127\.0\.0\.1|::1|metadata\.azure\.com|management\.azure\.com|mcp-|agent-orchestrator|frontend-ui)"
| project
    timestamp,
    session_id  = tostring(customDimensions["session_id"]),
    suspect_url = url,
    is_internal,
    result_snippet = tostring(customDimensions["result_snippet"]),
    client_ip   = client_IP
| order by timestamp desc
```

**Expected findings:**
- `http://169.254.169.254/metadata/instance` calls (Azure IMDS)
- Calls to internal `mcp-*` service URLs that expose secondary data
- HTTP 200 responses with managed identity tokens or internal API payloads

---

## NuGuard SBOM Findings Cross-Reference

| SBOM Vulnerability | CVE / Advisory | Package | KQL Query |
|---|---|---|---|
| Celery insecure deserialization | CVE-2021-23727 | `celery==3.1.26` | Query 06 (task_id in transfer chain) |
| urllib3 ReDoS | CVE-2021-33503 | `urllib3==1.25.11` | Query 02 (SSRF URLs) |
| LangChain prompt injection | CVE-2023-29374 | `langchain==0.0.300` | Query 04 (secret extraction) |
| Secrets in system prompt | OWASP ASI-02 | agents.py | Query 04 |
| Broken access control | OWASP ASI-03 | server.py | Query 01 |
| SSRF via tool | OWASP ASI-05 | server.py | Query 02 |
| Sensitive data in logs | OWASP ASI-08 | telemetry.py | Query 03 |
| Prompt injection → fund transfer (AI chain) | OWASP LLM01 + LLM08 | agents.py + server.py | Query 07 |
| Prompt injection → SSRF via market report (AI chain) | OWASP LLM01 + API7 | agents.py + server.py | Query 08 |
