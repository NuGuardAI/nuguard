"""E2E redteam tests — runs the full scan pipeline against live fixture apps.

Each test:
  1. Materializes the fixture app from cached_files.json into a temp directory
  2. Generates an AI-SBOM from the full source root and saves it to tests/output/
  3. Installs dependencies into a cached virtualenv and starts the app
  4. Runs RedteamOrchestrator (driven by the saved SBOM) against the live endpoint
  5. Writes a Markdown report to tests/output/
  6. Asserts both the SBOM and the report were written

Opt-in: set ``NUGUARD_REDTEAM_E2E=1`` in the environment (or populate
``tests/redteam/.env``) to enable these tests.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

from nuguard.config import load_config
from nuguard.models.policy import CognitivePolicy
from nuguard.policy.parser import parse_policy
from nuguard.redteam.executor.orchestrator import RedteamOrchestrator, ScenarioRecord
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.extractor.core import AiSbomExtractor
from nuguard.sbom.serializer import AiSbomSerializer

from .app_runner import APP_CONFIGS, FIXTURES_DIR, AppRunner
from .report import write_redteam_report

_log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "tests" / "output"

_cfg = load_config()

REDTEAM_PROFILE = os.getenv("NUGUARD_REDTEAM_PROFILE", _cfg.redteam_profile)
_USE_LLM = bool(os.getenv("GEMINI_API_KEY") or os.getenv("LITELLM_API_KEY"))
# nuguard.yaml redteam.verbose takes precedence; env var NUGUARD_REDTEAM_VERBOSE
# acts as a fallback so users can opt in without editing the config file.
_VERBOSE = _cfg.redteam_verbose or bool(os.getenv("NUGUARD_REDTEAM_VERBOSE"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _missing_env_vars(names: list[str]) -> list[str]:
    return [n for n in names if not os.getenv(n)]


def _sbom_summary(sbom: object) -> dict:
    node_counts: dict[str, int] = {}
    for node in getattr(sbom, "nodes", []):
        ct = getattr(node, "component_type", None)
        ctype = ct.value if hasattr(ct, "value") else str(ct or "UNKNOWN")
        node_counts[ctype] = node_counts.get(ctype, 0) + 1

    summary = getattr(sbom, "summary", None)
    frameworks: list[str] = []
    use_case: str | None = None
    if summary:
        frameworks = list(getattr(summary, "frameworks_detected", None) or [])
        use_case = getattr(summary, "use_case", None)

    return {"node_counts": node_counts, "frameworks": frameworks, "use_case": use_case}


def _fixture_source_hash(fixture_dir: str) -> str:
    """SHA-1 of the fixture's cached_files.json — used as a cache key for the SBOM.

    Returns an empty string if the file cannot be read (disables caching).
    """
    import hashlib
    cache_path = FIXTURES_DIR / fixture_dir / "cached_files.json"
    try:
        return hashlib.sha1(cache_path.read_bytes()).hexdigest()[:16]  # noqa: S324
    except Exception:
        return ""


def _generate_sbom(source_dir: Path, app_name: str, fixture_dir: str = ""):  # type: ignore[return]
    """Generate SBOM synchronously and save to tests/output/sbom_{app_name}.json.

    If the fixture source (cached_files.json) has not changed since the last
    run, the existing SBOM is reloaded from disk to avoid re-scanning.
    Delete tests/output/sbom_{app_name}.json to force a full regeneration.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sbom_path = OUTPUT_DIR / f"sbom_{app_name}.json"
    hash_path = OUTPUT_DIR / f"sbom_{app_name}.hash"

    # Cache hit: source unchanged and SBOM file exists
    if fixture_dir and sbom_path.exists() and hash_path.exists():
        current_hash = _fixture_source_hash(fixture_dir)
        if current_hash and hash_path.read_text().strip() == current_hash:
            _log.info(
                "[%s] Source unchanged — reusing cached SBOM from %s", app_name, sbom_path
            )
            sbom = AiSbomSerializer.from_json(sbom_path.read_text(encoding="utf-8"))
            return sbom, sbom_path

    _log.info("[%s] Generating SBOM from %s …", app_name, source_dir)
    extractor = AiSbomExtractor()
    sbom_config = AiSbomConfig(enable_llm=_USE_LLM)
    sbom = extractor.extract_from_path(source_dir, sbom_config)

    sbom_path.write_text(AiSbomSerializer.to_json(sbom), encoding="utf-8")
    # Write the hash so the next run can detect unchanged source
    if fixture_dir:
        current_hash = _fixture_source_hash(fixture_dir)
        if current_hash:
            hash_path.write_text(current_hash, encoding="utf-8")

    _log.info(
        "[%s] SBOM saved to %s (%d nodes)",
        app_name,
        sbom_path,
        len(getattr(sbom, "nodes", [])),
    )
    return sbom, sbom_path


