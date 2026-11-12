"""FastAPI application for the remote scan service (all routes under ``/v1``)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nuguard import __version__
from nuguard.common.logging import get_logger
from nuguard.service import SERVICE_VERSION
from nuguard.service.auth import Authenticator, Caller, FailedAuthLimiter
from nuguard.service.dispatcher import Dispatcher, WorkerRunner, job_view, problem
from nuguard.service.models import (
    TERMINAL_STATUSES,
    Capabilities,
    HealthStatus,
    ProblemDetail,
    ScanCreateRequest,
    ScanJob,
    ScanList,
    ScanResult,
    ScanStatus,
    ToolVersion,
)
from nuguard.service.settings import ServiceSettings
from nuguard.service.store import JobRecord, JobStore, ensure_private_dir, new_job_id, utcnow
from nuguard.service.vault import TokenVault

_log = get_logger("nuguard.service")

# Version of the HTTP contract (OpenAPI info.version), independent of the package version.
API_VERSION = "1.0"
IDEMPOTENCY_WINDOW = timedelta(hours=24)
MAX_WAIT_S = 120
RETENTION_INTERVAL_S = 15 * 60
_PREFER_WAIT_RE = re.compile(r"(?:^|[,;\s])wait=(\d+)")
_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ProblemDetail} for code in (401, 404, 409, 410, 413, 422, 429)
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers or {}


def _problem_response(status: int, code: str, detail: str | None = None,
                      headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(problem(code, detail, status=status), status_code=status,
                        headers=headers, media_type="application/problem+json")


class BodyLimitMiddleware:
    """Reject request bodies over *limit* bytes (declared or streamed) with 413."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers") or []:
            if name == b"content-length" and value.isdigit() and int(value) > self.limit:
                await _problem_response(413, "request_too_large",
                                        f"Body exceeds {self.limit} bytes")(scope, receive, send)
                return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise ApiError(413, "request_too_large", f"Body exceeds {self.limit} bytes")
            return message

        await self.app(scope, limited, send)


