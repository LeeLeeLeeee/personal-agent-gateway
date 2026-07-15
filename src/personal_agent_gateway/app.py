import json
import logging
import re
import traceback
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from personal_agent_gateway.api import (
    agents_router,
    artifacts_router,
    audit_router,
    auth_router,
    capabilities_router,
    health_router,
    hooks_router,
    jobs_router,
    operations_router,
    personas_router,
    rules_router,
    schedules_router,
    session_config_router,
    settings_router,
    team_runs_router,
    teams_router,
)
from personal_agent_gateway.artifacts import ArtifactStore
from personal_agent_gateway.agents import AgentRegistry
from personal_agent_gateway.api.auth import LoginRateLimiter
from personal_agent_gateway.api.chat_sessions import (
    ChatSessionContext,
    create_chat_sessions_router,
)
from personal_agent_gateway.audit import AuditService
from personal_agent_gateway.auth_sessions import AuthSessionService
from personal_agent_gateway.auth_store import AuthStore
from personal_agent_gateway.backup import BackupService
from personal_agent_gateway.capabilities import CapabilityRegistry
from personal_agent_gateway.config import AppConfig, load_config
from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.emergency_stop import EmergencyStopService
from personal_agent_gateway.health import HealthService
from personal_agent_gateway.hook_loop import HookLoop
from personal_agent_gateway.hook_runner import HookRunner
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hook_secrets import HookSecretStore
from personal_agent_gateway.hooks import HookService
from personal_agent_gateway.intake import IntakeGate
from personal_agent_gateway.job_worker import JobWorker
from personal_agent_gateway.jobs import JobService
from personal_agent_gateway.model_client import ClaudeModelClient, CodexModelClient, ModelClient
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.runtime import AgentRuntime
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory
from personal_agent_gateway.rule_sets import RuleSetService
from personal_agent_gateway.run_state import SessionRunRegistry, TeamRunRegistry
from personal_agent_gateway.runners.agent import AgentRunner
from personal_agent_gateway.runners.capture import CaptureRunner
from personal_agent_gateway.runners.ffmpeg import FfmpegRunner
from personal_agent_gateway.runners.shell import ShellRunner
from personal_agent_gateway.schedules import ScheduleService
from personal_agent_gateway.scheduler_loop import SchedulerLoop
from personal_agent_gateway.security_settings import SecuritySettingsService
from personal_agent_gateway.session_activity import SessionActivityPublisher, SessionActivityService
from personal_agent_gateway.sources.email import ImapEmailAdapter
from personal_agent_gateway.team_directory import TeamService
from personal_agent_gateway.team_runtime import TeamRuntime
from personal_agent_gateway.teams import TeamAgent, TeamRunService
from personal_agent_gateway.transcript import TranscriptStore


