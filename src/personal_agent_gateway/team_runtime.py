import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Protocol

from personal_agent_gateway.archive import ArchiveService
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelClient
from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.source_staging import StagedInputs
from personal_agent_gateway.team_acceptance import AcceptanceResult, TeamAcceptanceService
from personal_agent_gateway.team_artifact_publisher import (
    ArtifactPublicationError,
    TeamArtifactPublisher,
)
from personal_agent_gateway.team_outcomes import (
    TaskOutcome,
    TaskOutcomeError,
    parse_task_outcome,
)
from personal_agent_gateway.team_results import (
    TeamRunResultPackager,
    workspace_changes,
    workspace_snapshot,
)
from personal_agent_gateway.team_structured_output import normalize_json_envelope
from personal_agent_gateway.teams import (
    ACCEPTANCE_RECOVERY_CAP,
    TaskAcceptance,
    TeamAgent,
    TeamRun,
    TeamRunService,
    TeamTask,
    _validate_task_acceptance,
)

PLANNING_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
Goal: {goal}
Persona snapshot: {persona_snapshot_json}
Available team members: {team_roster_json}

Before creating tasks, identify any consequential choice that only the user can make.
First resolve ambiguity from the goal, frozen rules, and prior user decisions.
Return ONLY one of:
1. A JSON array of task objects. Each object must contain exactly:
   {{"title":"...", "description":"...", "owner_agent_id":"member-id or null",
   "required":true, "acceptance":{{"required_outputs":["relative/path"],
   "required_verifications":["verification-name"]}}}}
   Assign the member whose persona role and responsibilities best match the task.
   Use null only when no member is available. Do not assign by list order or
   previous completion status. Every task needs at least one required output or
   verification.
2. {{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why planning cannot safely continue","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"run"}}}}
Use ask_user only when the choice materially changes the plan and cannot be inferred safely."""

WORKER_PROMPT = """You are an agent in a personal-agent-gateway Team Run.
Persona:
{persona_snapshot_json}

Perform the concrete assignment below now. It is the complete user request.
Do not ask the user what work to do and do not substitute unrelated repository work.

CONCRETE ASSIGNMENT
Goal: {goal}
Assigned task: {task_title}
Task description: {task_description}

If you need information from another team member to proceed, end your reply with
ONLY this fenced block and nothing after it:
```json
{{"needs_info": {{"topic": "<short topic>", "question": "<your question>"}}}}
```
Otherwise, the final response must contain only this JSON object and no prose or
code fences:
{{"status":"completed|blocked|failed","summary":"concise result",
"reason_code":"stable-code or null","deliverables":[{{"path":"relative/path",
"kind":"file kind"}}],"verifications":[{{"name":"verification name",
"status":"passed|failed","evidence":"concrete evidence"}}]}}"""

ACCEPTANCE_REVIEW_PROMPT = f"""You are the leader reviewing a rejected Team Run task outcome.
Decide only from the goal, Cycle instruction, frozen rules, SPACE, Task contract,
outcome, failure reason, changed paths, history, and remaining attempts. The recovery
attempt cap is {ACCEPTANCE_RECOVERY_CAP}.

Prefer Worker correction when the contract is valid. Revise acceptance only when the
contract itself is wrong. Ask the user only for a consequential choice the Team cannot
infer. Never approve the current rejected outcome retroactively.

Return ONLY one JSON object in exactly one of these forms:
{{"resolution":{{"kind":"retry_worker","instruction":"concrete correction", "reason":"why the current outcome was rejected"}}}}
{{"resolution":{{"kind":"revise_acceptance","acceptance":{{"required_outputs":["relative/path"],"required_verifications":["verification"]}},"instruction":"concrete resubmission instruction", "reason":"why the contract is wrong"}}}}
{{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why the Team cannot infer the answer","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"task"}}}}
{{"resolution":{{"kind":"fail","reason_code":"stable-code","summary":"why recovery cannot continue"}}}}"""

SYNTHESIS_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
Goal: {goal}
Task results:
{results}

Before finalizing, identify any consequential choice that only the user can make to
produce an accurate final response. First use the goal, frozen rules, prior user
decisions, and task results.
Return either:
1. A concise plain-text summary of what was accomplished, including any failures.
2. ONLY {{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why the final response cannot be completed accurately","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"run"}}}}
At this stage, ask only about final interpretation or presentation that does not
require additional worker execution."""

MEDIATION_PROMPT = """You are the leader mediating a Team Run.
Goal: {goal}
A worker on task "{task_title}" asks: {question}

Team outputs so far:
{outputs}

First answer from the goal, frozen rules, prior user decisions, and completed outputs.
Return ONLY one JSON object in one of these forms:
{{"resolution":{{"kind":"answer","answer":"concise instruction for the worker"}}}}
{{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why work cannot safely continue","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"task or run"}}}}
Use ask_user only when the user must decide. Prefer task scope; use run scope only when remaining work would be invalid or cause major rework."""

ADD_WORK_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
The user is adding work to an in-flight run. Break the request into concrete tasks.
Return ONLY a JSON array of task objects using the same exact schema:
{{"title":"...", "description":"...", "owner_agent_id":"member-id or null",
"required":true, "acceptance":{{"required_outputs":["relative/path"],
"required_verifications":["verification-name"]}}}}
Run context:
{goal}
Existing tasks: {existing_titles}
Current cycle objective: {instruction}
Available team members: {team_roster_json}

