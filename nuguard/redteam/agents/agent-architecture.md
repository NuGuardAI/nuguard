# Attack Agent Architecture

## Design Principles

Inspired by:
- GOAT (multi-step adversarial reasoning)
- RL-based attackers like PISmith
- Autonomous agent systems

Modern research shows adaptive attackers outperform static ones :contentReference[oaicite:0]{index=0}

---

## Agent Types

### 1. Recon Agent
- Reads attack graph
- Identifies weak nodes:
  - open tools
  - high privilege APIs
  - sensitive data stores

---

### 2. Injection Agent
- Generates:
  - prompt injections
  - context poisoning attacks

---

### 3. Tool Abuse Agent
- Attempts:
  - unauthorized tool usage
  - API misuse

---

### 4. Exfiltration Agent
- Extracts:
  - DB data
  - vector data
  - secrets

---

### 5. Persistence Agent
- Maintains foothold:
  - memory poisoning
  - long-term context injection

---

## Agent Communication

Shared state:
- attack graph
- memory store
- exploit progress

---

## Agent Loop

1. Observe system state
2. Decide next action
3. Execute attack
4. Evaluate success
5. Update strategy