def create_app(
    settings: ServiceSettings,
    *,
    runner: WorkerRunner | None = None,
    store: JobStore | None = None,
    test_hooks: dict[str, list[str]] | None = None,
    validate: bool = True,
) -> FastAPI:
    """Build the app. *runner* / *store* / *test_hooks* exist for tests."""
    if validate:
        settings.validate_for_startup()
    authenticator = Authenticator.from_settings(settings)
    limiter = FailedAuthLimiter(settings.auth_fail_limit_per_minute)
    vault = TokenVault()
    state: dict[str, Any] = {}

    def base_url() -> str:
        return (settings.public_base_url or "").rstrip("/")

    def links_for(job_id: str) -> dict[str, str]:
        root = f"{base_url()}/v1/scans/{job_id}"
        return {"self": root, "result": f"{root}/result",
                "sarif": f"{root}/result?format=sarif", "sbom": f"{root}/sbom"}

    async def on_terminal(record: JobRecord) -> None:
        from nuguard.service.webhooks import deliver  # noqa: PLC0415

        try:
            request = ScanCreateRequest.model_validate_json(record.request_json)
        except ValueError:
            return
        if request.callback_url:
            body = job_view(record, links_for(record.id)).model_dump_json().encode()
            await deliver(request.callback_url, body, settings.webhooks)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        from nuguard.service.store.sqlite import SqliteJobStore  # noqa: PLC0415

        ensure_private_dir(settings.jobs.scratch_dir)
        job_store = store or SqliteJobStore(
            settings.jobs.db_path, inline_result_max_bytes=settings.jobs.inline_result_max_bytes
        )
        dispatcher = Dispatcher(settings, job_store, vault, runner, links_for=links_for,
                                on_terminal=on_terminal, test_hooks=test_hooks)
        state.update(store=job_store, dispatcher=dispatcher, started=time.time())
        await dispatcher.start()
        retention = asyncio.create_task(_retention_loop(settings, job_store, dispatcher))
        try:
            yield
        finally:
            retention.cancel()
            await dispatcher.stop()
            if store is None:
                job_store.close()

    app = FastAPI(
        title="NuGuard Remote Scan API",
        version=API_VERSION,
        openapi_url="/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.service = state
    app.add_middleware(BodyLimitMiddleware, limit=settings.max_body_bytes)

    # ── errors: never echo inputs, stack traces or paths ────────────────────

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return _problem_response(exc.status, exc.code, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        messages = []
        for err in exc.errors()[:10]:
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
            messages.append(f"{loc}: {msg}" if loc else msg)
        return _problem_response(422, "invalid_request", "; ".join(messages)[:1000])

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _problem_response(exc.status_code, code)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        _log.error("unhandled error: %s", type(exc).__name__)
        return _problem_response(500, "internal_error")

    # ── helpers ─────────────────────────────────────────────────────────────

    def remote_addr(request: Request) -> str:
        if settings.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def store_() -> JobStore:
        return state["store"]  # type: ignore[no-any-return]

    def dispatcher_() -> Dispatcher:
        return state["dispatcher"]  # type: ignore[no-any-return]

    def authenticate(request: Request) -> Caller:
        address = remote_addr(request)
        if limiter.blocked(address):
            raise ApiError(429, "rate_limited", "Too many failed authentications",
                           {"Retry-After": "60"})
        caller = authenticator.authenticate(request.headers.get("authorization"))
        if caller is None:
            limiter.record_failure(address)
            store_().audit("auth_fail", remote_addr=address)
            raise ApiError(401, "unauthorized", "Missing or invalid API key",
                           {"WWW-Authenticate": "Bearer"})
        return caller

    async def require_caller(request: Request) -> Caller:
        return authenticate(request)

    def owned(caller: Caller, job_id: str) -> JobRecord:
        record = store_().get(job_id)
        if record is None or record.owner_id != caller.id:
            raise ApiError(404, "not_found", "No such scan")
        return record

    def view(record: JobRecord) -> ScanJob:
        return job_view(record, links_for(record.id))

    # ── routes ──────────────────────────────────────────────────────────────

    @app.post("/v1/scans", status_code=202, response_model=ScanJob,
              responses={200: {"model": ScanJob}, **_PROBLEM_RESPONSES})
    async def create_scan(
        request: Request,
        body: ScanCreateRequest,
        caller: Caller = Depends(require_caller),
        idempotency_key: str | None = Header(default=None, max_length=128),
        prefer: str | None = Header(default=None),
    ) -> Response:
        _check_policy(body, settings)
        public = body.model_dump(mode="json", exclude={"github_token"})
        request_json = json.dumps(public, sort_keys=True, separators=(",", ":"))
        request_sha = hashlib.sha256(request_json.encode()).hexdigest()
        job_store = store_()
        now = utcnow()

        if idempotency_key:
            existing = job_store.find_idempotent(caller.id, idempotency_key,
                                                 now - IDEMPOTENCY_WINDOW)
            if existing is not None:
                if existing.request_sha256 != request_sha:
                    raise ApiError(409, "idempotency_conflict",
                                   "Idempotency-Key was used with a different request")
                return JSONResponse(view(existing).model_dump(mode="json"), status_code=200,
                                    headers={"Location": links_for(existing.id)["self"]})

        active = ("queued", "running")
        if job_store.count(("queued",), caller.id) >= caller.max_queued_scans:
            raise ApiError(429, "rate_limited", "Too many queued scans for this key",
                           {"Retry-After": "30"})
        if job_store.count(active) >= settings.jobs.queue_limit:
            raise ApiError(429, "queue_full", "The scan queue is full", {"Retry-After": "30"})

        if body.github_token is not None and body.github_token.get_secret_value().startswith("ghp_"):
            _log.warning("classic GitHub token used; prefer a fine-grained read-only token",
                         extra={"owner_id": caller.id})
        from nuguard.common.url_sanitization import sanitize_repository_url  # noqa: PLC0415

        record = JobRecord(
            id=new_job_id(),
            owner_id=caller.id,
            status="queued",
            phase="queued",
            repo_url=sanitize_repository_url(body.repo_url),
            ref_requested=body.ref,
            client_reference=body.client_reference,
            request_json=request_json,
            request_sha256=request_sha,
            idempotency_key=idempotency_key,
            has_token=body.github_token is not None,
            created_at=now,
        )
        if body.github_token is not None:
            vault.put(record.id, body.github_token)
        try:
            job_store.create(record)
        except Exception:
            vault.discard(record.id)
            # A concurrent request with the same Idempotency-Key won the insert.
            winner = (job_store.find_idempotent(caller.id, idempotency_key, now - IDEMPOTENCY_WINDOW)
                      if idempotency_key else None)
            if winner is None:
                raise
            if winner.request_sha256 != request_sha:
                raise ApiError(409, "idempotency_conflict",
                               "Idempotency-Key was used with a different request") from None
            return JSONResponse(view(winner).model_dump(mode="json"), status_code=200,
                                headers={"Location": links_for(winner.id)["self"]})
        job_store.audit("create", owner_id=caller.id, job_id=record.id,
                        repo_url=record.repo_url, remote_addr=remote_addr(request),
                        request_sha256=request_sha)
        _log.info("scan queued", extra={"job_id": record.id, "owner_id": caller.id})
        dispatcher_().notify()

        wait = _prefer_wait(prefer)
        headers = {"Location": links_for(record.id)["self"]}
        if wait:
            headers["Preference-Applied"] = f"wait={wait}"
            await dispatcher_().wait_for(record.id, wait)
            current = job_store.get(record.id) or record
            if ScanStatus(current.status) in TERMINAL_STATUSES:
                return JSONResponse(view(current).model_dump(mode="json"), status_code=200,
                                    headers=headers)
            record = current
        return JSONResponse(view(record).model_dump(mode="json"), status_code=202, headers=headers)

    @app.get("/v1/scans", response_model=ScanList, responses=_PROBLEM_RESPONSES)
    async def list_scans(
        caller: Caller = Depends(require_caller),
        status: ScanStatus | None = None,
        repo: str | None = Query(default=None, max_length=2048),
        cursor: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ScanList:
        try:
            records, next_cursor = store_().list_jobs(
                caller.id, status=status.value if status else None, repo=repo,
                cursor=cursor, limit=limit,
            )
        except ValueError:
            raise ApiError(422, "invalid_request", "invalid cursor") from None
        return ScanList(items=[view(r) for r in records], next_cursor=next_cursor)

    @app.get("/v1/scans/{job_id}", response_model=ScanJob, responses=_PROBLEM_RESPONSES)
    async def get_scan(job_id: str, caller: Caller = Depends(require_caller)) -> ScanJob:
        return view(owned(caller, job_id))

    @app.get("/v1/scans/{job_id}/result", response_model=ScanResult,
             responses={**_PROBLEM_RESPONSES,
                        200: {"content": {"application/sarif+json": {}}}})
    async def get_result(
        request: Request,
        job_id: str,
        caller: Caller = Depends(require_caller),
        format: str = Query(default="json", pattern="^(json|sarif)$"),
    ) -> Response:
        record = owned(caller, job_id)
        result_text = _terminal_result(store_(), record)[0]
        store_().audit("read_result", owner_id=caller.id, job_id=job_id,
                       remote_addr=remote_addr(request))
        if format == "sarif":
            from nuguard.service.results import render_sarif  # noqa: PLC0415

            sarif = render_sarif(ScanResult.model_validate_json(result_text))
            return Response(sarif, media_type="application/sarif+json")
        return Response(result_text, media_type="application/json")

    @app.get("/v1/scans/{job_id}/sbom", responses={**_PROBLEM_RESPONSES,
                                                   200: {"content": {"application/json": {},
                                                                     "text/markdown": {}}}})
    async def get_sbom(
        job_id: str,
        caller: Caller = Depends(require_caller),
        format: str = Query(default="json", pattern="^(json|cyclonedx|cyclonedx-ext|markdown)$"),
    ) -> Response:
        from nuguard.sbom.public_api import SbomRenderRequest, render_sbom  # noqa: PLC0415
        from nuguard.sbom.serializer import AiSbomSerializer  # noqa: PLC0415

        record = owned(caller, job_id)
        sbom_json = _terminal_result(store_(), record)[1]
        if not sbom_json:
            raise ApiError(404, "not_found", "No AI-SBOM was stored for this scan")
        doc = AiSbomSerializer.from_json(sbom_json)
        rendered = await render_sbom(SbomRenderRequest(format=format), sbom=doc)  # type: ignore[arg-type]
        return Response(rendered.content, media_type=rendered.media_type)

    @app.post("/v1/scans/{job_id}/cancel", status_code=202, response_model=ScanJob,
              responses=_PROBLEM_RESPONSES)
    async def cancel_scan(
        request: Request, job_id: str, caller: Caller = Depends(require_caller)
    ) -> Response:
        record = owned(caller, job_id)
        if ScanStatus(record.status) not in TERMINAL_STATUSES:
            dispatcher_().cancel(job_id)
            store_().audit("cancel", owner_id=caller.id, job_id=job_id,
                           remote_addr=remote_addr(request))
            if record.status == "running":
                await dispatcher_().wait_for(job_id, 2)
        current = store_().get(job_id) or record
        return JSONResponse(view(current).model_dump(mode="json"), status_code=202)

    @app.get("/v1/healthz", response_model=HealthStatus)
    async def healthz() -> HealthStatus:
        return HealthStatus(status="ok")

    @app.get("/v1/readyz", response_model=HealthStatus, responses={503: {"model": HealthStatus}})
    async def readyz() -> Response:
        failing = _readiness_failures(settings, state.get("store"))
        status = HealthStatus(status="not_ready" if failing else "ok", failing=failing)
        return JSONResponse(status.model_dump(mode="json"), status_code=503 if failing else 200)

    @app.get("/v1/capabilities", response_model=Capabilities, responses=_PROBLEM_RESPONSES)
    async def capabilities(caller: Caller = Depends(require_caller)) -> Capabilities:
        from nuguard.analysis.static_analyzer import tool_version  # noqa: PLC0415
        from nuguard.service.results import tool_versions  # noqa: PLC0415

        pinned = {t.name: t.version for t in tool_versions([])}
        allowed = settings.analysis_defaults.tools
        versions = [
            ToolVersion(name=t, version=pinned.get(t) or await asyncio.to_thread(tool_version, t))
            for t in allowed
        ]
        return Capabilities(
            nuguard_version=__version__,
            service_version=SERVICE_VERSION,
            tools=versions,
            tools_allowed=list(allowed),
            network_default=settings.analysis_defaults.network,
            llm_available=settings.llm.enabled,
            allowed_git_hosts=list(settings.git.allowed_hosts),
            limits={
                "max_concurrent": settings.jobs.max_concurrent,
                "job_timeout_s": settings.jobs.job_timeout_s,
                "queue_limit": settings.jobs.queue_limit,
                "result_ttl_hours": settings.jobs.result_ttl_hours,
                "max_repo_bytes": settings.git.max_repo_bytes,
                "max_body_bytes": settings.max_body_bytes,
                "max_wait_s": MAX_WAIT_S,
            },
        )

    if settings.metrics:
        @app.get("/v1/metrics", response_class=PlainTextResponse, include_in_schema=False)
        async def metrics() -> str:
            return _prometheus(dispatcher_(), store_())

    return app


# ── request policy ───────────────────────────────────────────────────────────


def _check_policy(body: ScanCreateRequest, settings: ServiceSettings) -> None:
    """Checks that depend on server configuration (structural ones are in the model)."""
    from nuguard.sbom.extractor.git_clone import CloneError, check_clone_url  # noqa: PLC0415
    from nuguard.service.pipeline import (  # noqa: PLC0415
        PipelineError,
        combine_subpaths,
        split_repo_url,
    )

    try:
        clone_url, _ref, url_subpath = split_repo_url(body.repo_url, body.ref)
        combine_subpaths(url_subpath, body.options.subpath)
        check_clone_url(clone_url, tuple(settings.git.allowed_hosts))
    except CloneError as exc:
        code = "host_not_allowed" if exc.code == "host_not_allowed" else "invalid_request"
        raise ApiError(422, code, str(exc)) from None
    except PipelineError as exc:
        raise ApiError(422, "invalid_request", exc.detail) from None
    allowed = set(settings.analysis_defaults.tools)
    if body.options.tools is not None:
        rejected = sorted({str(t) for t in body.options.tools} - allowed)
        if rejected:
            raise ApiError(422, "tool_not_allowed", f"Tools not allowed: {', '.join(rejected)}")
    if body.options.use_llm and not settings.llm.enabled:
        raise ApiError(422, "llm_not_available", "LLM use is not enabled on this server")
    if body.options.network and not settings.analysis_defaults.network:
        body.options.network = False  # narrow, never widen
    if body.callback_url is not None:
        from nuguard.service.webhooks import host_allowed  # noqa: PLC0415

        if not host_allowed(body.callback_url, settings.webhooks):
            raise ApiError(422, "invalid_request", "callback_url host is not allowed")


def _prefer_wait(prefer: str | None) -> int:
    if not prefer:
        return 0
    match = _PREFER_WAIT_RE.search(prefer)
    return min(int(match.group(1)), MAX_WAIT_S) if match else 0


def _terminal_result(job_store: JobStore, record: JobRecord) -> tuple[str, str | None]:
    status = ScanStatus(record.status)
    if status == ScanStatus.EXPIRED:
        raise ApiError(410, "expired", "The result has expired")
    if status not in TERMINAL_STATUSES:
        raise ApiError(409, "not_finished", "The scan has not finished",
                       {"Retry-After": "5"})
    if status in (ScanStatus.FAILED, ScanStatus.CANCELLED):
        raise ApiError(409, "no_result", f"The scan {status.value}; see the job's error")
    stored = job_store.get_result(record.id)
    if stored is None:
        raise ApiError(410, "expired", "The result is no longer available")
    return stored


def _readiness_failures(settings: ServiceSettings, job_store: JobStore | None) -> list[str]:
    from nuguard.service.dispatcher import worker_env  # noqa: F401,PLC0415

    failing: list[str] = []
    if job_store is None or not job_store.check_writable():
        failing.append("database")
    scratch = Path(settings.jobs.scratch_dir)
    try:
        ensure_private_dir(scratch)
        probe = scratch / ".ready"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        failing.append("scratch_dir")
    if shutil.which("git") is None:
        failing.append("git")
    binaries = {"semgrep": "semgrep", "checkov": "checkov", "trivy": "trivy", "grype": "grype"}
    for tool in settings.analysis_defaults.tools:
        if tool in binaries and shutil.which(binaries[tool]) is None:
            failing.append(tool)
    return failing


async def _retention_loop(settings: ServiceSettings, job_store: JobStore,
                          dispatcher: Dispatcher) -> None:
    last_vacuum = time.monotonic()
    while True:
        try:
            await asyncio.to_thread(_retention_pass, settings, job_store, dispatcher)
            if time.monotonic() - last_vacuum > 7 * 24 * 3600:
                await asyncio.to_thread(job_store.vacuum)
                last_vacuum = time.monotonic()
        except Exception as exc:
            _log.error("retention pass failed: %s", type(exc).__name__)
        await asyncio.sleep(RETENTION_INTERVAL_S)


def _retention_pass(settings: ServiceSettings, job_store: JobStore,
                    dispatcher: Dispatcher) -> None:
    expired = job_store.expire(utcnow())
    if expired:
        _log.info("expired %d result(s)", len(expired))
    scratch = Path(settings.jobs.scratch_dir)
    cutoff = time.time() - 2 * settings.jobs.job_timeout_s
    running = set(dispatcher._tasks)
    if scratch.is_dir():
        for entry in scratch.iterdir():
            if entry.name in running or not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue


def _prometheus(dispatcher: Dispatcher, job_store: JobStore) -> str:
    lines = ["# TYPE nuguard_scans_total counter"]
    for status, count in sorted(dispatcher.metrics["scans_total"].items()):
        lines.append(f'nuguard_scans_total{{status="{status}"}} {count}')
    lines.append("# TYPE nuguard_queue_depth gauge")
    lines.append(f"nuguard_queue_depth {job_store.count(('queued',))}")
    lines.append("# TYPE nuguard_tool_runs_total counter")
    for (tool, status), count in sorted(dispatcher.metrics["tool_runs_total"].items()):
        lines.append(f'nuguard_tool_runs_total{{tool="{tool}",status="{status}"}} {count}')
    durations = dispatcher.metrics["durations"]
    lines.append("# TYPE nuguard_scan_duration_seconds histogram")
    for bound in (10, 30, 60, 120, 300, 600, 900):
        lines.append(
            f'nuguard_scan_duration_seconds_bucket{{le="{bound}"}} '
            f"{sum(1 for d in durations if d <= bound)}"
        )
    lines.append(f'nuguard_scan_duration_seconds_bucket{{le="+Inf"}} {len(durations)}')
    lines.append(f"nuguard_scan_duration_seconds_sum {sum(durations):.3f}")
    lines.append(f"nuguard_scan_duration_seconds_count {len(durations)}")
    return "\n".join(lines) + "\n"
