"""NestJS hand-rolled service "tool" heuristic — usage-based TOOL detection.

Real-world NestJS apps commonly implement agent tools (code execution,
web search, RAG retrieval, ...) as plain ``@Injectable()`` services with a
``*Service`` suffix, no base class, no decorator beyond ``@Injectable()``,
and no central tools registry — every declaration-shape TOOL adapter
elsewhere in this package (framework Tool classes, ``@tool`` decorators,
OpenAI-style function-schema dicts) misses them entirely.

The only real signal available without full data-flow tracing is *usage
proximity*: such a service is constructor-injected into an "orchestrator"
service that also constructor-injects something that looks like an LLM
client. This adapter stacks two independent, cheap, single-file structural
signals rather than attempting real data-flow analysis (not reliably
doable with the existing regex/AST tooling):

1. The constructor injects a parameter whose type looks like an LLM client
   (a known SDK class from ``llm_clients.py``'s provider registry, or a
   narrow ``AiService``/``LlmClient``-style custom-wrapper naming pattern).
2. A *different* injected parameter's type name contains a short, curated
   action verb (search, execute, retrieve, ...) and does not end in a
   curated infra-suffix (Config, Logger, Cache, ...).

Both signals must hold in the same constructor before a TOOL node is
emitted, keeping false-positive risk low at the cost of missing tools with
unconventional names — deliberately erring toward precision over recall
per the accepted design tradeoff (see docs/sbom-misses.md §3).
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType, PrivilegeScope
from ..base import ComponentDetection
from ._ts_regex import TSFrameworkAdapter
from .llm_clients import _PROVIDERS
from .nestjs_adapter import _CLASS_RE, _find_class_body_span

_NESTJS_PACKAGES = ["@nestjs/common", "@nestjs/core"]

_INJECTABLE_RE = re.compile(r"@Injectable\(\)")
_CONSTRUCTOR_START_RE = re.compile(r"\bconstructor\s*\(")

# Parses one constructor parameter, e.g. "private readonly aiService: AiService"
# or "@Inject(TOKEN) private readonly foo: FooService". Type may carry generics
# / union members after it; we only need the leading type identifier.
_PARAM_RE = re.compile(
    r"(?:@\w+\([^)]*\)\s*)?"  # optional parameter decorator, e.g. @Inject(...)
    r"(?:public|private|protected)?\s*(?:readonly\s+)?"
    r"\w+\s*:\s*([A-Za-z_]\w*)"
)

_LLM_CLIENT_CLASSES = {cls for cfg in _PROVIDERS.values() for cls in cfg["classes"]}
_LLM_WRAPPER_NAME_RE = re.compile(r"\b(Ai|AI|Llm|LLM|Gpt|GPT)(Service|Client)\b")

_ACTION_VERBS = (
    "execute", "search", "retrieve", "lookup", "scan", "sandbox", "query",
    # AI/RAG-tool-domain nouns — narrow and low-collision-risk (unlike a
    # generic word such as "message"), added because real hand-rolled RAG
    # services are commonly named "KnowledgeBaseService"/"RagService" rather
    # than using a verb like "retrieve" in the type name itself.
    "knowledge", "rag", "embedding",
)
_INFRA_SUFFIX_DENYLIST = (
    "Config", "Logger", "Cache", "Mailer", "Queue", "Guard",
    "Interceptor", "Module", "Controller", "Repository", "Database",
    "Auth", "Session",
)

_CONFIDENCE = 0.58

# --- Second, independent firing path: outbound-call evidence -------------
# Catches services one hop removed from AiService (e.g. CodeSandboxService,
# injected into a dedicated controller/service pair rather than directly
# into an LLM-facing class — docs/sbom-fix2.md #3) without requiring the
# `has_llm_sibling` DI-adjacency signal at all: a tool-like-named
# `@Injectable()` service whose own body issues a real outbound HTTP call.
# Higher confidence than the DI-sibling path's default so, when both fire
# for the same component (e.g. Web Search: DI-sibling in the injecting
# file, this path in web-search.service.ts's own file, where the real
# `fetch()` call lives), the real-call-site evidence sorts first.
_CONFIDENCE_CALL_SITE = 0.62

_OUTBOUND_CALL_RE = re.compile(r"\b(?:fetch|axios(?:\.\w+)?|http\.request)\s*\(")
_CONFIG_OR_ENV_URL_RE = re.compile(
    r"\$\{[^}]*[Uu]rl[^}]*\}"  # `${sandboxUrl}/execute`
    r"|configService\.get\("
    r"|process\.env\.\w+"
)
_CODE_EXEC_BODY_HINT_RE = re.compile(r"\b(code|command|script)\b", re.IGNORECASE)


def _is_llm_client_type(type_name: str) -> bool:
    return type_name in _LLM_CLIENT_CLASSES or bool(_LLM_WRAPPER_NAME_RE.search(type_name))


_CAMEL_SPLIT_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _is_tool_like_type(type_name: str) -> bool:
    if any(type_name.endswith(suffix) for suffix in _INFRA_SUFFIX_DENYLIST):
        return False
    # Whole-word match on camelCase-split words — not a raw substring check —
    # so "ResearchGateway" (contains the substring "search" inside "Research")
    # doesn't false-positive against the "search" action verb.
    words = {w.lower() for w in _CAMEL_SPLIT_RE.findall(type_name)}
    return any(verb in words for verb in _ACTION_VERBS)


def _display_name(type_name: str) -> str:
    stripped = re.sub(r"(Service|Client)$", "", type_name) or type_name
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stripped)
    return words.strip() or type_name


def _parse_constructor_params(
    lines: list[str], body_start: int, body_end: int
) -> list[tuple[int, str]] | None:
    """Return ``(line_idx, param_text)`` pairs for the constructor's parameter
    list, or None if not found.

    Parameters keep their own source line (real-world NestJS constructors are
    conventionally formatted one parameter per line — see studyield-app's
    ``research.service.ts``/``chat.service.ts``) rather than all being
    attributed to the constructor's opening line. Collapsing them onto one
    shared line previously made ``core.py``'s ``_dedup_by_location`` pass
    treat two genuinely distinct injected services as duplicate detections
    of "the same source token" and silently drop one at random (tie-broken
    by an unstable set-iteration order across process runs).
    """
    for i in range(body_start, body_end + 1):
        m = _CONSTRUCTOR_START_RE.search(lines[i])
        if not m:
            continue
        depth = lines[i].count("(") - lines[i].count(")")
        collected = [(i, lines[i][m.end():])]
        j = i
        while depth > 0 and j < body_end:
            j += 1
            depth += lines[j].count("(") - lines[j].count(")")
            collected.append((j, lines[j]))
        # Trim the trailing ")" (and anything after, e.g. "{ ... }") on the
        # last collected line, which closed the parameter list.
        last_idx, last_text = collected[-1]
        close_idx = last_text.rfind(")")
        if close_idx != -1:
            collected[-1] = (last_idx, last_text[:close_idx])

        params: list[tuple[int, str]] = []
        for line_idx, text in collected:
            for chunk in text.split(","):
                chunk = chunk.strip()
                if chunk:
                    params.append((line_idx, chunk))
        return params
    return None


class NestJSToolDIAdapter(TSFrameworkAdapter):
    """Detects hand-rolled NestJS service "tools" via constructor-injection usage."""

    name = "nestjs_tool_di"
    priority = 56
    handles_imports = _NESTJS_PACKAGES

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if not content or not content.strip() or "@Injectable" not in content:
            return []

        lines = content.splitlines()
        detected: list[ComponentDetection] = []
        seen_canonical: set[str] = set()

        for idx, line in enumerate(lines):
            if not _INJECTABLE_RE.search(line):
                continue

            class_idx: int | None = None
            for k in range(idx, min(idx + 5, len(lines))):
                if _CLASS_RE.search(lines[k]):
                    class_idx = k
                    break
            if class_idx is None:
                continue

            body_start, body_end = _find_class_body_span(lines, class_idx)
            params = _parse_constructor_params(lines, body_start, body_end)
            if not params:
                continue

            param_types: list[tuple[int, str]] = []
            for line_idx, raw_param in params:
                pm = _PARAM_RE.search(raw_param)
                if pm:
                    param_types.append((line_idx, pm.group(1)))

            has_llm_sibling = any(_is_llm_client_type(t) for _, t in param_types)
            if not has_llm_sibling:
                continue

            for line_no, type_name in param_types:
                if _is_llm_client_type(type_name):
                    continue  # the LLM client itself is not the tool
                if not _is_tool_like_type(type_name):
                    continue

                canonical = canonicalize_text(f"tool:{type_name}")
                if canonical in seen_canonical:
                    continue
                seen_canonical.add(canonical)

                display = _display_name(type_name)
                llm_hint = next(t for _, t in param_types if _is_llm_client_type(t))
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.TOOL,
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=_CONFIDENCE,
                        metadata={
                            "detection_basis": "di_usage_heuristic",
                            "llm_client_hint": llm_hint,
                        },
                        file_path=file_path,
                        line=line_no + 1,
                        snippet=lines[line_no].strip()[:120],
                        evidence_kind="regex",
                    )
                )

        # --- Second, independent pass: outbound-call evidence -------------
        # No `has_llm_sibling` requirement — catches a tool-like-named
        # `@Injectable()` service whose own body issues a real outbound HTTP
        # call, regardless of what (if anything) it injects. See module-level
        # comment on `_CONFIDENCE_CALL_SITE` above.
        for idx, line in enumerate(lines):
            if not _INJECTABLE_RE.search(line):
                continue

            class_idx = None
            for k in range(idx, min(idx + 5, len(lines))):
                cls_m = _CLASS_RE.search(lines[k])
                if cls_m:
                    class_idx = k
                    break
            if class_idx is None:
                continue

            class_name = _CLASS_RE.search(lines[class_idx]).group(1)  # type: ignore[union-attr]
            if not _is_tool_like_type(class_name):
                continue

            body_start, body_end = _find_class_body_span(lines, class_idx)
            call_line: int | None = None
            for j in range(body_start, body_end + 1):
                if _OUTBOUND_CALL_RE.search(lines[j]):
                    call_line = j
                    break
            if call_line is None:
                continue

            canonical = canonicalize_text(f"tool:{class_name}")
            window = "\n".join(lines[call_line : min(call_line + 3, body_end + 1)])
            is_code_exec = bool(_CONFIG_OR_ENV_URL_RE.search(window)) and bool(
                _CODE_EXEC_BODY_HINT_RE.search(window)
            )

            meta: dict[str, Any] = {"detection_basis": "outbound_call_heuristic"}
            if is_code_exec:
                meta["privilege_scope"] = PrivilegeScope.CODE_EXECUTION.value
                meta["high_privilege"] = True

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonical,
                    display_name=_display_name(class_name),
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=_CONFIDENCE_CALL_SITE,
                    metadata=meta,
                    file_path=file_path,
                    line=call_line + 1,
                    snippet=lines[call_line].strip()[:120],
                    evidence_kind="regex",
                )
            )

        return detected
