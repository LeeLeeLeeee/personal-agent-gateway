import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Protocol

from personal_agent_gateway.archive import ArchiveService
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelClient, ModelResponse
from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.source_staging import StagedInputs
from personal_agent_gateway.team_acceptance import (
    AcceptanceResult,
    TeamAcceptanceService,
    is_recoverable_acceptance_failure,
    is_worker_declared_outcome,
    rejected_verification_names,
    terminal_rejected_status,
)
from personal_agent_gateway.team_coverage_report import extract_coverage_gaps
from personal_agent_gateway.team_artifact_publisher import (
    ArtifactPublicationError,
    TeamArtifactPublisher,
)
from personal_agent_gateway.team_outcomes import (
    TaskOutcome,
    TaskOutcomeError,
    parse_task_outcome,
)
from personal_agent_gateway.team_model_effects import (
    TeamModelEffectService,
    lead_decision_item_digest,
    team_model_effect_result_validators,
)
from personal_agent_gateway.team_model_invoker import (
    AmbiguousModelOperation,
    InvalidOperationResult,
    ProviderOperationUnavailable,
    TeamModelInvoker,
)
from personal_agent_gateway.team_lifecycle import (
    LifecycleIntegrityError,
    cycle_execution_disposition,
)
from personal_agent_gateway.team_model_operations import (
    OperationStage,
    OperationConflict,
    OperationSpec,
    TeamModelOperation,
    TeamModelOperationService,
    ValidatedOperationResult,
)
from personal_agent_gateway.team_provider_recovery import (
    ProviderOperationWaiting,
    TeamProviderRecovery,
)
from personal_agent_gateway.team_repair_stages import repair_stage_for
from personal_agent_gateway.team_results import (
    TeamRunResultPackager,
    WorkspaceSnapshot,
    workspace_changes,
    workspace_snapshot,
)
from personal_agent_gateway.team_output_contracts import (
    OutputContract,
    get_output_contract,
)
from personal_agent_gateway.team_structured_output import normalize_json_envelope
from personal_agent_gateway.team_task_inputs import TaskInputStager
from personal_agent_gateway.teams import (
    ACCEPTANCE_RECOVERY_CAP,
    TaskAcceptance,
    TeamAgent,
    TeamDecisionRequest,
    TeamMessage,
    TeamRun,
    TeamRunService,
    TeamTask,
    _task_acceptance_json,
    _validate_task_acceptance,
    parse_required_verifications,
)

PLANNING_PROMPT = """You are the leader agent for a personal-agent-gateway Team Run.
Goal: {goal}
Persona snapshot: {persona_snapshot_json}
Available team members: {team_roster_json}

Before creating tasks, identify any consequential choice that only the user can make.
First resolve ambiguity from the goal, frozen rules, and prior user decisions.
Return ONLY one of:
1. A JSON array of task objects. Each object must contain exactly:
   {{"plan_task_id":"stable-key", "title":"...", "description":"...", "owner_agent_id":"member-id or null",
   "required":true, "depends_on_task_ids":["stable-key"], "input_artifact_ids":["artifact-id"], "acceptance":{{"required_outputs":["relative/path"],
   "required_verifications":[{{"name":"verification-name","check":null}}]}}}}
   A verification may carry a check the server runs itself. Prefer one whenever a
   file can decide the question; use "check":null only for something no file can
   settle. Point "path" at a file this task produces; normally one of its own
   "required_outputs". Use exactly the fields shown; no others. Available checks,
   each with a workspace-relative "path":
   {{"type":"file_nonempty","path":"relative/path"}}
   {{"type":"file_contains","path":"relative/path","value":"substring"}}
   {{"type":"file_matches","path":"relative/path","pattern":"regex, at most 200 characters"}}
   {{"type":"json_parses","path":"relative/path"}}
   A check you supply decides the outcome; your own claim about it is ignored.
   Assign the member whose persona role and responsibilities best match the task.
   Use null only when no member is available. Do not assign by list order or
   previous completion status. Every task needs at least one required output or
   verification. input_artifact_ids must list only IDs from ALLOWED TASK INPUT
   ARTIFACTS below; use [] when the task needs none. plan_task_id is required
   and must be unique in this plan. depends_on_task_ids may reference only
   plan_task_id values in this response. A task that reads, revises, or
   verifies another task's required_outputs MUST list that task in its
   depends_on_task_ids; use [] only when the task truly has no prerequisite.
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
{{"resolution":{{"kind":"revise_acceptance","acceptance":{{"required_outputs":["relative/path"],"required_verifications":[{{"name":"verification-name","check":null}}]}},"instruction":"concrete resubmission instruction", "reason":"why the contract is wrong"}}}}
{{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why the Team cannot infer the answer","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"task"}}}}
{{"resolution":{{"kind":"fail","reason_code":"stable-code","summary":"why recovery cannot continue"}}}}
A revised verification may carry a check the server runs itself. Prefer one whenever a
file can decide the question; use "check":null only for something no file can settle.
Point "path" at a file this task produces; normally one of its own "required_outputs".
Use exactly the fields shown; no others. Available checks, each with a
workspace-relative "path": {{"type":"file_nonempty","path":"relative/path"}},
{{"type":"file_contains","path":"relative/path","value":"substring"}},
{{"type":"file_matches","path":"relative/path","pattern":"regex, at most 200 characters"}},
{{"type":"json_parses","path":"relative/path"}}.
A check you supply decides the outcome; your own claim about it is ignored."""

SYNTHESIS_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
Goal: {goal}
Task results:
{results}

Before finalizing, identify any consequential choice that only the user can make to
produce an accurate final response. First use the goal, frozen rules, prior user
decisions, and task results.
Return either:
1. A concise plain-text summary of what was accomplished, including any failures.
If any obligation in the accepted specification documents is owned by no task,
append a fenced block listing them, and nothing else after it:
```coverage-gaps
[{{"obligation": "short name", "document": "path §section", "note": "why it is unowned"}}]
```
Send an empty list if every obligation is owned. Omit the block entirely if you
did not check.
2. ONLY {{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why the final response cannot be completed accurately","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"run"}}}}
At this stage, ask only about final interpretation or presentation that does not
require additional worker execution."""

SYNTHESIS_CONTRACT_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
Goal: {goal}
Task results:
{results}

Before finalizing, identify any consequential choice that only the user can make to
produce an accurate final response. First use the goal, frozen rules, prior user
decisions, and task results.
Return either:
1. A short plain-text summary of what was accomplished, including any failures,
   followed by the final response in exactly the form the OUTPUT CONTRACT below
   requires. The contract governs this response, not a file you wrote during the
   run.
If any obligation in the accepted specification documents is owned by no task,
append a fenced block listing them, and nothing else after it:
```coverage-gaps
[{{"obligation": "short name", "document": "path §section", "note": "why it is unowned"}}]
```
Send an empty list if every obligation is owned. Omit the block entirely if you
did not check.
2. ONLY {{"resolution":{{"kind":"ask_user","topic":"short topic","question":"one concrete question","why_needed":"why the final response cannot be completed accurately","options":[{{"id":"stable-id","label":"label","impact":"tradeoff"}}],"recommended_option_id":"stable-id or null","blocking_scope":"run"}}}}
At this stage, ask only about final interpretation or presentation that does not
require additional worker execution.

OUTPUT CONTRACT
{contract}"""

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
{{"plan_task_id":"stable-key", "title":"...", "description":"...", "owner_agent_id":"member-id or null",
"required":true, "depends_on_task_ids":["stable-key"], "input_artifact_ids":[],
"acceptance":{{"required_outputs":["relative/path"],
"required_verifications":[{{"name":"verification-name","check":null}}]}}}}
A verification may carry a check the server runs itself. Prefer one whenever a
file can decide the question; use "check":null only for something no file can
settle. Point "path" at a file this task produces; normally one of its own
"required_outputs". Use exactly the fields shown; no others. Available checks,
each with a workspace-relative "path":
{{"type":"file_nonempty","path":"relative/path"}}
{{"type":"file_contains","path":"relative/path","value":"substring"}}
{{"type":"file_matches","path":"relative/path","pattern":"regex, at most 200 characters"}}
{{"type":"json_parses","path":"relative/path"}}
A check you supply decides the outcome; your own claim about it is ignored.
plan_task_id is required and must be unique in this response.
depends_on_task_ids may reference only plan_task_id values in this response.
A task that reads, revises, or verifies another task's required_outputs MUST list
that task in depends_on_task_ids; use [] only when it has no prerequisite.
Use [] for input_artifact_ids because add-work planning has no input artifact catalog.
Run context:
{goal}
Existing tasks: {existing_titles}
Current cycle objective: {instruction}
Available team members: {team_roster_json}

