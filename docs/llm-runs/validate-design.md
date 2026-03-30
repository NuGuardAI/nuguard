# LLM Validate Test Runner — Design

## Goal

Exercise every declared agent and tool across a running AI application by sending
realistic prompt messages through its API endpoint. The validate step confirms
**capability coverage** (every SBOM agent and tool was invoked at least once) and
**policy compliance** (responses respect the cognitive policy). Security and PHI
scenarios are handled by the redteam module — see TODOs below.

---

## What Validate Does

```
nuguard validate --config ./nuguard.yaml
        │
        ├── 1. Auth bootstrap   — verify target is reachable, credentials work
        ├── 2. Endpoint probe   — discover chat endpoint if not set in config
        ├── 3. Run scenarios    — POST messages, collect responses + tool_calls
        ├── 4. Evaluate         — check policy compliance and refusal assertions
        └── 5. Report           — capability map, findings, coverage %
```

---

## Auth and Endpoint Configuration

Every scenario sends an HTTP POST to the target with the same auth and payload
shape. These are set once in `nuguard.yaml`:

```yaml
validate:
  target: http://127.0.0.1:3001/
  target_endpoint: /run_langgraph      # omit to auto-discover from SBOM

  # Payload shape — wraps each message before sending
  chat_payload_key: phrases            # JSON key that holds the message text
  chat_payload_list: true              # if true, wraps value in a list: {"phrases": ["..."]}
  chat_response_key: prognosis         # JSON key to extract response text from reply

  # Auth — one of: bearer | api_key | basic | none
  auth:
    type: basic
    username: ${APP_USERNAME}          # resolved from environment / .env
    password: ${APP_PASSWORD}
```

**Auth types supported:**

| Type | Config keys | Header sent |
|---|---|---|
| `bearer` | `header: "Authorization: Bearer ${TOKEN}"` | `Authorization: Bearer <token>` |
| `api_key` | `header: "X-API-Key: ${KEY}"` | `X-API-Key: <key>` |
| `basic` | `username`, `password` | `Authorization: Basic <b64(user:pass)>` |
| `none` | — | (no auth header) |

All credential values use `${ENV_VAR}` interpolation — never hardcode secrets.

### Static auth vs. login-flow auth

**Static auth**: credentials are resolved from config/env once at startup and
injected as a fixed header on every request. Works when the app accepts HTTP
Basic directly, or when a long-lived API key or pre-obtained Bearer token is
available.

**Login-flow auth** (`type: login_flow`): for apps like healthcare-voice-agent
that issue short-lived JWTs from a `POST /login` endpoint. `AuthSession`
(in `nuguard/common/auth.py`) calls the login endpoint during bootstrap,
extracts the token, and attaches it as a Bearer header on every subsequent
request. On a 401 mid-run it re-executes the login automatically
(`refresh_on_401: true`).

```yaml
validate:
  auth:
    type: login_flow
    login_flow:
      endpoint: /login
      payload:
        username: ${APP_USERNAME}
        password: ${APP_PASSWORD}
      token_response_key: access_token       # dot-notation supported: e.g. data.token
      token_header: "Authorization: Bearer"  # header prefix for the acquired token
      refresh_on_401: true
```

Both validate and redteam share the same `AuthSession` — it is created once
by `AuthBootstrapper.run()` and exposed via `bootstrapper.session`. Runners
call `session.headers()` on every outbound request, and `session.refresh_if_needed()`
on any 401 response.

**Endpoint auto-discovery:** if `target_endpoint` is omitted, the runner inspects
the SBOM for `API_ENDPOINT` nodes with method `POST`, scores them by chat-path
likelihood (paths containing `run`, `chat`, `langgraph`, `agent`, etc.), and probes
each candidate with a connectivity test message until one responds with a valid
JSON body.

---

## Payload Construction

For each message turn, the runner builds the POST body from the config:

```
chat_payload_key=phrases, chat_payload_list=true, message="Hello"
  → POST body: {"phrases": ["Hello"]}

chat_payload_key=message, chat_payload_list=false, message="Hello"
  → POST body: {"message": "Hello"}
```