Assign every task to the member whose persona role and responsibilities best match it.
Return "owner_agent_id" using the exact ID from the available team members list.
Do not assign by list order or previous completion status."""

AGENT_REINVOCATION_CAP = 3


@dataclass(frozen=True)
class UserDecisionResolution:
    decision: dict[str, object]


@dataclass(frozen=True)
class AcceptanceReviewResolution:
    kind: Literal["retry_worker", "revise_acceptance", "ask_user", "fail"]
    reason: str
    instruction: str | None = None
    acceptance: TaskAcceptance | None = None
    decision: dict[str, object] | None = None
    reason_code: str | None = None


def _rules_block(snapshot: dict | None, include_persona_baseline: bool) -> str:
    if not snapshot:
        return ""
    sections: list[tuple[str, dict | None]] = [
        ("GLOBAL RULES", snapshot.get("global")),
        ("TEAM RULES", snapshot.get("team")),
    ]
    if include_persona_baseline:
        sections.append(("PERSONA BASELINE", snapshot.get("persona_baseline")))
    lines: list[str] = []
    for title, section in sections:
        if not section:
            continue
        personality = (section.get("personality") or "").strip()
        rules = section.get("rules") or []
        if not personality and not rules:
            continue
        lines.append(f"[{title}]")
        if personality:
            lines.append(personality)
        for rule in rules:
            prefix = "MUST" if rule.get("level") == "REQUIRED" else "SHOULD"
            lines.append(f"- {prefix}: {rule.get('text', '')}")
        lines.append("")
    if not lines:
        return ""
    return "TEAM RULES (frozen at run start):\n" + "\n".join(lines).strip() + "\n\n"


def _space_block(
    run: TeamRun,
    policy: dict | None,
    cycle_id: str | None = None,
) -> str:
    policy = policy or {}
    read_scope = policy.get("read_path") or policy.get("read_mode") or "configured SPACE"
    write_mode = policy.get("write_mode") or "isolated"
    working_root = run.working_root or run.workspace_root
    artifact_root = run.artifact_root or run.workspace_root
    frozen_at = "cycle" if cycle_id is not None else "run"
    write_instruction = (
        "- Writes outside the working root or artifact root are allowed by "
        "full_access mode.\n"
        if write_mode == "full_access"
        else "- Do not write outside the working root or artifact root.\n"
    )
    return (
        f"SPACE POLICY (frozen at {frozen_at} start):\n"
        f"- Read scope: {read_scope}\n"
        f"- Write mode: {write_mode}\n"
        f"- Working root: {working_root}\n"
        f"- Artifact root: {artifact_root}\n"
        f"{write_instruction}\n"
    )


class TeamModelFactory(Protocol):
    def __call__(
        self,
        agent: TeamAgent,
        cycle_id: str | None = None,
    ) -> ModelClient: ...


class TeamRuntime:
    def __init__(
        self,
        teams: TeamRunService,
        model_factory: TeamModelFactory,
        event_bus: EventBus | None = None,
        archive_service: ArchiveService | None = None,
        result_packager: TeamRunResultPackager | None = None,
        acceptance_service: TeamAcceptanceService | None = None,
        artifact_publisher: TeamArtifactPublisher | None = None,
        staged_inputs_resolver: Callable[[Path], StagedInputs | None] | None = None,
    ) -> None:
        self._teams = teams
        self._model_factory = model_factory
        self._event_bus = event_bus
        self._archive_service = archive_service
        self._result_packager = result_packager
        self._acceptance_service = acceptance_service or TeamAcceptanceService()
        self._artifact_publisher = artifact_publisher
        self._staged_inputs_resolver = staged_inputs_resolver

    def _model(
        self,
        agent: TeamAgent,
        cycle_id: str | None,
    ) -> ModelClient:
        if cycle_id is None:
            return self._model_factory(agent)
        return self._model_factory(agent, cycle_id)

    def _space_policy(
        self,
        run: TeamRun,
        cycle_id: str | None,
    ) -> dict | None:
        if cycle_id is None:
            return run.space_policy
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.team_run_id != run.id:
            raise ValueError("Cycle belongs to a different team run")
        return cycle.space_policy or run.space_policy

    def _goal_context(self, run: TeamRun, cycle_id: str | None) -> str:
        if cycle_id is None:
            return run.goal
        objective = self._teams.get_cycle_objective(cycle_id)
        if run.goal and objective and objective != run.goal:
            return (
                f"Base objective: {run.goal}\n"
                f"Current cycle objective: {objective}"
            )
        return objective or run.goal

    def _archive_block(
        self,
        query: str,
        *,
        persona_id: str,
        allow_request: bool,
    ) -> str:
        if self._archive_service is None:
            return ""
        return (
            self._archive_service.prompt_context(
                query,
                persona_id=persona_id,
                allow_request=allow_request,
            )
            + "\n\n"
        )

    def _finalize_persona_content(
        self,
        content: str,
        *,
        persona_id: str,
        team_run_id: str,
    ) -> str:
        if self._archive_service is None:
            return content
        clean, _requests = self._archive_service.capture_response_requests(
            content,
            persona_id=persona_id,
            team_run_id=team_run_id,
        )
        return clean

    async def start(self, team_run_id: str, cycle_id: str | None = None) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        self._validate_cycle(run, cycle_id)
        leader: TeamAgent | None = None
        try:
            if cycle_id is not None:
                self._activate_cycle(cycle_id)
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "planning")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.started", "team_run_id": run.id})

            planning_result = await self._plan(run, leader, cycle_id)
            if isinstance(planning_result, UserDecisionResolution):
                self._teams.defer_run_for_user_decision(
                    run.id,
                    planning_result.decision,
                    stage="planning",
                    cycle_id=cycle_id,
                )
                return await self._publish_user_decision_request(run, cycle_id)

            run = self._teams.get_team_run(run.id)
            if run.run_mode != "plan_and_execute":
                self._teams.set_agent_status(leader.id, "completed")
                run = self._teams.set_run_status(run.id, "completed")
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "completed")
                self._package_results(run, leader, cycle_id)
                await self._publish({"type": "team.run.completed", "team_run_id": run.id})
                return run

            workers = _find_workers(self._teams.list_agents(run.id))
            if not workers:
                error = "plan_and_execute run has no worker agents (empty member_persona_ids)"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run

            run = self._teams.set_run_status(run.id, "running")
            await self._publish({"type": "team.run.executing", "team_run_id": run.id})
            return await self._execute_and_synthesize(run, leader, workers, cycle_id)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._teams.set_run_status(run.id, "failed", error_message=error)
            if cycle_id is not None:
                self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return run

    async def _plan(
        self, run: TeamRun, leader: TeamAgent, cycle_id: str | None = None
    ) -> list[dict[str, object]] | UserDecisionResolution:
        leader_agent = self._teams.get_agent(leader.id)
        members = _find_workers(self._teams.list_agents(run.id))
        member_ids = {member.id for member in members}
        model = self._model(leader_agent, cycle_id)
        goal_context = self._goal_context(run, cycle_id)
        prompt = _space_block(
            run,
            self._space_policy(run, cycle_id),
            cycle_id,
        ) + _rules_block(
            self._rules_snapshot(run, cycle_id), include_persona_baseline=False
        ) + self._archive_block(
            goal_context,
            persona_id=leader_agent.persona_id,
            allow_request=False,
        ) + PLANNING_PROMPT.format(
            goal=goal_context,
            persona_snapshot_json=json.dumps(leader_agent.persona_snapshot, ensure_ascii=False),
            team_roster_json=_assignment_roster_json(members),
        )
        decision_context = self._teams.decision_context_for_run(
            run.id, stage="planning", cycle_id=cycle_id
        )
        if decision_context:
            prompt += (
                "\n\nResolved user decisions for planning:\n"
                f"{decision_context}\nDo not ask these resolved questions again."
            )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        resolution = _parse_mediation_resolution(response.content)
        if resolution["kind"] == "ask_user":
            return UserDecisionResolution(resolution)
        try:
            tasks = _parse_task_plan(response.content)
        except ValueError:
            retry = await model.complete(
                [{"role": "user", "content": prompt + "\nReturn ONLY a JSON array. No prose, no code fences."}]
            )
            if retry.upstream_session_id:
                self._teams.set_agent_session(leader_agent.id, retry.upstream_session_id)
            tasks = _parse_task_plan(retry.content)
        for task in tasks:
            owner_agent_id = task.get("owner_agent_id")
            created = self._teams.create_task(
                run.id,
                task["title"],
                task["description"],
                owner_agent_id=owner_agent_id if owner_agent_id in member_ids else None,
                cycle_id=cycle_id,
                required=task["required"],
                acceptance=task["acceptance"],
            )
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": created.id})
        self._teams.append_message(
            run.id,
            leader.id,
            None,
            "plan_note",
            f"Planning completed with {len(tasks)} tasks.",
            {},
            cycle_id=cycle_id,
        )
        return tasks

    async def _execute(
        self,
        run: TeamRun,
        leader: TeamAgent,
        workers: list[TeamAgent],
        cycle_id: str | None = None,
    ) -> None:
        counter = 0
        while True:
            pending = [
                task
                for task in self._teams.list_tasks(run.id, cycle_id)
                if task.status == "pending"
            ]
            if not pending:
                return
            task = pending[0]
            assigned = next(
                (worker for worker in workers if worker.id == task.owner_agent_id),
                None,
            )
            worker = assigned or workers[counter % len(workers)]
            counter += 1
            task, worker = self._teams.start_task(task.id, worker.id)
            await self._publish(
                {
                    "type": "team.task.updated",
                    "team_run_id": run.id,
                    "task_id": task.id,
                    "agent_id": worker.id,
                }
            )
            try:
                working_root = Path(run.working_root or run.workspace_root)
                before = workspace_snapshot(working_root)
                outcome = await self._run_task(run, leader, worker, task)
                if isinstance(outcome, UserDecisionResolution):
                    request = self._teams.defer_task_for_user_decision(
                        task.id, worker.id, outcome.decision
                    )
                    task = self._teams.get_task(task.id)
                    worker = self._teams.get_agent(worker.id)
                    await self._publish(
                        {
                            "type": "team.task.updated",
                            "team_run_id": run.id,
                            "task_id": task.id,
                            "agent_id": worker.id,
                            "decision_request_id": request.id,
                        }
                    )
                    if outcome.decision.get("blocking_scope") == "run":
                        return
                    continue
                changes = workspace_changes(
                    before,
                    workspace_snapshot(working_root),
                )
                self._teams.append_message(
                    run.id,
                    worker.id,
                    None,
                    "agent_output",
                    outcome.summary,
                    {
                        "task_id": task.id,
                        "outcome_status": outcome.status,
                        "reason_code": outcome.reason_code,
                        **changes,
                    },
                    cycle_id=cycle_id,
                )
                staged_inputs = (
                    self._staged_inputs_resolver(working_root)
                    if self._staged_inputs_resolver is not None
                    else None
                )
                acceptance = self._acceptance_service.evaluate(
                    task,
                    outcome,
                    working_root,
                    staged_inputs=staged_inputs,
                )
                if acceptance.accepted and outcome.deliverables:
                    try:
                        if self._artifact_publisher is None:
                            raise ArtifactPublicationError(
                                "artifact_publication_failed"
                            )
                        self._artifact_publisher.publish(
                            run.id,
                            cycle_id,
                            task,
                            outcome,
                            working_root,
                        )
                    except ArtifactPublicationError:
                        acceptance = AcceptanceResult(
                            accepted=False,
                            status="failed",
                            reason_code="artifact_publication_failed",
                            evidence={},
                        )
                self._teams.record_task_outcome(
                    task.id,
                    asdict(outcome),
                    asdict(acceptance),
                )
                task, worker = self._teams.finish_task(
                    task.id,
                    worker.id,
                    acceptance.status,
                    result=outcome.summary if acceptance.accepted else None,
                    error_message=(
                        None
                        if acceptance.accepted
                        else acceptance.reason_code or outcome.reason_code
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                error = redact_text(exc) or type(exc).__name__
                task, worker = self._teams.finish_task(
                    task.id, worker.id, "failed", error_message=error
                )
            await self._publish(
                {
                    "type": "team.task.updated",
                    "team_run_id": run.id,
                    "task_id": task.id,
                    "agent_id": worker.id,
                }
            )

    async def _run_task(
        self, run: TeamRun, leader: TeamAgent, worker: TeamAgent, task: TeamTask
    ) -> TaskOutcome | UserDecisionResolution:
        worker_agent = self._teams.get_agent(worker.id)
        model = self._model(worker_agent, task.cycle_id)
        response = await model.complete(
            [{"role": "user", "content": self._worker_prompt(run, worker_agent, task)}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        content = response.content

        while True:
            req = _parse_needs_info(content)
            if req is None:
                return self._task_outcome(
                    content,
                    persona_id=worker_agent.persona_id,
                    team_run_id=run.id,
                )
            run = self._teams.get_team_run(run.id)
            worker_agent = self._teams.get_agent(worker.id)
            if task.cycle_id is not None:
                cycle = self._teams.get_cycle(task.cycle_id)
                rounds_used = cycle.rounds_used
                rounds_budget = cycle.rounds_budget
            else:
                rounds_used = run.rounds_used
                rounds_budget = run.rounds_budget
            if rounds_used >= rounds_budget or worker_agent.reinvocations >= AGENT_REINVOCATION_CAP:
                content = await self._resume_worker(
                    worker.id,
                    "No more consultation is available. Produce your best-effort final "
                    "result now, without a needs_info block.",
                    task.cycle_id,
                )
                return self._task_outcome(
                    content,
                    persona_id=worker_agent.persona_id,
                    team_run_id=run.id,
                )
            query_message = self._teams.append_message(
                run.id, worker.id, leader.id, "query", req["question"],
                {"task_id": task.id, "topic": req["topic"]},
                cycle_id=task.cycle_id,
            )
            resolution = await self._mediate(run, leader, task, req["question"])
            if task.cycle_id is not None:
                rounds_used = self._teams.increment_cycle_rounds_used(
                    task.cycle_id
                ).rounds_used
            else:
                run = self._teams.increment_rounds_used(run.id)
                rounds_used = run.rounds_used
            if resolution["kind"] == "ask_user":
                resolution["query_message_id"] = query_message.id
                return UserDecisionResolution(resolution)
            answer = str(resolution["answer"])
            self._teams.append_message(
                run.id,
                leader.id,
                worker.id,
                "answer",
                answer,
                {"round": rounds_used, "query_id": query_message.id},
                cycle_id=task.cycle_id,
            )
            content = await self._resume_worker(
                worker.id,
                f"Answer to your question: {answer}\n\nContinue and produce your final "
                "result, or ask again only if essential.",
                task.cycle_id,
            )
            self._teams.increment_agent_reinvocations(worker.id)

    async def _resume_worker(
        self,
        worker_id: str,
        instruction: str,
        cycle_id: str | None = None,
    ) -> str:
        worker_agent = self._teams.get_agent(worker_id)
        model = self._model(worker_agent, cycle_id)
        response = await model.complete([{"role": "user", "content": instruction}])
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        return response.content

    async def _mediate(
        self, run: TeamRun, leader: TeamAgent, task: TeamTask, question: str
    ) -> dict[str, object]:
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model(leader_agent, task.cycle_id)
        goal_context = self._goal_context(run, task.cycle_id)
        prompt = _space_block(
            run,
            self._space_policy(run, task.cycle_id),
            task.cycle_id,
        ) + self._archive_block(
            f"{goal_context}\n{task.title}\n{question}",
            persona_id=leader_agent.persona_id,
            allow_request=False,
        ) + MEDIATION_PROMPT.format(
            goal=goal_context,
            task_title=task.title,
            question=question,
            outputs=self._collect_outputs(run, task.cycle_id),
        )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        return _parse_mediation_resolution(response.content)

    def _collect_outputs(self, run: TeamRun, cycle_id: str | None = None) -> str:
        lines = [
            f"[{task.title}]\n{task.result}"
            for task in self._teams.list_tasks(run.id, cycle_id)
            if task.status == "completed" and task.result
        ]
        return "\n\n".join(lines) if lines else "(no completed task outputs yet)"

    def _worker_prompt(self, run: TeamRun, worker: TeamAgent, task: TeamTask) -> str:
        goal_context = self._goal_context(run, task.cycle_id)
        prompt = _space_block(
            run,
            self._space_policy(run, task.cycle_id),
            task.cycle_id,
        ) + _rules_block(
            self._rules_snapshot(run, task.cycle_id), include_persona_baseline=True
        ) + self._archive_block(
            f"{goal_context}\n{task.title}\n{task.description}",
            persona_id=worker.persona_id,
            allow_request=True,
        ) + WORKER_PROMPT.format(
            persona_snapshot_json=json.dumps(worker.persona_snapshot, ensure_ascii=False),
            goal=goal_context,
            task_title=task.title,
            task_description=task.description,
        )
        prompt += "\n\nAcceptance criteria:\n" + json.dumps(
            {
                "required_outputs": list(task.acceptance.required_outputs),
                "required_verifications": list(
                    task.acceptance.required_verifications
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        decision_context = self._teams.decision_context_for_task(run.id, task.id)
        if decision_context:
            prompt += f"\n\nResolved user decisions for this task:\n{decision_context}"
        return prompt

    def _task_outcome(
        self,
        content: str,
        *,
        persona_id: str,
        team_run_id: str,
    ) -> TaskOutcome:
        try:
            outcome = parse_task_outcome(content)
        except TaskOutcomeError:
            finalized = self._finalize_persona_content(
                content,
                persona_id=persona_id,
                team_run_id=team_run_id,
            )
            return TaskOutcome(
                status="blocked",
                summary=finalized,
                reason_code="invalid_task_outcome",
                deliverables=(),
                verifications=(),
            )
        summary = self._finalize_persona_content(
            outcome.summary,
            persona_id=persona_id,
            team_run_id=team_run_id,
        )
        return replace(outcome, summary=summary)

    async def _execute_and_synthesize(
        self,
        run: TeamRun,
        leader: TeamAgent,
        workers: list[TeamAgent],
        cycle_id: str | None = None,
    ) -> TeamRun:
        while True:
            await self._execute(run, leader, workers, cycle_id)
            request = self._teams.get_active_decision_request(run.id, cycle_id)
            if request is not None and request.status == "collecting":
                return await self._publish_user_decision_request(run, cycle_id)
            tasks = self._teams.list_tasks(run.id, cycle_id)
            status = _terminal_status(tasks)
            if status in {"blocked", "failed"}:
                error = (
                    "Required task blocked"
                    if status == "blocked"
                    else "Required task failed"
                )
                run = self._teams.set_run_status(
                    run.id,
                    status,
                    error_message=error,
                )
                if cycle_id is not None:
                    self._teams.set_cycle_status(
                        cycle_id,
                        status,
                        error_message=error,
                    )
                self._teams.set_agent_status(leader.id, "failed")
                self._package_results(run, leader, cycle_id)
                await self._publish(
                    {
                        "type": f"team.run.{status}",
                        "team_run_id": run.id,
                        "error": error,
                    }
                )
                return run
            run = self._teams.set_run_status(run.id, "summarizing")
            await self._publish({"type": "team.run.summarizing", "team_run_id": run.id})
            summary = await self._leader_synthesis(run, leader, tasks, cycle_id)
            if isinstance(summary, UserDecisionResolution):
                self._teams.defer_run_for_user_decision(
                    run.id,
                    summary.decision,
                    stage="synthesis",
                    cycle_id=cycle_id,
                )
                return await self._publish_user_decision_request(run, cycle_id)
            if any(
                task.status == "pending"
                for task in self._teams.list_tasks(run.id, cycle_id)
            ):
                run = self._teams.set_run_status(run.id, "running")
                await self._publish({"type": "team.run.executing", "team_run_id": run.id})
                continue
            run = self._teams.set_run_status(run.id, status, summary=summary)
            if cycle_id is not None:
                self._teams.set_cycle_status(cycle_id, status, summary=summary)
            self._teams.set_agent_status(leader.id, "completed")
            self._package_results(run, leader, cycle_id)
            await self._publish({"type": "team.run.completed", "team_run_id": run.id})
            return run

    def _package_results(
        self,
        run: TeamRun,
        leader: TeamAgent,
        cycle_id: str | None,
    ) -> None:
        if self._result_packager is None:
            return
        try:
            self._result_packager.build(run, cycle_id)
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            self._teams.append_message(
                run.id,
                leader.id,
                None,
                "plan_note",
                f"Result package generation failed: {error}",
                {"result_package_error": True},
                cycle_id=cycle_id,
            )

    async def resume(self, team_run_id: str, cycle_id: str | None = None) -> TeamRun:
        run = self._teams.get_team_run(team_run_id)
        self._validate_cycle(run, cycle_id)
        if not self._teams.list_tasks(run.id, cycle_id):
            return await self.start(team_run_id, cycle_id)
        leader: TeamAgent | None = None
        try:
            if cycle_id is not None:
                self._activate_cycle(cycle_id)
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "running")
            leader = self._teams.set_agent_status(leader.id, "running")
            await self._publish({"type": "team.run.reopened", "team_run_id": run.id})
            workers = sorted(
                _find_workers(self._teams.list_agents(run.id)),
                key=lambda agent: agent.status != "pending",
            )
            if not workers:
                error = "resume has no worker agents"
                run = self._teams.set_run_status(run.id, "failed", error_message=error)
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
                self._teams.set_agent_status(leader.id, "failed")
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run
            return await self._execute_and_synthesize(run, leader, workers, cycle_id)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._teams.set_run_status(run.id, "failed", error_message=error)
            if cycle_id is not None:
                self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
            if leader is not None:
                self._teams.set_agent_status(leader.id, "failed")
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return run

    async def add_work(
        self, team_run_id: str, instruction: str, cycle_id: str | None = None
    ) -> list[TeamTask]:
        run = self._teams.get_team_run(team_run_id)
        self._validate_cycle(run, cycle_id)
        leader = _find_leader(self._teams.list_agents(run.id))
        leader_agent = self._teams.get_agent(leader.id)
        members = _find_workers(self._teams.list_agents(run.id))
        member_ids = {member.id for member in members}
        model = self._model(leader_agent, cycle_id)
        existing = (
            ", ".join(
                task.title for task in self._teams.list_tasks(run.id, cycle_id)
            )
            or "(none)"
        )
        goal_context = self._goal_context(run, cycle_id)
        prompt = _space_block(
            run,
            self._space_policy(run, cycle_id),
            cycle_id,
        ) + self._archive_block(
            f"{goal_context}\n{instruction}",
            persona_id=leader_agent.persona_id,
            allow_request=False,
        ) + ADD_WORK_PROMPT.format(
            goal=goal_context,
            existing_titles=existing,
            instruction=instruction,
            team_roster_json=_assignment_roster_json(members),
        )
        response = await model.complete([{"role": "user", "content": prompt}])
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        try:
            specs = _parse_task_plan(response.content)
        except ValueError:
            retry = await model.complete(
                [{"role": "user", "content": prompt + "\nReturn ONLY a JSON array. No prose, no code fences."}]
            )
            if retry.upstream_session_id:
                self._teams.set_agent_session(leader_agent.id, retry.upstream_session_id)
            specs = _parse_task_plan(retry.content)
        created: list[TeamTask] = []
        for spec in specs:
            owner_agent_id = spec.get("owner_agent_id")
            task = self._teams.create_task(
                run.id,
                spec["title"],
                spec["description"],
                owner_agent_id=owner_agent_id if owner_agent_id in member_ids else None,
                cycle_id=cycle_id,
                required=spec["required"],
                acceptance=spec["acceptance"],
            )
            created.append(task)
            await self._publish({"type": "team.task.created", "team_run_id": run.id, "task_id": task.id})
        self._teams.append_message(
            run.id,
            leader.id,
            None,
            "plan_note",
            f"Added {len(created)} task(s) from user request.",
            {},
            cycle_id=cycle_id,
        )
        return created

    async def _leader_synthesis(
        self,
        run: TeamRun,
        leader: TeamAgent,
        tasks: list[TeamTask],
        cycle_id: str | None = None,
    ) -> str | UserDecisionResolution:
        results = "\n\n".join(
            f"[{task.status}] {task.title}\n{task.result or task.error_message or ''}"
            for task in tasks
        )
        leader_agent = self._teams.get_agent(leader.id)
        model = self._model(leader_agent, cycle_id)
        goal_context = self._goal_context(run, cycle_id)
        prompt = _space_block(
            run,
            self._space_policy(run, cycle_id),
            cycle_id,
        ) + _rules_block(
            self._rules_snapshot(run, cycle_id), include_persona_baseline=False
        ) + self._archive_block(
            f"{goal_context}\n{results}",
            persona_id=leader_agent.persona_id,
            allow_request=True,
        ) + SYNTHESIS_PROMPT.format(
            goal=goal_context, results=results
        )
        decision_context = "\n\n".join(
            context
            for context in (
                self._teams.decision_context_for_run(
                    run.id, stage="planning", cycle_id=cycle_id
                ),
                self._teams.decision_context_for_run(
                    run.id, stage="synthesis", cycle_id=cycle_id
                ),
            )
            if context
        )
        if decision_context:
            prompt += (
                "\n\nResolved user decisions for final synthesis:\n"
                f"{decision_context}\nDo not ask these resolved questions again."
            )
        response = await model.complete(
            [{"role": "user", "content": prompt}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        resolution = _parse_mediation_resolution(response.content)
        if resolution["kind"] == "ask_user":
            return UserDecisionResolution(resolution)
        content = self._finalize_persona_content(
            response.content,
            persona_id=leader_agent.persona_id,
            team_run_id=run.id,
        )
        self._teams.append_message(
            run.id,
            leader.id,
            None,
            "synthesis",
            content,
            {},
            cycle_id=cycle_id,
        )
        return content

    async def _publish_user_decision_request(
        self,
        run: TeamRun,
        cycle_id: str | None,
    ) -> TeamRun:
        request = self._teams.publish_decision_request(run.id, cycle_id)
        if cycle_id is not None:
            self._teams.set_cycle_status(cycle_id, "waiting_for_user")
        run = self._teams.get_team_run(run.id)
        await self._publish(
            {
                "type": "team.run.input_requested",
                "team_run_id": run.id,
                "decision_request_id": request.id,
                "question_count": len(request.items),
            }
        )
        return run

    def _rules_snapshot(
        self, run: TeamRun, cycle_id: str | None
    ) -> dict | None:
        if cycle_id is not None:
            cycle = self._teams.get_cycle(cycle_id)
            if cycle.rules_snapshot is not None:
                return cycle.rules_snapshot
        return run.rules_snapshot

    def _settle_canceled(self, run: TeamRun, cycle_id: str | None = None) -> None:
        for task in self._teams.list_tasks(run.id, cycle_id):
            if task.status == "in_progress":
                if task.owner_agent_id:
                    self._teams.finish_task(task.id, task.owner_agent_id, "canceled")
                else:
                    self._teams.set_task_status(task.id, "canceled")
        for agent in self._teams.list_agents(run.id):
            if agent.status == "running":
                self._teams.set_agent_status(agent.id, "canceled")
        self._teams.set_run_status(run.id, "canceled")
        if cycle_id is not None:
            self._teams.set_cycle_status(cycle_id, "canceled")

    def _validate_cycle(self, run: TeamRun, cycle_id: str | None) -> None:
        if run.lifecycle_mode == "continuous" and cycle_id is None:
            raise ValueError("Continuous team runs require a cycle")
        if cycle_id is not None:
            cycle = self._teams.get_cycle(cycle_id)
            if cycle.team_run_id != run.id:
                raise ValueError("Cycle belongs to a different team run")

    def _activate_cycle(self, cycle_id: str) -> None:
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.status == "queued":
            self._teams.reset_agent_reinvocations(cycle.team_run_id)
        self._teams.set_cycle_status(cycle_id, "running")

    async def _publish(self, event: dict[str, object]) -> None:
        if self._event_bus is not None:
            enriched = dict(event)
            team_run_id = event.get("team_run_id")
            if isinstance(team_run_id, str) and str(event.get("type", "")).startswith(
                "team.run."
            ):
                try:
                    enriched["run"] = _run_delta(self._teams.get_team_run(team_run_id))
                except KeyError:
                    pass
            task_id = event.get("task_id")
            if isinstance(task_id, str):
                try:
                    enriched["task"] = _task_delta(self._teams.get_task(task_id))
                except KeyError:
                    pass
            agent_id = event.get("agent_id")
            if isinstance(agent_id, str):
                try:
                    enriched["agent"] = _agent_delta(self._teams.get_agent(agent_id))
                except KeyError:
                    pass
            await self._event_bus.publish(enriched)


def _find_leader(agents: list[TeamAgent]) -> TeamAgent:
    for agent in agents:
        if agent.role == "leader":
            return agent
    raise ValueError("Team run has no leader agent")


def _find_workers(agents: list[TeamAgent]) -> list[TeamAgent]:
    return [agent for agent in agents if agent.role != "leader"]


def _assignment_roster_json(members: list[TeamAgent]) -> str:
    return json.dumps(
        [
            {
                "owner_agent_id": member.id,
                "name": member.persona_snapshot.get("name"),
                "role": member.persona_snapshot.get("role"),
                "description": member.persona_snapshot.get("description"),
                "responsibilities": member.persona_snapshot.get("responsibilities", []),
            }
            for member in members
        ],
        ensure_ascii=False,
    )


def _terminal_status(tasks: list[TeamTask]) -> str:
    if not tasks:
        return "failed"
    required = [task for task in tasks if task.required]
    optional = [task for task in tasks if not task.required]
    if any(task.status == "failed" for task in required):
        return "failed"
    if any(task.status == "blocked" for task in required):
        return "blocked"
    if all(task.status == "completed" for task in required):
        if any(task.status in {"blocked", "failed"} for task in optional):
            return "completed_with_failures"
        return "completed"
    if not required and any(
        task.status in {"blocked", "failed"} for task in optional
    ):
        return "completed_with_failures"
    return "blocked"


def _run_delta(run: TeamRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "summary": run.summary,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def _task_delta(task: TeamTask) -> dict[str, object]:
    return {
        "id": task.id,
        "team_run_id": task.team_run_id,
        "cycle_id": task.cycle_id,
        "title": task.title,
        "description": task.description,
        "owner_agent_id": task.owner_agent_id,
        "status": task.status,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def _agent_delta(agent: TeamAgent) -> dict[str, object]:
    return {
        "id": agent.id,
        "team_run_id": agent.team_run_id,
        "status": agent.status,
        "current_task_id": agent.current_task_id,
        "started_at": agent.started_at,
        "finished_at": agent.finished_at,
        "updated_at": agent.updated_at,
    }


def _parse_task_plan(content: str) -> list[dict[str, object]]:
    stripped = normalize_json_envelope(content)
    if stripped.startswith("```"):
        raise ValueError("Planner response must not use code fences")
    raw = json.loads(stripped)
    if not isinstance(raw, list):
        raise ValueError("Planner response must be a JSON array")
    tasks: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Planner task must be an object")
        if set(item) != {
            "title",
            "description",
            "owner_agent_id",
            "required",
            "acceptance",
        }:
            raise ValueError("Planner task has missing or unknown fields")
        title = item.get("title")
        description = item.get("description")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(description, str)
            or not description.strip()
        ):
            raise ValueError("Planner task requires title and description")
        owner_agent_id = item.get("owner_agent_id")
        if owner_agent_id is not None and (
            not isinstance(owner_agent_id, str) or not owner_agent_id.strip()
        ):
            raise ValueError("Planner task owner must be a member ID or null")
        required = item.get("required")
        if not isinstance(required, bool):
            raise ValueError("Planner task required must be a boolean")
        acceptance = item.get("acceptance")
        if not isinstance(acceptance, dict) or set(acceptance) != {
            "required_outputs",
            "required_verifications",
        }:
            raise ValueError("Planner task requires exact acceptance fields")
        required_outputs = _string_list(
            acceptance.get("required_outputs"),
            "required_outputs",
        )
        required_verifications = _string_list(
            acceptance.get("required_verifications"),
            "required_verifications",
        )
        if len(set(required_outputs)) != len(required_outputs):
            raise ValueError("Planner task has duplicate required outputs")
        if len(set(required_verifications)) != len(required_verifications):
            raise ValueError("Planner task has duplicate required verifications")
        if any(not _safe_relative_output(path) for path in required_outputs):
            raise ValueError("Planner task output path must be relative and bounded")
        if not required_outputs and not required_verifications:
            raise ValueError("Planner task requires an output or verification")
        tasks.append(
            {
                "title": title.strip(),
                "description": description.strip(),
                "owner_agent_id": (
                    owner_agent_id.strip()
                    if isinstance(owner_agent_id, str)
                    else None
                ),
                "required": required,
                "acceptance": TaskAcceptance(
                    required_outputs=tuple(required_outputs),
                    required_verifications=tuple(required_verifications),
                ),
            }
        )
    return tasks


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"Planner task {field} must be non-empty strings")
    return [item.strip() for item in value]


def _safe_relative_output(value: str) -> bool:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        value not in {"", "."}
        and not posix.is_absolute()
        and not windows.is_absolute()
        and ".." not in posix.parts
        and ".." not in windows.parts
    )


def _parse_acceptance_review_resolution(content: str) -> AcceptanceReviewResolution:
    try:
        raw = json.loads(normalize_json_envelope(content))
        if not isinstance(raw, dict) or set(raw) != {"resolution"}:
            raise ValueError
        resolution = raw["resolution"]
        if not isinstance(resolution, dict):
            raise ValueError
        kind = resolution.get("kind")
        if kind == "retry_worker":
            if set(resolution) != {"kind", "instruction", "reason"}:
                raise ValueError
            reason = _acceptance_review_text(resolution.get("reason"))
            instruction = _acceptance_review_text(resolution.get("instruction"))
            return AcceptanceReviewResolution(
                kind="retry_worker",
                reason=reason,
                instruction=instruction,
            )
        if kind == "revise_acceptance":
            if set(resolution) != {"kind", "acceptance", "instruction", "reason"}:
                raise ValueError
            acceptance = _parse_revised_acceptance(resolution.get("acceptance"))
            return AcceptanceReviewResolution(
                kind="revise_acceptance",
                reason=_acceptance_review_text(resolution.get("reason")),
                instruction=_acceptance_review_text(resolution.get("instruction")),
                acceptance=acceptance,
            )
        if kind == "ask_user":
            if set(resolution) != {
                "kind",
                "topic",
                "question",
                "why_needed",
                "options",
                "recommended_option_id",
                "blocking_scope",
            }:
                raise ValueError
            _validate_acceptance_review_user_decision(resolution)
            decision = _parse_mediation_resolution(content)
            if decision.get("kind") != "ask_user":
                raise ValueError
            return AcceptanceReviewResolution(
                kind="ask_user",
                reason=_acceptance_review_text(decision["why_needed"]),
                decision=decision,
            )
        if kind == "fail":
            if set(resolution) != {"kind", "reason_code", "summary"}:
                raise ValueError
            return AcceptanceReviewResolution(
                kind="fail",
                reason=_acceptance_review_text(resolution.get("summary")),
                reason_code=_acceptance_review_text(resolution.get("reason_code")),
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    raise ValueError("Invalid acceptance review resolution")


def _validate_acceptance_review_user_decision(
    resolution: dict[str, object],
) -> None:
    for field in ("topic", "question", "why_needed"):
        _acceptance_review_text(resolution.get(field))

    options = resolution.get("options")
    if not isinstance(options, list):
        raise ValueError
    for option in options:
        if not isinstance(option, dict) or set(option) != {"id", "label", "impact"}:
            raise ValueError
        _acceptance_review_text(option.get("id"))
        _acceptance_review_text(option.get("label"))
        _acceptance_review_text(option.get("impact"))

    recommended = resolution.get("recommended_option_id")
    if recommended is not None:
        _acceptance_review_text(recommended)
    if resolution.get("blocking_scope") not in {"task", "run"}:
        raise ValueError


def _parse_revised_acceptance(value: object) -> TaskAcceptance:
    if not isinstance(value, dict) or set(value) != {
        "required_outputs",
        "required_verifications",
    }:
        raise ValueError
    acceptance = TaskAcceptance(
        required_outputs=tuple(_string_list(value.get("required_outputs"), "required_outputs")),
        required_verifications=tuple(
            _string_list(value.get("required_verifications"), "required_verifications")
        ),
    )
    _validate_task_acceptance(acceptance)
    if any(not _safe_relative_output(path) for path in acceptance.required_outputs):
        raise ValueError
    return acceptance


def _acceptance_review_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value.strip()


def _parse_mediation_resolution(content: str) -> dict[str, object]:
    stripped = content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1 and stripped.endswith("```"):
            stripped = stripped[first_newline + 1 : -3].strip()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError:
        return {"kind": "answer", "answer": content.strip()}
    if not isinstance(raw, dict) or not isinstance(raw.get("resolution"), dict):
        return {"kind": "answer", "answer": content.strip()}
    resolution = raw["resolution"]
    if resolution.get("kind") != "ask_user":
        answer = resolution.get("answer")
        return {
            "kind": "answer",
            "answer": answer.strip() if isinstance(answer, str) else content.strip(),
        }
    question = resolution.get("question")
    if not isinstance(question, str) or not question.strip():
        return {"kind": "answer", "answer": content.strip()}
    options: list[dict[str, str]] = []
    raw_options = resolution.get("options")
    if isinstance(raw_options, list):
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            option_id = option.get("id")
            label = option.get("label")
            if not isinstance(option_id, str) or not option_id.strip():
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            impact = option.get("impact")
            options.append(
                {
                    "id": option_id.strip(),
                    "label": label.strip(),
                    "impact": impact.strip() if isinstance(impact, str) else "",
                }
            )
    topic = resolution.get("topic")
    why_needed = resolution.get("why_needed")
    recommended = resolution.get("recommended_option_id")
    return {
        "kind": "ask_user",
        "topic": topic.strip() if isinstance(topic, str) else "",
        "question": question.strip(),
        "why_needed": why_needed.strip() if isinstance(why_needed, str) else "",
        "options": options,
        "recommended_option_id": recommended.strip() if isinstance(recommended, str) else None,
        "blocking_scope": "run" if resolution.get("blocking_scope") == "run" else "task",
    }


def _parse_needs_info(content: str) -> dict[str, str] | None:
    block = _last_json_block(content)
    if block is None:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    req = data.get("needs_info")
    if not isinstance(req, dict):
        return None
    question = req.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    topic = req.get("topic")
    return {"topic": topic if isinstance(topic, str) else "", "question": question.strip()}


def _last_json_block(content: str) -> str | None:
    fence = "```json"
    idx = content.rfind(fence)
    if idx != -1:
        rest = content[idx + len(fence):]
        end = rest.find("```")
        if end != -1:
            return rest[:end].strip()
    # 펜스가 없으면 마지막 중괄호 그룹 시도
    start = content.rfind("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        return content[start:end + 1].strip()
    return None
