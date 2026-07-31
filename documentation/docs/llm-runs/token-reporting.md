# Token Reporting API

Every NuGuard scan that invokes an LLM surfaces token consumption through a uniform
`TokenUsage` model. This document describes the model, where it appears in each scan
type's result object, and how it is embedded in JSON report output.

---

## The `TokenUsage` model

**Python path:** `nuguard.models.TokenUsage`

```python
from nuguard.models import TokenUsage
```

| Field | Type | Description |
|---|---|---|
| `input_tokens` | `int` | Prompt (input) tokens consumed |
| `output_tokens` | `int` | Completion (output) tokens consumed |
| `total_tokens` | `int` | `input_tokens + output_tokens` — computed, read-only |
| `llm_model` | `str \| None` | LiteLLM model string, e.g. `"gemini/gemini-2.0-flash"` |

`TokenUsage` is a Pydantic `BaseModel`. Call `.model_dump()` to serialise it:

```python
usage = TokenUsage(input_tokens=1200, output_tokens=340, llm_model="gemini/gemini-2.0-flash")
usage.total_tokens   # 1540
usage.model_dump()
# {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540, "llm_model": "gemini/gemini-2.0-flash"}
```

Instances support addition for aggregation:

```python
total = usage_a + usage_b   # returns a new TokenUsage
```

---

## SBOM (`nuguard sbom generate`)

Token usage is stored on the **`ScanSummary`** object inside `AiSbomDocument.summary`.

**Python SDK:**

```python
sbom: AiSbomDocument = ...         # result of sbom generate

summary = sbom.summary
summary.input_tokens_used          # int | None — prompt tokens
summary.output_tokens_used         # int | None — completion tokens
summary.tokens_used_for_enrichment # int | None — total (prompt + completion)
summary.llm_model_used             # str | None — model string
```

These fields are populated only when `--llm` is passed (or `llm: true` in config).
`tokens_used_for_enrichment` equals `input_tokens_used + output_tokens_used`.

**JSON output** (`nuguard sbom generate --output json`):

```json
{
  "summary": {
    "input_tokens_used": 4800,
    "output_tokens_used": 1200,
    "tokens_used_for_enrichment": 6000,
    "llm_model_used": "gemini/gemini-2.0-flash"
  }
}
```

> Note: SBOM uses its own `nuguard.sbom.llm_client.SBOMLLMClient` (with a configurable
> `budget_tokens` cap) rather than the shared `LLMClient`. The token tracking API is
> the same but the budget enforcement is SBOM-specific.

---

## Static analysis (`nuguard analyze`)

Token usage is stored on **`AnalysisResult.token_usage`**.

**Python SDK:**

```python
from nuguard.analysis.static_analyzer import StaticAnalyzer

analyzer = StaticAnalyzer(sbom=sbom, llm_client=llm)
result: AnalysisResult = await analyzer.run()

result.token_usage.input_tokens    # int
result.token_usage.output_tokens   # int
result.token_usage.total_tokens    # int (computed)
result.token_usage.llm_model       # str | None
```

Tokens are accumulated from each analysis plugin that makes LLM calls. Currently the
`ATLASAnnotatorPlugin` is the only LLM-enriched plugin; its per-run counts are summed
into `StaticAnalyzer.token_usage` and written to the result.

**JSON output** (`nuguard analyze --output json`):

```json
{
  "token_usage": {
    "input_tokens": 2400,
    "output_tokens": 610,
    "total_tokens": 3010,
    "llm_model": "gemini/gemini-2.0-flash"
  }
}
```

---

## Policy check (`nuguard policy check`)

Token usage is stored on **`PolicyCheckResult.token_usage`**.

**Python SDK:**

```python
from nuguard.policy.checker import PolicyChecker, PolicyCheckResult

checker = PolicyChecker(policy=policy, sbom=sbom)
result: PolicyCheckResult = checker.check()

result.token_usage.input_tokens    # int
result.token_usage.output_tokens   # int
result.token_usage.total_tokens    # int (computed)
result.token_usage.llm_model       # str | None

# Backward-compat flat properties (delegates to token_usage):
result.input_tokens_used           # int
result.output_tokens_used          # int
```