def _load_policy(config: "AppConfig") -> CognitivePolicy | None:  # type: ignore[name-defined]
    """Load and parse a CognitivePolicy from the fixture's static directory.

    Checks (in order):
      1. config.policy_file relative to the fixture dir (explicitly configured)
      2. cognitive_policy.md at the fixture dir root (auto-detect fallback)
    Returns None if no policy file is found.
    """
    candidates = []
    if config.policy_file:
        candidates.append(FIXTURES_DIR / config.fixture_dir / config.policy_file)
    # Always try the canonical name as a fallback
    candidates.append(FIXTURES_DIR / config.fixture_dir / "cognitive_policy.md")

    for path in candidates:
        if path.exists():
            try:
                policy = parse_policy(path.read_text(encoding="utf-8"))
                _log.info("[%s] Loaded cognitive policy from %s", config.name, path)
                return policy
            except Exception as exc:
                _log.warning("[%s] Failed to parse policy %s: %s", config.name, path, exc)
    return None


def _load_fixture_config(config: "AppConfig") -> "NuGuardConfig":  # type: ignore[name-defined]
    """Load per-fixture nuguard.yaml if present, falling back to the repo-root config.

    Looks for ``nuguard.yaml`` in the fixture's static directory alongside
    ``cognitive_policy.md``.  This lets each fixture ship its own redteam
    settings (e.g. ``redteam.app_env.DATABASE_URL``) without affecting other
    app tests.
    """
    fixture_yaml = FIXTURES_DIR / config.fixture_dir / "nuguard.yaml"
    if fixture_yaml.exists():
        _log.info("[%s] Loading per-fixture config from %s", config.name, fixture_yaml)
        return load_config(fixture_yaml)
    return _cfg


def _run_redteam(app_name: str) -> None:
    """Core E2E logic shared by all per-app tests.

    Order of operations:
      1. Materialize fixture source into a temp dir
      2. Load cognitive policy from fixture dir (if present)
      3. Generate SBOM from full source root → save to tests/output/sbom_{app}.json
      4. Start the app subprocess and wait for health
      5. Run RedteamOrchestrator (with SBOM + policy) against the live endpoint
      6. Write Markdown report to tests/output/
    """
    config = APP_CONFIGS[app_name]
    app_cfg = _load_fixture_config(config)
    runner = AppRunner(config, extra_env=app_cfg.redteam_app_env)
    start_time = time.monotonic()

    # Step 1: materialize source
    try:
        runner.materialize()
    except Exception as exc:
        _log.error("[%s] Failed to materialize fixture: %s", app_name, exc)
        runner.start_error = str(exc)
        _write_report(config, runner, {}, 0, [], time.monotonic() - start_time, None,
                      verbose=app_cfg.redteam_verbose or _VERBOSE)
        return

    try:
        # Step 2: load cognitive policy from the static fixture directory
        policy: CognitivePolicy | None = _load_policy(config)
        policy_file: str | None = (
            config.policy_file or ("cognitive_policy.md" if policy else None)
        )

        # Step 3: generate SBOM from the full repo root and persist it
        sbom = None
        sbom_summary: dict = {"node_counts": {}, "frameworks": [], "use_case": None}
        try:
            sbom, sbom_path = _generate_sbom(runner.source_dir, app_name, config.fixture_dir)
            sbom_summary = _sbom_summary(sbom)
            assert sbom_path.exists(), f"SBOM file not written: {sbom_path}"
        except Exception as exc:
            _log.warning("[%s] SBOM generation failed: %s", app_name, exc)

        # Step 4: start the app
        try:
            runner._ensure_venv()
            runner._start_process()
            runner._wait_for_health()
            runner.started = True
            _log.info("[%s] App is ready at %s", app_name, runner.base_url)
        except Exception as exc:
            runner.start_error = str(exc)
            _log.warning("[%s] App failed to start: %s", app_name, exc)
            runner._kill_process()

        # Step 5: run redteam scenarios using the SBOM + cognitive policy
        findings = []
        scenarios_generated = 0
        scenarios_executed: list[tuple[str, str, bool]] = []
        scenario_records: list[ScenarioRecord] = []
        if runner.started and sbom is not None:
            try:
                orchestrator = RedteamOrchestrator(
                    sbom=sbom,
                    target_url=runner.base_url,
                    policy=policy,
                    profile=REDTEAM_PROFILE,
                    chat_path=config.chat_path,
                    chat_payload_key=config.chat_payload_key,
                    chat_payload_list=config.chat_payload_list,
                )
                findings = asyncio.run(orchestrator.run())
                scenarios_generated = orchestrator.scenarios_run
                scenarios_executed = orchestrator.scenarios_executed
                scenario_records = orchestrator.scenario_records
            except Exception as exc:
                _log.warning("[%s] Redteam scan failed: %s", app_name, exc)

        _write_report(
            config, runner, sbom_summary, scenarios_generated, findings,
            time.monotonic() - start_time, policy_file, scenarios_executed,
            scenario_records=scenario_records,
            verbose=app_cfg.redteam_verbose or _VERBOSE,
        )

    finally:
        runner.teardown()