The response is parsed with `chat_response_key` to extract the text:

```
chat_response_key=prognosis
  → response.json()["prognosis"]
```

Multi-turn scenarios send one POST per message. The session / conversation ID
from the previous response is forwarded in subsequent requests if the app returns
one (key detected by heuristic: `session_id`, `conversation_id`, `thread_id`).

---

## Scenario Types

| Type | Purpose |
|---|---|
| `happy_path` | Multi-turn realistic conversation; confirms the app responds coherently end-to-end |
| `capability_probe` | Single targeted prompt designed to invoke one specific agent or tool |
| `agent_routing` | Multi-turn conversation that must reach a named specialist agent |
| `policy_compliance` | Response checked against a cognitive policy clause (non-security) |
| `boundary_assertion` | App-level behavioral check — confirms the app declines clearly out-of-scope requests |

> **Security and PHI scenarios (prompt injection, jailbreaks, cross-patient data
> access, system prompt extraction) are NOT in validate.** They belong in the
> redteam module which has purpose-built attack generators and impact scoring.
> See [TODO: Redteam coverage](#todo-redteam-coverage) below.

---

## Scenario Schema

```yaml
- id: <APP>-<TYPE>-<NNN>
  name: <human readable>
  type: happy_path | capability_probe | agent_routing | policy_compliance | boundary_assertion
  priority: critical | high | medium | low
  description: <what is being tested>

  target_agents: []        # SBOM AGENT node names expected to activate
  target_tools:  []        # SBOM TOOL node names expected to be called
  target_endpoint: <path>  # per-scenario endpoint override (optional)

  messages:
    - "<turn 1>"
    - "<turn 2>"           # multi-turn: sent sequentially, each awaiting a response

  expect:
    response_contains: []  # strings that must appear in the response
    response_excludes: []  # strings that must NOT appear
    tools_called: []       # tool names that must appear in the tool_calls list
    refused: false         # true = expect the app to decline the request
    policy_clause: ""      # the cognitive policy clause being checked
```

---

## Coverage Map

After all scenarios run, the runner produces a `CapabilityMap`:

| Status | Meaning |
|---|---|
| **Exercised** | Agent/tool name appeared in `tool_calls` of a passing scenario |
| **Targeted** | Scenario declared it in `target_agents`/`target_tools` but it was never observed in `tool_calls` → `CAPABILITY_GAP` finding |
| **Uncovered** | No scenario in the catalog names this agent/tool at all |

Every SBOM `AGENT` and `TOOL` node should reach **Exercised** status on a full run.
Uncovered nodes are reported as gaps but do not block CI by default.

---

## TODO: Redteam Coverage

The following scenario categories from the catalog are **intentionally omitted**
from validate. Confirm implementation in the redteam module before closing:

| Category | Redteam scenario type |
|---|---|
| Cross-patient PHI access | `data-exfiltration` → cross-tenant |
| System prompt / credential extraction | `prompt-injection` → system-prompt extraction |
| Jailbreak / goal override | `prompt-injection` → jailbreak |
| Bulk export without auth | `privilege-escalation` → IDOR / unauthorized action |
| Drug interaction alert override | `policy-violation` → restricted action |
| Emergency escalation bypass | `policy-violation` → restricted action |
| Wildcard CORS / HITL bypass | `tool-abuse` |
| PHI in raw tool output | `data-exfiltration` → RAG poisoning / output leakage |

Scenarios AHA-BOUND-001 through AHA-BOUND-008 (agentic-healthcare) and
HVA-BOUND-001 through HVA-BOUND-006 (healthcare-voice-agent) in the catalog
docs are tagged for redteam, not validate.

---

## File Structure

```
docs/llm-runs/
├── validate-design.md                 ← this file
├── agentic-healthcare-ai.md           ← scenario catalog
└── healthcare-voice-agent.md          ← scenario catalog

tests/agentic-healthcare-ai/
└── nuguard.yaml                       ← validate config (auth, endpoint, payload shape)

tests/healthcare-voice-agent/
└── nuguard.yaml                       ← validate config
```
