from __future__ import annotations

import re
from dataclasses import dataclass

from xelo.adapters.base import DetectionAdapter, FrameworkAdapter, RegexAdapter
from xelo.adapters.frameworks import builtin_framework_specs
from xelo.adapters.privilege import privilege_adapters
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


@dataclass(frozen=True)
class IntakeCandidate:
    adapter_name: str
    source_path: str
    status: str
    priority: int


def intake_candidates() -> tuple[IntakeCandidate, ...]:
    candidates: list[IntakeCandidate] = []
    for spec in builtin_framework_specs():
        candidates.append(
            IntakeCandidate(
                adapter_name=spec.adapter_name,
                source_path=f"xelo.adapters.frameworks:{spec.adapter_name}",
                status=spec.status,
                priority=spec.priority,
            )
        )
    return tuple(candidates)


def default_framework_adapters() -> tuple[FrameworkAdapter, ...]:
    """Return all AST-aware framework adapters in priority order.

    Includes both Python adapters (run against ``.py`` and ``.ipynb`` files)
    and TypeScript adapters (run against ``.ts``, ``.tsx``, ``.js``, ``.jsx``
    files).
    """
    from xelo.adapters.data_classification import DataClassificationPythonAdapter
    from xelo.adapters.python import (
        AgnoAdapter,
        AutoGenAdapter,
        AzureAIAgentsAdapter,
        BedrockAgentCoreAdapter,
        CrewAIAdapter,
        GoogleADKPythonAdapter,
        GuardrailsAIAdapter,
        LangGraphAdapter,
        LlamaIndexAdapter,
        LLMClientsAdapter,
        MCPServerAdapter,
        OpenAIAgentsAdapter,
        SemanticKernelAdapter,
    )
    from xelo.adapters.typescript import (
        AgnoTSAdapter,
        AzureAIAgentsTSAdapter,
        BedrockAgentsTSAdapter,
        DatastoreTSAdapter,
        GoogleADKAdapter,
        LangGraphTSAdapter,
        LLMClientTSAdapter,
        OpenAIAgentsTSAdapter,
        PromptTSAdapter,
    )

    adapters: list[FrameworkAdapter] = [
        # Data classification (Python models — Pydantic, SQLAlchemy, dataclasses)
        DataClassificationPythonAdapter(),
        # Python AI framework adapters
        LangGraphAdapter(),
        OpenAIAgentsAdapter(),
        AutoGenAdapter(),
        GuardrailsAIAdapter(),
        SemanticKernelAdapter(),
        CrewAIAdapter(),
        LlamaIndexAdapter(),
        LLMClientsAdapter(),
        AgnoAdapter(),
        AzureAIAgentsAdapter(),
        BedrockAgentCoreAdapter(),
        GoogleADKPythonAdapter(),
        MCPServerAdapter(),
        # TypeScript / JavaScript adapters
        LangGraphTSAdapter(),
        OpenAIAgentsTSAdapter(),
        GoogleADKAdapter(),
        LLMClientTSAdapter(),
        BedrockAgentsTSAdapter(),
        DatastoreTSAdapter(),
        PromptTSAdapter(),
        AgnoTSAdapter(),
        AzureAIAgentsTSAdapter(),
    ]
    return tuple(sorted(adapters, key=lambda a: (a.priority, canonicalize_text(a.name))))