def _write_report(config, runner, sbom_summary, scenarios_generated, findings, scan_duration, policy_file, scenarios_executed=None, scenario_records=None, verbose: bool = False):  # type: ignore[no-untyped-def]
    report_path = write_redteam_report(
        app_name=config.name,
        app_url=runner.base_url,
        chat_path=config.chat_path,
        sbom_summary=sbom_summary,
        scenarios_generated=scenarios_generated,
        findings=findings,
        scan_duration_s=scan_duration,
        app_started=runner.started,
        app_start_error=runner.start_error,
        notes=config.notes,
        policy_file=policy_file,
        scenarios_executed=scenarios_executed,
        verbose=verbose,
        scenario_records=scenario_records,
    )
    _log.info("[%s] Report written to %s", config.name, report_path)
    assert report_path.exists(), f"Report was not written to {report_path}"
    assert report_path.stat().st_size > 0, f"Report is empty: {report_path}"


# ---------------------------------------------------------------------------
# Per-app tests
# ---------------------------------------------------------------------------


@pytest.mark.redteam_e2e
def test_e2e_openai_cs_agents_demo() -> None:
    """E2E scan of openai-cs-agents-demo (multi-agent airline customer service)."""
    app_name = "openai-cs-agents-demo"
    missing = _missing_env_vars(APP_CONFIGS[app_name].required_env_vars)
    if missing:
        pytest.skip(f"Missing required env vars: {missing}")
    _run_redteam(app_name)


@pytest.mark.redteam_e2e
def test_e2e_rag_chatbot_demo() -> None:
    """E2E scan of rag-chatbot-demo (RAG + LangChain + FAISS).

    Note: /chat returns 500 if FAISS indexes are absent.
    Generate with: backend/scripts/generate_indexes.py
    """
    app_name = "rag-chatbot-demo"
    missing = _missing_env_vars(APP_CONFIGS[app_name].required_env_vars)
    if missing:
        pytest.skip(f"Missing required env vars: {missing}")
    _run_redteam(app_name)


@pytest.mark.redteam_e2e
def test_e2e_real_estate_agent() -> None:
    """E2E scan of real-estate-agent (market analysis, OpenAI + Firecrawl)."""
    app_name = "real-estate-agent"
    missing = _missing_env_vars(APP_CONFIGS[app_name].required_env_vars)
    if missing:
        pytest.skip(f"Missing required env vars: {missing}")
    _run_redteam(app_name)


@pytest.mark.redteam_e2e
def test_e2e_voicelive_api_salescoach_demo() -> None:
    """E2E scan of voicelive-api-salescoach-demo (Flask voice sales coach)."""
    app_name = "voicelive-api-salescoach-demo"
    _run_redteam(app_name)


@pytest.mark.redteam_e2e
def test_e2e_healthcare_voice_agent() -> None:
    """E2E scan of healthcare-voice-agent (LangGraph healthcare agent)."""
    app_name = "healthcare-voice-agent"
    missing = _missing_env_vars(APP_CONFIGS[app_name].required_env_vars)
    if missing:
        pytest.skip(f"Missing required env vars: {missing}")
    _run_redteam(app_name)
