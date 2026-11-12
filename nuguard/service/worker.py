"""Worker process entry point: ``python -m nuguard.service.worker``.

Protocol (one job per process):

* **stdin**: a single JSON object ``{"spec": JobSpec, "token": str | null}``.
  The token arrives only here — never in argv or the environment.
* **stdout**: JSON lines — ``{"type": "phase", "phase": …}``,
  ``{"type": "commit", "sha": …}``, then exactly one of
  ``{"type": "result", "path": …, "sbom_path": …}`` or
  ``{"type": "error", "code": …, "detail": …}``.

Library output that writes to stdout is redirected to stderr so it can never
corrupt the protocol stream. The parent builds this process's environment
explicitly (see ``dispatcher.worker_env``) and starts it in a new session so
the whole process tree can be killed on timeout or cancel.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, TextIO

TEST_HOOKS_ENV = "NUGUARD_SERVICE_TEST_HOOKS"


def _apply_rlimits() -> None:
    """Linux-only address-space / file-size / process limits (best effort)."""
    if not sys.platform.startswith("linux"):
        return
    import resource  # noqa: PLC0415

    limits = {
        "RLIMIT_AS": os.environ.get("NUGUARD_WORKER_RLIMIT_AS"),
        "RLIMIT_FSIZE": os.environ.get("NUGUARD_WORKER_RLIMIT_FSIZE"),
        "RLIMIT_NPROC": os.environ.get("NUGUARD_WORKER_RLIMIT_NPROC"),
    }
    for name, value in limits.items():
        if not value:
            continue
        try:
            limit = int(value)
            resource.setrlimit(getattr(resource, name), (limit, limit))
        except (ValueError, OSError, AttributeError):
            pass


def _channel() -> TextIO:
    """Private copy of stdout for the protocol; fd 1 then points at stderr."""
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return proto


def main() -> int:
    proto = _channel()

    def emit(message: dict[str, Any]) -> None:
        proto.write(json.dumps(message) + "\n")
        proto.flush()

    _apply_rlimits()
    try:
        envelope = json.loads(sys.stdin.readline() or "{}")
    except ValueError:
        emit({"type": "error", "code": "worker_lost", "detail": "invalid worker input"})
        return 2
    sys.stdin.close()

    from nuguard.service.pipeline import JobSpec, PipelineError, run_pipeline  # noqa: PLC0415

    spec = JobSpec.model_validate(envelope.get("spec") or {})
    token = envelope.get("token") or None
    del envelope
    scratch = Path.cwd()

    hooks = spec.test_hooks if os.environ.get(TEST_HOOKS_ENV) == "1" else []
    try:
        if hooks:
            _run_test_hooks(hooks, spec, scratch, emit)
            return 0
        body, sbom = run_pipeline(spec, token, scratch, emit)
    except PipelineError as exc:
        emit({"type": "error", "code": exc.code, "detail": exc.detail})
        return 0
    except Exception:  # never surface tracebacks: they can carry paths
        emit({"type": "error", "code": "analysis_crashed", "detail": "the scan failed"})
        return 0
    finally:
        token = None

    result_path = scratch / "result.json"
    sbom_path = scratch / "sbom.json"
    result_path.write_text(body.model_dump_json(), encoding="utf-8")
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    emit({"type": "result", "path": str(result_path), "sbom_path": str(sbom_path)})
    return 0


def _run_test_hooks(hooks: list[str], spec: Any, scratch: Path, emit: Any) -> None:
    """Deterministic behaviours for process-level tests (enabled only by the parent)."""
    for hook in hooks:
        if hook == "dump_env":
            emit({"type": "test_env", "env": dict(os.environ), "argv": sys.argv})
        elif hook == "pid":
            emit({"type": "test_pid", "pid": os.getpid()})
        elif hook == "grandchild":
            import subprocess  # noqa: PLC0415

            # Mimics a scanner started in its own session (scanner_runtime does this).
            child = subprocess.Popen(["sleep", "300"], start_new_session=True)
            emit({"type": "test_grandchild", "pid": child.pid})
        elif hook.startswith("sleep:"):
            emit({"type": "phase", "phase": "cloning"})
            time.sleep(float(hook.split(":", 1)[1]))
        elif hook == "crash":
            os._exit(3)
        elif hook.startswith("error:"):
            emit({"type": "error", "code": hook.split(":", 1)[1], "detail": "test error"})
            return
        elif hook.startswith("stub"):
            _stub_result(spec, scratch, emit, partial=hook == "stub_partial")
            return


def _stub_result(spec: Any, scratch: Path, emit: Any, *, partial: bool) -> None:
    from nuguard import __version__  # noqa: PLC0415
    from nuguard.models.tool_run import ToolRun, ToolRunStatus  # noqa: PLC0415
    from nuguard.service import SERVICE_VERSION  # noqa: PLC0415
    from nuguard.service.models import InventorySummary, Provenance  # noqa: PLC0415
    from nuguard.service.results import (  # noqa: PLC0415
        ScanBody,
        build_coverage,
        severity_summary,
        status_for,
    )

    emit({"type": "phase", "phase": "cloning"})
    emit({"type": "commit", "sha": "0" * 40})
    runs = [
        ToolRun(tool=t, status=ToolRunStatus.FAILED if partial and t == "osv" else ToolRunStatus.OK,
                reason="network unreachable" if partial and t == "osv" else None)
        for t in spec.tools
    ]
    coverage = build_coverage(runs, spec.tools, languages=["python"], files_scanned=1,
                              files_skipped=0)
    body = ScanBody(
        status=status_for(coverage),
        commit_sha="0" * 40,
        provenance=Provenance(
            nuguard_version=__version__, service_version=SERVICE_VERSION,
            rulepack_sha256="0" * 64, options_effective=spec.options,
        ),
        coverage=coverage,
        summary=severity_summary([]),
        findings=[],
        inventory=InventorySummary(),
    )
    (scratch / "result.json").write_text(body.model_dump_json(), encoding="utf-8")
    (scratch / "sbom.json").write_text(json.dumps({"target": spec.repo_url, "nodes": [],
                                                   "edges": [], "deps": []}), encoding="utf-8")
    emit({"type": "result", "path": str(scratch / "result.json"),
          "sbom_path": str(scratch / "sbom.json")})


if __name__ == "__main__":
    sys.exit(main())
