"""Regression test for docs/sbom-fix2.md #5's "Generic" TOOL false positive:
a stray bare `rq` token match inside a TypeScript file (unrelated to the
Python `rq` task-queue package) at exam-clone.service.ts:898.

The job-scheduling keyword group (celery/rq/dramatiq/arq/APScheduler/...) is
now its own RegexAdapter, scoped away from TS/JS files via skip_extensions,
since these are Python-ecosystem package names with no TS equivalents.
"""

from __future__ import annotations

from nuguard.sbom.adapters.base import RegexAdapter
from nuguard.sbom.adapters.registry import default_registry


def _adapter(name: str) -> RegexAdapter:
    for a in default_registry():
        if isinstance(a, RegexAdapter) and a.name == name:
            return a
    raise AssertionError(f"no RegexAdapter named {name!r} in default_registry()")


class TestJobSchedulingScopedAwayFromTsJs:
    def test_job_scheduling_adapter_skips_ts_and_js_extensions(self) -> None:
        adapter = _adapter("tool_job_scheduling")
        assert adapter.skip_extensions is not None
        assert {".ts", ".tsx", ".js", ".jsx"} <= adapter.skip_extensions

    def test_bare_rq_token_still_matches_in_python_context(self) -> None:
        """The pattern itself is unchanged — only file-extension scope moved
        the check off TS/JS; a genuine Python `rq` usage still matches."""
        adapter = _adapter("tool_job_scheduling")
        code = "from rq import Queue\nqueue = Queue(connection=redis_conn)\n"
        detection = adapter.detect(code)
        assert detection is not None

    def test_generic_tool_adapter_no_longer_matches_bare_rq(self) -> None:
        adapter = _adapter("tool_generic")
        code = (
            "async function getSpacedRepetitionSchedule(rq: ExamRequest) {\n"
            "  return this.examRepository.findDueCards(rq);\n"
            "}\n"
        )
        assert adapter.detect(code) is None
