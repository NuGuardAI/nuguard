from __future__ import annotations

import re
from dataclasses import dataclass

from .base import DetectionAdapter, FrameworkAdapter, RegexAdapter
from .frameworks import builtin_framework_specs
from .privilege import privilege_adapters
from ..normalization import canonicalize_text
from ..types import ComponentType


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
    from .data_classification import DataClassificationPythonAdapter
    from .python import (
        AgnoAdapter,
        AutoGenAdapter,
        AzureAIAgentsAdapter,
        BedrockAgentCoreAdapter,
        CrewAIAdapter,
        FastAPIAdapter,
        FlaskAdapter,
        GoogleADKPythonAdapter,
        GuardrailsAIAdapter,
        LangGraphAdapter,
        LlamaIndexAdapter,
        LLMClientsAdapter,
        MCPServerAdapter,
        OpenAIAgentsAdapter,
        SemanticKernelAdapter,
    )
    from .typescript import (
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
        FastAPIAdapter(),
        FlaskAdapter(),
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
    from .frameworks import builtin_framework_adapters

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
                        r"|bigquery|snowflake|clickhouse|couchbase|mariadb"
                        r"|azuresql|azure[_-]sql|sqlserver|mssql"
                        r"|appwrite|nhost"
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
                        # Cloud CLI tools listed FIRST so cloud commands surface as the
                        # primary evidence snippet (takes priority over generic keywords
                        # like "deployment" that appear in comments).
                        # Bare 'az' excluded (too short); az sub-commands required.
                        r"\b(gcloud|gsutil|bq"
                        r"|kubectl|kustomize|skaffold|argocd|fluxcd"
                        r"|ansible(?:[_-]playbook)?|ansible[_-]galaxy"
                        r"|pulumi|cdktf"
                        r"|azd|azure[_-]cli|az\s+(?:login|group|webapp|container|acr|aks|"
                        r"functionapp|storage|keyvault|cosmos|deploy)"
                        r"|aws\s+(?:ec2|s3|lambda|ecs|eks|rds|cloudformation|deploy|"
                        r"ecr|codecommit|codedeploy|codepipeline))\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # PaaS / serverless / edge deployment platforms
                        r"\b(flyctl|fly\.io|heroku|vercel|netlify|railway|render"
                        r"|serverless[_-]framework|sam[_-]cli|amplify[_-]cli"
                        r"|wrangler|cloudflare[_-]pages|deno[_-]deploy)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Container / orchestration runtimes (last — generic keywords
                        # like "deployment" are common in comments and strings)
                        r"\b(docker|kubernetes|helm|terraform|compose|deployment"
                        r"|nginx|certbot|letsencrypt|gunicorn|uvicorn|caddy|traefik"
                        r"|reverse[._]proxy|ssl[._]certificate|systemd[._]service)\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="deployment:generic",
            ),
            # Web search / information-retrieval tools used by AI agents.
            # Covers direct library usage, LangChain community wrappers, and
            # standalone retrieval SDKs (DuckDuckGo, Tavily, SerpAPI, Perplexity, etc.).
            RegexAdapter(
                name="tool_search",
                component_type=ComponentType.TOOL,
                priority=174,
                patterns=(
                    re.compile(
                        r"\b(duckduckgo[_-]search|DDGS"
                        r"|DuckDuckGoSearch(?:Run|Results|APIWrapper)?"
                        r"|TavilyClient|TavilySearch(?:Results)?"
                        r"|GoogleSerperAPIWrapper|SerpAPIWrapper"
                        r"|BraveSearch(?:Wrapper)?|ExaSearchResults?"
                        r"|PerplexityClient|perplexipy|perplexity[_-](?:search|client))\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="tool:search",
            ),
            # OpenAI and Anthropic built-in / platform tools.
            # Detects:
            #   - OpenAI Agents SDK built-in tool classes (WebSearchTool,
            #     FileSearchTool, CodeInterpreterTool)
            #   - OpenAI Responses API built-in type strings
            #     ("web_search_preview", "code_interpreter")
            #   - Anthropic computer-use beta tool identifiers (computer_use,
            #     ComputerTool, text_editor, bash when from anthropic SDK)
            RegexAdapter(
                name="tool_openai_builtin",
                component_type=ComponentType.TOOL,
                priority=174,
                patterns=(
                    re.compile(
                        # OpenAI Agents SDK built-in tool classes
                        r"\b(WebSearchTool|FileSearchTool|CodeInterpreterTool"
                        # OpenAI Responses API type-string literals
                        r"|web_search_preview|code_interpreter"
                        # Anthropic computer-use tool identifiers
                        r"|ComputerTool|computer_use|text_editor_tool"
                        r"|anthropic[._](?:computer|bash|text.editor))\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="tool:platform_builtin",
            ),
            # Workspace / SaaS connector tools used by AI agents.
            # Covers LangChain community toolkits, vendor SDKs, and
            # direct API client imports for common business platforms.
            RegexAdapter(
                name="tool_workspace_connector",
                component_type=ComponentType.TOOL,
                priority=174,
                patterns=(
                    re.compile(
                        # Google Drive and Gmail (LangChain toolkits + raw SDK)
                        r"\b(GoogleDrive(?:Tool(?:kit)?|APIWrapper)"
                        r"|GmailTool(?:kit)?|GmailSendMessage|GmailGetMessage"
                        r"|GmailCreateDraft|pydrive2?)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Dropbox, Box, SharePoint, OneDrive
                        r"\b(dropbox|DropboxAPIWrapper|BoxAPIWrapper|boxsdk"
                        r"|SharePoint(?:Tool(?:kit)?|Loader|APIWrapper)?"
                        r"|shareplum|OneDrive(?:Tool(?:kit)?|APIWrapper)?)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # CRM / support platforms
                        r"\b(SimpleSalesforce|simple[_-]salesforce|SalesforceAPIWrapper"
                        r"|hubspot|HubSpot(?:Tool|Client|APIWrapper)?"
                        r"|zendesk|ZendeskAPI|ZendeskTool(?:kit)?)\b",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        # Productivity / no-code databases
                        r"\b(airtable|pyairtable|AirtableAPIWrapper|AirtableLoader)\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="tool:workspace_connector",
            ),
            # Agent observability / tracing tools.
            # These SDKs capture LLM inputs, outputs, and traces and send them
            # to external services — relevant for data-flow and privacy analysis.
            RegexAdapter(
                name="tool_observability",
                component_type=ComponentType.TOOL,
                priority=174,
                patterns=(
                    re.compile(
                        r"\b(langfuse|langsmith|LangSmithClient"
                        r"|mlflow|arize(?:[_-]phoenix)?|openinference"
                        r"|helicone|HeliconeAsyncLogger"
                        r"|weave\.init|wandb[._]weave)\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="tool:observability",
            ),
            # RPA / robotic-process-automation tools used by AI agents.
            RegexAdapter(
                name="tool_rpa",
                component_type=ComponentType.TOOL,
                priority=174,
                patterns=(
                    re.compile(
                        r"\b(uipath|UiPath|UiRobot|Orchestrator(?:Client|API))\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="tool:rpa",
            ),
            # Browser automation tools (playwright/selenium/puppeteer) are filtered
            # to skip test directories and JSON config files.  These tools are
            # legitimately used as agent capabilities (e.g. RUN playwright install
            # in Dockerfiles, browser_agent.py) but commonly appear as false
            # positives in devDependency entries in package.json and test runner
            # files (*.spec.js, tests/).
            RegexAdapter(
                name="tool_browser_automation",
                component_type=ComponentType.TOOL,
                priority=175,
                patterns=(
                    re.compile(
                        # Browser automation / headless browser tools
                        r"\b(playwright|puppeteer|selenium)\b",
                        re.IGNORECASE,
                    ),
                ),
                canonical_name="tool:browser_automation",
                # Skip test directories — playwright/selenium in test files means
                # the project uses them as a *test runner*, not as an agent tool.
                skip_path_parts=frozenset({"tests", "test", "__tests__", "spec", "e2e"}),
                # Skip JSON manifests — playwright in package.json is a package
                # reference, not an agentic capability declaration.
                skip_extensions=frozenset({".json"}),
            ),
            RegexAdapter(
                name="tool_generic",
                component_type=ComponentType.TOOL,
                priority=175,
                patterns=(
                    re.compile(
                        # Web scraping tools used in agent data pipelines
                        r"\b(beautifulsoup|scrapy)\b",
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
