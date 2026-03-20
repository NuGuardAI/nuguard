"""Generic/fallback adapters using regex pattern matching.

These adapters run on all files (Python and TypeScript) as a fallback after
AST-based adapters.  They produce lower-confidence nodes (0.5-0.6) which the
deduplication layer in core.py will merge with higher-confidence AST findings.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from nuguard.models.sbom import (
    DatastoreType,
    Edge,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
    PrivilegeScope,
)


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _evidence(
    kind: EvidenceKind,
    confidence: float,
    detail: str,
    path: Path,
    line: int | None = None,
) -> Evidence:
    return Evidence(
        kind=kind,
        confidence=confidence,
        detail=detail,
        location=EvidenceLocation(path=str(path), line=line),
    )


class ModelGenericAdapter:
    """Regex-based model name detection as fallback."""

    _PATTERNS = [
        r"\bgpt-4[o\-][\w.-]*\b",           # gpt-4o, gpt-4o-mini, gpt-4-turbo
        r"\bgpt-3\.5[\w.-]*\b",
        r"\bclaude-3[-.][\w.-]+\b",          # claude-3-5-sonnet, claude-3-opus
        r"\bclaude-4[-.][\w.-]+\b",
        r"\bgemini[-/][\w.-]+\b",            # gemini-2.0-flash, gemini/gemini-pro
        r"\bllama[-/]\d[\w.-]*\b",           # llama-3.1, llama/llama-3
        r"\bmistral[-/][\w.-]+\b",
        r"\bdeepseek[-/][\w.-]+\b",
        r"\bphi[-/]\d[\w.-]*\b",
        r"\bqwen[\d][\w.-]*\b",
        r"\b[\w-]+/[\w.-]+-\d+[Bb]\b",      # HuggingFace org/model-7B format
        r"\bollama:[:\w.-]+\b",              # ollama:llama3.2
    ]

    def __init__(self) -> None:
        self._compiled = [re.compile(p) for p in self._PATTERNS]

    def can_handle(self, source: str) -> bool:
        return True

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()
        for pat in self._compiled:
            for m in pat.finditer(source):
                model_name = m.group(0)
                if model_name in seen:
                    continue
                seen.add(model_name)
                nid = _stable_id(model_name, NodeType.MODEL)
                nodes.append(
                    Node(
                        id=nid,
                        name=model_name,
                        component_type=NodeType.MODEL,
                        confidence=0.55,
                        metadata=NodeMetadata(model_name=model_name),
                        evidence=[
                            _evidence(
                                EvidenceKind.REGEX,
                                0.55,
                                f"Model name pattern match: {model_name!r}",
                                file_path,
                            )
                        ],
                    )
                )
        return nodes, []


class DatastoreGenericAdapter:
    """Regex-based datastore detection as fallback."""

    _RELATIONAL = [
        "postgres", "postgresql", "mysql", "sqlite", "mssql", "oracle", "supabase"
    ]
    _VECTOR = [
        "pinecone", "weaviate", "qdrant", "milvus", "chroma", "faiss", "pgvector", "opensearch"
    ]
    _KV = [
        "redis", "memcached", "dynamodb", "firestore", "mongodb", "cosmosdb"
    ]
    _KB = [
        "elasticsearch", "solr", "neo4j", "arangodb"
    ]

    def can_handle(self, source: str) -> bool:
        return True

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()
        source_lower = source.lower()

        def _add(name: str, ds_type: DatastoreType) -> None:
            if name in seen:
                return
            seen.add(name)
            nid = _stable_id(name, NodeType.DATASTORE)
            nodes.append(
                Node(
                    id=nid,
                    name=name,
                    component_type=NodeType.DATASTORE,
                    confidence=0.5,
                    metadata=NodeMetadata(datastore_type=ds_type),
                    evidence=[
                        _evidence(
                            EvidenceKind.REGEX,
                            0.5,
                            f"Datastore keyword match: {name!r}",
                            file_path,
                        )
                    ],
                )
            )

        for kw in self._RELATIONAL:
            if kw in source_lower:
                _add(kw, DatastoreType.RELATIONAL)
        for kw in self._VECTOR:
            if kw in source_lower:
                _add(kw, DatastoreType.VECTOR)
        for kw in self._KV:
            if kw in source_lower:
                _add(kw, DatastoreType.KV)
        for kw in self._KB:
            if kw in source_lower:
                _add(kw, DatastoreType.KNOWLEDGE_BASE)

        return nodes, []


class AuthGenericAdapter:
    """Regex-based auth detection."""

    _PATTERNS = [
        (r'os\.getenv\(["\'](\w*(?:API_KEY|SECRET|TOKEN)\w*)["\']', "api_key"),
        (r"\bjwt\b|\bJWT\b", "jwt"),
        (r"\boauth2?\b|\bOAuth\b", "oauth2"),
        (r"\bbearer\s+token\b", "bearer"),
    ]

    def __init__(self) -> None:
        self._compiled = [(re.compile(p), auth_type) for p, auth_type in self._PATTERNS]

    def can_handle(self, source: str) -> bool:
        return True

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()

        for pat, auth_type in self._compiled:
            for m in pat.finditer(source):
                # Use capture group name if available, else use auth_type
                if m.lastindex and m.lastindex >= 1:
                    name = m.group(1)
                else:
                    name = auth_type
                if name in seen:
                    continue
                seen.add(name)
                nid = _stable_id(name, NodeType.AUTH)
                nodes.append(
                    Node(
                        id=nid,
                        name=name,
                        component_type=NodeType.AUTH,
                        confidence=0.55,
                        metadata=NodeMetadata(auth_type=auth_type),
                        evidence=[
                            _evidence(
                                EvidenceKind.REGEX,
                                0.55,
                                f"Auth pattern match: {name!r} ({auth_type})",
                                file_path,
                            )
                        ],
                    )
                )
        return nodes, []


_SCOPE_MAP = {
    "rbac": PrivilegeScope.RBAC,
    "admin": PrivilegeScope.ADMIN,
    "filesystem_write": PrivilegeScope.FILESYSTEM_WRITE,
    "code_execution": PrivilegeScope.CODE_EXECUTION,
    "network_out": PrivilegeScope.NETWORK_OUT,
    "email_out": PrivilegeScope.EMAIL_OUT,
    "social_media_out": PrivilegeScope.SOCIAL_MEDIA_OUT,
    "db_write": PrivilegeScope.DB_WRITE,
}


class PrivilegeAdapter:
    """Cross-cutting PRIVILEGE node detection across all file types."""

    _SCOPES: dict[str, list[str]] = {
        "rbac": [
            "rbac", "require_roles", "check_permission", "AccessControl",
            "RBACMiddleware", "role_required",
        ],
        "admin": [
            "is_superuser", "is_staff", "runas", "setuid",
            "@admin_required", "sudo",
        ],
        "filesystem_write": [
            "open(", "write_text", "write_bytes", "os.makedirs",
            "shutil.copy", "FileWriteTool",
        ],
        "code_execution": [
            "subprocess", "os.system", "exec(", "eval(",
            "BashTool", "ShellTool",
        ],
        "network_out": [
            "requests.post", "httpx.post", "aiohttp.post", "webhook",
        ],
        "email_out": [
            "smtplib", "sendgrid", "mailgun", "resend", "smtp.send",
        ],
        "social_media_out": [
            "tweepy", "praw", "discord.py", "telethon", "slack_sdk",
        ],
        "db_write": [
            "session.add(", "session.commit(", ".save(", "INSERT INTO", "db.execute",
        ],
    }

    def can_handle(self, source: str) -> bool:
        return True

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()

        for scope_name, keywords in self._SCOPES.items():
            for kw in keywords:
                if kw in source:
                    if scope_name in seen:
                        break
                    seen.add(scope_name)
                    nid = _stable_id(scope_name, NodeType.PRIVILEGE)
                    scope_enum = _SCOPE_MAP.get(scope_name)
                    nodes.append(
                        Node(
                            id=nid,
                            name=scope_name,
                            component_type=NodeType.PRIVILEGE,
                            confidence=0.6,
                            metadata=NodeMetadata(privilege_scope=scope_enum),
                            evidence=[
                                _evidence(
                                    EvidenceKind.REGEX,
                                    0.6,
                                    f"Privilege pattern: {kw!r} → {scope_name}",
                                    file_path,
                                )
                            ],
                        )
                    )
                    break

        return nodes, []


class ToolGenericAdapter:
    """Regex-based generic tool detection."""

    _PATTERNS = [
        (r"\bplaywright\b", "playwright"),
        (r"\bpuppeteer\b", "puppeteer"),
        (r"\bselenium\b", "selenium"),
        (r"\bbrowser.?use\b", "browser-use"),
        (r"\bBashTool\b", "BashTool"),
        (r"\bFileWriteTool\b", "FileWriteTool"),
        (r"\bmem0\b", "mem0"),
        (r"\bPyGithub\b", "pygithub"),
        (r"\bcelery\b", "celery"),
        (r"\bAPScheduler\b", "APScheduler"),
        (r"\btavily\b", "tavily-search"),
        (r"\bserper\b", "serper"),
        (r"\bBraveSearch\b", "brave-search"),
        (r"\bDuckDuckGoSearch\b", "duckduckgo-search"),
        (r"\bwikipedia\b", "wikipedia"),
        (r"\bArxiv\b", "arxiv"),
        (r"\bYahooFinance\b", "yahoo-finance"),
    ]

    def __init__(self) -> None:
        self._compiled = [(re.compile(p), name) for p, name in self._PATTERNS]

    def can_handle(self, source: str) -> bool:
        return True

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()

        for pat, tool_name in self._compiled:
            if pat.search(source):
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                nid = _stable_id(tool_name, NodeType.TOOL)
                nodes.append(
                    Node(
                        id=nid,
                        name=tool_name,
                        component_type=NodeType.TOOL,
                        confidence=0.55,
                        metadata=NodeMetadata(),
                        evidence=[
                            _evidence(
                                EvidenceKind.REGEX,
                                0.55,
                                f"Tool pattern match: {tool_name!r}",
                                file_path,
                            )
                        ],
                    )
                )
        return nodes, []
