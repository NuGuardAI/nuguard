"""Top-level v2 runner: surface → plan → schedule → execute → evaluate → report.

Phase 0 status
--------------
This is a **scaffold**.  :class:`RedteamV2Orchestrator` wires the engine into the
CLI and config system and runs end-to-end, but it does not yet generate or
execute any scenarios — :meth:`RedteamV2Orchestrator.run` returns an empty
result.  Subsequent phases fill in:

* Phase 1 — technique knowledge base (``knowledge/``)
* Phase 2 — attack-surface graph, recon, per-target catalog (``surface/``)
* Phase 3 — coverage matrix + objective generation (``planning/``)
* Phase 4 — phased scheduler (``scheduler/``)
* Phase 5 — adaptive execution over v1 executors (``execution/``)
* Phase 6 — layered evaluation pipeline (``evaluation/``)
* Phase 7 — findings + reporting + regression export (``findings/``, ``report.py``)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger
from nuguard.models.token_usage import TokenUsage

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig
    from nuguard.common.llm_client import LLMClient
    from nuguard.config import RedteamV2Settings
    from nuguard.models.finding import Finding
    from nuguard.models.policy import CognitivePolicy
    from nuguard.redteam.target.canary import CanaryConfig

_log = get_logger(__name__)


@dataclass
class RedteamV2Result:
    """Aggregate output of a v2 run.

    Mirrors the fields the CLI report path needs so the v2 engine can reuse the
    same Markdown/JSON/SARIF rendering as v1.  Phase 0 populates only the
    defaults; later phases fill in findings, scenario records, and coverage.
    """

    findings: list["Finding"] = field(default_factory=list)
    scenario_records: list[Any] = field(default_factory=list)
    scan_outcome: str = "no_findings"
    config_notes: list[str] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    resolved_chat_path: str = "/chat"
    resolved_chat_path_source: str = "config"


class RedteamV2Orchestrator:
    """Coordinates the v2 pipeline stages.

    The constructor intentionally accepts the same core inputs as the v1
    :class:`~nuguard.redteam.executor.orchestrator.RedteamOrchestrator` so the
    CLI can switch engines with minimal branching.  Extra v1-only keyword
    arguments are accepted and ignored via ``**_ignored`` during the scaffold
    phase; each will be consumed as the corresponding stage lands.
    """

    def __init__(
        self,
        *,
        sbom: object,
        target_url: str,
        settings: "RedteamV2Settings | None" = None,
        policy: "CognitivePolicy | None" = None,
        policy_controls: list | None = None,
        canary_config: "CanaryConfig | None" = None,
        profile: str = "ci",
        chat_path: str = "/chat",
        auth_config: "AuthConfig | None" = None,
        redteam_llm: "LLMClient | None" = None,
        eval_llm: "LLMClient | None" = None,
        verbose: bool = False,
        **_ignored: Any,
    ) -> None:
        from nuguard.config import RedteamV2Settings as _Settings

        self.sbom = sbom
        self.target_url = target_url
        self.settings = settings or _Settings()
        self.policy = policy
        self.policy_controls = policy_controls or []
        self.canary_config = canary_config
        self.profile = profile
        self.chat_path = chat_path or "/chat"
        self.auth_config = auth_config
        self.redteam_llm = redteam_llm
        self.eval_llm = eval_llm
        self.verbose = verbose

    async def run(self) -> RedteamV2Result:
        """Execute the v2 pipeline.

        Phase 0: no-op that returns an empty result and logs that the engine is
        a scaffold so users are not surprised by zero findings.
        """
        _log.warning(
            "redteam v2 engine is a scaffold (Phase 0): no scenarios are generated "
            "or executed yet. Use --engine v1 for a functional scan."
        )
        return RedteamV2Result(
            resolved_chat_path=self.chat_path,
            resolved_chat_path_source="config",
        )