_LOGGER = logging.getLogger("personal_agent_gateway.errors")
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def create_app(config: AppConfig | None = None, runtime: AgentRuntime | None = None) -> FastAPI:
    app_config = config or load_config()
    transcript = TranscriptStore(app_config.session_dir)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.team_run_service.interrupt_active_runs()
        application.state.job_service.recover_interrupted_jobs()
        await application.state.job_worker.start()
        for job in application.state.job_service.list_jobs(
            statuses=["queued"],
            sources=["manual", "schedule", "api"],
        ):
            await application.state.job_worker.enqueue(job.id)
        application.state.hook_run_service.recover_interrupted_runs()
        await application.state.scheduler_loop.start()
        await application.state.hook_runner.start()
        await application.state.hook_loop.start()
        try:
            yield
        finally:
            await application.state.scheduler_loop.stop()
            await application.state.hook_loop.stop()
            await application.state.hook_runner.stop()
            team_run_ids = await application.state.team_run_registry.cancel_all(
                reason="shutdown"
            )
            for team_run_id in team_run_ids:
                application.state.team_run_service.interrupt_run(
                    team_run_id,
                    include_canceled=True,
                )
            await application.state.job_worker.stop()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        supplied = request.headers.get("x-correlation-id", "")
        correlation_id = supplied if _CORRELATION_ID.fullmatch(supplied) else uuid4().hex
        request.state.correlation_id = correlation_id
        try:
            response = await call_next(request)
        except Exception as exc:
            response = _internal_error_response(request, exc)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    app.state.transcript_store = transcript
    package_dir = Path(__file__).parent
    static_dir = package_dir / "static"
    frontend_assets_dir = package_dir / "frontend_dist" / "assets"
    event_bus = EventBus()
    run_registry = SessionRunRegistry()
    team_run_registry = TeamRunRegistry()
    app.state.event_bus = event_bus
    app.state.run_registry = run_registry
    app.state.team_run_registry = team_run_registry
    runtime_factory = _attach_local_services(app, app_config, transcript, event_bus)
    assert app_config.backup_root is not None
    assert app_config.auth_dir is not None
    assert app_config.artifact_root is not None
    app.state.emergency_stop_service = EmergencyStopService(
        intake_gate=app.state.intake_gate,
        session_runs=run_registry,
        team_runs=team_run_registry,
        team_run_service=app.state.team_run_service,
        job_worker=app.state.job_worker,
        audit=app.state.audit_service,
    )
    app.state.backup_service = BackupService(
        database=app.state.database,
        backup_root=app_config.backup_root,
        auth_dir=app_config.auth_dir,
        session_dir=app_config.session_dir,
        artifact_root=app_config.artifact_root,
        workspace_root=app_config.workspace_root,
        intake_gate=app.state.intake_gate,
    )
    app.state.team_runtime = TeamRuntime(
        app.state.team_run_service,
        _team_model_factory(app_config),
        event_bus,
    )
    app.state.team_run_service.backfill_agent_avatars()

    injected_runtime = runtime
    if injected_runtime is not None and hasattr(injected_runtime, "attach_event_bus"):
        injected_runtime.attach_event_bus(app.state.session_activity_publisher)

    def active_runtime() -> AgentRuntime:
        if injected_runtime is not None:
            return injected_runtime
        return runtime_factory.create_runtime_for_active_session()

    def runtime_for_session(session_id: str) -> AgentRuntime:
        if injected_runtime is not None:
            if hasattr(injected_runtime, "for_session"):
                return injected_runtime.for_session(session_id)
            return injected_runtime
        return runtime_factory.create_runtime_for_session(session_id)

    chat_sessions_router = create_chat_sessions_router(
        ChatSessionContext(
            config=app_config,
            transcript=transcript,
            event_bus=event_bus,
            run_registry=run_registry,
            active_runtime=active_runtime,
            runtime_for_session=runtime_for_session,
            activity_service=app.state.session_activity_service,
            activity_publisher=app.state.session_activity_publisher,
            intake_gate=app.state.intake_gate,
        )
    )

    app.include_router(auth_router)
    app.include_router(chat_sessions_router)
    app.include_router(health_router)
    app.include_router(audit_router)
    app.include_router(capabilities_router)
    app.include_router(jobs_router)
    app.include_router(operations_router)
    app.include_router(artifacts_router)
    app.include_router(schedules_router)
    app.include_router(hooks_router)
    app.include_router(agents_router)
    app.include_router(session_config_router)
    app.include_router(settings_router)
    app.include_router(personas_router)
    app.include_router(team_runs_router)
    app.include_router(teams_router)
    app.include_router(rules_router)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_error_payload(
                request,
                code=f"http_{exc.status_code}",
                detail=exc.detail,
                retryable=_retryable_status(exc.status_code),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                request,
                code="validation_error",
                detail=exc.errors(),
                retryable=False,
            ),
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return _internal_error_response(request, exc)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _select_frontend_index(package_dir).read_text(encoding="utf-8")

    @app.get("/static/app.js")
    def app_script() -> FileResponse:
        return FileResponse(static_dir / "app.js", media_type="text/javascript")

    @app.get("/static/styles.css")
    def styles() -> FileResponse:
        return FileResponse(static_dir / "styles.css", media_type="text/css")

    app.mount("/static/vendor", StaticFiles(directory=static_dir / "vendor"), name="vendor")
    app.mount("/static/avatars", StaticFiles(directory=static_dir / "avatars"), name="avatars")
    if frontend_assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=frontend_assets_dir), name="frontend_assets")

    return app


def _error_payload(
    request: Request,
    *,
    code: str,
    detail: object,
    retryable: bool,
) -> dict[str, object]:
    return {
        "code": code,
        "detail": detail,
        "retryable": retryable,
        "correlation_id": getattr(request.state, "correlation_id", None),
    }


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429} or status_code >= 500


def _internal_error_response(request: Request, exc: Exception) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None)
    stack = [
        {
            "file": Path(frame.filename).name,
            "line": frame.lineno,
            "function": frame.name,
        }
        for frame in traceback.extract_tb(exc.__traceback__)[-20:]
    ]
    _LOGGER.error(
        json.dumps(
            {
                "event": "request.failed",
                "correlation_id": correlation_id,
                "exception_type": type(exc).__name__,
                "stack": stack,
            },
            ensure_ascii=False,
        )
    )
    return JSONResponse(
        status_code=500,
        content=_error_payload(
            request,
            code="internal_error",
            detail="Internal Server Error",
            retryable=True,
        ),
    )