Assign every task to the member whose persona role and responsibilities best match it.
Return "owner_agent_id" using the exact ID from the available team members list.
Do not assign by list order or previous completion status."""

AGENT_REINVOCATION_CAP = 3
_SESSION_UNSET = object()
_PLANNING_REPAIR_INSTRUCTION = (
    "The previous response was invalid. If an owner_agent_id was not one of the "
    "fixed team member IDs, replace it with an exact ID from Available team "
    "members or null. Return ONLY a JSON array. No prose, no code fences."
)
_WORKER_REPAIR_INSTRUCTION = (
    "Return ONLY the required TaskOutcome JSON object or "
    "the exact needs_info JSON block."
)


@dataclass(frozen=True)
class UserDecisionResolution:
    decision: dict[str, object]


class UnparsableLeadOutput(RuntimeError):
    """A leader stage could not be parsed twice, so the run is paused, not failed.

    Raised from the escalation so the acceptance flow stops instead of handing a
    failed operation to a caller that expects a usable review. resume() treats it
    like the other pause signals and returns the waiting run.
    """

    def __init__(self, stage: str, operation_id: str) -> None:
        super().__init__(f"{stage} output could not be parsed twice")
        self.stage = stage
        self.operation_id = operation_id


@dataclass(frozen=True)
class OpenOperationRecovery:
    operation: TeamModelOperation
    result: object


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
        *,
        operations: TeamModelOperationService | None = None,
        model_invoker: TeamModelInvoker | None = None,
        model_effects: TeamModelEffectService | None = None,
        provider_recovery: TeamProviderRecovery | None = None,
    ) -> None:
        self._teams = teams
        self._model_factory = model_factory
        self._event_bus = event_bus
        self._archive_service = archive_service
        self._result_packager = result_packager
        self._acceptance_service = acceptance_service or TeamAcceptanceService()
        self._artifact_publisher = artifact_publisher
        self._staged_inputs_resolver = staged_inputs_resolver
        self._operations = operations or TeamModelOperationService(
            teams._db,
            result_validators=team_model_effect_result_validators(),
        )
        self._model_invoker = model_invoker or TeamModelInvoker(self._operations)
        self._model_effects = model_effects or TeamModelEffectService(
            teams._db,
            teams,
            self._operations,
        )
        self._provider_recovery = provider_recovery
        self._task_input_stager = TaskInputStager(teams._db, teams)

    def _model(
        self,
        agent: TeamAgent,
        cycle_id: str | None,
        *,
        upstream_session_id: str | None | object = _SESSION_UNSET,
    ) -> ModelClient:
        if upstream_session_id is not _SESSION_UNSET:
            agent = replace(
                agent,
                upstream_session_id=(
                    upstream_session_id
                    if isinstance(upstream_session_id, str)
                    else None
                ),
            )
        if cycle_id is None:
            return self._model_factory(agent)
        return self._model_factory(agent, cycle_id)

    async def _invoke_with_repair(
        self,
        spec: OperationSpec,
        agent: TeamAgent,
        messages: list[dict[str, object]],
        parser: Callable[[ModelResponse], ValidatedOperationResult],
        *,
        repair_messages: list[dict[str, object]]
        | Callable[[str | None], list[dict[str, object]]]
        | None = None,
        repair_parser: Callable[[ModelResponse], ValidatedOperationResult]
        | None = None,
        repair_ordinal: int | None = None,
        on_exhausted: Callable[
            [TeamModelOperation], Awaitable[None]
        ] | None = None,
    ) -> TeamModelOperation:
        """Invoke a stage, and ask once more when the response will not parse.

        Repair used to be wired per stage, which meant a stage could inherit
        none -- acceptance_lead had none, so one unparseable review ended the
        run. Routing every stage through here makes that omission impossible.
        """
        try:
            return await self._invoke_operation(spec, agent, messages, parser)
        except InvalidOperationResult as exc:
            failed = self._operations.get(exc.operation_id)

        return await self._repair_operation(
            spec.team_run_id,
            spec.cycle_id,
            agent,
            spec.stage,
            failed,
            parser,
            repair_messages=repair_messages,
            repair_parser=repair_parser,
            repair_ordinal=repair_ordinal,
            on_exhausted=on_exhausted,
            task_id=spec.task_id,
        )

    async def _repair_operation(
        self,
        team_run_id: str,
        cycle_id: str,
        agent: TeamAgent,
        stage: OperationStage,
        failed: TeamModelOperation,
        parser: Callable[[ModelResponse], ValidatedOperationResult],
        *,
        repair_messages: list[dict[str, object]]
        | Callable[[str | None], list[dict[str, object]]]
        | None = None,
        repair_parser: Callable[[ModelResponse], ValidatedOperationResult]
        | None = None,
        repair_ordinal: int | None = None,
        on_exhausted: Callable[
            [TeamModelOperation], Awaitable[None]
        ] | None = None,
        task_id: str | None = None,
    ) -> TeamModelOperation:
        """Ask once more for a response that would not parse.

        Split out from _invoke_with_repair because the ledger recovery path
        arrives here already holding the failed operation -- it resumed into a
        stage rather than invoking one, so it has nothing to retry through the
        seam's own invoke.
        """
        repair_stage = repair_stage_for(stage)
        # worker_execution repairs in place at the next ordinal; every other
        # stage repairs under its own name at the ordinal that failed. Callers
        # override only where the ordinal carries meaning the stage name does
        # not -- cycle_planning and cycle_add_work share cycle_planning_repair,
        # so recovery tells them apart by ordinal 1 versus 2.
        if repair_ordinal is None:
            repair_ordinal = (
                failed.stage_ordinal + 1
                if repair_stage == stage
                else failed.stage_ordinal
            )
        # A prompt that quotes the reason code can only be built once the
        # failure exists, so a caller may pass the builder instead of a prompt.
        if callable(repair_messages):
            prompt = repair_messages(failed.reason_code)
        else:
            prompt = repair_messages or _repair_messages(failed.reason_code)
        repair_spec = _operation_spec(
            self._teams.get_team_run(team_run_id),
            cycle_id,
            agent,
            repair_stage,
            repair_ordinal,
            prompt,
            task_id=task_id,
            upstream_session_id=failed.upstream_session_id,
        )
        try:
            return await self._invoke_operation(
                repair_spec, agent, prompt, repair_parser or parser
            )
        except InvalidOperationResult as exc:
            if on_exhausted is None:
                raise
            exhausted = self._operations.get(exc.operation_id)
            await on_exhausted(exhausted)
            return exhausted

    async def _escalate_unparsable_lead_output(
        self,
        run: TeamRun,
        cycle_id: str,
        task: TeamTask | None,
        stage: OperationStage,
        agent: TeamAgent,
        failed: TeamModelOperation,
    ) -> None:
        """Pause rather than fail. A leader stage failing costs the whole run,
        and the work waiting for review is still good.

        Nothing is reserved for the retry. Two earlier designs tried to leave a
        prepared operation for resume to pick up: reusing the failed key returns
        the failed row, and using the next ordinal collides with the repair key
        of the next acceptance attempt. Resume does not need one -- the task is
        still in progress with its outcome persisted, so the acceptance flow
        re-enters at the next attempt on its own.
        """
        if task is not None:
            self._teams.consume_acceptance_attempt(task.id)
        where = f" on task '{task.title}'" if task is not None else ""
        self._teams.raise_system_decision(
            run.id,
            cycle_id,
            topic=f"{stage} output could not be parsed",
            question=(
                f"The leader's {stage} response failed to parse twice{where}. "
                "The recorded failure shape is on the operation. Answer to retry "
                "it; use Stop to end the run instead."
            ),
        )
        await self._publish(
            {
                "type": "team.run.input_requested",
                "team_run_id": run.id,
                "reason": "unparsable_lead_output",
                "stage": stage,
            }
        )
        raise UnparsableLeadOutput(stage, failed.id)

    async def _invoke_operation(
        self,
        spec: OperationSpec,
        agent: TeamAgent,
        messages: list[dict[str, object]],
        parser: Callable[[ModelResponse], ValidatedOperationResult],
    ) -> TeamModelOperation:
        open_operation = self._operations.get_open_for_cycle(spec.cycle_id)
        if (
            open_operation is not None
            and open_operation.operation_key != spec.operation_key
        ):
            raise OperationConflict("Cycle already has an open model operation")
        operation = self._operations.reserve(spec)
        if (
            operation.status == "prepared"
            and operation.stage
            in {"worker_execution", "mediation_worker", "acceptance_worker"}
            and operation.task_id is not None
        ):
            current_run = self._teams.get_team_run(operation.team_run_id)
            self._teams.record_operation_workspace_baseline(
                operation.id,
                team_run_id=operation.team_run_id,
                cycle_id=operation.cycle_id,
                task_id=operation.task_id,
                agent_id=operation.agent_id,
                snapshot=workspace_snapshot(
                    Path(
                        current_run.working_root
                        or current_run.workspace_root
                    )
                ),
            )
        if operation.status in {"completed", "applied"}:
            return operation
        if operation.status in {"waiting_for_provider", "ambiguous", "invoking"}:
            raise OperationConflict(
                f"Operation status {operation.status} cannot be invoked"
            )
        client = self._model(
            agent,
            spec.cycle_id,
            upstream_session_id=operation.upstream_session_id,
        )
        try:
            return await self._model_invoker.invoke(
                operation,
                client,
                messages,
                parser,
            )
        except ProviderOperationUnavailable as exc:
            if self._provider_recovery is None:
                raise
            self._provider_recovery.wait_for_operation(
                exc.operation_id,
                reason_code=exc.reason_code,
            )
            raise ProviderOperationWaiting(exc.operation_id) from exc
        except AmbiguousModelOperation as exc:
            if self._provider_recovery is None:
                raise OperationConflict(exc.reason_code) from exc
            await self._provider_recovery.interrupt_ambiguous_operation(
                exc.operation_id,
                consumer_run_id=exc.consumer_run_id,
                upstream_session_id=None,
            )
            raise

    async def _recover_open_operation(
        self,
        run: TeamRun,
        leader: TeamAgent,
        cycle_id: str,
        *,
        planning_stage: Literal["cycle_planning", "cycle_add_work"] | None = None,
        planning_messages: list[dict[str, object]] | None = None,
        planning_parser: Callable[
            [ModelResponse],
            ValidatedOperationResult,
        ]
        | None = None,
        synthesis_messages: list[dict[str, object]] | None = None,
        synthesis_parser: Callable[
            [ModelResponse],
            ValidatedOperationResult,
        ]
        | None = None,
        synthesis_contract: OutputContract | None = None,
    ) -> OpenOperationRecovery | None:
        operation = self._operations.get_open_for_cycle(cycle_id)
        if operation is None:
            return None
        if operation.status in {
            "invoking",
            "waiting_for_provider",
            "ambiguous",
        }:
            raise OperationConflict(
                f"Operation status {operation.status} cannot be invoked"
            )

        if operation.stage in {
            "cycle_planning",
            "cycle_planning_repair",
            "cycle_add_work",
        }:
            if operation.status == "completed":
                return OpenOperationRecovery(
                    operation,
                    self._model_effects.apply_plan(operation.id),
                )
            if planning_stage is None or planning_messages is None:
                if (
                    operation.stage == "cycle_add_work"
                    or (
                        operation.stage == "cycle_planning_repair"
                        and operation.stage_ordinal == 2
                    )
                ):
                    instruction = (
                        self._teams.get_cycle_effective_instruction(cycle_id)
                        or self._teams.get_cycle_objective(cycle_id)
                    )
                    if instruction is None:
                        raise OperationConflict(
                            "Prepared add-work operation has no persisted instruction"
                        )
                    planning_stage = "cycle_add_work"
                    planning_messages = self._add_work_messages(
                        run,
                        leader,
                        _find_workers(self._teams.list_agents(run.id)),
                        instruction,
                        cycle_id,
                    )
                else:
                    raise OperationConflict(
                        "Open planning operation requires its source request"
                    )
            expected_repair_ordinal = (
                1 if planning_stage == "cycle_planning" else 2
            )
            if operation.stage == planning_stage:
                messages = planning_messages
            elif (
                operation.stage == "cycle_planning_repair"
                and operation.stage_ordinal == expected_repair_ordinal
            ):
                messages = _planning_repair_messages(planning_messages)
            else:
                raise OperationConflict(
                    "Open planning operation does not match this request"
                )
            recovered = operation
            if operation.status == "prepared":
                recovered = await self._invoke_existing_operation(
                    operation,
                    leader,
                    messages,
                    planning_parser or _validated_task_plan,
                )
            return OpenOperationRecovery(
                recovered,
                self._model_effects.apply_plan(recovered.id),
            )

        if operation.stage in {"mediation_lead", "mediation_lead_repair"}:
            if operation.task_id is None:
                raise OperationConflict("Mediation operation has no task")
            task = self._teams.get_task(operation.task_id)
            worker = self._teams.get_agent(task.owner_agent_id)
            query_operation = next(
                (
                    candidate
                    for candidate in reversed(
                        self._operations.list_for_cycle(cycle_id)
                    )
                    if candidate.task_id == task.id
                    and candidate.status == "applied"
                    and candidate.result_kind == "worker_query"
                ),
                None,
            )
            if query_operation is None:
                raise OperationConflict(
                    "Mediation operation has no applied Worker query"
                )
            query = _operation_worker_query(query_operation)
            messages = self._mediation_messages(
                run,
                leader,
                task,
                query["question"],
            )
            recovered = operation
            if operation.status == "prepared":
                recovered = await self._invoke_existing_operation(
                    operation,
                    leader,
                    messages,
                    _validated_mediation_result,
                )
            resolution = _operation_mediation_resolution(recovered)
            effect = self._model_effects.apply_mediation_lead(
                recovered.id,
                resolution,
            )
            result = await self._continue_cycle_mediation_effect(
                run,
                leader,
                worker,
                task,
                resolution,
                effect,
            )
            return OpenOperationRecovery(recovered, result)

        if operation.stage in {"acceptance_lead", "acceptance_lead_repair"}:
            if operation.task_id is None:
                raise OperationConflict("Acceptance operation has no task")
            task = self._teams.get_task(operation.task_id)
            worker = self._teams.get_agent(task.owner_agent_id)
            messages = self._acceptance_review_messages(
                run,
                leader,
                worker,
                task,
            )
            recovered = operation
            if operation.status == "prepared":
                recovered = await self._invoke_existing_operation(
                    operation,
                    leader,
                    messages,
                    _validated_acceptance_review,
                )
            resolution = _operation_acceptance_resolution(recovered)
            effect = self._model_effects.apply_acceptance_lead(
                recovered.id,
                resolution,
            )
            result = await self._continue_cycle_acceptance_effect(
                run,
                leader,
                worker,
                task,
                resolution,
                effect,
            )
            return OpenOperationRecovery(recovered, result)

        if operation.stage in {
            "worker_execution",
            "mediation_worker",
            "mediation_worker_repair",
            "acceptance_worker",
            "acceptance_worker_repair",
        }:
            if operation.task_id is None:
                raise OperationConflict("Worker operation has no task")
            task = self._teams.get_task(operation.task_id)
            worker = self._teams.get_agent(operation.agent_id)
            if operation.stage == "worker_execution":
                base_messages: list[dict[str, object]] = [
                    {
                        "role": "user",
                        "content": self._worker_prompt(run, worker, task),
                    }
                ]
                if operation.stage_ordinal == 0:
                    messages = base_messages
                elif operation.stage_ordinal == 1:
                    messages = _worker_repair_messages(base_messages)
                else:
                    raise OperationConflict("Worker repair ordinal is invalid")
                def parser(response):
                    return self._validated_worker_result(
                        response,
                        worker,
                        run,
                    )
            elif operation.stage == "mediation_worker":
                lead_operation = self._operations.get_by_key(
                    _operation_key(
                        cycle_id,
                        "mediation_lead",
                        operation.stage_ordinal,
                        task_id=task.id,
                    )
                )
                if (
                    lead_operation is None
                    or lead_operation.status != "applied"
                ):
                    cycle = self._teams.get_cycle(cycle_id)
                    if (
                        cycle.rounds_used < cycle.rounds_budget
                        and worker.reinvocations
                        < AGENT_REINVOCATION_CAP
                    ):
                        raise OperationConflict(
                            "Mediation Worker has no applied Lead operation"
                        )
                    messages = _mediation_budget_messages()

                    def parser(response):
                        return _validated_task_outcome_result(
                            response,
                            worker,
                            run,
                            self._finalize_persona_content,
                        )
                else:
                    messages = _mediation_worker_messages(
                        _operation_mediation_resolution(lead_operation)
                    )

                    def parser(response):
                        return self._validated_worker_result(
                            response,
                            worker,
                            run,
                        )
            elif operation.stage == "acceptance_worker":
                lead_operation = self._operations.get_by_key(
                    _operation_key(
                        cycle_id,
                        "acceptance_lead",
                        operation.stage_ordinal,
                        task_id=task.id,
                    )
                )
                if (
                    lead_operation is None
                    or lead_operation.status != "applied"
                ):
                    raise OperationConflict(
                        "Acceptance Worker has no applied Lead operation"
                    )
                resolution = _operation_acceptance_resolution(
                    lead_operation
                )
                messages = _acceptance_worker_messages(task, resolution)
                def parser(response):
                    return _validated_task_outcome_result(
                        response,
                        worker,
                        run,
                        self._finalize_persona_content,
                    )
            else:
                failed_operation = self._operations.get_by_key(
                    _operation_key(
                        cycle_id,
                        "acceptance_worker",
                        operation.stage_ordinal,
                        task_id=task.id,
                    )
                )
                if (
                    failed_operation is None
                    or failed_operation.status != "failed"
                    or failed_operation.reason_code
                    != "invalid_structured_output"
                ):
                    raise OperationConflict(
                        "Acceptance Worker repair has no failed source operation"
                    )
                messages = _acceptance_worker_repair_messages(
                    failed_operation.reason_code
                )

                def parser(response):
                    return _validated_task_outcome_result(
                        response,
                        worker,
                        run,
                        self._finalize_persona_content,
                    )

            if operation.stage == "acceptance_worker_repair":
                before = self._teams.get_operation_workspace_baseline(
                    failed_operation.id,
                    team_run_id=operation.team_run_id,
                    cycle_id=operation.cycle_id,
                    task_id=task.id,
                    agent_id=worker.id,
                )
            else:
                before = (
                    workspace_snapshot(
                        Path(run.working_root or run.workspace_root)
                    )
                    if operation.status == "prepared"
                    else None
                )
            recovered = operation
            if operation.status == "prepared":
                recovered = await self._invoke_existing_operation(
                    operation,
                    worker,
                    messages,
                    parser,
                )
            result = await self._apply_cycle_worker_operation(
                run,
                leader,
                worker,
                task,
                recovered,
                before=before,
            )
            return OpenOperationRecovery(recovered, result)

        if operation.stage in {"cycle_synthesis", "cycle_synthesis_repair"}:
            if synthesis_messages is None or synthesis_parser is None:
                return OpenOperationRecovery(operation, None)
            recovered = operation
            if operation.status == "prepared":
                if operation.stage == "cycle_synthesis_repair":
                    repair_messages = _synthesis_repair_prompt(
                        synthesis_messages, synthesis_contract
                    )
                    recovered = await self._invoke_existing_operation(
                        operation,
                        leader,
                        repair_messages,
                        lambda response: self._validated_synthesis_result(
                            response,
                            leader,
                            run,
                            synthesis_contract,
                            strict=False,
                        ),
                    )
                else:
                    recovered = await self._invoke_existing_operation(
                        operation,
                        leader,
                        synthesis_messages,
                        synthesis_parser,
                    )
            return OpenOperationRecovery(
                recovered,
                self._apply_cycle_synthesis_operation(recovered),
            )

        raise OperationConflict(
            f"Open operation stage {operation.stage} is not recoverable here"
        )

    async def _invoke_existing_operation(
        self,
        operation: TeamModelOperation,
        agent: TeamAgent,
        messages: list[dict[str, object]],
        parser: Callable[[ModelResponse], ValidatedOperationResult],
    ) -> TeamModelOperation:
        spec = OperationSpec(
            operation_key=operation.operation_key,
            team_run_id=operation.team_run_id,
            cycle_id=operation.cycle_id,
            task_id=operation.task_id,
            agent_id=operation.agent_id,
            provider=operation.provider,
            stage=operation.stage,
            stage_ordinal=operation.stage_ordinal,
            request_digest=_operation_request_digest(
                operation.stage,
                operation.stage_ordinal,
                operation.agent_id,
                messages,
            ),
            upstream_session_id=operation.upstream_session_id,
        )
        return await self._invoke_operation(
            spec,
            agent,
            messages,
            parser,
        )

    async def _repair_synthesis_operation(
        self,
        run: TeamRun,
        cycle_id: str,
        leader_agent: TeamAgent,
        contract: OutputContract | None,
        messages: list[dict[str, object]],
        failed: TeamModelOperation,
    ) -> TeamModelOperation:
        """Repair a synthesis the ledger recovery path resumed into.

        The retry parses with strict=False: a summary that still misses the
        contract is worth keeping, because losing the cycle's synthesis costs
        more than an unvalidated payload does. A synthesis without a contract
        used to re-raise here and end the run; it now gets the shape-agnostic
        prompt, which is all there is to say when nothing declares a shape.
        """
        return await self._repair_operation(
            run.id,
            cycle_id,
            leader_agent,
            "cycle_synthesis",
            failed,
            lambda response: self._validated_synthesis_result(
                response, leader_agent, run, contract, strict=False
            ),
            repair_messages=_synthesis_repair_prompt(messages, contract),
        )

    def _apply_cycle_synthesis_operation(
        self,
        operation: TeamModelOperation,
    ) -> str | UserDecisionResolution:
        if operation.result_kind == "user_decision":
            self._model_effects.apply_synthesis_decision(operation.id)
            return UserDecisionResolution(_operation_user_decision(operation))
        if operation.result_kind != "synthesis":
            raise OperationConflict("Completed synthesis operation is invalid")
        summary = _operation_synthesis_summary(operation)
        return self._model_effects.apply_synthesis(operation.id, summary)

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

    def _cycle_output_contract(self, cycle_id: str | None) -> OutputContract | None:
        if cycle_id is None:
            return None
        return get_output_contract(
            self._teams.get_cycle_output_contract_id(cycle_id)
        )

    def _add_work_messages(
        self,
        run: TeamRun,
        leader: TeamAgent,
        members: list[TeamAgent],
        instruction: str,
        cycle_id: str | None,
    ) -> list[dict[str, object]]:
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
            persona_id=leader.persona_id,
            allow_request=False,
        ) + ADD_WORK_PROMPT.format(
            goal=goal_context,
            existing_titles=existing,
            instruction=instruction,
            team_roster_json=_assignment_roster_json(members),
        )
        return [{"role": "user", "content": prompt}]

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
                run = self._settle_failed(run, error, cycle_id)
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run

            run = self._teams.set_run_status(run.id, "running")
            await self._publish({"type": "team.run.executing", "team_run_id": run.id})
            return await self._execute_and_synthesize(run, leader, workers, cycle_id)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except UnparsableLeadOutput:
            return self._teams.get_team_run(run.id)
        except (ProviderOperationWaiting, AmbiguousModelOperation):
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._settle_failed(run, error, cycle_id)
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return run

    async def _plan(
        self, run: TeamRun, leader: TeamAgent, cycle_id: str | None = None
    ) -> list[dict[str, object]] | UserDecisionResolution:
        leader_agent = self._teams.get_agent(leader.id)
        members = _find_workers(self._teams.list_agents(run.id))
        member_ids = {member.id for member in members}
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
        if cycle_id is not None:
            prompt += _cycle_input_artifacts_block(
                self._teams.list_cycle_input_artifacts(cycle_id)
            )
        decision_context = self._teams.decision_context_for_run(
            run.id, stage="planning", cycle_id=cycle_id
        )
        if decision_context:
            prompt += (
                "\n\nResolved user decisions for planning:\n"
                f"{decision_context}\nDo not ask these resolved questions again."
            )
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        if cycle_id is not None:
            operation = await self._invoke_plan_with_repair(
                run,
                cycle_id,
                leader_agent,
                "cycle_planning",
                messages,
            )
            created_tasks = self._model_effects.apply_plan(operation.id)
            for created in created_tasks:
                await self._publish(
                    {
                        "type": "team.task.created",
                        "team_run_id": run.id,
                        "task_id": created.id,
                    }
                )
            return _operation_task_specs(operation)

        model = self._model(leader_agent, cycle_id)
        response = await model.complete(messages)
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

    async def _invoke_plan_with_repair(
        self,
        run: TeamRun,
        cycle_id: str,
        leader: TeamAgent,
        stage: Literal["cycle_planning", "cycle_add_work"],
        messages: list[dict[str, object]],
    ) -> TeamModelOperation:
        member_ids = {
            member.id for member in _find_workers(self._teams.list_agents(run.id))
        }

        def validate_plan(response: ModelResponse) -> ValidatedOperationResult:
            return _validated_task_plan(
                response,
                allowed_owner_agent_ids=member_ids,
            )

        recovery = await self._recover_open_operation(
            run,
            leader,
            cycle_id,
            planning_stage=stage,
            planning_messages=messages,
            planning_parser=validate_plan,
        )
        if recovery is not None:
            return recovery.operation
        spec = _operation_spec(
            run,
            cycle_id,
            leader,
            stage,
            0,
            messages,
        )
        return await self._invoke_with_repair(
            spec,
            leader,
            messages,
            validate_plan,
            repair_messages=_planning_repair_messages(messages),
            repair_ordinal=1 if stage == "cycle_planning" else 2,
        )

    async def _execute(
        self,
        run: TeamRun,
        leader: TeamAgent,
        workers: list[TeamAgent],
        cycle_id: str | None = None,
    ) -> None:
        counter = 0
        while True:
            if cycle_id is not None:
                recovery = await self._recover_open_operation(
                    run,
                    leader,
                    cycle_id,
                )
                if recovery is not None:
                    open_operation = recovery.operation
                    if open_operation.stage in {
                        "cycle_synthesis",
                        "cycle_synthesis_repair",
                    }:
                        return
                    if open_operation.stage in {
                        "cycle_planning",
                        "cycle_planning_repair",
                        "cycle_add_work",
                    }:
                        continue
                    if open_operation.stage not in {
                        "worker_execution",
                        "mediation_lead",
                        "mediation_lead_repair",
                        "mediation_worker",
                        "mediation_worker_repair",
                        "acceptance_lead",
                        "acceptance_lead_repair",
                        "acceptance_worker",
                        "acceptance_worker_repair",
                    }:
                        raise OperationConflict(
                            "Cycle has an open operation for another stage"
                        )
                    decision = recovery.result
                    if (
                        decision is not None
                        and not isinstance(decision, TeamDecisionRequest)
                    ):
                        raise OperationConflict(
                            "Worker recovery result is invalid"
                        )
                    if decision is not None:
                        if open_operation.task_id is None:
                            raise OperationConflict(
                                "Recovered task operation has no task"
                            )
                        open_task = self._teams.get_task(
                            open_operation.task_id
                        )
                        open_worker = self._teams.get_agent(
                            open_task.owner_agent_id
                        )
                        await self._publish(
                            {
                                "type": "team.task.updated",
                                "team_run_id": run.id,
                                "task_id": open_task.id,
                                "agent_id": open_worker.id,
                                "decision_request_id": decision.id,
                            }
                        )
                        if any(
                            item.get("blocking_scope") == "run"
                            for item in decision.items
                        ):
                            return
                    continue
                recovered_applied, decision = (
                    await self._recover_applied_operation_chain(
                        run,
                        leader,
                        cycle_id,
                    )
                )
                if recovered_applied:
                    if decision is not None:
                        task_id = next(
                            (
                                item.get("blocking_task_ids", [None])[0]
                                for item in decision.items
                                if item.get("blocking_task_ids")
                            ),
                            None,
                        )
                        await self._publish(
                            {
                                "type": "team.task.updated",
                                "team_run_id": run.id,
                                "task_id": task_id,
                                "decision_request_id": decision.id,
                            }
                        )
                        if any(
                            item.get("blocking_scope") == "run"
                            for item in decision.items
                        ):
                            return
                    continue
            ready_tasks = self._teams.list_dependency_ready_tasks(run.id, cycle_id)
            if not ready_tasks:
                return
            task = ready_tasks[0]
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
                if isinstance(outcome, TeamModelOperation):
                    decision = await self._apply_cycle_worker_operation(
                        run,
                        leader,
                        worker,
                        task,
                        outcome,
                        before=before,
                    )
                    task = self._teams.get_task(task.id)
                    worker = self._teams.get_agent(worker.id)
                    if decision is not None:
                        await self._publish(
                            {
                                "type": "team.task.updated",
                                "team_run_id": run.id,
                                "task_id": task.id,
                                "agent_id": worker.id,
                                "decision_request_id": decision.id,
                            }
                        )
                        if any(
                            item.get("blocking_scope") == "run"
                            for item in decision.items
                        ):
                            return
                    await self._publish(
                        {
                            "type": "team.task.updated",
                            "team_run_id": run.id,
                            "task_id": task.id,
                            "agent_id": worker.id,
                        }
                    )
                    continue
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
                recovered = await self._recover_task_outcome(
                    run,
                    leader,
                    worker,
                    task,
                    outcome,
                    acceptance,
                    working_root,
                    before,
                    staged_inputs,
                )
                if isinstance(recovered, UserDecisionResolution):
                    request = self._teams.defer_task_for_user_decision(
                        task.id, worker.id, recovered.decision
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
                    if recovered.decision.get("blocking_scope") == "run":
                        return
                    continue
                task, outcome, acceptance = recovered
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
                terminal_status = (
                    acceptance.status
                    if acceptance.accepted
                    else terminal_rejected_status(
                        acceptance.status,
                        worker_declared=is_worker_declared_outcome(outcome),
                    )
                )
                task, worker = self._teams.finish_task(
                    task.id,
                    worker.id,
                    terminal_status,
                    result=outcome.summary if acceptance.accepted else None,
                    error_message=(
                        None
                        if acceptance.accepted
                        else acceptance.reason_code or outcome.reason_code
                    ),
                )
            except asyncio.CancelledError:
                raise
            except (
                ProviderOperationWaiting,
                AmbiguousModelOperation,
                UnparsableLeadOutput,
            ):
                # Pause signals, not task failures: the outcome under review is
                # still good and the run is waiting on the operator.
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

    async def _recover_applied_operation_chain(
        self,
        run: TeamRun,
        leader: TeamAgent,
        cycle_id: str,
    ) -> tuple[bool, TeamDecisionRequest | None]:
        operations = self._operations.list_for_cycle(cycle_id)
        for task in self._teams.list_tasks(run.id, cycle_id):
            if (
                task.status not in {"in_progress", "pending"}
                or task.owner_agent_id is None
            ):
                continue
            operation = next(
                (
                    candidate
                    for candidate in reversed(operations)
                    if candidate.task_id == task.id
                    and candidate.status == "applied"
                ),
                None,
            )
            if operation is None:
                continue
            worker = self._teams.get_agent(task.owner_agent_id)
            answer = self._resolved_lead_decision_answer(
                operation,
                task,
            )
            if answer is not None:
                if task.status == "pending":
                    task, worker = self._teams.start_task(task.id, worker.id)
                elif (
                    worker.status != "running"
                    or worker.current_task_id != task.id
                ):
                    raise OperationConflict(
                        "Resolved Lead decision task is not actively owned"
                    )
                if operation.stage == "mediation_lead":
                    stage = "mediation_worker"
                    messages = _mediation_worker_messages(
                        {"answer": answer}
                    )

                    def parser(response):
                        return self._validated_worker_result(
                            response,
                            worker,
                            run,
                        )

                elif operation.stage == "acceptance_lead":
                    stage = "acceptance_worker"
                    messages = _acceptance_user_answer_messages(
                        task,
                        answer,
                    )

                    def parser(response):
                        return _validated_task_outcome_result(
                            response,
                            worker,
                            run,
                            self._finalize_persona_content,
                        )

                else:
                    raise OperationConflict(
                        "Resolved Lead decision stage is invalid"
                    )
                continuation = await self._invoke_operation(
                    _operation_spec(
                        run,
                        cycle_id,
                        worker,
                        stage,
                        operation.stage_ordinal,
                        messages,
                        task_id=task.id,
                    ),
                    worker,
                    messages,
                    parser,
                )
                return True, await self._apply_cycle_worker_operation(
                    run,
                    leader,
                    worker,
                    task,
                    continuation,
                    before=None,
                )
            if task.status == "pending":
                continue
            if operation.stage == "acceptance_lead":
                resolution = _operation_acceptance_resolution(operation)
                effect = self._model_effects.apply_acceptance_lead(
                    operation.id,
                    resolution,
                )
                return True, await self._continue_cycle_acceptance_effect(
                    run,
                    leader,
                    worker,
                    task,
                    resolution,
                    effect,
                )
            if operation.stage == "mediation_lead":
                resolution = _operation_mediation_resolution(operation)
                effect = self._model_effects.apply_mediation_lead(
                    operation.id,
                    resolution,
                )
                return True, await self._continue_cycle_mediation_effect(
                    run,
                    leader,
                    worker,
                    task,
                    resolution,
                    effect,
                )
            effect_ref = operation.effect_ref_json
            next_stage = (
                effect_ref.get("next_stage")
                if isinstance(effect_ref, dict)
                else None
            )
            if next_stage == "acceptance_lead":
                return True, await self._run_cycle_acceptance(
                    run,
                    leader,
                    worker,
                    task,
                )
            if (
                next_stage == "mediation_lead"
                and operation.result_kind == "worker_query"
            ):
                return True, await self._run_cycle_mediation(
                    run,
                    leader,
                    worker,
                    task,
                    _operation_worker_query(operation),
                )
        return False, None

    def _resolved_lead_decision_answer(
        self,
        operation: TeamModelOperation,
        task: TeamTask,
    ) -> str | None:
        if operation.stage not in {"mediation_lead", "acceptance_lead"}:
            return None
        effect_ref = operation.effect_ref_json
        if (
            operation.effect_type != operation.stage
            or not isinstance(effect_ref, dict)
            or effect_ref.get("next_stage") != "user_decision"
            or not isinstance(effect_ref.get("decision_request_id"), str)
            or not isinstance(effect_ref.get("decision_item_id"), str)
            or not isinstance(effect_ref.get("decision_item_digest"), str)
        ):
            return None
        request = self._teams._get_decision_request(
            effect_ref["decision_request_id"]
        )
        item = next(
            (
                candidate
                for candidate in request.items
                if candidate.get("id") == effect_ref["decision_item_id"]
            ),
            None,
        )
        if (
            request.team_run_id != operation.team_run_id
            or request.cycle_id != operation.cycle_id
            or request.status != "resolved"
            or item is None
            or lead_decision_item_digest(
                item,
                task.id,
                (
                    effect_ref.get("query_message_id")
                    if operation.stage == "mediation_lead"
                    else None
                ),
            )
            != effect_ref["decision_item_digest"]
            or task.id not in item.get("blocking_task_ids", [])
        ):
            raise OperationConflict(
                "Resolved Lead decision receipt is invalid"
            )
        if (
            operation.stage == "mediation_lead"
            and effect_ref.get("query_message_id")
            not in item.get("query_message_ids", [])
        ):
            raise OperationConflict(
                "Resolved mediation decision query is invalid"
            )
        answer = request.answers.get(effect_ref["decision_item_id"])
        if not isinstance(answer, str) or not answer.strip():
            raise OperationConflict("Resolved Lead decision has no answer")
        return answer.strip()

    async def _apply_cycle_worker_operation(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        operation: TeamModelOperation,
        *,
        before: WorkspaceSnapshot | None,
    ) -> TeamDecisionRequest | None:
        if operation.result_kind == "worker_query":
            applied = self._model_effects.apply_worker_query(operation.id)
            assert applied.message is not None
            query = _operation_worker_query(operation)
            return await self._run_cycle_mediation(
                run,
                leader,
                worker,
                task,
                query,
            )
        if operation.result_kind != "task_outcome":
            raise OperationConflict("Completed Worker operation result is invalid")
        outcome = _operation_task_outcome(operation)
        working_root = Path(run.working_root or run.workspace_root)
        try:
            before = self._teams.get_operation_workspace_baseline(
                operation.id,
                team_run_id=operation.team_run_id,
                cycle_id=operation.cycle_id,
                task_id=task.id,
                agent_id=worker.id,
            )
        except KeyError:
            if before is None:
                before = workspace_snapshot(working_root)
        changes = (
            _operation_workspace_changes(
                workspace_changes(before, workspace_snapshot(working_root))
            )
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
        acceptance = _reject_lingering_undeclared_paths(
            acceptance,
            _persisted_undeclared_paths(
                task,
                self._teams.list_messages(run.id),
            ),
            task.acceptance.required_outputs,
            working_root,
        )
        if acceptance.accepted and outcome.deliverables:
            try:
                if self._artifact_publisher is None:
                    raise ArtifactPublicationError("artifact_publication_failed")
                self._artifact_publisher.publish(
                    run.id,
                    task.cycle_id,
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
        applied = self._model_effects.apply_worker_outcome(
            operation.id,
            acceptance,
            workspace_changes=changes,
        )
        if applied.next_stage != "acceptance_lead":
            return None
        return await self._run_cycle_acceptance(
            run,
            leader,
            worker,
            applied.task,
        )

    async def _run_cycle_mediation(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        query: dict[str, str],
    ) -> TeamDecisionRequest | None:
        cycle = self._teams.get_cycle(task.cycle_id)
        worker_agent = self._teams.get_agent(worker.id)
        if (
            cycle.rounds_used >= cycle.rounds_budget
            or worker_agent.reinvocations >= AGENT_REINVOCATION_CAP
        ):
            messages = _mediation_budget_messages()
            operation = await self._invoke_operation(
                _operation_spec(
                    run,
                    task.cycle_id,
                    worker_agent,
                    "mediation_worker",
                    max(cycle.rounds_used, worker_agent.reinvocations) + 1,
                    messages,
                    task_id=task.id,
                ),
                worker_agent,
                messages,
                lambda response: _validated_task_outcome_result(
                    response,
                    worker_agent,
                    run,
                    self._finalize_persona_content,
                ),
            )
            return await self._apply_cycle_worker_operation(
                run,
                leader,
                worker_agent,
                task,
                operation,
                before=None,
            )
        leader_agent = self._teams.get_agent(leader.id)
        round_number = (
            self._teams.get_cycle(task.cycle_id).rounds_used + 1
        )
        messages = self._mediation_messages(
            run,
            leader_agent,
            task,
            query["question"],
        )
        lead_operation = await self._invoke_operation(
            _operation_spec(
                run,
                task.cycle_id,
                leader_agent,
                "mediation_lead",
                round_number,
                messages,
                task_id=task.id,
            ),
            leader_agent,
            messages,
            _validated_mediation_result,
        )
        resolution = _operation_mediation_resolution(lead_operation)
        lead_effect = self._model_effects.apply_mediation_lead(
            lead_operation.id,
            resolution,
        )
        return await self._continue_cycle_mediation_effect(
            run,
            leader,
            worker,
            task,
            resolution,
            lead_effect,
        )

    async def _continue_cycle_mediation_effect(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        resolution: dict[str, object],
        lead_effect,
    ) -> TeamDecisionRequest | None:
        if lead_effect.next_stage == "user_decision":
            assert lead_effect.decision_request is not None
            return lead_effect.decision_request
        assert lead_effect.message is not None
        worker_agent = self._teams.get_agent(worker.id)
        worker_messages = _mediation_worker_messages(resolution)
        before = workspace_snapshot(
            Path(run.working_root or run.workspace_root)
        )
        worker_operation = await self._invoke_operation(
            _operation_spec(
                run,
                task.cycle_id,
                worker_agent,
                "mediation_worker",
                self._teams.get_cycle(task.cycle_id).rounds_used,
                worker_messages,
                task_id=task.id,
            ),
            worker_agent,
            worker_messages,
            lambda response: self._validated_worker_result(
                response,
                worker_agent,
                run,
            ),
        )
        return await self._apply_cycle_worker_operation(
            run,
            leader,
            worker,
            task,
            worker_operation,
            before=before,
        )

    async def _run_cycle_acceptance(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
    ) -> TeamDecisionRequest | None:
        task = self._teams.get_task(task.id)
        leader_agent = self._teams.get_agent(leader.id)
        worker_agent = self._teams.get_agent(worker.id)
        attempt = task.acceptance_recovery_attempts + 1
        messages = self._acceptance_review_messages(
            run,
            leader_agent,
            worker_agent,
            task,
        )
        lead_operation = await self._invoke_with_repair(
            _operation_spec(
                run,
                task.cycle_id,
                leader_agent,
                "acceptance_lead",
                attempt,
                messages,
                task_id=task.id,
            ),
            leader_agent,
            messages,
            _validated_acceptance_review,
            on_exhausted=lambda failed: self._escalate_unparsable_lead_output(
                run, task.cycle_id, task, "acceptance_lead", leader_agent, failed
            ),
        )
        resolution = _operation_acceptance_resolution(lead_operation)
        lead_effect = self._model_effects.apply_acceptance_lead(
            lead_operation.id,
            resolution,
        )
        await self._publish(
            {
                "type": "team.task.updated",
                "team_run_id": run.id,
                "task_id": task.id,
                "agent_id": worker.id,
                "acceptance_reviewed": True,
            }
        )
        return await self._continue_cycle_acceptance_effect(
            run,
            leader,
            worker,
            task,
            resolution,
            lead_effect,
        )

    async def _continue_cycle_acceptance_effect(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        resolution: AcceptanceReviewResolution,
        lead_effect,
    ) -> TeamDecisionRequest | None:
        if lead_effect.next_stage == "user_decision":
            assert lead_effect.decision_request is not None
            return lead_effect.decision_request
        if lead_effect.next_stage is None:
            return None

        assert resolution.instruction is not None
        task = self._teams.get_task(task.id)
        worker_messages = _acceptance_worker_messages(task, resolution)
        before = workspace_snapshot(
            Path(run.working_root or run.workspace_root)
        )
        worker_agent = self._teams.get_agent(worker.id)
        def parser(response):
            return _validated_task_outcome_result(
                response,
                worker_agent,
                run,
                self._finalize_persona_content,
            )

        worker_operation = await self._invoke_with_repair(
            _operation_spec(
                run,
                task.cycle_id,
                worker_agent,
                "acceptance_worker",
                lead_effect.attempt,
                worker_messages,
                task_id=task.id,
            ),
            worker_agent,
            worker_messages,
            parser,
            repair_messages=_acceptance_worker_repair_messages,
        )
        return await self._apply_cycle_worker_operation(
            run,
            leader,
            worker,
            task,
            worker_operation,
            before=before,
        )

    def _acceptance_review_messages(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        *,
        outcome: TaskOutcome | None = None,
        acceptance: AcceptanceResult | None = None,
        changes: dict[str, list[str]] | None = None,
    ) -> list[dict[str, object]]:
        outcome = outcome or _persisted_task_outcome_value(task)
        acceptance = acceptance or _persisted_acceptance_value(task)
        if changes is None:
            changes = self._acceptance_workspace_changes(run, task)
        history = [
            {
                "content": message.content,
                "metadata": message.metadata,
            }
            for message in self._teams.list_messages(run.id)
            if message.kind == "acceptance_review"
            and message.metadata.get("task_id") == task.id
        ]
        goal_context = self._goal_context(run, task.cycle_id)
        prompt = (
            _space_block(
                run,
                self._space_policy(run, task.cycle_id),
                task.cycle_id,
            )
            + _rules_block(
                self._rules_snapshot(run, task.cycle_id),
                include_persona_baseline=False,
            )
            + ACCEPTANCE_REVIEW_PROMPT
            + "\n\nAuthoritative review context:\n"
            + json.dumps(
                {
                    "goal_and_cycle_instruction": goal_context,
                    "task": {
                        "id": task.id,
                        "title": task.title,
                        "description": task.description,
                    },
                    "worker": {
                        "id": worker.id,
                        "persona_snapshot": worker.persona_snapshot,
                    },
                    "acceptance": json.loads(_task_acceptance_json(task.acceptance)),
                    "outcome": asdict(outcome),
                    "acceptance_result": asdict(acceptance),
                    "workspace_changes": changes,
                    "prior_acceptance_reviews": history,
                    "remaining_attempts": (
                        ACCEPTANCE_RECOVERY_CAP
                        - task.acceptance_recovery_attempts
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return [{"role": "user", "content": prompt}]

    def _acceptance_workspace_changes(
        self,
        run: TeamRun,
        task: TeamTask,
    ) -> dict[str, list[str]]:
        operation = next(
            (
                candidate
                for candidate in reversed(
                    self._operations.list_for_cycle(task.cycle_id)
                )
                if candidate.task_id == task.id
                and candidate.status == "applied"
                and candidate.result_kind == "task_outcome"
            ),
            None,
        )
        effect_ref = (
            operation.effect_ref_json
            if operation is not None
            else None
        )
        message_id = (
            effect_ref.get("message_id")
            if isinstance(effect_ref, dict)
            else None
        )
        message = next(
            (
                candidate
                for candidate in self._teams.list_messages(
                    run.id,
                    task.cycle_id,
                )
                if candidate.id == message_id
            ),
            None,
        )
        if (
            operation is None
            or message is None
            or message.metadata.get("operation_id") != operation.id
        ):
            raise OperationConflict(
                "Acceptance operation has no exact Worker effect receipt"
            )
        changes: dict[str, list[str]] = {}
        for source, target in (
            ("created", "files_created"),
            ("modified", "files_modified"),
            ("deleted", "files_deleted"),
        ):
            value = message.metadata.get(source)
            if not isinstance(value, list) or any(
                not isinstance(path, str) for path in value
            ):
                raise OperationConflict(
                    "Worker workspace effect receipt is invalid"
                )
            changes[target] = value
        return changes

    def _mediation_messages(
        self,
        run: TeamRun,
        leader: TeamAgent,
        task: TeamTask,
        question: str,
    ) -> list[dict[str, object]]:
        goal_context = self._goal_context(run, task.cycle_id)
        prompt = _space_block(
            run,
            self._space_policy(run, task.cycle_id),
            task.cycle_id,
        ) + self._archive_block(
            f"{goal_context}\n{task.title}\n{question}",
            persona_id=leader.persona_id,
            allow_request=False,
        ) + MEDIATION_PROMPT.format(
            goal=goal_context,
            task_title=task.title,
            question=question,
            outputs=self._collect_outputs(run, task.cycle_id),
        )
        return [{"role": "user", "content": prompt}]

    async def _review_acceptance(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        outcome: TaskOutcome,
        acceptance: AcceptanceResult,
        working_root: Path,
        task_snapshot: WorkspaceSnapshot,
    ) -> AcceptanceReviewResolution:
        task = self._teams.get_task(task.id)
        leader_agent = self._teams.get_agent(leader.id)
        changes = workspace_changes(
            task_snapshot,
            workspace_snapshot(working_root),
        )
        messages = self._acceptance_review_messages(
            run,
            leader_agent,
            worker,
            task,
            outcome=outcome,
            acceptance=acceptance,
            changes=changes,
        )
        model = self._model(leader_agent, task.cycle_id)
        response = await model.complete(messages)
        if response.upstream_session_id:
            self._teams.set_agent_session(
                leader_agent.id, response.upstream_session_id
            )
        try:
            return _parse_acceptance_review_resolution(response.content)
        except ValueError:
            retry = await model.complete(
                [
                    {
                        "role": "user",
                        "content": (
                            str(messages[0]["content"])
                            + "\nReturn ONLY one valid acceptance review JSON object. "
                            "No prose or code fences."
                        ),
                    }
                ]
            )
            if retry.upstream_session_id:
                self._teams.set_agent_session(
                    leader_agent.id, retry.upstream_session_id
                )
            return _parse_acceptance_review_resolution(retry.content)

    async def _recover_task_outcome(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        outcome: TaskOutcome,
        acceptance: AcceptanceResult,
        working_root: Path,
        task_snapshot: WorkspaceSnapshot,
        staged_inputs: StagedInputs | None,
    ) -> tuple[TeamTask, TaskOutcome, AcceptanceResult] | UserDecisionResolution:
        task = self._teams.get_task(task.id)
        rejected_paths = _persisted_undeclared_paths(
            task,
            self._teams.list_messages(run.id),
        )
        acceptance = _reject_lingering_undeclared_paths(
            acceptance,
            rejected_paths,
            task.acceptance.required_outputs,
            working_root,
        )
        while not acceptance.accepted:
            self._teams.record_task_outcome(
                task.id,
                asdict(outcome),
                asdict(acceptance),
            )
            task = self._teams.get_task(task.id)
            if not is_recoverable_acceptance_failure(
                acceptance.reason_code,
                worker_declared=is_worker_declared_outcome(outcome),
            ):
                return task, outcome, acceptance
            if task.acceptance_recovery_attempts >= ACCEPTANCE_RECOVERY_CAP:
                return task, outcome, acceptance
            if acceptance.reason_code == "undeclared_deliverable":
                rejected_paths.update(
                    item.path
                    for item in outcome.deliverables
                    if item.path not in task.acceptance.required_outputs
                )

            resolution = await self._review_acceptance(
                run,
                leader,
                worker,
                task,
                outcome,
                acceptance,
                working_root,
                task_snapshot,
            )
            reason_code = acceptance.reason_code or outcome.reason_code or "task_failed"
            verification_status = {
                item.name: item.status for item in outcome.verifications
            }
            task = self._teams.record_acceptance_review(
                task.id,
                leader.id,
                worker.id,
                action=resolution.kind,
                reason_code=(
                    resolution.reason_code
                    if resolution.kind == "fail" and resolution.reason_code
                    else reason_code
                ),
                reason=resolution.reason,
                instruction=resolution.instruction,
                acceptance_after=resolution.acceptance,
                rejected_deliverables=tuple(
                    item.path for item in outcome.deliverables
                ),
                rejected_verifications=tuple(
                    rejected_verification_names(
                        (
                            (required.name, required.check is not None)
                            for required in task.acceptance.required_verifications
                        ),
                        verification_status,
                        acceptance.evidence,
                    )
                ),
            )
            await self._publish(
                {
                    "type": "team.task.updated",
                    "team_run_id": run.id,
                    "task_id": task.id,
                    "agent_id": worker.id,
                    "acceptance_reviewed": True,
                }
            )

            if resolution.kind == "ask_user":
                assert resolution.decision is not None
                return UserDecisionResolution(resolution.decision)
            if resolution.kind == "fail":
                return (
                    task,
                    outcome,
                    AcceptanceResult(
                        accepted=False,
                        status="failed",
                        reason_code=resolution.reason_code,
                        evidence={},
                    ),
                )

            assert resolution.instruction is not None
            current_acceptance = json.loads(_task_acceptance_json(task.acceptance))
            content = await self._resume_worker(
                worker.id,
                (
                    f"{resolution.instruction}\n\n"
                    "Authoritative current acceptance criteria:\n"
                    f"{json.dumps(current_acceptance, ensure_ascii=False, sort_keys=True)}\n"
                    "Return only the required TaskOutcome JSON object."
                ),
                task.cycle_id,
            )
            outcome = self._task_outcome(
                content,
                persona_id=worker.persona_id,
                team_run_id=run.id,
            )
            changes = workspace_changes(
                task_snapshot,
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
                cycle_id=task.cycle_id,
            )
            task = self._teams.get_task(task.id)
            acceptance = self._acceptance_service.evaluate(
                task,
                outcome,
                working_root,
                staged_inputs=staged_inputs,
            )
            acceptance = _reject_lingering_undeclared_paths(
                acceptance,
                rejected_paths,
                task.acceptance.required_outputs,
                working_root,
            )
        return task, outcome, acceptance

    async def _run_task(
        self, run: TeamRun, leader: TeamAgent, worker: TeamAgent, task: TeamTask
    ) -> TaskOutcome | UserDecisionResolution | TeamModelOperation:
        worker_agent = self._teams.get_agent(worker.id)
        if task.cycle_id is not None:
            messages: list[dict[str, object]] = [
                {
                    "role": "user",
                    "content": self._worker_prompt(run, worker_agent, task),
                }
            ]
            spec = _operation_spec(
                run,
                task.cycle_id,
                worker_agent,
                "worker_execution",
                0,
                messages,
                task_id=task.id,
            )
            def parser(response):
                return self._validated_worker_result(
                    response,
                    worker_agent,
                    run,
                )
            return await self._invoke_with_repair(
                spec,
                worker_agent,
                messages,
                parser,
                repair_messages=_worker_repair_messages(messages),
            )

        model = self._model(worker_agent, task.cycle_id)
        response = await model.complete(
            [{"role": "user", "content": self._worker_prompt(run, worker_agent, task)}]
        )
        if response.upstream_session_id:
            self._teams.set_agent_session(worker_agent.id, response.upstream_session_id)
        content = response.content
        return await self._continue_worker_content(
            run,
            leader,
            worker,
            task,
            content,
        )

    async def _continue_worker_content(
        self,
        run: TeamRun,
        leader: TeamAgent,
        worker: TeamAgent,
        task: TeamTask,
        content: str,
        *,
        query_message: TeamMessage | None = None,
    ) -> TaskOutcome | UserDecisionResolution:
        worker_agent = self._teams.get_agent(worker.id)
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
            if query_message is None:
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
            query_message = None

    def _validated_worker_result(
        self,
        response: ModelResponse,
        worker: TeamAgent,
        run: TeamRun,
    ) -> ValidatedOperationResult:
        query = _parse_needs_info(response.content)
        if query is not None:
            return ValidatedOperationResult("worker_query", query)
        outcome = parse_task_outcome(response.content)
        summary = self._finalize_persona_content(
            outcome.summary,
            persona_id=worker.persona_id,
            team_run_id=run.id,
        )
        return ValidatedOperationResult(
            "task_outcome",
            asdict(replace(outcome, summary=summary)),
        )

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
        response = await model.complete(
            self._mediation_messages(
                run,
                leader_agent,
                task,
                question,
            )
        )
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
        task_inputs = self._teams.list_task_input_artifacts(task.id)
        staged_paths: tuple[str, ...] = ()
        if task_inputs:
            staged_paths = self._task_input_stager.stage(
                task,
                Path(run.working_root or run.workspace_root),
            ).paths
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
        prompt += "\n\nAcceptance criteria:\n" + _task_acceptance_json(task.acceptance)
        prompt += (
            "\n\nALLOWED TASK INPUTS\n"
            + (
                "\n".join(staged_paths)
                if staged_paths
                else "(none)"
            )
            + "\nRead only these workspace-relative staged paths when input is needed."
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
            self._teams.skip_pending_dependency_failures(run.id, cycle_id)
            tasks = self._teams.list_tasks(run.id, cycle_id)
            dependencies = self._teams.list_task_dependency_map(run.id, cycle_id)
            status = _terminal_status(tasks, dependencies)
            if status is None:
                unresolved = sorted(
                    task.id
                    for task in tasks
                    if task.status
                    not in {
                        "completed",
                        "skipped",
                        "blocked",
                        "failed",
                        "canceled",
                    }
                )
                raise LifecycleIntegrityError(
                    f"Cycle has unresolved tasks with no executable path: {unresolved}"
                )
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
                if cycle_id is None:
                    self._teams.defer_run_for_user_decision(
                        run.id,
                        summary.decision,
                        stage="synthesis",
                        cycle_id=cycle_id,
                    )
                return await self._publish_user_decision_request(run, cycle_id)
            if cycle_id is not None:
                run = self._teams.get_team_run(run.id)
                self._package_results(run, leader, cycle_id)
                await self._publish(
                    {"type": "team.run.completed", "team_run_id": run.id}
                )
                return run
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
        if cycle_id is not None:
            open_operation = self._operations.get_open_for_cycle(cycle_id)
            if open_operation is not None:
                if open_operation.status == "ambiguous":
                    raise AmbiguousModelOperation(
                        open_operation.id,
                        open_operation.consumer_run_id or "",
                        open_operation.reason_code
                        or "ambiguous_remote_result",
                    )
                if open_operation.status == "waiting_for_provider":
                    raise ProviderOperationWaiting(open_operation.id)
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
                run = self._settle_failed(run, error, cycle_id)
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return run
            return await self._execute_and_synthesize(run, leader, workers, cycle_id)
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except UnparsableLeadOutput:
            # The escalation already published the decision request and moved the
            # run to waiting_for_user. Return that state rather than failing.
            return self._teams.get_team_run(run.id)
        except (ProviderOperationWaiting, AmbiguousModelOperation):
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._settle_failed(run, error, cycle_id)
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return run

    async def add_work(
        self, team_run_id: str, instruction: str, cycle_id: str | None = None
    ) -> list[TeamTask]:
        run = self._teams.get_team_run(team_run_id)
        self._validate_cycle(run, cycle_id)
        if cycle_id is not None:
            instruction = (
                self._teams.get_cycle_effective_instruction(cycle_id)
                or instruction
            )
        leader = _find_leader(self._teams.list_agents(run.id))
        leader_agent = self._teams.get_agent(leader.id)
        members = _find_workers(self._teams.list_agents(run.id))
        member_ids = {member.id for member in members}
        messages = self._add_work_messages(
            run,
            leader_agent,
            members,
            instruction,
            cycle_id,
        )
        if cycle_id is not None:
            operation = await self._invoke_plan_with_repair(
                run,
                cycle_id,
                leader_agent,
                "cycle_add_work",
                messages,
            )
            created = self._model_effects.apply_plan(operation.id)
            for task in created:
                await self._publish(
                    {
                        "type": "team.task.created",
                        "team_run_id": run.id,
                        "task_id": task.id,
                    }
                )
            return created

        model = self._model(leader_agent, cycle_id)
        response = await model.complete(messages)
        if response.upstream_session_id:
            self._teams.set_agent_session(leader_agent.id, response.upstream_session_id)
        try:
            specs = _parse_task_plan(response.content)
        except ValueError:
            retry = await model.complete(
                _planning_repair_messages(messages)
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
        goal_context = self._goal_context(run, cycle_id)
        contract = self._cycle_output_contract(cycle_id)
        synthesis_block = (
            SYNTHESIS_CONTRACT_PROMPT.format(
                goal=goal_context,
                results=results,
                contract=contract.instructions,
            )
            if contract is not None
            else SYNTHESIS_PROMPT.format(goal=goal_context, results=results)
        )
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
        ) + synthesis_block
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
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        if cycle_id is not None:
            def synthesis_parser(response):
                return self._validated_synthesis_result(
                    response,
                    leader_agent,
                    run,
                    contract,
                )

            try:
                recovery = await self._recover_open_operation(
                    run,
                    leader_agent,
                    cycle_id,
                    synthesis_messages=messages,
                    synthesis_parser=synthesis_parser,
                    synthesis_contract=contract,
                )
            except InvalidOperationResult as exc:
                operation = await self._repair_synthesis_operation(
                    run,
                    cycle_id,
                    leader_agent,
                    contract,
                    messages,
                    self._operations.get(exc.operation_id),
                )
                return self._apply_cycle_synthesis_operation(operation)
            if recovery is not None:
                if not isinstance(
                    recovery.result,
                    (str, UserDecisionResolution),
                ):
                    raise OperationConflict(
                        "Synthesis recovery result is invalid"
                    )
                return recovery.result
            resolved_request_ids = {
                request.id
                for request in self._teams.list_decision_requests(run.id)
                if request.cycle_id == cycle_id and request.status == "resolved"
            }
            synthesis_ordinal = sum(
                operation.stage in {"cycle_synthesis", "cycle_synthesis_repair"}
                and operation.status == "applied"
                and operation.result_kind == "user_decision"
                and isinstance(operation.effect_ref_json, dict)
                and operation.effect_ref_json.get("decision_request_id")
                in resolved_request_ids
                for operation in self._operations.list_for_cycle(cycle_id)
            )
            spec = _operation_spec(
                run,
                cycle_id,
                leader_agent,
                "cycle_synthesis",
                synthesis_ordinal,
                messages,
            )
            operation = await self._invoke_with_repair(
                spec,
                leader_agent,
                messages,
                synthesis_parser,
                repair_messages=_synthesis_repair_prompt(messages, contract),
                repair_parser=lambda response: self._validated_synthesis_result(
                    response, leader_agent, run, contract, strict=False
                ),
            )
            return self._apply_cycle_synthesis_operation(operation)

        model = self._model(leader_agent, cycle_id)
        response = await model.complete(messages)
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

    def _validated_synthesis_result(
        self,
        response: ModelResponse,
        leader: TeamAgent,
        run: TeamRun,
        contract: OutputContract | None = None,
        *,
        strict: bool = True,
    ) -> ValidatedOperationResult:
        resolution = _parse_mediation_resolution(response.content)
        if resolution["kind"] == "ask_user":
            return ValidatedOperationResult("user_decision", resolution)
        content = self._finalize_persona_content(
            response.content,
            persona_id=leader.persona_id,
            team_run_id=run.id,
        )
        summary, gaps = extract_coverage_gaps(content)
        payload: dict[str, object] = {"summary": summary}
        if gaps is not None:
            payload["coverage_gaps"] = gaps
        if contract is None:
            return ValidatedOperationResult("synthesis", payload)
        try:
            contract.validate(summary)
        except ValueError:
            if strict:
                raise
            return ValidatedOperationResult("synthesis", payload)
        payload["summary"] = contract.human_summary(summary)
        payload["contract_payload"] = summary
        return ValidatedOperationResult("synthesis", payload)

    async def _publish_user_decision_request(
        self,
        run: TeamRun,
        cycle_id: str | None,
    ) -> TeamRun:
        request = self._teams.publish_decision_request(run.id, cycle_id)
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

    def _settle_failed(
        self,
        run: TeamRun,
        error: str,
        cycle_id: str | None = None,
    ) -> TeamRun:
        for task in self._teams.list_tasks(run.id, cycle_id):
            if task.status != "in_progress":
                continue
            if task.owner_agent_id:
                self._teams.finish_task(
                    task.id,
                    task.owner_agent_id,
                    "failed",
                    result=task.result,
                    error_message=error,
                )
                continue
            self._teams.set_task_status(
                task.id,
                "failed",
                result=task.result,
                error_message=error,
            )
        for agent in self._teams.list_agents(run.id):
            if agent.status == "running":
                self._teams.set_agent_status(agent.id, "failed")
        failed = self._teams.set_run_status(run.id, "failed", error_message=error)
        if cycle_id is not None:
            self._teams.set_cycle_status(cycle_id, "failed", error_message=error)
        return failed

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
            self._teams.reset_agents_for_new_cycle(cycle.team_run_id)
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


def _cycle_input_artifacts_block(inputs: list[object]) -> str:
    catalog = [
        {
            "id": item.artifact_id,
            "relative_path": item.relative_path,
        }
        for item in inputs
    ]
    return (
        "\n\nALLOWED TASK INPUT ARTIFACTS\n"
        f"{json.dumps(catalog, ensure_ascii=False)}\n"
        "Only these IDs may appear in input_artifact_ids. "
        "Task descriptions and prior reports do not grant input access."
    )


def _terminal_status(
    tasks: list[TeamTask],
    dependencies: dict[str, list[str]] | None = None,
) -> str | None:
    disposition = cycle_execution_disposition(tasks, dependencies or {})
    if disposition.kind != "terminal":
        return None
    return disposition.terminal_status


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


def _operation_key(
    cycle_id: str,
    stage: str,
    stage_ordinal: int,
    *,
    task_id: str | None = None,
) -> str:
    if task_id is None:
        return f"{cycle_id}:{stage}:{stage_ordinal}"
    return f"{cycle_id}:{task_id}:{stage}:{stage_ordinal}"


def _operation_request_digest(
    stage: str,
    stage_ordinal: int,
    actor_id: str,
    messages: list[dict[str, object]],
) -> str:
    serialized = json.dumps(
        {
            "stage": stage,
            "ordinal": stage_ordinal,
            "actor_id": actor_id,
            "messages": messages,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _planning_repair_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": f"{messages[0]['content']}\n{_PLANNING_REPAIR_INSTRUCTION}",
        }
    ]


def _synthesis_repair_messages(
    messages: list[dict[str, object]],
    contract: OutputContract,
) -> list[dict[str, object]]:
    correction = (
        "Your previous response did not satisfy the output contract. "
        "Send the same result again: first a short plain-text summary, "
        "then the contract output in exactly the required form, with "
        "nothing after it.\n\nOUTPUT CONTRACT\n"
        f"{contract.instructions}"
    )
    return [{"role": "user", "content": f"{messages[0]['content']}\n\n{correction}"}]


def _synthesis_retry_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    """The no-contract counterpart of _synthesis_repair_messages.

    Deterministic in the messages alone, deliberately not in the reason code:
    the ledger stores only a request digest, so recovery has to rebuild a
    prepared repair's prompt byte for byte from what it still holds, and it
    does not hold the failed operation.
    """
    correction = (
        "Your previous response could not be parsed. Send the same result "
        "again: either a short plain-text summary, or the exact ask_user JSON "
        "block, with nothing after it."
    )
    return [{"role": "user", "content": f"{messages[0]['content']}\n\n{correction}"}]


def _synthesis_repair_prompt(
    messages: list[dict[str, object]],
    contract: OutputContract | None,
) -> list[dict[str, object]]:
    if contract is None:
        return _synthesis_retry_messages(messages)
    return _synthesis_repair_messages(messages, contract)


def _worker_repair_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": f"{messages[0]['content']}\n\n{_WORKER_REPAIR_INSTRUCTION}",
        }
    ]


def _mediation_worker_messages(
    resolution: dict[str, object],
) -> list[dict[str, object]]:
    answer = resolution.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise OperationConflict(
            "Mediation Worker operation requires a Lead answer"
        )
    return [
        {
            "role": "user",
            "content": (
                f"Answer to your question: {answer}\n\n"
                "Continue and produce your final result, or ask again only "
                "if essential."
            ),
        }
    ]


def _mediation_budget_messages() -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": (
                "No more consultation is available. Produce your best-effort "
                "final result now, without a needs_info block."
            ),
        }
    ]


def _acceptance_worker_messages(
    task: TeamTask,
    resolution: AcceptanceReviewResolution,
) -> list[dict[str, object]]:
    if not resolution.instruction:
        raise OperationConflict(
            "Acceptance Worker operation requires a correction instruction"
        )
    return [
        {
            "role": "user",
            "content": (
                f"{resolution.instruction}\n\n"
                "Authoritative current acceptance criteria:\n"
                f"{_task_acceptance_json(task.acceptance)}\n"
                "Return only the required TaskOutcome JSON object."
            ),
        }
    ]


def _repair_messages(reason_code: str | None) -> list[dict[str, object]]:
    """Shape-agnostic on purpose.

    Only the parser knows the expected keys and there is no schema to read them
    from, so naming keys here would be right for one stage and subtly wrong for
    the rest. A stage that wants to restate its keys passes its own
    repair_messages.
    """
    error = reason_code or "invalid_structured_output"
    return [
        {
            "role": "user",
            "content": (
                "Your previous response could not be parsed.\n"
                f"Error: {error}.\n\n"
                "Do not repeat the work and do not modify files. Re-emit only "
                "the previous final result as one raw JSON object. No "
                "explanations, no Markdown, no code fences."
            ),
        }
    ]


def _acceptance_worker_repair_messages(
    reason_code: str | None,
) -> list[dict[str, object]]:
    error = reason_code or "invalid_structured_output"
    return [
        {
            "role": "user",
            "content": (
                "Your previous response could not be parsed.\n"
                f"Error: {error}. The response was not a valid "
                "TaskOutcome JSON object.\n\n"
                "Do not repeat the task or modify files. Re-emit only the "
                "previous final result as one raw JSON object with exactly "
                "these keys: status, summary, reason_code, deliverables, "
                "verifications. Do not include explanations, Markdown, or "
                "code fences."
            ),
        }
    ]


def _acceptance_user_answer_messages(
    task: TeamTask,
    answer: str,
) -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": (
                f"User decision: {answer}\n\n"
                "Continue the rejected task using that decision.\n"
                "Authoritative current acceptance criteria:\n"
                f"{_task_acceptance_json(task.acceptance)}\n"
                "Return only the required TaskOutcome JSON object."
            ),
        }
    ]


def _operation_workspace_changes(
    changes: dict[str, list[str]],
) -> dict[str, list[str]]:
    return {
        "created": changes["files_created"],
        "modified": changes["files_modified"],
        "deleted": changes["files_deleted"],
    }


def _operation_spec(
    run: TeamRun,
    cycle_id: str,
    agent: TeamAgent,
    stage,
    stage_ordinal: int,
    messages: list[dict[str, object]],
    *,
    task_id: str | None = None,
    upstream_session_id: str | None | object = _SESSION_UNSET,
) -> OperationSpec:
    return OperationSpec(
        operation_key=_operation_key(
            cycle_id,
            stage,
            stage_ordinal,
            task_id=task_id,
        ),
        team_run_id=run.id,
        cycle_id=cycle_id,
        task_id=task_id,
        agent_id=agent.id,
        provider=agent.backend,
        stage=stage,
        stage_ordinal=stage_ordinal,
        request_digest=_operation_request_digest(
            stage,
            stage_ordinal,
            agent.id,
            messages,
        ),
        upstream_session_id=(
            agent.upstream_session_id
            if upstream_session_id is _SESSION_UNSET
            else (
                upstream_session_id
                if isinstance(upstream_session_id, str)
                else None
            )
        ),
    )


def _validated_task_plan(
    response: ModelResponse,
    *,
    allowed_owner_agent_ids: set[str] | None = None,
) -> ValidatedOperationResult:
    tasks = _parse_task_plan(
        response.content,
        allowed_owner_agent_ids=allowed_owner_agent_ids,
    )
    return ValidatedOperationResult(
        "task_plan",
        {
            "tasks": [
                {
                    **task,
                    "acceptance": json.loads(
                        _task_acceptance_json(task["acceptance"])
                    ),
                }
                for task in tasks
            ]
        },
    )


def _validated_mediation_result(
    response: ModelResponse,
) -> ValidatedOperationResult:
    return ValidatedOperationResult(
        "mediation_resolution",
        _parse_mediation_resolution(response.content),
    )


def _validated_acceptance_review(
    response: ModelResponse,
) -> ValidatedOperationResult:
    resolution = _parse_acceptance_review_resolution(response.content)
    return ValidatedOperationResult(
        "acceptance_review",
        _acceptance_resolution_json(resolution),
    )


def _validated_task_outcome_result(
    response: ModelResponse,
    worker: TeamAgent,
    run: TeamRun,
    finalize: Callable,
) -> ValidatedOperationResult:
    outcome = parse_task_outcome(response.content)
    summary = finalize(
        outcome.summary,
        persona_id=worker.persona_id,
        team_run_id=run.id,
    )
    return ValidatedOperationResult(
        "task_outcome",
        asdict(replace(outcome, summary=summary)),
    )


def _operation_mediation_resolution(
    operation: TeamModelOperation,
) -> dict[str, object]:
    stored = operation.result_json
    payload = stored.get("payload") if isinstance(stored, dict) else None
    if (
        operation.result_kind != "mediation_resolution"
        or not isinstance(payload, dict)
    ):
        raise OperationConflict(
            "Completed mediation operation is invalid"
        )
    return payload


def _acceptance_resolution_json(
    resolution: AcceptanceReviewResolution,
) -> dict[str, object]:
    return {
        "kind": resolution.kind,
        "reason": resolution.reason,
        "instruction": resolution.instruction,
        "reason_code": resolution.reason_code,
        "acceptance": (
            json.loads(_task_acceptance_json(resolution.acceptance))
            if resolution.acceptance is not None
            else None
        ),
        "decision": resolution.decision,
    }


def _operation_acceptance_resolution(
    operation: TeamModelOperation,
) -> AcceptanceReviewResolution:
    stored = operation.result_json
    payload = stored.get("payload") if isinstance(stored, dict) else None
    if (
        operation.result_kind != "acceptance_review"
        or not isinstance(payload, dict)
    ):
        raise OperationConflict(
            "Completed acceptance operation is invalid"
        )
    acceptance_payload = payload.get("acceptance")
    acceptance = None
    if isinstance(acceptance_payload, dict):
        try:
            acceptance = TaskAcceptance(
                required_outputs=tuple(
                    acceptance_payload["required_outputs"]
                ),
                required_verifications=parse_required_verifications(
                    acceptance_payload["required_verifications"]
                ),
            )
            _validate_task_acceptance(acceptance)
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationConflict(
                "Completed acceptance operation is invalid"
            ) from exc
    try:
        return AcceptanceReviewResolution(
            kind=payload["kind"],
            reason=payload["reason"],
            instruction=payload["instruction"],
            acceptance=acceptance,
            decision=payload["decision"],
            reason_code=payload["reason_code"],
        )
    except (KeyError, TypeError) as exc:
        raise OperationConflict(
            "Completed acceptance operation is invalid"
        ) from exc


def _persisted_task_outcome_value(task: TeamTask) -> TaskOutcome:
    if not isinstance(task.outcome, dict):
        raise OperationConflict(
            "Acceptance operation requires a persisted outcome"
        )
    try:
        return parse_task_outcome(
            json.dumps(task.outcome, ensure_ascii=False, sort_keys=True)
        )
    except TaskOutcomeError as exc:
        raise OperationConflict(
            "Persisted task outcome is invalid"
        ) from exc


def _persisted_acceptance_value(task: TeamTask) -> AcceptanceResult:
    value = task.acceptance_result
    if not isinstance(value, dict):
        raise OperationConflict(
            "Acceptance operation requires a persisted rejection"
        )
    try:
        return AcceptanceResult(
            accepted=value["accepted"],
            status=value["status"],
            reason_code=value["reason_code"],
            evidence=value["evidence"],
        )
    except (KeyError, TypeError) as exc:
        raise OperationConflict(
            "Persisted acceptance result is invalid"
        ) from exc


def _operation_task_specs(
    operation: TeamModelOperation,
) -> list[dict[str, object]]:
    stored = operation.result_json
    if (
        not isinstance(stored, dict)
        or stored.get("kind") != "task_plan"
        or not isinstance(stored.get("payload"), dict)
        or not isinstance(stored["payload"].get("tasks"), list)
    ):
        raise OperationConflict("Completed planning operation is invalid")
    return stored["payload"]["tasks"]


def _operation_task_outcome(operation: TeamModelOperation) -> TaskOutcome:
    stored = operation.result_json
    if (
        not isinstance(stored, dict)
        or stored.get("kind") != "task_outcome"
        or not isinstance(stored.get("payload"), dict)
    ):
        raise OperationConflict("Completed Worker outcome is invalid")
    try:
        return parse_task_outcome(
            json.dumps(
                stored["payload"],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    except TaskOutcomeError as exc:
        raise OperationConflict("Completed Worker outcome is invalid") from exc


def _operation_worker_query(
    operation: TeamModelOperation,
) -> dict[str, str]:
    stored = operation.result_json
    payload = stored.get("payload") if isinstance(stored, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"topic", "question"}
        or not isinstance(payload["topic"], str)
        or not isinstance(payload["question"], str)
        or not payload["question"].strip()
    ):
        raise OperationConflict("Completed Worker query is invalid")
    return {
        "topic": payload["topic"],
        "question": payload["question"],
    }


def _worker_query_content(query: dict[str, str]) -> str:
    return (
        "```json\n"
        f"{json.dumps({'needs_info': query}, ensure_ascii=False)}\n"
        "```"
    )


def _operation_user_decision(
    operation: TeamModelOperation,
) -> dict[str, object]:
    stored = operation.result_json
    payload = stored.get("payload") if isinstance(stored, dict) else None
    if (
        not isinstance(payload, dict)
        or stored.get("kind") != "user_decision"
    ):
        raise OperationConflict("Completed user decision is invalid")
    return payload


def _operation_synthesis_summary(operation: TeamModelOperation) -> str:
    stored = operation.result_json
    payload = stored.get("payload") if isinstance(stored, dict) else None
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if (
        not isinstance(stored, dict)
        or stored.get("kind") != "synthesis"
        or not isinstance(summary, str)
        or not summary.strip()
    ):
        raise OperationConflict("Completed synthesis result is invalid")
    return summary


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


def _parse_task_plan(
    content: str,
    allowed_input_artifact_ids: set[str] | None = None,
    allowed_owner_agent_ids: set[str] | None = None,
) -> list[dict[str, object]]:
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
        required_fields = {
            "title",
            "description",
            "owner_agent_id",
            "required",
            "acceptance",
        }
        if not required_fields <= set(item) or set(item) - (
            required_fields
            | {"input_artifact_ids", "plan_task_id", "depends_on_task_ids"}
        ):
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
        if (
            isinstance(owner_agent_id, str)
            and allowed_owner_agent_ids is not None
            and owner_agent_id.strip() not in allowed_owner_agent_ids
        ):
            raise ValueError(
                "Planner task owner_agent_id was not one of the fixed team member IDs"
            )
        required = item.get("required")
        if not isinstance(required, bool):
            raise ValueError("Planner task required must be a boolean")
        plan_task_id = item.get("plan_task_id")
        if not isinstance(plan_task_id, str) or not plan_task_id.strip():
            raise ValueError("Planner task plan_task_id must be a non-empty string")
        depends_on_task_ids = item.get("depends_on_task_ids", [])
        if not isinstance(depends_on_task_ids, list) or any(
            not isinstance(dependency, str) or not dependency.strip()
            for dependency in depends_on_task_ids
        ):
            raise ValueError("Planner task depends_on_task_ids must be strings")
        depends_on_task_ids = [dependency.strip() for dependency in depends_on_task_ids]
        if len(set(depends_on_task_ids)) != len(depends_on_task_ids):
            raise ValueError("Planner task has duplicate dependencies")
        input_artifact_ids = item.get("input_artifact_ids", [])
        if not isinstance(input_artifact_ids, list) or any(
            not isinstance(artifact_id, str) or not artifact_id.strip()
            for artifact_id in input_artifact_ids
        ):
            raise ValueError("Planner task input_artifact_ids must be strings")
        input_artifact_ids = [artifact_id.strip() for artifact_id in input_artifact_ids]
        if len(set(input_artifact_ids)) != len(input_artifact_ids):
            raise ValueError("Planner task has duplicate input artifact IDs")
        if (
            allowed_input_artifact_ids is not None
            and not set(input_artifact_ids) <= allowed_input_artifact_ids
        ):
            raise ValueError("Planner task has unknown task input artifact")
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
        required_verifications = parse_required_verifications(
            acceptance.get("required_verifications")
        )
        if len(set(required_outputs)) != len(required_outputs):
            raise ValueError("Planner task has duplicate required outputs")
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
                "input_artifact_ids": input_artifact_ids,
                "plan_task_id": plan_task_id.strip(),
                "depends_on_task_ids": depends_on_task_ids,
                "acceptance": TaskAcceptance(
                    required_outputs=tuple(required_outputs),
                    required_verifications=required_verifications,
                ),
            }
        )
    _validate_task_plan_dependencies(tasks)
    return tasks


def _validate_task_plan_dependencies(tasks: list[dict[str, object]]) -> None:
    plan_ids = [task["plan_task_id"] for task in tasks if task["plan_task_id"]]
    if len(set(plan_ids)) != len(plan_ids):
        raise ValueError("Planner task plan_task_id values must be unique")
    plan_id_set = set(plan_ids)
    graph = {
        task["plan_task_id"]: set(task["depends_on_task_ids"])
        for task in tasks
        if task["plan_task_id"]
    }
    for plan_task_id, dependencies in graph.items():
        if plan_task_id in dependencies:
            raise ValueError("Planner task cannot depend on itself")
        if not dependencies <= plan_id_set:
            raise ValueError("Planner task has unknown dependency")
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(plan_task_id: str) -> None:
        if plan_task_id in visiting:
            raise ValueError("Planner task has dependency cycle")
        if plan_task_id in visited:
            return
        visiting.add(plan_task_id)
        for dependency in graph[plan_task_id]:
            visit(dependency)
        visiting.remove(plan_task_id)
        visited.add(plan_task_id)

    for plan_task_id in graph:
        visit(plan_task_id)


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
        and not windows.drive
        and not windows.anchor
        and ".." not in posix.parts
        and ".." not in windows.parts
    )


def _persisted_undeclared_paths(
    task: TeamTask,
    messages: list[TeamMessage],
) -> set[str]:
    paths: set[str] = set()
    if (
        task.acceptance_result
        and task.acceptance_result.get("reason_code") == "undeclared_deliverable"
        and task.outcome
    ):
        deliverables = task.outcome.get("deliverables")
        if isinstance(deliverables, list):
            paths.update(
                path
                for item in deliverables
                if isinstance(item, dict)
                and isinstance((path := item.get("path")), str)
                and path not in task.acceptance.required_outputs
            )

    for message in messages:
        metadata = message.metadata
        if (
            message.kind != "acceptance_review"
            or metadata.get("task_id") != task.id
            or metadata.get("reason_code") != "undeclared_deliverable"
        ):
            continue
        acceptance_before = metadata.get("acceptance_before")
        required_outputs = (
            acceptance_before.get("required_outputs", ())
            if isinstance(acceptance_before, dict)
            else ()
        )
        rejected = metadata.get("rejected_deliverables")
        if not isinstance(rejected, list):
            continue
        paths.update(
            path
            for path in rejected
            if isinstance(path, str) and path not in required_outputs
        )
    return paths


def _reject_lingering_undeclared_paths(
    acceptance: AcceptanceResult,
    rejected_paths: set[str],
    required_outputs: tuple[str, ...],
    working_root: Path,
) -> AcceptanceResult:
    if not acceptance.accepted:
        return acceptance
    remaining_paths = sorted(
        path
        for path in rejected_paths
        if path not in required_outputs
        and _bounded_path_exists(working_root, path)
    )
    if not remaining_paths:
        return acceptance
    return AcceptanceResult(
        accepted=False,
        status="failed",
        reason_code="undeclared_deliverable",
        evidence={"remaining_undeclared_paths": remaining_paths},
    )


def _bounded_path_exists(working_root: Path, relative_path: str) -> bool:
    if not _safe_relative_output(relative_path):
        return True
    try:
        root = working_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return True
    candidate = root / relative_path
    try:
        candidate.relative_to(root)
    except (OSError, ValueError):
        return True
    current = candidate
    while current != root:
        try:
            is_junction = getattr(current, "is_junction", lambda: False)
            if current.is_symlink() or is_junction():
                return True
        except (OSError, RuntimeError):
            return True
        parent = current.parent
        if parent == current:
            return True
        current = parent
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return True
    try:
        return candidate.exists()
    except (OSError, RuntimeError):
        return True


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
        required_verifications=parse_required_verifications(
            value.get("required_verifications")
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
