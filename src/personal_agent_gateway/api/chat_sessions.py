import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from personal_agent_gateway.api.dependencies import record_domain_audit, session_dependency
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.intake import IntakeClosedError, IntakeGate
from personal_agent_gateway.run_state import SessionAlreadyRunningError, SessionRunRegistry
from personal_agent_gateway.runtime import AgentRuntime, RuntimeResult
from personal_agent_gateway.session_activity import (
    SessionActivityPublisher,
    SessionActivityService,
)
from personal_agent_gateway.session_config import SessionAgentConfigService
from personal_agent_gateway.transcript import TranscriptStore


class ChatRequest(BaseModel):
    message: str


class RenameRequest(BaseModel):
    title: str


@dataclass(frozen=True)
class ChatSessionContext:
    config: AppConfig
    transcript: TranscriptStore
    event_bus: EventBus
    run_registry: SessionRunRegistry
    active_runtime: Callable[[], AgentRuntime]
    runtime_for_session: Callable[[str], AgentRuntime]
    activity_service: SessionActivityService
    activity_publisher: SessionActivityPublisher
    intake_gate: IntakeGate


def create_chat_sessions_router(context: ChatSessionContext) -> APIRouter:
    router = APIRouter(tags=["chat-sessions"])

    def require_session_id(session_id: str) -> str:
        if context.transcript.session_origin(session_id) != "chat":
            raise HTTPException(status_code=404, detail="Session not found")
        return session_id

    def require_intake_open() -> None:
        try:
            context.intake_gate.require_open()
        except IntakeClosedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def chat_for_session(session_id: str, message: str) -> dict[str, object]:
        require_intake_open()
        request_id = uuid4().hex
        try:
            started = context.run_registry.start_if_exists(
                session_id,
                request_id,
                lambda: context.transcript.exists(session_id),
            )
        except SessionAlreadyRunningError as exc:
            raise HTTPException(status_code=409, detail="Session is already running") from exc
        if not started:
            raise HTTPException(status_code=404, detail="Session not found")
        context.run_registry.attach_task(session_id, request_id, asyncio.current_task())
        try:
            result = await context.runtime_for_session(session_id).handle_user_message(message)
            return {
                **_runtime_response(result),
                "session_id": session_id,
                "request_id": request_id,
                "last_event_id": _last_session_event_id(
                    context.event_bus.recent(), session_id
                ),
            }
        except asyncio.CancelledError:
            await context.activity_publisher.publish(
                {"type": "runtime.interrupted", "session_id": session_id}
            )
            return {
                "messages": [],
                "pending_approval": False,
                "session_id": session_id,
                "request_id": request_id,
                "last_event_id": _last_session_event_id(
                    context.event_bus.recent(), session_id
                ),
                "interrupted": True,
            }
        finally:
            context.run_registry.finish(session_id, request_id)

    @router.get("/api/history")
    def history(
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        cursor: str | None = None,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        session_id = context.transcript.active_id()
        if session_id is None:
            return {"events": [], "next_cursor": None}
        try:
            events, next_cursor = context.transcript.page_events(
                session_id, limit=limit, cursor=cursor
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
        return {
            "events": [_event_payload(event) for event in events],
            "next_cursor": next_cursor,
        }

    @router.get("/api/status")
    def status(_session: None = session_dependency) -> dict[str, object]:
        session_id = context.transcript.active_id()
        if session_id is None:
            session_config = {
                "session_id": None,
                "agent_id": "codex",
                "model": "default",
                "options": {},
                "editable": True,
                "source": "default",
                "updated_at": None,
            }
            events = []
            provider = context.config.model_provider
            model = context.config.model
        else:
            effective_config = SessionAgentConfigService(
                context.transcript
            ).effective_config(session_id)
            session_config = effective_config.model_dump(mode="json")
            events = context.transcript.load(session_id)
            if effective_config.source == "explicit":
                provider = effective_config.agent_id
                model = effective_config.model
            else:
                provider = context.config.model_provider
                model = context.config.model
        return {
            "provider": provider,
            "model": model,
            "workspace_root": str(context.config.workspace_root),
            "environment_title": context.config.environment_title or None,
            "session_id": session_id,
            "message_count": sum(
                1 for event in events if event.kind in {"user", "assistant"}
            ),
            "pending_approval": _pending_shell_approval(events) or False,
            "session_status": _session_status(
                events, session_id, context.run_registry
            ),
            "cookie_secure": context.config.cookie_secure,
            "session_config": session_config,
        }

    @router.get("/api/events")
    async def events(
        request: Request,
        _session: None = session_dependency,
    ) -> StreamingResponse:
        recent_events = context.event_bus.recent()
        replay_through_id = (
            int(recent_events[-1]["id"]) if recent_events else None
        )
        subscriber = context.event_bus.subscribe(
            last_event_id=request.headers.get("last-event-id")
        )
        return StreamingResponse(
            _sse_events(
                request,
                context.event_bus,
                subscriber,
                replay_through_id=replay_through_id,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/sessions")
    def sessions(
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        cursor: str | None = None,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        try:
            page, next_cursor = context.transcript.page_sessions(
                limit=limit, cursor=cursor, origin="chat"
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
        return {
            "sessions": [
                _session_payload(session, context.run_registry) for session in page
            ],
            "next_cursor": next_cursor,
        }

    @router.get("/api/sessions/search")
    def search_sessions(
        q: str = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        cursor: str | None = None,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        if not q.strip():
            return {"sessions": [], "next_cursor": None}
        try:
            page, next_cursor = context.transcript.page_sessions(
                limit=limit, cursor=cursor, query=q, origin="chat"
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
        return {
            "sessions": [
                _session_payload(session, context.run_registry) for session in page
            ],
            "next_cursor": next_cursor,
        }

    @router.get("/api/sessions/{session_id}/history")
    def session_history(
        session_id: str,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        cursor: str | None = None,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        try:
            events, next_cursor = context.transcript.page_events(
                session_id, limit=limit, cursor=cursor
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
        return {
            "session_id": session_id,
            "events": [_event_payload(event) for event in events],
            "next_cursor": next_cursor,
        }

    @router.get("/api/sessions/{session_id}/activity")
    def session_activity(
        session_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        cursor: str | None = None,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        try:
            events, next_cursor = context.activity_service.page(
                session_id, limit=limit, cursor=cursor
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
        return {
            "session_id": session_id,
            "events": [event.to_event_payload() for event in events],
            "next_cursor": next_cursor,
        }

    @router.get("/api/sessions/{session_id}/status")
    def session_status(
        session_id: str, _session: None = session_dependency
    ) -> dict[str, object]:
        require_session_id(session_id)
        events = context.transcript.load(session_id)
        activity_events = context.activity_service.list(session_id)
        effective_config = SessionAgentConfigService(
            context.transcript
        ).effective_config(session_id)
        return {
            "session_id": session_id,
            "status": _session_status(events, session_id, context.run_registry),
            "pending_approval": _pending_shell_approval(events) or False,
            "message_count": sum(
                1 for event in events if event.kind in {"user", "assistant"}
            ),
            "last_event_id": _last_session_event_id(
                context.event_bus.recent(), session_id
            ),
            "last_activity_id": _last_activity_event_id(activity_events),
            "session_config": effective_config.model_dump(mode="json"),
        }

    @router.post("/api/sessions/{session_id}/chat")
    async def session_chat(
        session_id: str,
        payload: ChatRequest,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        return await chat_for_session(session_id, payload.message)

    @router.post("/api/sessions/{session_id}/interrupt")
    async def interrupt_session(
        session_id: str,
        request: Request,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        if not context.run_registry.interrupt(session_id):
            raise HTTPException(status_code=409, detail="Session is not running")
        record_domain_audit(
            request,
            _session,
            event_type="chat.session_interrupt_requested",
            action="sessions.interrupt",
            resource_type="session",
            resource_id=session_id,
            session_id=session_id,
        )
        return {"session_id": session_id, "interrupting": True}

    @router.post("/api/sessions/{session_id}/activate")
    def activate_session(
        session_id: str, _session: None = session_dependency
    ) -> dict[str, object]:
        require_session_id(session_id)
        if not context.transcript.activate(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "session_id": session_id,
            "events": [
                _event_payload(event) for event in context.transcript.load_active()
            ],
        }

    @router.post("/api/sessions/{session_id}/title")
    def rename_session(
        session_id: str,
        payload: RenameRequest,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        require_session_id(session_id)
        if not context.transcript.set_title(session_id, title[:120]):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": session_id, "title": title[:120]}

    @router.delete("/api/sessions/{session_id}")
    def delete_session(
        session_id: str,
        request: Request,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_session_id(session_id)
        try:
            deleted = context.run_registry.delete_if_idle(
                session_id, lambda: context.transcript.delete(session_id)
            )
        except SessionAlreadyRunningError as exc:
            raise HTTPException(status_code=409, detail="Session is running") from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        context.activity_service.delete_session(session_id)
        record_domain_audit(
            request,
            _session,
            event_type="chat.session_deleted",
            action="sessions.delete",
            resource_type="session",
            resource_id=session_id,
            session_id=session_id,
        )
        return {
            "deleted": True,
            "active_session_id": context.transcript.active_id(),
        }

    @router.post("/api/chat")
    async def chat(
        payload: ChatRequest,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_intake_open()
        session_id = context.transcript.active_id() or context.transcript.start_new()
        response = await chat_for_session(session_id, payload.message)
        return _compat_chat_response(response)

    async def decide_approval(
        *,
        session_id: str,
        approval_id: str,
        request: Request,
        principal: object,
        decision: str,
    ) -> dict[str, object]:
        if decision == "approve":
            require_intake_open()
        require_session_id(session_id)
        request_id = uuid4().hex
        try:
            started = context.run_registry.start_if_exists(
                session_id,
                request_id,
                lambda: context.transcript.exists(session_id),
            )
        except SessionAlreadyRunningError as exc:
            raise HTTPException(status_code=409, detail="Session is already running") from exc
        if not started:
            raise HTTPException(status_code=404, detail="Session not found")
        try:
            runtime = context.runtime_for_session(session_id)
            result = (
                await runtime.approve(approval_id)
                if decision == "approve"
                else await runtime.deny(approval_id)
            )
            record_domain_audit(
                request,
                principal,
                event_type=(
                    "chat.approval_approved"
                    if decision == "approve"
                    else "chat.approval_denied"
                ),
                action=f"chat.approvals.{decision}",
                resource_type="approval",
                resource_id=approval_id,
                session_id=session_id,
                status=_runtime_audit_status(result),
            )
            return {
                **_runtime_response(result),
                "session_id": session_id,
                "request_id": request_id,
            }
        finally:
            context.run_registry.finish(session_id, request_id)

    @router.post("/api/sessions/{session_id}/approvals/{approval_id}/approve")
    async def session_approve(
        session_id: str,
        approval_id: str,
        request: Request,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        return await decide_approval(
            session_id=session_id,
            approval_id=approval_id,
            request=request,
            principal=_session,
            decision="approve",
        )

    @router.post("/api/sessions/{session_id}/approvals/{approval_id}/deny")
    async def session_deny(
        session_id: str,
        approval_id: str,
        request: Request,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        return await decide_approval(
            session_id=session_id,
            approval_id=approval_id,
            request=request,
            principal=_session,
            decision="deny",
        )

    @router.post("/api/approvals/{approval_id}/approve")
    async def approve(
        approval_id: str,
        request: Request,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        require_intake_open()
        session_id = context.transcript.active_id()
        if session_id is not None:
            return _compat_chat_response(
                await decide_approval(
                    session_id=session_id,
                    approval_id=approval_id,
                    request=request,
                    principal=_session,
                    decision="approve",
                )
            )
        return _runtime_response(await context.active_runtime().approve(approval_id))

    @router.post("/api/approvals/{approval_id}/deny")
    async def deny(
        approval_id: str,
        request: Request,
        _session: None = session_dependency,
    ) -> dict[str, object]:
        session_id = context.transcript.active_id()
        if session_id is not None:
            return _compat_chat_response(
                await decide_approval(
                    session_id=session_id,
                    approval_id=approval_id,
                    request=request,
                    principal=_session,
                    decision="deny",
                )
            )
        return _runtime_response(await context.active_runtime().deny(approval_id))

    @router.post("/api/reset")
    def reset(_session: None = session_dependency) -> dict[str, object]:
        session_id = context.transcript.reset()
        return {"events": [], "session_id": session_id}

    return router


async def _sse_events(
    request: Request,
    event_bus: EventBus,
    subscriber: asyncio.Queue[dict[str, object]],
    replay_through_id: int | None = None,
):
    try:
        yield ": connected\n\n"
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(subscriber.get(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            event_id = event.get("id")
            payload = event
            if (
                replay_through_id is not None
                and isinstance(event_id, int)
                and event_id <= replay_through_id
            ):
                payload = {**event, "replayed": True}
            yield f"id: {event_id}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    finally:
        event_bus.unsubscribe(subscriber)


def _event_payload(event: BaseModel) -> dict[str, object]:
    payload = event.model_dump(mode="json")
    return {str(key): value for key, value in payload.items()}


def _runtime_response(result: RuntimeResult) -> dict[str, object]:
    return {"messages": result.messages, "pending_approval": result.pending_approval}


def _runtime_audit_status(result: RuntimeResult) -> str:
    for message in result.messages:
        content = message.get("content")
        if isinstance(content, str) and content.startswith("Error:"):
            return "failed"
    return "success"


def _compat_chat_response(payload: dict[str, object]) -> dict[str, object]:
    return {
        "messages": payload["messages"],
        "pending_approval": payload["pending_approval"],
    }


def _session_payload(
    session: BaseModel, run_registry: SessionRunRegistry
) -> dict[str, object]:
    payload = session.model_dump(mode="json")
    payload["status"] = run_registry.status(
        str(payload["id"]),
        payload.get("status") == "waiting_approval",
        payload.get("status") == "failed",
    )
    return {str(key): value for key, value in payload.items()}


def _session_status(
    events: list[object],
    session_id: str | None,
    run_registry: SessionRunRegistry,
) -> str:
    return run_registry.status(
        session_id,
        _has_pending_shell_approval(events),
        bool(events and getattr(events[-1], "kind", "") == "runtime_error"),
    )


def _last_activity_event_id(events: list[object]) -> int | None:
    if not events:
        return None
    return int(getattr(events[-1], "id"))


def _last_session_event_id(
    events: list[dict[str, object]], session_id: str
) -> int | None:
    for event in reversed(events):
        if event.get("session_id") == session_id:
            return int(event["id"])
    return None


def _has_pending_shell_approval(events: list[object]) -> bool:
    return _pending_shell_approval(events) is not None


def _pending_shell_approval(events: list[object]) -> dict[str, object] | None:
    pending_by_tool_id: set[str] = set()
    pending_by_tool_id_payload: dict[str, dict[str, object]] = {}
    for event in events:
        kind = getattr(event, "kind", "")
        payload = getattr(event, "payload", {})
        if kind == "tool_request" and payload.get("name") == "shell.run":
            tool_id = str(payload.get("id", ""))
            pending_by_tool_id.add(tool_id)
            pending_by_tool_id_payload[tool_id] = payload
        elif kind in {"tool_result", "tool_denial"}:
            tool_id = str(payload.get("id", ""))
            pending_by_tool_id.discard(tool_id)
            pending_by_tool_id_payload.pop(tool_id, None)
    if not pending_by_tool_id:
        return None
    tool_id = next(reversed(tuple(pending_by_tool_id)))
    payload = pending_by_tool_id_payload.get(tool_id, {})
    arguments = payload.get("arguments")
    command = arguments.get("command") if isinstance(arguments, dict) else None
    approval_id = payload.get("approval_id")
    if not isinstance(approval_id, str) or not isinstance(command, str):
        return None
    return {"id": approval_id, "command": command}
