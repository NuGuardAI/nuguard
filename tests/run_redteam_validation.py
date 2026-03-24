from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = WORKSPACE_ROOT / "tmp" / "validation"
SUMMARY_PATH = ARTIFACT_DIR / "redteam-validation-summary.md"
JSON_PATH = ARTIFACT_DIR / "redteam-validation-results.json"
SMOKE_OUTPUT_PATH = WORKSPACE_ROOT / "tmp" / "remote-redteam-findings.json"
SMOKE_CONFIG_PATH = WORKSPACE_ROOT / "tmp" / "remote-target.nuguard.yaml"

RUFF_TARGETS = [
    "nuguard/redteam/target/client.py",
    "nuguard/redteam/target/interaction_profile.py",
    "nuguard/redteam/target/session.py",
    "nuguard/redteam/executor/orchestrator.py",
    "nuguard/cli/commands/redteam.py",
    "nuguard/config.py",
    "nuguard/sbom/models.py",
    "nuguard/models/sbom.py",
    "nuguard/redteam/tests/test_target_client.py",
    "nuguard/redteam/tests/test_interaction_profile.py",
    "nuguard/redteam/tests/test_orchestrator_outcome.py",
    "tests/test_nuguard_config.py",
]

MYPY_TARGETS = [
    "nuguard/redteam/target/client.py",
    "nuguard/redteam/target/interaction_profile.py",
    "nuguard/redteam/target/session.py",
    "nuguard/redteam/executor/orchestrator.py",
    "nuguard/cli/commands/redteam.py",
    "nuguard/config.py",
]

BASELINE_MYPY_PATHS = {
    "nuguard/sbom/adapters/python/mcp_server.py",
    "nuguard/sbom/adapters/python/fastapi_adapter.py",
}


@dataclass
class ValidationResult:
    name: str
    command: list[str]
    status: str
    exit_code: int
    artifact_path: str
    summary: str


@dataclass
class ValidationRun:
    overall_status: str
    summary_path: str
    json_path: str
    results: list[ValidationResult]


def _extract_mypy_error_paths(output: str) -> set[str]:
    paths: set[str] = set()
    for line in output.splitlines():
        if ": error:" not in line:
            continue
        path, _, _ = line.partition(": error:")
        if path:
            paths.add(path.split(":", 1)[0])
    return paths


def _classify_mypy_result(exit_code: int, output: str) -> tuple[str, str]:
    if exit_code == 0:
        return "passed", "Touched-module type checks passed."

    error_paths = _extract_mypy_error_paths(output)
    if error_paths and error_paths.issubset(BASELINE_MYPY_PATHS):
        details = ", ".join(sorted(error_paths))
        return "warning", f"Known baseline mypy errors only: {details}."

    if not error_paths:
        return "failed", "mypy failed without parsable error paths."

    details = ", ".join(sorted(error_paths))
    return "failed", f"New or non-baseline mypy errors detected: {details}."


def _classify_smoke_result(exit_code: int, output: str) -> tuple[str, str]:
    if exit_code != 0:
        return "failed", "CLI smoke run failed."
    if "Target HTTP 401" in output:
        return "warning", "CLI smoke run completed; target rejected bootstrap auth with HTTP 401."
    return "passed", "CLI smoke run completed successfully."


def _run_command(name: str, command: list[str], artifact_path: Path) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    artifact_path.write_text(output)
    return completed.returncode, output


def _build_results() -> list[ValidationResult]:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[ValidationResult] = []

    commands: list[tuple[str, list[str]]] = [
        (
            "focused-tests",
            [
                "uv",
                "run",
                "pytest",
                "nuguard/redteam/tests/test_interaction_profile.py",
                "nuguard/redteam/tests/test_target_client.py",
                "nuguard/redteam/tests/test_orchestrator_outcome.py",
                "tests/test_nuguard_config.py",
                "-q",
            ],
        ),
        (
            "redteam-tests",
            ["uv", "run", "pytest", "nuguard/redteam/tests", "-q"],
        ),
        (
            "cli-integration-tests",
            ["uv", "run", "pytest", "tests/cli", "tests/redteam", "-q"],
        ),
        (
            "ruff-touched",
            ["uv", "run", "ruff", "check", "--select", "F,I", *RUFF_TARGETS],
        ),
        (
            "mypy-touched",
            ["uv", "run", "mypy", *MYPY_TARGETS],
        ),
    ]

    if SMOKE_CONFIG_PATH.exists():
        commands.append(
            (
                "cli-smoke",
                [
                    "uv",
                    "run",
                    "nuguard",
                    "redteam",
                    "--config",
                    str(SMOKE_CONFIG_PATH.relative_to(WORKSPACE_ROOT)),
                    "--output",
                    str(SMOKE_OUTPUT_PATH.relative_to(WORKSPACE_ROOT)),
                ],
            )
        )

    for name, command in commands:
        artifact_path = ARTIFACT_DIR / f"{name}.txt"
        exit_code, output = _run_command(name, command, artifact_path)

        status = "passed" if exit_code == 0 else "failed"
        summary = f"Command exited with code {exit_code}."
        if name == "mypy-touched":
            status, summary = _classify_mypy_result(exit_code, output)
        elif name == "cli-smoke":
            status, summary = _classify_smoke_result(exit_code, output)

        results.append(
            ValidationResult(
                name=name,
                command=command,
                status=status,
                exit_code=exit_code,
                artifact_path=str(artifact_path.relative_to(WORKSPACE_ROOT)),
                summary=summary,
            )
        )

    return results


def _overall_status(results: list[ValidationResult]) -> str:
    statuses = {result.status for result in results}
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    return "passed"


def _write_markdown(run: ValidationRun) -> None:
    lines = [
        "# Redteam Validation Summary",
        "",
        f"Overall status: {run.overall_status}",
        "",
        "| Check | Status | Exit | Artifact | Summary |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for result in run.results:
        lines.append(
            "| {name} | {status} | {exit_code} | {artifact_path} | {summary} |".format(
                name=result.name,
                status=result.status,
                exit_code=result.exit_code,
                artifact_path=result.artifact_path,
                summary=result.summary.replace("|", "/"),
            )
        )
    lines.append("")
    lines.append(f"JSON artifact: {run.json_path}")
    SUMMARY_PATH.write_text("\n".join(lines))


def main() -> int:
    results = _build_results()
    run = ValidationRun(
        overall_status=_overall_status(results),
        summary_path=str(SUMMARY_PATH.relative_to(WORKSPACE_ROOT)),
        json_path=str(JSON_PATH.relative_to(WORKSPACE_ROOT)),
        results=results,
    )
    JSON_PATH.write_text(json.dumps(asdict(run), indent=2))
    _write_markdown(run)

    print(f"Validation status: {run.overall_status}")
    print(f"Summary: {run.summary_path}")
    print(f"JSON: {run.json_path}")

    return 1 if run.overall_status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())