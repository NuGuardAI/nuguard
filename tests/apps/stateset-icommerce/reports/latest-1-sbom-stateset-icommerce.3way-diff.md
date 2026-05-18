# SBOM 3-Way Diff: stateset-icommerce

**Generated:** 2026-04-08  
**Target app:** `../stateset-icommerce`  
**Tool:** `nuguard sbom generate`

| Run | Config | Model | Nodes | Edges | Report |
|-----|--------|-------|------:|------:|--------|
| **A** no-llm  | `nuguard_no_llm.yaml`   | _(deterministic)_        | 68 | 72 | `latest-1-sbom-stateset-icommerce.no-llm.json` |
| **B** openai  | `nuguard_openai.yaml`   | `openai/gpt-4.1-mini`    | 69 | 72 | `latest-1-sbom-stateset-icommerce.with-llm.openai.json` |
| **C** azure   | `nuguard_azure.yaml`    | `azure/gpt-4.1-mini`     | 69 | 72 | `latest-1-sbom-stateset-icommerce.with-llm.azure.json` |

---

## 1. Node Count Summary

| Component Type  | A no-llm | B openai | C azure | Notes |
|-----------------|:--------:|:--------:|:-------:|-------|
| API_ENDPOINT    | 1 | 1 | 1 | — |
| AUTH            | 1 | 1 | 1 | — |
| CONTAINER_IMAGE | 6 | 6 | 6 | — |
| DATASTORE       | 3 | 3 | 3 | — |
| DEPLOYMENT      | 11 | 11 | 11 | — |
| FRAMEWORK       | 1 | 1 | 1 | — |
| **MODEL**       | **16** | **17** | **17** | ⬆ LLM adds 1 model |
| PRIVILEGE       | 6 | 6 | 6 | — |
| PROMPT          | 20 | 20 | 20 | — |
| TOOL            | 3 | 3 | 3 | — |
| **TOTAL**       | **68** | **69** | **69** | — |

Edge count is identical across all three runs: **72 edges**.

---

## 2. MODEL Node Differences

The only node-count difference across runs is in the `MODEL` type. LLM enrichment resolves ambiguous model name aliases and surfaces additional low-confidence detections.

| Model Name | A no-llm | B openai | C azure | Notes |
|------------|:--------:|:--------:|:-------:|-------|
| `claude-3` | ✓ | ✓ | ✓ | — |
| `claude-3-5-haiku-20241022` | ✓ | ✓ | ✓ | — |
| `claude-3-5-sonnet-20241022` | ✓ | ✓ | ✓ | — |
| `claude-haiku-3-5` | ✓ | — | — | **Renamed** → `claude-haiku-3-5-20241022` by LLM |
| `claude-haiku-3-5-20241022` | — | ✓ | ✓ | ⬆ LLM canonical form (evidence=21, regex_conf=0.95) |
| `claude-opus-4-5` | — | ✓ | ✓ | ⬆ **New** discovery by LLM (evidence=2, regex_conf=0.55) |
| `claude-opus-4-5-20251101` | ✓ | — | — | **Renamed** → `claude-opus-4-5` by LLM |
| `claude-sonnet-4` | ✓ | ✓ | ✓ | — |
| `claude-sonnet-4-20250514` | ✓ | ✓ | ✓ | — |
| `codellama` | ✓ | ✓ | ✓ | — |
| `command-registry.js` | ✓ | ✓ | ✓ | — |
| `gemini-1.5` | ✓ | ✓ | ✓ | — |
| `gpt-4o-2024-01` | ✓ | ✓ | ✓ | — |
| `gpt-4o-mini` | ✓ | ✓ | ✓ | — |
| `gpt-5-ultra` | ✓ | ✓ | ✓ | — |
| `llama3:latest` | ✓ | ✓ | ✓ | — |
| `o1-mini` | — | ✓ | ✓ | ⬆ **New** discovery by LLM (evidence=44, regex_conf=0.95) |
| `o1-model` | ✓ | ✓ | ✓ | — |
| `o2` | ✓ | ✓ | ✓ | — |

**Net effect (no-llm → LLM):**
- 2 models renamed to canonical versioned forms (`claude-haiku-3-5` → `claude-haiku-3-5-20241022`, `claude-opus-4-5-20251101` → `claude-opus-4-5`)
- 3 models newly surfaced: `claude-haiku-3-5-20241022`, `claude-opus-4-5`, `o1-mini`
- Net +1 node (3 new, 2 existing replaced)

**OpenAI vs Azure (B vs C):** Identical MODEL set — both providers produce the same 17 models with the same canonical names.

---

## 3. TOOL Node Enrichment