The policy checker is currently static (no LLM calls), so all token counts are `0`
unless extended. The `token_usage` field is present and ready for future LLM enrichment.

**JSON output** (`nuguard policy check --output json`):

```json
{
  "token_usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "llm_model": null
  }
}
```

---

## Behavior analysis (`nuguard behavior`)

Token usage is stored on **`BehaviorAnalysisResult.token_usage`**.

**Python SDK:**

```python
from nuguard.behavior.analyzer import BehaviorAnalyzer

analyzer = BehaviorAnalyzer(config=cfg, sbom=sbom, policy=policy, llm_client=llm)
result: BehaviorAnalysisResult = await analyzer.analyze()

result.token_usage.input_tokens    # int — total across dynamic runner + LLM calls
result.token_usage.output_tokens   # int
result.token_usage.total_tokens    # int (computed)
result.token_usage.llm_model       # str | None
```

The reported count aggregates tokens from two sources:

| Source | What it covers |
|---|---|
| `BehaviorRunner` (dynamic) | LLM judge calls, scenario-level evaluations |
| Analyzer-level LLM | Remediation synthesis, executive summary generation |

**JSON output** (`nuguard behavior --output json`):

```json
{
  "token_usage": {
    "input_tokens": 8300,
    "output_tokens": 2100,
    "total_tokens": 10400,
    "llm_model": "gemini/gemini-2.0-flash"
  },
  "input_tokens_used": 8300,
  "output_tokens_used": 2100
}
```

The flat `input_tokens_used` / `output_tokens_used` keys are kept for backward
compatibility. Prefer `token_usage` for new consumers.

---

## Red-team (`nuguard redteam`)

Token usage is available on **`RedteamOrchestrator.token_usage`** (a computed property).

**Python SDK:**

```python
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator

orchestrator = RedteamOrchestrator(sbom=sbom, config=cfg, ...)
await orchestrator.run()

orchestrator.token_usage.input_tokens    # int — total across all attack scenarios
orchestrator.token_usage.output_tokens   # int
orchestrator.token_usage.total_tokens    # int (computed)
orchestrator.token_usage.llm_model       # str | None

# Backward-compat flat attributes:
orchestrator.input_tokens_used           # int
orchestrator.output_tokens_used          # int
```

The count covers all LLM calls made during scenario generation, attack execution, LLM
response evaluation, adaptive mutation, and remediation synthesis.

**JSON output** (`nuguard redteam --output json`):

```json
{
  "token_usage": {
    "input_tokens": 15400,
    "output_tokens": 4800,
    "total_tokens": 20200,
    "llm_model": "gemini/gemini-2.0-flash"
  },
  "input_tokens_used": 15400,
  "output_tokens_used": 4800
}
```

---

## Summary table

| Scan type | Result object | `token_usage` field | JSON key |
|---|---|---|---|
| SBOM | `AiSbomDocument.summary` | flat fields (`input_tokens_used`, `output_tokens_used`, `tokens_used_for_enrichment`) | `summary.input_tokens_used` |
| Analyze | `AnalysisResult` | `token_usage: TokenUsage` | `token_usage` |
| Policy | `PolicyCheckResult` | `token_usage: TokenUsage` | `token_usage` |
| Behavior | `BehaviorAnalysisResult` | `token_usage: TokenUsage` | `token_usage` |
| Redteam | `RedteamOrchestrator` | `.token_usage` (property) | `token_usage` |

SBOM predates the unified `TokenUsage` model and uses flat fields directly on
`ScanSummary`. All other scan types use `TokenUsage`.

---

## Importing `TokenUsage` for third-party use

```python
from nuguard.models import TokenUsage          # canonical import
from nuguard.models.token_usage import TokenUsage  # also valid
```

The model is exported from `nuguard.models.__init__` alongside
`CredentialCheckResult` and `TargetHealthReport`.