def default_registry() -> tuple[DetectionAdapter, ...]:
    """Return regex-based adapters: framework detectors + generic component detectors.

    Framework detectors (from ``builtin_framework_adapters``) run on all file
    types and serve as a lightweight fallback for non-Python files (YAML, Terraform,
    Dockerfiles, etc.) and as a text-based signal for Python comments/configs.
    """
    from xelo.adapters.frameworks import builtin_framework_adapters

    adapters: list[DetectionAdapter] = list(builtin_framework_adapters())

    # Baseline generic component detectors (used as fallback for non-Python files)
    adapters.extend(
        [
            RegexAdapter(
                name="model_generic",
                component_type=ComponentType.MODEL,
                priority=110,
                patterns=(
                    re.compile(
                        # Match model name strings, not library names.
                        # - llama-<digit>: canonical dash form (llama-3.3-70b-versatile)
                        # - llama<digit>:<tag>: Ollama colon-tag format (llama3.2:3b)
                        # - o-series: require word boundary or letter suffix to avoid hex
                        # - deepseek/qwen/phi: common open-weight families
                        # - <name>:<size_tag>: generic Ollama pull format (mistral:7b)
                        r"\b(gpt-[\d][\w.-]*|claude-[\d][\w.-]*|claude-(?:sonnet|opus|haiku|instant)[\d-][\w.-]*|gemini-[\d][\w.]*"
                        r"|llama-[\d][\w.-]*|llama[\d][\w.]*:[a-z0-9]+"
                        r"|mistral-[\w.-]+|o\d(?:-[a-z][a-z0-9-]*)?\b"
                        r"|deepseek-[\w.-]+|qwen[\d][\w.-]*|phi[\d][\w.-]*"
                        r"|command-(?:r|light|nightly|a\d)[\w.-]*"
                        # Ollama colon-tag format: require a meaningful prefix containing
                        # at least one letter, and omit 'latest' (too generic — it just
                        # means "pull the newest image" and is not a real model name).
                        r"|[\w.-]+:(?:7b|13b|70b|3b|1b|8b|14b|32b|mini|instruct|chat)\b)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # HuggingFace Hub org/model-id format (non-high-FP orgs).
                        # Matches strings like "meta-llama/Llama-3.1-8B-Instruct",
                        # "mistralai/Mistral-7B-v0.3".
                        # Anchored to known orgs to avoid matching arbitrary file paths.
                        # Negative lookbehind: skip matches inside URLs or npm @scope
                        #   (e.g. "github.com/meta-llama/..." or "@eleutherai/...").
                        # Negative lookahead: skip GitHub URL path fragments.
                        r"(?<![/@])\b(?:meta-llama|mistralai|HuggingFaceH4|facebook"
                        r"|EleutherAI|tiiuae|databricks|Qwen|deepseek-ai|THUDM|bigscience"
                        r"|openchat|NousResearch|teknium|WizardLM|lmsys|stabilityai"
                        r"|togethercomputer|codellama|sentence-transformers|cohere"
                        r"|ai21labs|allenai|huggingface)"
                        r"/(?!(?:blob|tree|issues|pulls|commit|releases|compare|raw)/)"
                        r"[\w][\w./:-]*",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # High-FP orgs (google/microsoft/openai): require a digit
                        # in the model ID to distinguish real model names like
                        # "google/gemma-2-27b-it" from SDK/repo names like
                        # "google/adk" or "openai/openai-cookbook".
                        # Negative lookbehind: skip URL paths (/google/) and npm scopes.
                        r"(?<![/@])\b(?:google|microsoft|openai)"
                        r"/(?!(?:blob|tree|issues|pulls|commit|releases|compare|raw)/)"
                        r"[\w][\w.-]*\d[\w./:-]*",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # HuggingFace-origin standalone model families not covered above.
                        # - bert/roberta/t5: encoder/seq2seq classics
                        # - gpt2/gpt-j/gpt-neo: GPT-2-era models
                        # - bloom/bloomz: BigScience multilingual LLMs
                        # - falcon: TII open-weight LLMs
                        # - starcoder/starcoderbase: code generation
                        # - codellama: Meta code model
                        # - zephyr/vicuna/solar/dolly/wizard/orca: RLHF fine-tunes
                        # - gemma: Google open-weight family (no digit prefix used)
                        # - nomic-embed/bge/e5: embedding models
                        r"\b(bert-(?:base|large)[\w.-]*"
                        r"|roberta-(?:base|large)[\w.-]*"
                        r"|distilbert[\w.-]*"
                        r"|t5-(?:small|base|large|xl|xxl)[\w.-]*"
                        r"|gpt-?2[\w.-]*|gpt-j[\w.-]*|gpt-neo[\w.-]*"
                        r"|bloom[\d-][\w.-]*|bloomz[\w.-]*"
                        r"|falcon-\d[\w.-]*"
                        r"|starcoder[\w.-]*"
                        r"|codellama[\w.-]*"
                        r"|zephyr-\d[\w.-]*|vicuna-\d[\w.-]*|solar-\d[\w.-]*"
                        r"|dolly-[\w.-]+|wizardlm[\w.-]*|wizardcoder[\w.-]*"
                        r"|orca[\w.-]*"
                        r"|gemma-\d[\w.-]*"
                        r"|nomic-embed[\w.-]*|bge-[\w.-]+|e5-[\w.-]+)\b",
                        re.IGNORECASE,
                    ),
                ),
                metadata={"normalizer": "model-name"},
            ),
            RegexAdapter(
                name="datastore_generic",
                component_type=ComponentType.DATASTORE,
                priority=130,
                patterns=(
                    re.compile(
                        r"\b(postgres|mysql|mongodb|redis|pinecone|faiss|chroma|weaviate|qdrant|milvus"
                        r"|sqlite|aiosqlite|sqlite3|dynamodb|firestore|cosmosdb|supabase|neon"
                        r"|cassandra|elasticsearch|opensearch|neo4j|tidb|cockroachdb"
                        r"|kendra)\b",
                        re.IGNORECASE,
                    ),
                ),
                metadata={"normalizer": "datastore"},
            ),
            # AWS S3 — require boto3.client/resource context to avoid matching
            # matching unrelated variable names (s3_client, s3_path, etc.).
            RegexAdapter(
                name="datastore_s3",
                component_type=ComponentType.DATASTORE,
                priority=130,
                patterns=(
                    re.compile(
                        "boto3\\.(?:client|resource)\\(['\"]s3['\"]",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="s3",
                metadata={"normalizer": "datastore"},
            ),
            # Auth — two-tier approach:
            # Tier 1 (priority 135): executable runtime credential access patterns.
            #   Matches os.getenv("OPENAI_API_KEY"), load_dotenv(), etc. in code.
            #   Higher priority than the generic adapter so runtime evidence wins.
            RegexAdapter(
                name="auth_runtime",
                component_type=ComponentType.AUTH,
                priority=135,
                patterns=(
                    # os.getenv for API/auth key names
                    re.compile(
                        r"""os\.(?:getenv|environ\.get)\s*\(\s*['"][^'"]*(?:API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)[^'"]*['"]\s*\)""",
                        re.IGNORECASE,
                    ),
                    # load_dotenv() — explicit runtime .env loading
                    re.compile(r"\bload_dotenv\s*\(", re.IGNORECASE),
                    # Framework API-key constructor kwargs — e.g. openai.OpenAI(api_key=...)
                    re.compile(
                        r"\bapi_key\s*=\s*(?:os\.(?:getenv|environ)|[\w.]+\.get\s*\()",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="auth:generic",
                # Exclude synthetic data-generator folders — they may reference
                # credential env-var names in config/template prose, not runtime access.
                skip_path_parts=frozenset({"data-generators", "data_generators", "generators"}),
            ),
            # Tier 2 (priority 140): broader auth keyword patterns.
            #   Excluded from non-runtime paths (YAML configs, data-generator dirs)
            #   to reduce false positives.
            RegexAdapter(
                name="auth_generic",
                component_type=ComponentType.AUTH,
                priority=140,
                patterns=(
                    # Auth scheme identifiers — short, unambiguous
                    re.compile(r"\b(jwt|oauth2?|apikey|api_key|bearer)\b", re.IGNORECASE),
                    # Full authentication/authorization words — avoids gcloud auth, auth@v2, etc.
                    re.compile(r"\bauth(?:entication|orization|enticate|orize)\b", re.IGNORECASE),
                    # Compound token forms — avoids bare CI token vars like token=$TOKEN
                    re.compile(r"\b(?:access|refresh|api|auth|id)_token\b", re.IGNORECASE),
                    # Password hashing and session-based auth patterns
                    re.compile(
                        r"\b(bcrypt|passlib|argon2|pbkdf2|scrypt"
                        r"|session[._]cookie|cookie[._]jar|http[._]only|csrf[._]token"
                        r"|verify[._]password|hash[._]password)\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="auth:generic",
                skip_path_parts=frozenset({"data-generators", "data_generators", "generators"}),
            ),
            *privilege_adapters(),
            RegexAdapter(
                name="api_endpoint_generic",
                component_type=ComponentType.API_ENDPOINT,
                priority=160,
                patterns=(
                    re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+/[\w/{}:-]+"),
                    re.compile(r"@(app|router)\.(get|post|put|delete|patch)\(", re.IGNORECASE),
                ),
                canonical_name="api_endpoint:generic",
            ),
            RegexAdapter(
                name="deployment_generic",
                component_type=ComponentType.DEPLOYMENT,
                priority=170,
                patterns=(
                    re.compile(
                        r"\b(docker|kubernetes|helm|terraform|compose|deployment"
                        r"|nginx|certbot|letsencrypt|gunicorn|uvicorn|caddy|traefik"
                        r"|reverse[._]proxy|ssl[._]certificate|systemd[._]service)\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="deployment:generic",
            ),
            RegexAdapter(
                name="tool_generic",
                component_type=ComponentType.TOOL,
                priority=175,
                patterns=(
                    re.compile(
                        # Web automation and browser control
                        r"\b(playwright|puppeteer|selenium|beautifulsoup|scrapy)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # AI-driven browser agents and headless browser tools
                        r"\b(browser[_-]use|browserbase|stagehand|multion|agentql"
                        r"|camoufox|nodriver|undetected.chromedriver|mechanize)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Computer-use / GUI automation tools
                        r"\b(computer[_-]use[_-]?tool|ComputerUseTool|pyautogui|pynput"
                        r"|pygetwindow|ahk|autohotkey|xdotool|e2b[_-]desktop|screenpipe)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Terminal / sandboxed code-execution tools
                        r"\b(BashTool|bash[_-]tool|ShellTool|shell[_-]tool|terminal[_-]tool"
                        r"|CommandLineTool|command[_-]line[_-]tool"
                        r"|e2b[_-]code[_-]interpreter|E2BSandbox|modal[_-]sandbox"
                        r"|daytona[_-]sdk|subprocess[_-]tool)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Filesystem read/write tools used by agents
                        r"\b(FileReadTool|ReadFileTool|FileWriteTool|WriteFileTool"
                        r"|FileSystemTool|filesystem[_-]tool|file[_-]management[_-]tool"
                        r"|DirectoryReadTool|DirectoryListTool)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Agent memory / context-window tools
                        r"\b(mem0|MemoryClient|ZepClient|zep[_-]python|letta[_-]client"
                        r"|MemGPT|langmem|MemoryTool|memory[_-]tool)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Social platform SDKs
                        r"\b(praw|twikit|tweepy|telethon|python.telegram.bot|discord\.py)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Git / GitHub / version-control tools used by agents
                        r"\b(GitTool|git[_-]tool|GithubTool|github[_-]tool|GitHubToolkit"
                        r"|github[_-]toolkit|pygithub|PyGithub|gitpython|GitPython"
                        r"|ghapi|github3\.py|GitLabTool|gitlab[_-]tool|python[_-]gitlab)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Cloud CLI / shell tools used by agents (AWS, GCP, Azure)
                        r"\b(AWSCloudShellTool|aws[_-](?:tool|cli[_-]tool|shell[_-]tool)"
                        r"|GcloudTool|gcloud[_-]tool|AzureCLITool|azure[_-]cli[_-]tool"
                        r"|S3Tool|s3[_-]tool|EC2Tool|ec2[_-]tool|LambdaTool|lambda[_-]tool"
                        r"|CloudStorageTool|cloud[_-]storage[_-]tool|BigQueryTool|bigquery[_-]tool"
                        r"|gsutil[_-]tool|awscli|boto3[_-]tool)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # DevOps / CI-CD / IaC / monitoring tools used by agents
                        r"\b(TerraformTool|terraform[_-]tool|AnsibleTool|ansible[_-]tool"
                        r"|PulumiTool|pulumi[_-]tool|DockerTool|docker[_-]tool"
                        r"|KubernetesTool|kubectl[_-]tool|HelmTool|helm[_-]tool"
                        r"|JiraTool|jira[_-]tool|jira[_-]python|atlassian[_-]python[_-]api"
                        r"|LinearTool|linear[_-]tool|NotionTool|notion[_-]tool|notion[_-]client"
                        r"|ConfluenceTool|confluence[_-]tool|SlackTool|slack[_-]tool|slack[_-]sdk"
                        r"|DatadogTool|datadog[_-]tool|GrafanaTool|grafana[_-]tool"
                        r"|PagerDutyTool|pagerduty[_-]tool|SentryTool|sentry[_-]tool)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Job scheduling and task queues
                        r"\b(APScheduler|BackgroundScheduler|AsyncIOScheduler|BlockingScheduler"
                        r"|celery|rq|dramatiq|arq)\b",
                    ),
                ),
                canonical_name="tool:generic",
            ),
            RegexAdapter(
                name="prompt_generic",
                component_type=ComponentType.PROMPT,
                priority=180,
                patterns=(
                    re.compile(
                        r"\b(system[_ ]prompt|prompt[_ ]template"
                        r"|few[_. ]shot|chain[_. ]of[_. ]thought|prompt[_ ]injection)\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="prompt:generic",
            ),
        ]
    )

    return tuple(
        sorted(adapters, key=lambda adapter: (adapter.priority, canonicalize_text(adapter.name)))
    )