def _attach_local_services(
    app: FastAPI, config: AppConfig, transcript: TranscriptStore, event_bus: EventBus
) -> AgentRuntimeFactory:
    assert config.app_db_path is not None
    assert config.artifact_root is not None
    assert config.temp_dir is not None
    assert config.auth_dir is not None
    db = Database(config.app_db_path)
    db.initialize()
    transcript.attach_database(db)
    auth_session_service = AuthSessionService(
        db,
        absolute_ttl_seconds=config.auth_session_absolute_seconds,
        idle_ttl_seconds=config.auth_session_idle_seconds,
    )
    session_activity_service = SessionActivityService(db)
    session_activity_publisher = SessionActivityPublisher(session_activity_service, event_bus)
    persona_service = PersonaService(db)
    team_run_service = TeamRunService(db, persona_service, config.workspace_root)
    team_directory_service = TeamService(db, persona_service)
    rule_set_service = RuleSetService(db)
    rule_set_service.seed_defaults()
    registry = CapabilityRegistry.default()
    job_service = JobService(db, registry)
    schedule_service = ScheduleService(db, registry)
    artifact_store = ArtifactStore(db, config.artifact_root)
    runtime_factory = AgentRuntimeFactory(config, transcript, job_service, session_activity_publisher)
    job_worker = JobWorker(
        job_service,
        artifact_store,
        {
            "ffmpeg": FfmpegRunner(
                ffmpeg_binary=config.ffmpeg_binary,
                ffprobe_binary=config.ffprobe_binary,
                workspace_root=config.workspace_root,
                temp_dir=config.temp_dir,
            ),
            "capture": CaptureRunner(
                capture_binary=config.capture_binary,
                temp_dir=config.temp_dir,
            ),
            "shell": ShellRunner(config.workspace_root),
            "agent": AgentRunner(runtime_factory),
        },
    )
    intake_gate = IntakeGate()
    scheduler_loop = SchedulerLoop(
        schedule_service,
        job_service,
        job_worker,
        intake_gate=intake_gate,
    )
    assert config.hooks_dir is not None
    hook_secret_store = HookSecretStore(config.hooks_dir)
    hook_service = HookService(
        db, hook_secret_store, {"email": ImapEmailAdapter()}
    )
    hook_run_service = HookRunService(db)
    hook_runner = HookRunner(hook_service, hook_run_service, runtime_factory, event_bus)
    hook_loop = HookLoop(
        hook_service,
        hook_run_service,
        hook_runner,
        interval_seconds=config.hook_poll_interval_seconds,
    )
    agent_registry = AgentRegistry(config)
    audit_service = AuditService(db, retention_days=config.audit_retention_days)
    security_settings = SecuritySettingsService(db, config.access_mode)
    health_service = HealthService(
        db,
        job_worker,
        scheduler_loop,
        agent_registry,
        config.model_provider,
        intake_gate,
    )
    app.state.app_config = config
    app.state.database = db
    app.state.agent_registry = agent_registry
    app.state.auth_store = AuthStore(config.auth_dir)
    app.state.auth_session_service = auth_session_service
    app.state.login_rate_limiter = LoginRateLimiter()
    app.state.audit_service = audit_service
    app.state.security_settings = security_settings
    app.state.intake_gate = intake_gate
    app.state.health_service = health_service
    app.state.capability_registry = registry
    app.state.job_service = job_service
    app.state.schedule_service = schedule_service
    app.state.artifact_store = artifact_store
    app.state.job_worker = job_worker
    app.state.scheduler_loop = scheduler_loop
    app.state.persona_service = persona_service
    app.state.session_activity_publisher = session_activity_publisher
    app.state.session_activity_service = session_activity_service
    app.state.team_run_service = team_run_service
    app.state.team_directory_service = team_directory_service
    app.state.rule_set_service = rule_set_service
    app.state.hook_service = hook_service
    app.state.hook_run_service = hook_run_service
    app.state.hook_runner = hook_runner
    app.state.hook_loop = hook_loop
    return runtime_factory


def main() -> None:
    config = load_config()
    uvicorn.run(create_app(config), host=config.web_host, port=config.web_port)


def _team_model_factory(config: AppConfig) -> Callable[[TeamAgent], ModelClient]:
    def team_model_factory(agent: TeamAgent) -> ModelClient:
        session = agent.upstream_session_id or None
        workspace_root = config.workspace_root / agent.team_run_id
        workspace_root.mkdir(parents=True, exist_ok=True)
        raw_options = agent.persona_snapshot.get("default_options")
        options = raw_options if isinstance(raw_options, dict) else {}
        if agent.backend == "claude":
            return ClaudeModelClient(
                binary=config.claude_binary,
                model=agent.model,
                workspace_root=workspace_root,
                effort=str(options.get("effort") or "high"),
                permission_mode=str(
                    options.get("permission_mode") or config.claude_permission_mode
                ),
                agent=str(options["agent"]) if options.get("agent") else None,
                timeout_seconds=config.codex_timeout_seconds,
                upstream_session_id=session,
            )
        return CodexModelClient(
            binary=config.codex_binary,
            model=agent.model,
            workspace_root=workspace_root,
            sandbox=str(options.get("sandbox") or config.codex_sandbox),
            approval_policy=str(
                options.get("approval_policy") or config.codex_approval_policy
            ),
            profile=str(options["profile"]) if options.get("profile") else None,
            effort=str(options.get("effort") or "high"),
            timeout_seconds=config.codex_timeout_seconds,
            idle_timeout_seconds=config.codex_idle_timeout_seconds,
            upstream_session_id=session,
        )

    return team_model_factory


def _select_frontend_index(package_dir: Path) -> Path:
    vite_index = package_dir / "frontend_dist" / "index.html"
    if vite_index.exists():
        return vite_index
    return package_dir / "static" / "index.html"


__all__ = ["create_app", "main"]