No-LLM produces TOOL nodes with no natural-language descriptions. Both LLM runs add a `description` field to all 3 tool nodes. Descriptions are **only added to TOOL nodes** — MODEL, PROMPT, DEPLOYMENT, and all other node types receive no `description` field in any run.

| Node type enriched | A no-llm | B openai | C azure |
|--------------------|:--------:|:--------:|:-------:|
| Nodes with `description` | 0 / 68 | **3 / 69** | **3 / 69** |
| Node types with descriptions | — | TOOL only | TOOL only |
| NODE types without descriptions | all | MODEL, PROMPT, DEPLOYMENT, AUTH, DATASTORE, FRAMEWORK, CONTAINER_IMAGE, PRIVILEGE, API_ENDPOINT | same |

### `stateset-commerce`

| Run | Description |
|-----|-------------|
| **A no-llm** | _(no description)_ |
| **B openai** | Stateset-commerce is a tool designed to manage and optimize e-commerce operations, facilitating seamless integration, transactions, and order management within commerce platforms. |
| **C azure** | Stateset-commerce is a tool designed to manage and process e-commerce transactions, enabling seamless integration and handling of commerce operations. |

> Minor wording difference: OpenAI says "manage and **optimize**… facilitating seamless integration, transactions, and order management"; Azure says "manage and **process**… enabling seamless integration and handling".

### `browser_automation`

| Run | Description |
|-----|-------------|
| **A no-llm** | _(no description)_ |
| **B openai** | Enables automated interaction with web browsers to perform tasks such as navigation, data extraction, and form submission. |
| **C azure** | Enables automated interaction with web browsers to perform tasks such as navigation, data extraction, and form submission. |

> **Identical** between OpenAI and Azure.

### `workspace_connector`

| Run | Description |
|-----|-------------|
| **A no-llm** | _(no description)_ |
| **B openai** | The workspace_connector tool integrates and synchronizes data between different workspaces, enabling seamless collaboration and data sharing across teams. |
| **C azure** | The workspace_connector tool integrates and synchronizes data between different work environments, enabling seamless collaboration and data sharing across teams. |

> Minor wording difference: OpenAI says "different **workspaces**"; Azure says "different **work environments**".

---

## 4. Summary / `use_case` Field

The top-level `summary.use_case` string is generated differently across runs.

| Run | `use_case` |
|-----|------------|
| **A no-llm** | _"This application implements an agentic AI workflow with 0 agent(s), 3 tool integration(s), and 0 guardrail control(s). Detected use cases include general agentic task orchestration. Multi-modal support: Voice supported, Images supported, Video not supported."_ |
| **B openai** | _"This AI application facilitates general agentic task orchestration without autonomous agents, integrating three external tools to support its workflows. It serves users requiring multi-modal interactions including text, voice, and images."_ |
| **C azure** | _"This AI application facilitates general agentic task orchestration without employing autonomous agents, integrating three external tools to support its workflows. It serves users requiring multi-modal interactions including text, voice, and images."_ |

**A vs B/C:** No-LLM uses a template formula (`X agent(s), Y tool integration(s)`). LLM runs produce a natural-language paragraph conveying the same facts.

**B vs C:** Nearly identical. Azure adds "**employing**" (`without employing autonomous agents` vs `without autonomous agents`).

**Identical across all three:** `frameworks`, `deployment_platforms` (`["AWS", "GitHub Actions", "Azure"]`), `modalities` (`["TEXT", "VOICE", "IMAGE"]`).

---

## 5. Key Findings

| Area | A no-llm | B openai | C azure |
|------|----------|----------|---------|
| NODE COUNT | 68 | 69 | 69 |
| MODEL count | 16 | 17 | 17 |
| Nodes with `description` field | 0 / 68 | 3 / 69 (TOOL only) | 3 / 69 (TOOL only) |
| Model name normalization | alias forms | canonical versioned names | canonical versioned names |
| New model discoveries vs no-llm | — | `o1-mini`, `claude-opus-4-5` | `o1-mini`, `claude-opus-4-5` |
| Summary style | template string | natural language | natural language |
| Edge count | 72 | 72 | 72 |
| OpenAI vs Azure agreement | — | identical node set | identical node set |

**Overall:** LLM enrichment (OpenAI or Azure) adds one net model node, normalizes model names to canonical versioned forms, and adds natural-language descriptions to all TOOL nodes and the summary use-case. The two LLM providers (`openai/gpt-4.1-mini` and `azure/gpt-4.1-mini`) produce nearly identical SBOMs — differences are purely minor wording variations in 2 of 3 tool descriptions and the use_case sentence.
