import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personal_agent_gateway.api.dependencies import (
    record_domain_audit,
    require_intake_open,
    session_dependency,
)
from personal_agent_gateway.auth_sessions import SessionPrincipal
from personal_agent_gateway.pagination import decode_cursor, encode_cursor
from personal_agent_gateway.team_build_evidence import (
    run_build_evidence,
    task_build_evidence,
)
from personal_agent_gateway.team_lifecycle import MAX_CONCURRENT_WORKERS
from personal_agent_gateway.team_cycles import TeamAutoSeries, TeamCycleRequest
from personal_agent_gateway.team_delivery import TeamRunDeliveryError
from personal_agent_gateway.team_provider_recovery import (
    AmbiguousOperationNotReconcilable,
)
from personal_agent_gateway.teams import (
    TeamAgent,
    TeamDecisionRequest,
    TeamMessage,
    TeamRun,
    TeamRunCycle,
    TeamTask,
    required_verifications_payload,
)


router = APIRouter(prefix="/api/team-runs", tags=["team-runs"])

_ACTIVE = {"planning", "running", "summarizing"}
_TERMINAL = {
    "completed",
    "completed_with_failures",
    "blocked",
    "failed",
    "canceled",
}


class CreateTeamRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    goal: str | None = None
    execution_policy: Literal["auto", "triggered"]
    auto_repeat_count: Annotated[int | None, Field(ge=1)] = None
    auto_interval_minutes: Annotated[int | None, Field(ge=1)] = None
    parent_team_run_id: str | None = None
    plan_negotiation: bool = False
    # Was not accepted at all, and the endpoint passed the constant 1 -- so a
    # run created through the product could never overlap anything, whatever
    # the executor allowed. Bounded by the executor's own ceiling rather than
    # left open: a larger number would be stored, reported, and then not
    # delivered.
    max_workers: Annotated[int, Field(ge=1, le=MAX_CONCURRENT_WORKERS)] = 1

    @model_validator(mode="after")
    def validate_policy_settings(self) -> Self:
        self.goal = (self.goal or "").strip()
        self.parent_team_run_id = (self.parent_team_run_id or "").strip() or None
        auto_fields = (self.auto_repeat_count, self.auto_interval_minutes)
        if self.execution_policy == "auto":
            if not self.goal:
                raise ValueError("AUTO requires a base objective")
            if None in auto_fields:
                raise ValueError("AUTO requires repeat count and interval")
        if self.execution_policy == "triggered" and any(
            value is not None for value in auto_fields
        ):
            raise ValueError("TRIGGERED does not accept AUTO settings")
        return self


