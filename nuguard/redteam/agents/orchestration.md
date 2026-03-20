# Orchestration Engine

## Responsibilities

- Manage agent lifecycle
- Coordinate multi-step attacks
- Track exploit chains

---

## Execution Model

Directed Acyclic Graph (DAG) of actions

Example:

Prompt Injection
  ↓
Tool Invocation
  ↓
DB Access
  ↓
Exfiltration