class TriggerCycleRequest(BaseModel):
    instruction: Annotated[str, Field(min_length=1)]
    client_request_id: Annotated[str, Field(min_length=1)]
    previous_cycle_id: str | None = None

    @field_validator("instruction", "client_request_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ContestRequest(BaseModel):
    objection: Annotated[str, Field(min_length=1)]
    client_request_id: Annotated[str, Field(min_length=1)]

    @field_validator("objection", "client_request_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class AddWorkRequest(BaseModel):
    instruction: str


class AnswerDecisionRequest(BaseModel):
    request_id: str
    revision: int
    answers: dict[str, str]


class CommitDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ResolveDeliveryConflictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["target", "team", "manual"]
    content: Annotated[str | None, Field(max_length=1_000_000)] = None

    @model_validator(mode="after")
    def require_manual_content(self) -> Self:
        if self.mode == "manual" and self.content is None:
            raise ValueError("manual mode requires content")
        return self


@router.get("")
def list_team_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        runs, next_cursor = request.app.state.team_run_service.page_team_runs_enriched(
            limit=limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    for run in runs:
        stored = request.app.state.team_run_service.get_team_run(str(run["id"]))
        run["execution_policy"] = stored.execution_policy
    return {"team_runs": runs, "next_cursor": next_cursor}


@router.post("")
async def create_team_run(
    request: Request,
    payload: CreateTeamRunRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        run = request.app.state.team_run_service.create_team_run_from_team(
            request.app.state.team_directory_service,
            request.app.state.rule_set_service,
            team_id=payload.team_id,
            goal=payload.goal,
            run_mode="plan_and_execute",
            max_workers=payload.max_workers,
            lifecycle_mode="continuous",
            execution_policy=payload.execution_policy,
            auto_repeat_count=payload.auto_repeat_count,
            auto_interval_seconds=(
                payload.auto_interval_minutes * 60
                if payload.auto_interval_minutes is not None
                else None
            ),
            parent_team_run_id=payload.parent_team_run_id,
            plan_negotiation=payload.plan_negotiation,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if payload.execution_policy == "auto":
        first = request.app.state.team_cycle_service.list_requests(run.id)[0]
        await request.app.state.event_bus.publish(
            {
                "type": "team.cycle_request.queued",
                "team_run_id": run.id,
                "cycle_request_id": first.id,
                "source_type": "auto",
            }
        )
        await request.app.state.team_cycle_dispatcher.enqueue_run(run.id)
    record_domain_audit(
        request,
        principal,
        event_type="team.run_created",
        action="team_runs.create",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
        metadata={
            "run_mode": run.run_mode,
            "team_id": run.team_id,
            "execution_policy": run.execution_policy,
            "parent_team_run_id": run.parent_team_run_id,
        },
    )
    return {"team_run": _team_run_payload(run, request.app.state.team_run_service)}


@router.post("/{team_run_id}/cycle-requests")
async def trigger_cycle(
    request: Request,
    team_run_id: str,
    payload: TriggerCycleRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        created = request.app.state.team_cycle_service.enqueue_request(
            team_run_id,
            "manual",
            payload.client_request_id,
            payload.instruction,
            previous_cycle_id=payload.previous_cycle_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish(
        {
            "type": "team.cycle_request.queued",
            "team_run_id": team_run_id,
            "cycle_request_id": created.id,
            "source_type": "manual",
        }
    )
    await request.app.state.team_cycle_dispatcher.enqueue_run(team_run_id)
    record_domain_audit(
        request,
        principal,
        event_type="team.cycle_request.queued",
        action="team_runs.trigger_cycle",
        resource_type="team_cycle_request",
        resource_id=created.id,
        team_run_id=team_run_id,
    )
    return {
        "cycle_request": _cycle_request_payload(created),
        "queue_position": request.app.state.team_cycle_service.queue_position(created.id),
    }


@router.post("/{team_run_id}/contests")
async def contest_plan(
    request: Request,
    team_run_id: str,
    payload: ContestRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        created = request.app.state.team_cycle_service.enqueue_request(
            team_run_id,
            "contest",
            payload.client_request_id,
            payload.objection,
            previous_cycle_id=None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Both, on purpose. team.contest.queued names what happened; the client
    # keys its run-list refresh (and therefore the queue count) off
    # team.cycle_request.queued, which every other enqueue publishes.
    await request.app.state.event_bus.publish(
        {
            "type": "team.cycle_request.queued",
            "team_run_id": team_run_id,
            "cycle_request_id": created.id,
            "source_type": "contest",
        }
    )
    await request.app.state.event_bus.publish(
        {
            "type": "team.contest.queued",
            "team_run_id": team_run_id,
            "cycle_request_id": created.id,
            "source_type": "contest",
        }
    )
    await request.app.state.team_cycle_dispatcher.enqueue_run(team_run_id)
    record_domain_audit(
        request,
        principal,
        event_type="team.contest.queued",
        action="team_runs.contest_plan",
        resource_type="team_cycle_request",
        resource_id=created.id,
        team_run_id=team_run_id,
    )
    return {
        "cycle_request": _cycle_request_payload(created),
        "queue_position": request.app.state.team_cycle_service.queue_position(created.id),
    }


@router.post("/{team_run_id}/auto-series/{series_id}/retry")
async def retry_auto_cycle(
    request: Request,
    team_run_id: str,
    series_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        created = request.app.state.team_cycle_service.retry_failed(
            team_run_id, series_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="AUTO Series not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish(
        {
            "type": "team.cycle_request.queued",
            "team_run_id": team_run_id,
            "cycle_request_id": created.id,
            "source_type": "retry",
        }
    )
    await request.app.state.team_cycle_dispatcher.enqueue_run(team_run_id)
    record_domain_audit(
        request,
        principal,
        event_type="team.auto_series.retried",
        action="team_runs.retry_auto_cycle",
        resource_type="team_auto_series",
        resource_id=series_id,
        team_run_id=team_run_id,
    )
    return {"cycle_request": _cycle_request_payload(created)}


@router.post("/{team_run_id}/auto-series/{series_id}/continue")
def continue_auto_cycle(
    request: Request,
    team_run_id: str,
    series_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        series = request.app.state.team_cycle_service.continue_failed(
            team_run_id, series_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="AUTO Series not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="team.auto_series.continued",
        action="team_runs.continue_auto_cycle",
        resource_type="team_auto_series",
        resource_id=series_id,
        team_run_id=team_run_id,
    )
    return {"auto_series": _auto_series_payload(series)}


@router.post("/{team_run_id}/auto-series/restart")
async def restart_auto_series(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    try:
        series, created = request.app.state.team_cycle_service.restart_series(
            team_run_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await request.app.state.event_bus.publish(
        {
            "type": "team.cycle_request.queued",
            "team_run_id": team_run_id,
            "cycle_request_id": created.id,
            "source_type": "auto",
        }
    )
    await request.app.state.team_cycle_dispatcher.enqueue_run(team_run_id)
    record_domain_audit(
        request,
        principal,
        event_type="team.auto_series.restarted",
        action="team_runs.restart_auto_series",
        resource_type="team_auto_series",
        resource_id=series.id,
        team_run_id=team_run_id,
    )
    return {
        "auto_series": _auto_series_payload(series),
        "cycle_request": _cycle_request_payload(created),
    }


@router.get("/{team_run_id}")
def get_team_run(request: Request, team_run_id: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"team_run": _team_run_payload(run, request.app.state.team_run_service)}


@router.get("/{team_run_id}/detail")
def get_team_run_detail(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    _session: None = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    try:
        run = service.get_team_run(team_run_id)
        agents = service.list_agents(team_run_id)
        tasks = service.list_tasks(team_run_id)
        task_dependencies = service.list_task_dependency_map(team_run_id)
        messages = service.list_messages(team_run_id)
        cycles = service.list_cycles(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    operations = request.app.state.team_model_operation_service
    failure_shapes = operations.latest_failure_shapes(team_run_id)
    coverage_by_cycle: dict[str, list[dict[str, str]] | None] = {}
    verdict_payload_by_cycle: dict[str, dict[str, object]] = {}
    for cycle in cycles:
        cycle_operations = operations.list_for_cycle(cycle.id)
        # The last synthesis whose result actually is a synthesis. A cycle whose
        # synthesis asked the user has cycle_synthesis:0 applied with
        # result_kind "user_decision" and the real synthesis at the next
        # ordinal, so taking the first applied one reported "did not report"
        # for a cycle where the leader did. hook_runner picks the same way.
        synthesis = next(
            (
                operation
                for operation in reversed(cycle_operations)
                if operation.stage in {"cycle_synthesis", "cycle_synthesis_repair"}
                and operation.status == "applied"
                and operation.result_kind == "synthesis"
            ),
            None,
        )
        if synthesis is not None:
            coverage_by_cycle[cycle.id] = (
                (synthesis.result_json or {}).get("payload") or {}
            ).get("coverage_gaps")
        if cycle.source_type != "contest":
            continue
        verdict_operation = next(
            (
                operation
                for operation in cycle_operations
                if operation.stage in {"cycle_contest", "cycle_contest_repair"}
                and operation.status == "applied"
            ),
            None,
        )
        if verdict_operation is not None:
            verdict_payload_by_cycle[cycle.id] = (
                verdict_operation.result_json or {}
            ).get("payload") or {}
    selected_tasks = tasks[-limit:]
    selected_messages = messages[-limit:]
    cycle_service = request.app.state.team_cycle_service
    cycle_requests = cycle_service.list_requests(team_run_id)
    workspace = _resolved_workspace(run)
    task_evidence = {
        task.id: task_build_evidence(task, workspace) for task in selected_tasks
    }
    return {
        "team_run": _team_run_payload(run, request.app.state.team_run_service),
        "agents": [_agent_payload(agent) for agent in agents],
        "tasks": [
            _task_payload(
                task,
                task_dependencies.get(task.id, []),
                failure_shapes.get(task.id),
                task_evidence.get(task.id),
            )
            for task in selected_tasks
        ],
        "messages": [_message_payload(message) for message in selected_messages],
        "cycles": [
            _cycle_payload(cycle, coverage_by_cycle.get(cycle.id))
            for cycle in cycles
        ],
        "decision_request": _decision_request_payload(
            service.get_active_decision_request(team_run_id)
        ),
        "policy_status": cycle_service.policy_status(team_run_id),
        "active_auto_series": _auto_series_payload(
            cycle_service.get_active_series(team_run_id)
        ),
        "queue_count": cycle_service.count_queued(team_run_id),
        "active_request": _cycle_request_payload(
            cycle_service.get_dispatching(team_run_id)
        ),
        "document_summary": _document_summary(workspace),
        # Rolled up from the per-task evidence already computed above, not
        # recomputed: every declared path costs a filesystem round-trip and
        # /detail is polled.
        "build_evidence_summary": run_build_evidence(
            list(task_evidence.values())
        ),
        "truncated": {
            "tasks": len(tasks) > len(selected_tasks),
            "messages": len(messages) > len(selected_messages),
            # The rollup covers the same window as tasks, so above the limit it
            # is a tail, not a run total. Saying so is the only honest option
            # while the label reads as one.
            "build_evidence_summary": len(tasks) > len(selected_tasks),
        },
        "contests": _contests_payload(cycle_requests, cycles, verdict_payload_by_cycle),
        "plan_revisions": [
            {
                "revision": revision.revision,
                "status": revision.status,
                "required_approver_agent_ids": list(
                    revision.required_approver_agent_ids
                ),
                "reviews": service.plan_reviews(revision.id),
                "objections": service.plan_review_objections(revision.id),
            }
            for revision in service.list_plan_revisions(team_run_id)
        ],
    }


@router.get("/{team_run_id}/delivery")
def get_team_run_delivery(
    request: Request,
    team_run_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"delivery": request.app.state.team_run_delivery_service.preview(run)}


@router.post("/{team_run_id}/delivery/commit")
def commit_team_run_delivery(
    request: Request,
    team_run_id: str,
    payload: CommitDeliveryRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
        delivery = request.app.state.team_run_delivery_service.commit(run, payload.message)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    except TeamRunDeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="team.run_delivery_committed",
        action="team_runs.delivery.commit",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
        metadata={"source_head": delivery["source"]["head"]},
    )
    return {"delivery": delivery}


@router.post("/{team_run_id}/delivery/apply")
def apply_team_run_delivery(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
        delivery = request.app.state.team_run_delivery_service.apply(run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    except TeamRunDeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conflict_session = delivery.get("conflict_session")
    record_domain_audit(
        request,
        principal,
        event_type=(
            "team.run_delivery_conflicted"
            if conflict_session
            else "team.run_delivery_applied"
        ),
        action="team_runs.delivery.apply",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
        metadata={
            "applied_commits": delivery.get("applied_commits", []),
            "result_head": delivery.get("result_head"),
            "auto_resolved_files": delivery.get("auto_resolved_files", []),
            "conflict_count": (
                conflict_session.get("total_count", 0)
                if isinstance(conflict_session, dict)
                else 0
            ),
        },
    )
    return {"delivery": delivery}


@router.post("/{team_run_id}/delivery/conflicts/{conflict_id}/resolve")
def resolve_team_run_delivery_conflict(
    request: Request,
    team_run_id: str,
    conflict_id: str,
    payload: ResolveDeliveryConflictRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
        delivery = request.app.state.team_run_delivery_service.resolve(
            run,
            conflict_id,
            payload.mode,
            payload.content,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    except TeamRunDeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="team.run_delivery_conflict_resolved",
        action="team_runs.delivery.conflicts.resolve",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
        metadata={"conflict_id": conflict_id, "mode": payload.mode},
    )
    return {"delivery": delivery}


@router.post("/{team_run_id}/delivery/continue")
def continue_team_run_delivery(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
        delivery = request.app.state.team_run_delivery_service.continue_apply(run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    except TeamRunDeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conflict_session = delivery.get("conflict_session")
    record_domain_audit(
        request,
        principal,
        event_type=(
            "team.run_delivery_conflicted"
            if conflict_session
            else "team.run_delivery_applied"
        ),
        action="team_runs.delivery.continue",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
        metadata={
            "applied_commits": delivery.get("applied_commits", []),
            "result_head": delivery.get("result_head"),
            "auto_resolved_files": delivery.get("auto_resolved_files", []),
        },
    )
    return {"delivery": delivery}


@router.delete("/{team_run_id}/delivery/conflicts")
def cancel_team_run_delivery_conflicts(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
        delivery = request.app.state.team_run_delivery_service.cancel(run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    except TeamRunDeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="team.run_delivery_conflict_canceled",
        action="team_runs.delivery.conflicts.cancel",
        resource_type="team_run",
        resource_id=run.id,
        team_run_id=run.id,
    )
    return {"delivery": delivery}


@router.post("/{team_run_id}/start")
async def start_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if run.lifecycle_mode == "continuous":
        raise HTTPException(
            status_code=409,
            detail="Continuous team runs start through the cycle request queue",
        )
    if registry.is_running(team_run_id) or run.status in _ACTIVE:
        raise HTTPException(status_code=409, detail="Team run already running")
    if run.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft team runs can be started")

    request.app.state.team_run_orchestrator.start(team_run_id)
    record_domain_audit(
        request,
        principal,
        event_type="team.run_start_requested",
        action="team_runs.start",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
    )
    return {"team_run": _team_run_payload(run, request.app.state.team_run_service)}


@router.post("/{team_run_id}/resume")
async def resume_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.is_running(team_run_id):
        raise HTTPException(status_code=409, detail="Team run already running")
    if run.status != "interrupted":
        raise HTTPException(status_code=409, detail="Only interrupted team runs can be resumed")
    cycle_id = None
    if run.lifecycle_mode == "continuous":
        cycle = next(
            (
                candidate
                for candidate in service.list_cycles(team_run_id)
                if candidate.status == "interrupted"
            ),
            None,
        )
        if cycle is None:
            raise HTTPException(status_code=409, detail="No interrupted cycle to resume")
        cycle_id = cycle.id

    provider_recovery = request.app.state.team_provider_recovery
    recovery_claim = None
    if provider_recovery.has_ambiguous_operation(team_run_id):
        try:
            recovery_claim = provider_recovery.prepare_explicit_resume(
                team_run_id
            )
        except AmbiguousOperationNotReconcilable as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        run = service.get_team_run(team_run_id)
    dispatcher = request.app.state.team_cycle_dispatcher
    if recovery_claim is not None:
        dispatcher.resume_recovered_operation(recovery_claim)
    else:
        dispatcher.resume(team_run_id, cycle_id)
    record_domain_audit(
        request,
        principal,
        event_type="team.run_resume_requested",
        action="team_runs.resume",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
    )
    return {"team_run": _team_run_payload(run, request.app.state.team_run_service)}


@router.get("/{team_run_id}/decision-request")
def get_decision_request(
    request: Request,
    team_run_id: str,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        decision_request = request.app.state.team_run_service.get_active_decision_request(
            team_run_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    return {"decision_request": _decision_request_payload(decision_request)}


@router.post("/{team_run_id}/decision-request/answer")
async def answer_decision_request(
    request: Request,
    team_run_id: str,
    payload: AnswerDecisionRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    if registry.is_running(team_run_id):
        raise HTTPException(status_code=409, detail="Team run already running")
    try:
        run, decision_request = service.answer_decision_request(
            team_run_id,
            payload.request_id,
            payload.revision,
            payload.answers,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await request.app.state.event_bus.publish(
        {
            "type": "team.run.input_resolved",
            "team_run_id": team_run_id,
            "decision_request_id": decision_request.id,
            "run": _team_run_payload(run, request.app.state.team_run_service),
        }
    )

    request.app.state.team_cycle_dispatcher.resume(
        team_run_id, decision_request.cycle_id
    )
    record_domain_audit(
        request,
        principal,
        event_type="team.user_decision_answered",
        action="team_runs.answer_decision",
        resource_type="team_decision_request",
        resource_id=decision_request.id,
        team_run_id=team_run_id,
        metadata={
            "revision": payload.revision,
            "question_count": len(payload.answers),
        },
    )
    return {
        "team_run": _team_run_payload(run, request.app.state.team_run_service),
        "decision_request": _decision_request_payload(decision_request),
    }


@router.post("/{team_run_id}/tasks/{task_id}/retry")
async def retry_team_task(
    request: Request,
    team_run_id: str,
    task_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    if registry.is_running(team_run_id):
        raise HTTPException(status_code=409, detail="Team run already running")
    try:
        current = service.get_team_run(team_run_id)
        rules_snapshot = request.app.state.rule_set_service.snapshot_for_team(
            current.team_id
        )
        run, task, cycle = service.retry_failed_task(
            team_run_id,
            task_id,
            rules_snapshot_json=json.dumps(rules_snapshot, ensure_ascii=False),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run or task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # retry_failed_task snapshots the rules and the space policy onto its new
    # cycle but not the provider capabilities, and the model factory reads
    # those back off the cycle rather than off the registry. An unfrozen retry
    # cycle therefore accepted the retry, reported success, and then failed the
    # resume it told the operator to perform -- with "capabilities_unavailable"
    # naming a provider that was live and ready.
    #
    # Frozen here rather than inside retry_failed_task because the freeze needs
    # the registry, and create-then-freeze is what the dispatcher already does
    # for every other cycle. A failure is reported now, while the operator is
    # standing here, instead of at resume: the cycle is settled as failed so
    # the run does not keep a cycle nobody can execute.
    if cycle is not None:
        try:
            request.app.state.team_provider_recovery.freeze_cycle(cycle.id)
        except Exception as exc:  # noqa: BLE001 - any freeze failure is the same
            service.set_cycle_status(cycle.id, "failed", error_message=str(exc))
            raise HTTPException(
                status_code=409,
                detail=f"provider capabilities could not be frozen: {exc}",
            ) from exc
    task_payload = _task_payload(
        task,
        [
            dependency.depends_on_task_id
            for dependency in service.list_task_dependencies(task.id)
        ],
    )
    await request.app.state.event_bus.publish(
        {
            "type": "team.task.updated",
            "team_run_id": team_run_id,
            "task_id": task.id,
            "original_task_id": task_id,
            "task": task_payload,
        }
    )
    record_domain_audit(
        request,
        principal,
        event_type="team.task_retry_requested",
        action="team_runs.retry_task",
        resource_type="team_task",
        resource_id=task_id,
        team_run_id=team_run_id,
        team_task_id=task_id,
        metadata={
            "retry_cycle_id": cycle.id if cycle else None,
            "retry_task_id": task.id,
            "status": task.status,
        },
    )
    return {
        "team_run": _team_run_payload(run, request.app.state.team_run_service),
        "task": task_payload,
        "cycle": _cycle_payload(cycle) if cycle else None,
    }


@router.post("/{team_run_id}/add-work")
async def add_work(
    request: Request,
    team_run_id: str,
    payload: AddWorkRequest,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    require_intake_open(request)
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    runtime = request.app.state.team_runtime
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if run.lifecycle_mode == "continuous":
        raise HTTPException(
            status_code=409,
            detail="Continuous team runs accept work through cycle requests",
        )
    if run.run_mode != "plan_and_execute":
        raise HTTPException(status_code=409, detail="Additional work is only supported for plan_and_execute runs")
    if run.status == "draft":
        raise HTTPException(status_code=409, detail="Start the run before adding work")
    if run.status == "interrupted":
        raise HTTPException(status_code=409, detail="Resume the run before adding work")
    if run.status == "waiting_for_user":
        raise HTTPException(status_code=409, detail="Answer the pending decision request first")
    # "canceled" is in _TERMINAL, so without this guard a canceled run passes
    # here and the resume branch below puts it back to running -- Stop reports
    # success while the agent keeps writing to the workspace. Completed runs are
    # still reopenable; only an explicit cancel is refused.
    if run.status == "canceled":
        raise HTTPException(
            status_code=409,
            detail="Canceled team runs cannot accept additional work",
        )

    await runtime.add_work(team_run_id, payload.instruction)

    run = service.get_team_run(team_run_id)
    if run.status in _TERMINAL and not registry.is_running(team_run_id):
        request.app.state.team_run_orchestrator.resume(team_run_id)

    record_domain_audit(
        request,
        principal,
        event_type="team.additional_work_requested",
        action="team_runs.add_work",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
        metadata={"instruction_length": len(payload.instruction)},
    )
    return {"team_run": _team_run_payload(service.get_team_run(team_run_id), service)}


@router.post("/{team_run_id}/cancel")
async def cancel_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if await registry.cancel_and_wait(team_run_id):
        run = service.get_team_run(team_run_id)
    if run.lifecycle_mode == "continuous":
        request.app.state.team_cycle_service.cancel_run(
            team_run_id,
            reason="user",
        )
        run = service.get_team_run(team_run_id)
    elif run.status == "waiting_for_user":
        run = service.cancel_waiting_decision(team_run_id)
    elif run.status not in _TERMINAL:
        run = service.set_run_status(team_run_id, "canceled")
    record_domain_audit(
        request,
        principal,
        event_type="team.run_canceled",
        action="team_runs.cancel",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
    )
    return {"team_run": _team_run_payload(run, request.app.state.team_run_service)}


@router.delete("/{team_run_id}")
def delete_team_run(
    request: Request,
    team_run_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    registry = request.app.state.team_run_registry
    try:
        run = service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    if registry.is_running(team_run_id) or run.status in _ACTIVE or run.status == "waiting_for_user":
        raise HTTPException(status_code=409, detail="Running team runs cannot be deleted")
    try:
        service.delete_team_run(team_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_domain_audit(
        request,
        principal,
        event_type="team.run_deleted",
        action="team_runs.delete",
        resource_type="team_run",
        resource_id=team_run_id,
        team_run_id=team_run_id,
        metadata={"workspace_deleted": True},
    )
    return {"deleted": True}


@router.get("/{team_run_id}/agents")
def list_team_agents(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        agents = request.app.state.team_run_service.list_agents(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    try:
        selected, next_cursor = _page_entities(agents, limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "agents": [_agent_payload(agent) for agent in selected],
        "next_cursor": next_cursor,
    }


@router.get("/{team_run_id}/tasks")
def list_team_tasks(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    service = request.app.state.team_run_service
    try:
        tasks = service.list_tasks(team_run_id)
        task_dependencies = service.list_task_dependency_map(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    try:
        selected, next_cursor = _page_entities(tasks, limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "tasks": [
            _task_payload(task, task_dependencies.get(task.id, []))
            for task in selected
        ],
        "next_cursor": next_cursor,
    }


@router.get("/{team_run_id}/messages")
def list_team_messages(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        messages = request.app.state.team_run_service.list_messages(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    try:
        selected, next_cursor = _page_entities(messages, limit, cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return {
        "messages": [_message_payload(message) for message in selected],
        "next_cursor": next_cursor,
    }


@router.get("/{team_run_id}/documents")
def list_team_documents(
    request: Request,
    team_run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
    _session: None = session_dependency,
) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    documents = _document_items(root)
    if cursor:
        try:
            values = decode_cursor(cursor, 2)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor") from exc
        if not isinstance(values[0], str) or not isinstance(values[1], str):
            raise HTTPException(status_code=400, detail="Invalid cursor")
        documents = [
            item for item in documents
            if (str(item["modified_at"]), str(item["path"])) < (values[0], values[1])
        ]
    selected = documents[: limit + 1]
    has_more = len(selected) > limit
    selected = selected[:limit]
    next_cursor = None
    if has_more and selected:
        next_cursor = encode_cursor(selected[-1]["modified_at"], selected[-1]["path"])
    return {"documents": selected, "next_cursor": next_cursor}


@router.get("/{team_run_id}/documents/content")
def read_team_document(request: Request, team_run_id: str, path: str, _session: None = session_dependency) -> dict[str, object]:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    try:
        target = _safe_child(root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_visible_document(root, target):
        raise HTTPException(status_code=404, detail="Document not found")
    kind = _doc_kind(target)
    size = target.stat().st_size
    if kind == "image":
        encoded_run_id = quote(team_run_id, safe="")
        encoded_path = quote(path, safe="")
        return {
            "path": path,
            "kind": kind,
            "content": None,
            "previewable": True,
            "preview_url": (
                f"/api/team-runs/{encoded_run_id}/documents/image?path={encoded_path}"
            ),
        }
    if kind == "binary" or _is_sensitive_document(target.name) or size > _MAX_PREVIEW_BYTES:
        return {"path": path, "kind": kind, "content": None, "previewable": False,
                "reason": "binary or too large"}
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"path": path, "kind": "binary", "content": None, "previewable": False,
                "reason": "not utf-8 text"}
    return {"path": path, "kind": kind, "content": content, "previewable": True}


@router.get("/{team_run_id}/documents/image")
def read_team_document_image(
    request: Request, team_run_id: str, path: str, _session: None = session_dependency
) -> FileResponse:
    try:
        run = request.app.state.team_run_service.get_team_run(team_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Team run not found") from exc
    root = _resolved_workspace(run)
    try:
        target = _safe_child(root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    if not _is_visible_document(root, target):
        raise HTTPException(status_code=404, detail="Document not found")
    media_type = _IMAGE_MIME_TYPES.get(target.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=415, detail="Unsupported image type")
    return FileResponse(
        target,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


_TEXT_EXTS = {".txt", ".log", ".csv", ".yaml", ".yml", ".toml", ".ini"}
_CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".sh", ".sql"}
_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_IGNORED_DOCUMENT_DIRS = {
    ".git", ".hg", ".svn", ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".venv", "venv", "__pycache__", "node_modules", "dist", "build", "coverage",
    "project", "workspace",
}
_MAX_PREVIEW_BYTES = 1_000_000


def _doc_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "md"
    if suffix == ".json":
        return "json"
    if suffix == ".html":
        return "html"
    if suffix in _IMAGE_MIME_TYPES:
        return "image"
    if suffix in _TEXT_EXTS:
        return "text"
    if suffix in _CODE_EXTS:
        return "code"
    return "binary"


def _resolved_workspace(run) -> Path:
    return Path(run.working_root or run.workspace_root).resolve()


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escapes workspace")
    return candidate


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _is_sensitive_document(name: str) -> bool:
    lowered = name.lower()
    return lowered == ".env" or lowered.startswith(".env.")


def _is_visible_document(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return (
        not _is_sensitive_document(relative.name)
        and all(part.lower() not in _IGNORED_DOCUMENT_DIRS for part in relative.parts[:-1])
    )


def _document_items(root: Path) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    if not root.is_dir():
        return documents
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name.lower() not in _IGNORED_DOCUMENT_DIRS]
        for name in files:
            if _is_sensitive_document(name):
                continue
            walked_path = Path(current) / name
            relative = walked_path.relative_to(root).as_posix()
            try:
                path = _safe_child(root, relative)
            except ValueError:
                continue
            kind = _doc_kind(path)
            if kind == "binary":
                continue
            size = path.stat().st_size
            if kind != "image":
                if size > _MAX_PREVIEW_BYTES:
                    continue
                try:
                    path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
            documents.append(
                {
                    "path": relative,
                    "size": size,
                    "modified_at": _iso_mtime(path),
                    "kind": kind,
                    "previewable": True,
                }
            )
    return sorted(
        documents,
        key=lambda item: (str(item["modified_at"]), str(item["path"])),
        reverse=True,
    )


def _document_summary(root: Path) -> dict[str, object]:
    count = 0
    size_bytes = 0
    kinds: dict[str, int] = {}
    for item in _document_items(root):
        count += 1
        size_bytes += int(item["size"])
        kind = str(item["kind"])
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"count": count, "size_bytes": size_bytes, "kinds": kinds}


def _page_entities(items: list, limit: int, cursor: str | None) -> tuple[list, str | None]:
    selected_items = sorted(items, key=lambda item: (item.created_at, item.id))
    if cursor:
        created_at, entity_id = decode_cursor(cursor, 2)
        if not isinstance(created_at, str) or not isinstance(entity_id, str):
            raise ValueError("Invalid cursor")
        selected_items = [
            item
            for item in selected_items
            if (item.created_at, item.id) > (created_at, entity_id)
        ]
    selected = selected_items[: limit + 1]
    has_more = len(selected) > limit
    selected = selected[:limit]
    next_cursor = None
    if has_more and selected:
        next_cursor = encode_cursor(selected[-1].created_at, selected[-1].id)
    return selected, next_cursor


def _team_run_payload(run: TeamRun, service=None) -> dict[str, object]:
    # The service owns the calculation because only it knows whether
    # concurrency is enabled; omitting it reports the pre-concurrency
    # answer, which is the safe default for any caller that has no service.
    effective_workers = (
        service.effective_workers(run.max_workers) if service is not None else 1
    )
    return {
        "id": run.id,
        "goal": run.goal,
        "team_name": _team_name(run),
        "status": run.status,
        "run_mode": run.run_mode,
        "lifecycle_mode": run.lifecycle_mode,
        "execution_policy": run.execution_policy,
        "leader_agent_id": run.leader_agent_id,
        # See TeamRunService._effective_workers: reported as what execution
        # will actually overlap, not as the constant this used to be.
        "max_workers": effective_workers,
        "configured_max_workers": run.max_workers,
        "execution_mode": "concurrent" if effective_workers > 1 else "sequential",
        "workspace_root": run.workspace_root,
        "working_root": run.working_root,
        "artifact_root": run.artifact_root,
        "space_policy": run.space_policy,
        "team_id": run.team_id,
        "parent_team_run_id": run.parent_team_run_id,
        "rules_snapshot": run.rules_snapshot,
        "summary": run.summary,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "pause_requested_at": run.pause_requested_at,
        "updated_at": run.updated_at,
    }


def _team_name(run: TeamRun) -> str | None:
    snapshot = run.rules_snapshot or {}
    team = snapshot.get("team")
    if not isinstance(team, dict):
        return None
    name = str(team.get("name") or "").strip()
    return name or None


def _agent_payload(agent: TeamAgent) -> dict[str, object]:
    return {
        "id": agent.id,
        "team_run_id": agent.team_run_id,
        "name": agent.name,
        "role": agent.role,
        "persona_id": agent.persona_id,
        "persona_snapshot": agent.persona_snapshot,
        "backend": agent.backend,
        "model": agent.model,
        "status": agent.status,
        "workspace_path": agent.workspace_path,
        "current_task_id": agent.current_task_id,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "started_at": agent.started_at,
        "finished_at": agent.finished_at,
    }


def _task_payload(
    task: TeamTask,
    depends_on_task_ids: list[str] | None = None,
    failure_shape: dict[str, object] | None = None,
    build_evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": task.id,
        "team_run_id": task.team_run_id,
        "cycle_id": task.cycle_id,
        "retry_of_task_id": task.retry_of_task_id,
        "depends_on_task_ids": list(depends_on_task_ids or []),
        "title": task.title,
        "description": task.description,
        "owner_agent_id": task.owner_agent_id,
        "status": task.status,
        "required": task.required,
        "acceptance": {
            "required_outputs": list(task.acceptance.required_outputs),
            "required_verifications": required_verifications_payload(
                task.acceptance.required_verifications
            ),
        },
        "outcome": task.outcome,
        "acceptance_result": task.acceptance_result,
        "acceptance_recovery_attempts": task.acceptance_recovery_attempts,
        "failure_shape": failure_shape,
        "build_evidence": build_evidence,
        "result": task.result,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _message_payload(message: TeamMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "team_run_id": message.team_run_id,
        "cycle_id": message.cycle_id,
        "sender_agent_id": message.sender_agent_id,
        "recipient_agent_id": message.recipient_agent_id,
        "kind": message.kind,
        "content": message.content,
        "metadata": message.metadata,
        "created_at": message.created_at,
    }


def _cycle_payload(
    cycle: TeamRunCycle,
    coverage_gaps: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    execution_metadata = cycle.execution_metadata or {}
    semantic_source = execution_metadata.get("semantic_source")
    effective_instruction = (
        semantic_source.get("effective_instruction")
        if isinstance(semantic_source, dict)
        else None
    )
    return {
        "id": cycle.id,
        "team_run_id": cycle.team_run_id,
        "sequence": cycle.sequence,
        "source_type": cycle.source_type,
        "source_id": cycle.source_id,
        "status": cycle.status,
        "rounds_budget": cycle.rounds_budget,
        "rounds_used": cycle.rounds_used,
        "rules_snapshot": cycle.rules_snapshot,
        "space_policy": cycle.space_policy,
        "effective_instruction": effective_instruction,
        "summary": cycle.summary,
        "coverage_gaps": coverage_gaps,
        "error_message": cycle.error_message,
        "created_at": cycle.created_at,
        "started_at": cycle.started_at,
        "finished_at": cycle.finished_at,
        "updated_at": cycle.updated_at,
    }


def _decision_request_payload(
    decision_request: TeamDecisionRequest | None,
) -> dict[str, object] | None:
    if decision_request is None:
        return None
    return {
        "id": decision_request.id,
        "team_run_id": decision_request.team_run_id,
        "cycle_id": decision_request.cycle_id,
        "status": decision_request.status,
        "revision": decision_request.revision,
        "items": decision_request.items,
        "answers": decision_request.answers,
        "file_path": decision_request.file_path,
        "created_at": decision_request.created_at,
        "published_at": decision_request.published_at,
        "answered_at": decision_request.answered_at,
        "updated_at": decision_request.updated_at,
    }


def _auto_series_payload(
    series: TeamAutoSeries | None,
) -> dict[str, object] | None:
    if series is None:
        return None
    return {
        "id": series.id,
        "team_run_id": series.team_run_id,
        "series_number": series.series_number,
        "status": series.status,
        "target_slots": series.target_slots,
        "settled_slots": series.settled_slots,
        "interval_seconds": series.interval_seconds,
        "next_run_at": series.next_run_at,
        "pause_reason": series.pause_reason,
        "paused_cycle_id": series.paused_cycle_id,
        "created_at": series.created_at,
        "started_at": series.started_at,
        "completed_at": series.completed_at,
        "updated_at": series.updated_at,
    }


def _cycle_request_payload(
    cycle_request: TeamCycleRequest | None,
) -> dict[str, object] | None:
    if cycle_request is None:
        return None
    return {
        "id": cycle_request.id,
        "team_run_id": cycle_request.team_run_id,
        "auto_series_id": cycle_request.auto_series_id,
        "slot_ordinal": cycle_request.slot_ordinal,
        "source_type": cycle_request.source_type,
        "source_id": cycle_request.source_id,
        "status": cycle_request.status,
        "instruction": cycle_request.instruction,
        "previous_cycle_id": cycle_request.previous_cycle_id,
        "previous_summary_text": cycle_request.previous_summary_text,
        "retry_of_request_id": cycle_request.retry_of_request_id,
        "created_at": cycle_request.created_at,
        "claimed_at": cycle_request.claimed_at,
        "settled_at": cycle_request.settled_at,
        "updated_at": cycle_request.updated_at,
    }


def _contests_payload(
    cycle_requests: list[TeamCycleRequest],
    cycles: list[TeamRunCycle],
    verdict_payload_by_cycle: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Objection, verdict, and -- when there is no verdict -- why not.

    kind alone cannot say whether a contest is still waiting or its cycle died:
    both leave it null, and refiling the same objection is idempotent, so a dead
    contest that reads as pending never resolves and never retries. The request
    status and the cycle's error message are what separate the two.
    """
    cycle_by_request_id = {
        cycle.request_id: cycle for cycle in cycles if cycle.request_id is not None
    }
    contests = []
    for cycle_request in cycle_requests:
        if cycle_request.source_type != "contest":
            continue
        cycle = cycle_by_request_id.get(cycle_request.id)
        payload = verdict_payload_by_cycle.get(cycle.id) if cycle is not None else None
        contests.append(
            {
                "objection": cycle_request.instruction,
                "kind": payload.get("kind") if payload else None,
                "reason": payload.get("reason") if payload else None,
                "supersedes": list(payload.get("supersedes") or []) if payload else [],
                "status": cycle_request.status,
                "error_message": cycle.error_message if cycle is not None else None,
                "created_at": cycle_request.created_at,
            }
        )
    return contests
