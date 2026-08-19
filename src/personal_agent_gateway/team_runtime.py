import asyncio
import hashlib
import json
import logging
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
from personal_agent_gateway.team_collaboration import (
    MENTION_BATCH_LIMIT,
    radio_block,
    roster_block,
)
from personal_agent_gateway.team_collaboration_service import (
    TeamCollaborationService,
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
    ContestOutcome,
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
    TERMINAL_RUN_STATUSES,
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
from personal_agent_gateway.team_plan_negotiation import (
    PlanReview,
    discarded_task_ids,
    next_revision,
    parse_plan_review,
    task_label,
    verdict_for,
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
    TeamPlanRevision,
    TeamRun,
    TeamRunService,
    TeamTask,
    _task_acceptance_json,
    _validate_task_acceptance,
    parse_required_verifications,
)

_LOGGER = logging.getLogger(__name__)

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
"checked":true,"status":"passed|failed","evidence":"concrete evidence"}}]}}
Set "checked":false with "status":null when you could not actually confirm a
verification -- a tool that is missing, a command that failed to run, a check you
had no way to perform -- and say why in "evidence". Do not report a status you did
not observe.

You may also include an optional "mentions" array to leave a short note for a
teammate: [{{"to":"roster label","text":"note, up to 2000 characters"}}]. Name
the recipient by its roster label; at most 10 mentions per response.

The same rule applies to what your result says, not just to its verifications.
When you state something as fact about this repository, name the file that shows
it, with the line when you can. If the assignment appears to take something for
granted that you could not confirm, say that instead of asserting it. A claim
nobody checked, written as fact, is worse than a stated gap: it reads as an
answer and cannot be told apart from one."""

ACCEPTANCE_REVIEW_PROMPT = f"""You are the leader reviewing a rejected Team Run task outcome.
Decide only from the goal, Cycle instruction, frozen rules, SPACE, Task contract,
outcome, failure reason, changed paths, history, and remaining attempts. The recovery
attempt cap is {ACCEPTANCE_RECOVERY_CAP}.

Prefer Worker correction when the contract is valid. Revise acceptance only when the
contract itself is wrong. Ask the user only for a consequential choice the Team cannot
infer. Never approve the current rejected outcome retroactively.

When the failure reason is undeclared_deliverable, files that the contract does
not list are counted against this task -- either declared by this outcome, or
left in the workspace by an earlier round. Keeping them means the contract was
too narrow: return revise_acceptance with required_outputs extended to include
every such path. Use retry_worker only to have the worker remove them, and name
every path to remove in the instruction -- retry_worker leaves the contract
unchanged, so without those paths the same rejection returns.

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

CONTEST_PROMPT = """You are the leader of a personal-agent-gateway Team Run.
The user is contesting the plan, not adding work to it. Judge the objection.
Goal: {goal}
Current tasks:
{tasks}
Earlier objections and how you ruled on them:
{history}
The user's objection:
{objection}

Return ONLY one JSON object:
{{"kind":"amend|partial|reject|ask_back","reason":"why you ruled this way",
"tasks":[{{"title":"...","description":"...","owner_agent_id":"member id or null",
"required":true,"acceptance":{{"required_outputs":["..."],"required_verifications":["..."]}}}}],
"question":"ask_back only","supersedes":[{{"document_path":"...","decision":"..."}}]}}
reason is required for every kind. amend and partial carry at least one task;
reject and ask_back carry none. If ruling for the user reverses a decision an
accepted document still states, list it in supersedes and include a task that
corrects that document -- a supersedes entry without a task is rejected.
If the objection is that a finished task's acceptance criteria were too narrow,
do not try to rewrite that task: create a follow-up task carrying the criteria
that should have been there. A settled task's contract cannot be revised."""

PLAN_REVIEW_PROMPT = """You are {agent_label} in a personal-agent-gateway Team Run.

The leader proposed the task plan below. Review it before any work starts.
You own the tasks marked YOURS.

Goal: {goal}

Plan:
{plan_block}

Report only these five kinds of problem:
- overlap: two tasks would do the same work or write the same file
- gap: the goal needs work that no task covers
- dependency_conflict: a task assumes something another task has not produced yet
- scope: a task assigned to you is not something you can carry out
- unverified_premise: the goal states something as fact and no task checks it
  before the answer would rely on it. Name the task that would rely on it.

Do not object to wording, ordering, or style. Approve a plan that can be carried
out and whose answer will rest on facts some task establishes. A plan you can
execute while stating unchecked claims as fact is not workable.

The final response must contain only this JSON object and no prose or code fences:
{{"decision":"approve|object","objections":[{{"kind":"overlap|gap|dependency_conflict|scope","task_ref":"T-01","detail":"what is wrong"}}]}}
Use "objections":[] when you approve. Every objection needs a task_ref from the
plan above and a concrete detail the leader can act on."""

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
_PLAN_REVIEW_REPAIR_INSTRUCTION = (
    "The previous response was invalid. Return ONLY the JSON object with the "
    'keys "decision" and "objections". Every objection needs exactly "kind", '
    '"task_ref" and "detail", and task_ref must be one of the T-xx labels in '
    "the plan above. No prose, no code fences."
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


class PinnedNotesUnavailable(RuntimeError):
    """이 호출에 이미 묶인 쪽지를 다시 읽지 못해 호출을 포기한다.

    강등(접두사 없이 그대로 보내기)은 아무것도 묶이지 않았음이 **확인된** 첫
    시도에서만 정직하다. 배달이 이미 열려 있거나, 열렸는지 확인조차 못한
    (delivery_for가 던진) 호출에서는 강등이 두 가지 금지된 결과 중 하나로
    끝난다 -- 프롬프트에 실린 적 없는 쪽지가 '전달됨'으로 굳는 조용한 유실,
    또는 원장이 거부하는 지문 불일치. 그래서 모델을 부르지 않고 이 예외로
    단계를 포기한다: 쪽지는 묶인 채 미전달로 남고 다음 시도가 같은 접두사를
    다시 만든다.
    """

    def __init__(self, operation_key: str) -> None:
        super().__init__(
            f"pinned peer notes could not be read for {operation_key}"
        )
        self.operation_key = operation_key


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


def undeclared_retry_is_futile(
    resolution: AcceptanceReviewResolution,
    extra_paths: frozenset[str],
) -> bool:
    """Can this resolution clear an undeclared_deliverable rejection?

    retry_worker leaves the contract untouched, so acceptance requires the next
    outcome to declare fewer files -- which the worker can only do if it is told
    which ones to drop. Naming them is therefore a floor, not a preference.

    A path counts as named when it appears in the instruction bounded by
    non-path characters (or string edges). Path characters are ASCII letters,
    digits, `.`, `_`, `-`, `/`, `\\`; anything else is a boundary. This handles
    parentheses, quotes, punctuation, and whitespace without text manipulation.
    Special case: when `.` immediately follows the match, look ahead; if the
    next character is a path character, the `.` continues the path (e.g.,
    `a/b.md.bak`); if it is a boundary or the string ends, the `.` is sentence
    punctuation and the occurrence counts as named.

    The rule does not establish whether prose actually instructs the worker to
    drop a file -- a leader could write "declare outputs/extra.md along with
    the others" (names the path, keeps it) and this rule lets it through:
    naming every extra is necessary, not sufficient. Comparison is case-sensitive,
    matching the gate's own set-difference logic in team_acceptance.py: on
    case-insensitive filesystems a leader naming the path with different case
    is scored as not having named it. Path separators are also compared
    literally: an extra `a\b.md` is not matched by an instruction naming
    `a/b.md`, since the conventions may differ.
    """
    if resolution.kind != "retry_worker" or not extra_paths:
        return False
    instruction = resolution.instruction or ""

    # Path continuation characters
    path_chars = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/\\"
    )

    def path_is_named(path: str) -> bool:
        """Check if path appears in instruction with boundaries on both sides."""
        pos = 0
        while True:
            pos = instruction.find(path, pos)
            if pos == -1:
                return False
            # Check boundary before
            if pos > 0 and instruction[pos - 1] in path_chars:
                pos += 1
                continue
            # Check boundary after
            end_pos = pos + len(path)
            if end_pos < len(instruction):
                char_after = instruction[end_pos]
                # Special case for trailing period: look ahead
                if char_after == ".":
                    # If the character after the period is a path char, the period
                    # continues the path (e.g., a/b.md.bak)
                    if end_pos + 1 < len(instruction):
                        if instruction[end_pos + 1] in path_chars:
                            pos += 1
                            continue
                    # Otherwise the period is punctuation, so this is a boundary
                elif char_after in path_chars:
                    pos += 1
                    continue
            # Both boundaries satisfied
            return True

    # Return True (futile) if any path is not named
    return any(not path_is_named(path) for path in extra_paths)


def _extra_deliverable_paths(
    outcome: TaskOutcome,
    acceptance: AcceptanceResult,
    required_outputs: tuple[str, ...],
) -> frozenset[str]:
    """Every out-of-contract path an undeclared_deliverable rejection can name.

    There are two ways such a rejection carries its extras:

    - A fresh rejection from TeamAcceptanceService.evaluate() names them
      through outcome.deliverables -- this round's declared paths minus the
      contract.
    - A lingering rejection re-stamped by _reject_lingering_undeclared_paths
      names them instead through evidence["remaining_undeclared_paths"],
      because the round that triggered it can have declared nothing at all
      (the worker stopped declaring the file; it just never deleted it).

    Unioning both means the caller does not have to know which mechanism
    produced this particular acceptance.

    This function does not check reason_code, and a non-empty result is NOT
    proof the rejection is actually about undeclared deliverables: evaluate()
    returns early on outcome.status != "completed" before it ever compares
    declared paths against the contract, so a blocked/failed outcome can
    declare a stray out-of-contract path while the real rejection reason is
    something else entirely (e.g. waiting_for_input). Callers MUST also check
    acceptance.reason_code == "undeclared_deliverable" before treating this
    result as meaningful.
    """
    extra = {
        item.path
        for item in outcome.deliverables
        if item.path not in required_outputs
    }
    lingering = acceptance.evidence.get("remaining_undeclared_paths")
    if isinstance(lingering, list):
        extra.update(path for path in lingering if isinstance(path, str))
    return frozenset(extra)


def _undeclared_retry_error_message(extra_paths: frozenset[str]) -> str:
    """What a refused resolution is told, at the ledger and in the escalation."""
    return (
        "retry_worker cannot clear undeclared_deliverable unless the "
        "instruction names every path to remove: " + ", ".join(sorted(extra_paths))
    )


class _AcceptanceReviewGuard:
    """One acceptance review's parser, its repair prompt, and what it refused.

    There are three acceptance-review invocation sites -- _run_cycle_acceptance,
    the prepared-operation branch of _recover_open_operation, and the
    ledger-free _review_acceptance -- and the futile-retry refusal has to apply
    at all of them. It first shipped as a closure inside _run_cycle_acceptance,
    which left the resume branch invoking the module-level
    _validated_acceptance_review: a run that crashed inside the review window
    silently reverted to accepting a retry that cannot succeed. Binding the
    three pieces to the task in one object is what makes the coverage
    checkable, since a site that builds a guard cannot pick up the parser
    without the prompt that answers it.
    """

    def __init__(
        self,
        task: TeamTask,
        messages: list[dict[str, object]],
        outcome: TaskOutcome | None = None,
        acceptance: AcceptanceResult | None = None,
    ) -> None:
        outcome = outcome or _persisted_task_outcome_value(task)
        acceptance = acceptance or _persisted_acceptance_value(task)
        self._messages = messages
        self._extra_paths = _extra_deliverable_paths(
            outcome, acceptance, task.acceptance.required_outputs
        )
        # _extra_deliverable_paths unions both sources this rejection can carry
        # its extras through -- outcome.deliverables for a fresh rejection,
        # evidence["remaining_undeclared_paths"] for one
        # _reject_lingering_undeclared_paths re-stamped -- but that union can be
        # non-empty even when the rejection is about something else entirely: a
        # blocked/failed outcome can still declare a stray out-of-contract path
        # while its real reason_code is unrelated (e.g. waiting_for_input). The
        # reason_code is what decides whether this rule applies at all;
        # extra_paths only says what to name if it does.
        self._applies = bool(self._extra_paths) and (
            acceptance.reason_code == "undeclared_deliverable"
        )
        self.refusal: str | None = None
        """Why the last parse refused, or None when it did not refuse.

        Both failures reach the ledger as invalid_structured_output, so nothing
        downstream can tell a resolution that parsed but cannot clear the
        rejection from output that would not parse at all. The repair prompt and
        the operator escalation both need that distinction, and both would
        otherwise state a falsehood about one of the two.
        """

    def parse(self, response: ModelResponse) -> ValidatedOperationResult:
        """Validate a review, refusing a retry that cannot clear the rejection."""
        self.refusal = None
        validated = _validated_acceptance_review(response)
        if self._applies:
            self.refuse_if_futile(
                _parse_acceptance_review_resolution(response.content)
            )
        return validated

    def refuse_if_futile(self, resolution: AcceptanceReviewResolution) -> None:
        """Raise when this resolution cannot clear an undeclared rejection.

        Separate from parse() for _review_acceptance, which has no ledger
        operation to hand a parser to and so parses the resolution itself.

        `from None` unconditionally: _review_acceptance's own retry calls this
        from inside an `except ValueError:` block, where the refusal would
        otherwise chain to the parse failure that triggered it. Elsewhere no
        exception is being handled and `from None` is a no-op.
        """
        if not self._applies:
            return
        if undeclared_retry_is_futile(resolution, self._extra_paths):
            self.refusal = _undeclared_retry_error_message(self._extra_paths)
            raise ValueError(self.refusal) from None

    def repair_messages(
        self,
        reason_code: str | None,
    ) -> list[dict[str, object]]:
        """The prompt that answers the failure that actually happened.

        Passed to the repair seam as a callable, not a list: the seam builds the
        prompt after the failure exists, which is the only moment the two causes
        are distinguishable. A static undeclared-specific prompt would tell a
        leader whose review was pure prose that its resolution cannot clear the
        rejection -- there was no resolution -- and would drop the "no prose or
        code fences" instruction that is what actually fixes prose.
        """
        if self.refusal is None:
            return _repair_messages(reason_code)
        return _undeclared_retry_repair_messages(
            self._messages, self._extra_paths
        )


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
        collaboration: TeamCollaborationService | None = None,
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
        self._collaboration = collaboration
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
        cause: str | None = None,
    ) -> None:
        """Pause rather than fail. A leader stage failing costs the whole run,
        and the work waiting for review is still good.

        Nothing is reserved for the retry. Two earlier designs tried to leave a
        prepared operation for resume to pick up: reusing the failed key returns
        the failed row, and using the next ordinal collides with the repair key
        of the next acceptance attempt. Resume does not need one -- the task is
        still in progress with its outcome persisted, so the acceptance flow
        re-enters at the next attempt on its own.

        `cause` describes a resolution that parsed and was refused anyway. Both
        failures reach the ledger as invalid_structured_output and the parser's
        message is not carried on the operation, so without it the one person
        who could widen the contract by hand is told the model emitted garbage.

        The pause is bounded by the recovery budget, and it has to be bounded
        here. The only other cap check on this path lives in _apply_task_outcome,
        which runs once when a worker outcome is applied -- and resume re-enters
        acceptance through _recover_applied_operation_chain, which trusts the
        next_stage stored back when the counter was still low. Before this guard
        a leader returning prose every round was escalated, answered, escalated
        again, with attempts climbing past the cap and the operator answering the
        same question forever.
        """
        if task is not None:
            current = self._teams.get_task(task.id)
            if current.acceptance_recovery_attempts >= ACCEPTANCE_RECOVERY_CAP:
                # Out of budget: stop asking and let the failed operation end the
                # task, which is what an unrecoverable acceptance failure does.
                return
            self._teams.consume_acceptance_attempt(task.id)
        where = f" on task '{task.title}'" if task is not None else ""
        if cause is None:
            topic = f"{stage} output could not be parsed"
            question = (
                f"The leader's {stage} response failed to parse twice{where}. "
                "The recorded failure shape is on the operation. Answer to retry "
                "it; use Stop to end the run instead."
            )
        else:
            topic = f"{stage} resolution cannot clear the rejection"
            question = (
                f"The leader's {stage} resolution was refused twice{where}: "
                f"{cause}. Answer to retry the review, or widen the task "
                "contract to cover those paths yourself; use Stop to end the "
                "run instead."
            )
        self._teams.raise_system_decision(
            run.id,
            cycle_id,
            topic=topic,
            question=question,
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
        # 예약 직전에 둔다. 이 검사보다 앞서면 그 흔한 OperationConflict가
        # 배달만 열어둔 채 예약 없이 나가고, 그 뒤 같은 키로 재진입하면
        # _with_radio가 배달을 보고 접두사를 재현해야 하는데 읽기가 실패하면
        # 접두사 없는 지문으로 예약되어 쪽지가 조용히 유실된다.
        messages, spec = self._with_radio(spec, agent, messages)
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

    def _with_radio(
        self,
        spec: OperationSpec,
        agent: TeamAgent,
        messages: list[dict[str, object]],
    ) -> tuple[list[dict[str, object]], OperationSpec]:
        """명단과 미전달 쪽지를 첫 메시지 앞에 붙이고 지문을 다시 계산한다.

        stage를 가리지 않는다: 목록을 만들면 새 stage에서 조용히 누락되고, 이
        저장소는 그 실패로 completeness 테스트를 두고 있다.

        프롬프트 템플릿에 자리를 만들지 않는 이유는 별개다 -- WORKER_PROMPT를
        정확히 네 키로 .format()하는 테스트가 있어(tests/test_team_runtime.py:3413)
        새 자리를 만들면 KeyError가 된다. 접두사로 붙이면 SPACE 정책 블록보다
        앞에 와서 마지막 말이 정책이 되는 배치까지 동시에 만족한다.
        """
        if self._collaboration is None or not messages:
            return messages, spec
        # 아래 except가 이 값으로 갈린다. 기본이 True인 이유: 첫 조회
        # (delivery_for)가 던지면 배달이 있는지 **알 수 없고**, 모르는 채로
        # 강등하면 배달이 실제로 있던 경우에 (a) 조용한 유실이나 (b) 지문
        # 충돌이 그대로 남는다. 그래서 판단이 서기 전까지는 묶인 쪽으로 센다.
        #
        # 대가를 분명히 해 둔다: 아무것도 묶이지 않은 진짜 첫 시도인데
        # delivery_for가 일시적으로 던지면, 예전에는 접두사 없이 성공했던
        # 그 호출이 이제 런을 실패시킨다. 재시도 가능한 실패를 성공한 호출과
        # 맞바꾸는 것이고, 이는 아래 pinned 분기가 이미 받아들인 거래를
        # "어느 쪽인지 알 수 없는 경우"까지 넓힌 것이다.
        pinned = True
        try:
            if self._collaboration.delivery_for(spec.operation_key) is not None:
                # 이미 확정된 호출이다. 쪽지가 0개였더라도 그 사실을 재현해야
                # 한다 -- 다시 조회하면 그 사이 도착한 쪽지가 섞여 지문이 달라지고
                # reserve가 거부한다.
                #
                # pinned는 위에서 이미 True다. 이 조회들(그리고 아래 명단 조회)이
                # 던지는 것이(측정된 5.5초 write-lock 경합이 그 경로다) 바로 이
                # 결함의 경로였다: 그때 강등하면 이미 쪽지를 묶은 호출이 접두사
                # 없이 나가 유실이거나 지문 충돌로 끝난다.
                notes = self._collaboration.notes_by_id(
                    spec.team_run_id,
                    self._collaboration.delivery_message_ids(spec.operation_key),
                )
            elif self._operations.get_by_key(spec.operation_key) is not None:
                # operation은 이미 있는데 배달은 없다: 이 기능이 배선되기 전에
                # 예약된 호출이다. 새로 붙이면 지문이 달라져 복구가 영구히
                # 막히므로, 접두사 없이 원래 요청을 재현한다.
                return messages, spec
            else:
                # 여기서 비로소 이 키에 묶인 것이 없음을 안다: 이 아래의 실패는
                # 접두사 없는 정직한 요청으로 강등해도 아무것도 잃지 않는다.
                pinned = False
                notes = self._collaboration.undelivered(spec.team_run_id, agent.id)[
                    :MENTION_BATCH_LIMIT
                ]
            prefix = roster_block(self._roster_entries(spec.team_run_id)) + radio_block(
                [(sender, text) for _, sender, text in notes]
            )
            if not prefix:
                return messages, spec
            if not pinned:
                # 접두사가 만들어진 **뒤에** 확정한다. 확정과 접두사 사이에서 무엇이
                # 던지면 아래 except가 접두사 없는 요청을 보내는데, 그 operation은
                # applied에 도달하고 _UNDELIVERED_SQL은 묶인 쪽지를 영구히 제외한다
                # -- 프롬프트에 실린 적 없는 쪽지가 '전달됨'으로 굳는 조용한 유실이다.
                self._collaboration.open_delivery(
                    spec.team_run_id,
                    agent.id,
                    spec.operation_key,
                    [note[0] for note in notes],
                )
        except Exception as exc:  # noqa: BLE001 - 곁다리가 런을 죽이지 않는다
            content = f"radio-lite disabled for this step: {exc}"
            try:
                self._teams.append_message(
                    spec.team_run_id,
                    None,
                    agent.id,
                    "collaboration_degraded",
                    content,
                    {"reason_code": "collaboration_unavailable"},
                )
            except Exception:  # noqa: BLE001 - 강등 기록이 호출을 죽이지 않는다
                # 이 쓰기도 위와 같은 이유로 실패할 수 있고, 놓아주면 곁다리의
                # 실패가 모델 호출 경로로 전파된다 -- radio 실패는 절대 전파되지
                # 않는다는 제약이 금하는 바로 그것이다.
                _LOGGER.warning(
                    "could not record degraded collaboration for run %s: %s",
                    spec.team_run_id,
                    content,
                    exc_info=True,
                )
            if pinned:
                # 양쪽이 다르게 끝나는 이유. 아무것도 묶이지 않은 것이 **확인된**
                # 첫 시도(branch 3, 그리고 반환으로 빠지는 branch 2)에서는 강등이
                # 정직하다: 접두사 없는 요청이 그 호출의
                # 진실이고, 쪽지는 그대로 미전달로 남고, 지문도 어긋나지 않는다.
                # 그러나 이 키에 배달이 이미 열린 재진입에서는 강등이 반드시 두
                # 금지된 결과 중 하나로 끝난다 -- (a) operation이 아직 없으면
                # 접두사 없는 지문으로 예약되어 applied까지 가고 _UNDELIVERED_SQL이
                # 그 쪽지를 영구히 제외한다(프롬프트에 실린 적 없는 쪽지가 '전달됨'
                # 으로 굳는 조용한 유실), (b) operation이 있으면 접두사 없는 지문이
                # _validate_existing_spec에 거부되어 OperationConflict가 이 try
                # 밖에서 런을 실패로 정리한다.
                # 그래서 계획의 "radio 실패는 절대 전파되지 않는다"를 이 한쪽에서만
                # 좁힌다: 유실 0인 재시도 가능한 실패가 쪽지 열 장을 먹은 완주보다
                # 낫다. 강등은 위에 이미 기록했고, 쪽지는 묶인 채 미전달로 남는다.
                raise PinnedNotesUnavailable(spec.operation_key) from exc
            return messages, spec
        head, *rest = messages
        amended = [{**head, "content": prefix + str(head["content"])}, *rest]
        return amended, replace(
            spec,
            request_digest=_operation_request_digest(
                spec.stage, spec.stage_ordinal, agent.id, amended
            ),
        )

    def _roster_entries(self, team_run_id: str) -> list[tuple[str, str]]:
        labels = self._collaboration.labels_for_run(team_run_id)
        by_agent = {agent.id: agent for agent in self._teams.list_agents(team_run_id)}
        return [
            (label, str(by_agent[agent_id].persona_snapshot.get("name", "")))
            for label, agent_id in sorted(labels.items())
            if agent_id in by_agent
        ]

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
                # A prepared review is the third acceptance-review invocation
                # site, and it has to apply the futile-retry refusal like the
                # other two. Invoking the module-level parser here made a crash
                # or provider park inside the review window the one condition
                # under which a resolution that cannot clear the rejection was
                # applied anyway -- silently, and only on the runs that
                # hiccuped.
                guard = _AcceptanceReviewGuard(task, messages)
                acceptance_task = task

                async def escalate(failed: TeamModelOperation) -> None:
                    await self._escalate_unparsable_lead_output(
                        run,
                        cycle_id,
                        acceptance_task,
                        "acceptance_lead",
                        leader,
                        failed,
                        cause=guard.refusal,
                    )

                try:
                    recovered = await self._invoke_existing_operation(
                        operation,
                        leader,
                        messages,
                        guard.parse,
                    )
                except InvalidOperationResult as exc:
                    failed_review = self._operations.get(exc.operation_id)
                    if operation.stage == "acceptance_lead_repair":
                        # The repair round itself is what failed, so the seam
                        # has no second ask left to offer: pause on the operator
                        # exactly as the seam's own on_exhausted would.
                        await escalate(failed_review)
                    recovered = await self._repair_operation(
                        run.id,
                        cycle_id,
                        leader,
                        "acceptance_lead",
                        failed_review,
                        guard.parse,
                        repair_messages=guard.repair_messages,
                        on_exhausted=escalate,
                        task_id=task.id,
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

        if operation.stage in {"cycle_contest", "cycle_contest_repair"}:
            recovered = operation
            if operation.status == "prepared":
                if operation.stage == "cycle_contest":
                    objection = (
                        self._teams.get_cycle_effective_instruction(cycle_id)
                        or self._teams.get_cycle_objective(cycle_id)
                    )
                    if objection is None:
                        raise OperationConflict(
                            "Prepared contest operation has no persisted objection"
                        )
                    messages = self._contest_messages(
                        run, leader, cycle_id, objection
                    )
                else:
                    failed_operation = self._operations.get_by_key(
                        _operation_key(
                            cycle_id,
                            "cycle_contest",
                            operation.stage_ordinal,
                        )
                    )
                    if (
                        failed_operation is None
                        or failed_operation.status != "failed"
                    ):
                        raise OperationConflict(
                            "Contest repair has no failed source operation"
                        )
                    messages = _contest_repair_messages(
                        failed_operation.reason_code
                    )
                recovered = await self._invoke_existing_operation(
                    operation,
                    leader,
                    messages,
                    _validated_contest_verdict,
                )
            return OpenOperationRecovery(
                recovered,
                await self._apply_contest_outcome(run, cycle_id, recovered.id),
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

    def _contest_messages(
        self,
        run: TeamRun,
        leader: TeamAgent,
        cycle_id: str,
        objection: str,
    ) -> list[dict[str, object]]:
        # The whole run, not this cycle: a contest always owns a fresh cycle, so
        # scoping the list to cycle_id made it unconditionally empty and asked
        # the leader to judge coverage with no plan in front of it. The
        # predictable answer to "does any task own this?" with an empty list is
        # "no", which granted every objection and made reject unreachable.
        # required_outputs comes along because the prompt tells the leader to
        # judge whether a finished task's acceptance criteria were too narrow,
        # and the criteria are exactly what that needs.
        tasks = (
            "\n".join(
                f"- [{task.status}] {task.title}"
                f" (required_outputs: "
                f"{', '.join(task.acceptance.required_outputs) or 'none'})"
                for task in self._teams.list_tasks(run.id)
            )
            or "(none)"
        )
        history = _contest_history_text(self._teams.list_messages(run.id))
        prompt = CONTEST_PROMPT.format(
            goal=self._goal_context(run, cycle_id),
            tasks=tasks,
            history=history,
            objection=objection,
        )
        return [{"role": "user", "content": prompt}]

    async def _apply_contest_outcome(
        self,
        run: TeamRun,
        cycle_id: str,
        operation_id: str,
    ) -> ContestOutcome:
        outcome = self._model_effects.apply_contest_verdict(operation_id)
        # Same announcement _plan and add_work make: a task the UI never hears
        # about only appears on the next poll, and an amend's tasks are the
        # whole visible result of the verdict.
        for task in outcome.tasks:
            await self._publish(
                {
                    "type": "team.task.created",
                    "team_run_id": run.id,
                    "task_id": task.id,
                }
            )
        if outcome.kind == "ask_back":
            self._teams.raise_system_decision(
                run.id,
                cycle_id,
                topic="Contest objection is ambiguous",
                question=outcome.question or "",
            )
        return outcome

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

    def _close_collaboration(self, run: TeamRun) -> TeamRun:
        """런이 끝났으면 못 전한 쪽지 수를 남긴다.

        조용히 사라지면 "유실 0"을 확인할 방법이 없다. 실패해도 종료를 막지
        않는다 -- 곁다리 기능이 런의 마무리를 붙잡으면 안 된다.
        """
        if self._collaboration is None or run.status not in TERMINAL_RUN_STATUSES:
            return run
        if run.lifecycle_mode == "continuous" and run.status == "completed":
            # 연속 런은 사이클마다 completed를 지난다. 다음 사이클이 전달할 쪽지를
            # 매번 미전달로 적으면 그 기록은 소음이 되고, 소음이 된 기록은 읽히지
            # 않는다.
            return run
        try:
            already_recorded = any(
                message.kind == "collaboration_undelivered"
                for message in self._teams.list_messages(run.id)
            )
            pending = self._collaboration.undelivered_count(run.id)
            if pending and not already_recorded:
                # At most one collaboration_undelivered message per run:
                # start()/resume()/settle_contest() can each close the same
                # run (resume delegating into start, settle_contest called
                # again on an already-closed cycle), and a second identical
                # message would be exactly the noise the continuous-run guard
                # above exists to prevent.
                self._teams.append_message(
                    run.id,
                    None,
                    None,
                    "collaboration_undelivered",
                    f"{pending} peer notes were never delivered",
                    {"count": pending},
                )
        except Exception as exc:  # noqa: BLE001 - 곁다리가 런을 죽이지 않는다
            content = f"collaboration close failed for this run: {exc}"
            try:
                self._teams.append_message(
                    run.id,
                    None,
                    None,
                    "collaboration_degraded",
                    content,
                    {"reason_code": "collaboration_unavailable"},
                )
            except Exception:  # noqa: BLE001 - 강등 기록이 런을 죽이지 않는다
                # 이 쓰기도 위와 같은 이유로 실패할 수 있고, 놓아주면 곁다리의
                # 실패가 종료 경로로 전파된다.
                _LOGGER.warning(
                    "could not record degraded collaboration close for run %s: %s",
                    run.id,
                    content,
                    exc_info=True,
                )
        return run

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
                return self._close_collaboration(
                    await self._publish_user_decision_request(run, cycle_id)
                )

            run = self._teams.get_team_run(run.id)
            if run.run_mode != "plan_and_execute":
                self._teams.set_agent_status(leader.id, "completed")
                run = self._teams.set_run_status(run.id, "completed")
                if cycle_id is not None:
                    self._teams.set_cycle_status(cycle_id, "completed")
                self._package_results(run, leader, cycle_id)
                await self._publish({"type": "team.run.completed", "team_run_id": run.id})
                return self._close_collaboration(run)

            workers = _find_workers(self._teams.list_agents(run.id))
            if not workers:
                error = "plan_and_execute run has no worker agents (empty member_persona_ids)"
                run = self._settle_failed(run, error, cycle_id)
                await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
                return self._close_collaboration(run)

            # Opt-in. A run without the flag reaches execution exactly as it did
            # before, with no revision row and no extra model call. The
            # cycle_id guard is deliberate: the cycle-less planning branch
            # bypasses the operation ledger, so negotiation there would not
            # survive a restart.
            if run.plan_negotiation_enabled and cycle_id is not None:
                if not await self._negotiate_plan(run, leader, workers, cycle_id):
                    return self._close_collaboration(self._teams.get_team_run(run.id))
                run = self._teams.get_team_run(run.id)

            run = self._teams.set_run_status(run.id, "running")
            await self._publish({"type": "team.run.executing", "team_run_id": run.id})
            return self._close_collaboration(
                await self._execute_and_synthesize(run, leader, workers, cycle_id)
            )
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except UnparsableLeadOutput:
            return self._close_collaboration(self._teams.get_team_run(run.id))
        except (ProviderOperationWaiting, AmbiguousModelOperation):
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._settle_failed(run, error, cycle_id)
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return self._close_collaboration(run)

    def _planning_prompt(
        self, run: TeamRun, leader_agent: TeamAgent, cycle_id: str | None
    ) -> str:
        """The prompt the leader plans from.

        Extracted from _plan so that a replan after an objection asks the same
        question with the objections appended, rather than a second prompt that
        would drift away from this one.
        """
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
            team_roster_json=_assignment_roster_json(
                _find_workers(self._teams.list_agents(run.id))
            ),
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
        return prompt

    async def _apply_plan_operation(
        self, run: TeamRun, operation: TeamModelOperation
    ) -> list[TeamTask]:
        """Turn a completed planning operation into tasks and announce them."""
        created_tasks = self._model_effects.apply_plan(operation.id)
        for created in created_tasks:
            await self._publish(
                {
                    "type": "team.task.created",
                    "team_run_id": run.id,
                    "task_id": created.id,
                }
            )
        return created_tasks

    async def _plan(
        self, run: TeamRun, leader: TeamAgent, cycle_id: str | None = None
    ) -> list[dict[str, object]] | UserDecisionResolution:
        leader_agent = self._teams.get_agent(leader.id)
        members = _find_workers(self._teams.list_agents(run.id))
        member_ids = {member.id for member in members}
        prompt = self._planning_prompt(run, leader_agent, cycle_id)
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        if cycle_id is not None:
            operation = await self._invoke_plan_with_repair(
                run,
                cycle_id,
                leader_agent,
                "cycle_planning",
                messages,
            )
            await self._apply_plan_operation(run, operation)
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

    async def _negotiate_plan(
        self,
        run: TeamRun,
        leader: TeamAgent,
        workers: list[TeamAgent],
        cycle_id: str,
    ) -> bool:
        """Hold the plan until its owners agree, or end the run without executing.

        Returns True when execution may proceed. Returns False only after
        ``_abandon_negotiation`` has already settled the run, so the caller
        returns immediately instead of deciding the terminal status a second
        time.
        """
        while True:
            await self._resume_interrupted_replan(run, leader, cycle_id)
            tasks = _plan_under_review(
                self._teams.list_tasks(run.id, cycle_id)
            )
            # Resume continues the open revision. Creating a second one here is
            # how a restart would refresh the budget.
            revision = self._teams.get_active_plan_revision(run.id, cycle_id)
            if revision is None and not tasks:
                # A restart between superseding a revision and reserving its
                # replan leaves the cycle with no live plan and nothing open to
                # finish. Opening a revision over zero tasks would ask the
                # owners to approve nothing, so the replan is reissued from the
                # objections instead -- they are still on the ledger, and the
                # budget the crash did not spend is still there.
                revisions = self._teams.list_plan_revisions(run.id, cycle_id)
                superseded = [
                    candidate
                    for candidate in revisions
                    if candidate.status == "superseded"
                ]
                if superseded and next_revision(revisions[-1].revision) is not None:
                    await self._replan_after_objections(
                        run, leader, superseded[-1], cycle_id
                    )
                    continue
                if revisions:
                    # Every revision this cycle could hold was proposed and
                    # refused, so the reason code is the true one.
                    await self._abandon_negotiation(run, leader, cycle_id, tasks)
                    return False
                # No plan was ever proposed to anyone, so no approval is
                # missing. Saying otherwise would send the operator looking
                # for a negotiation that never happened.
                raise LifecycleIntegrityError(
                    "Plan negotiation was reached with no plan to review"
                )
            if revision is None:
                approvers = [
                    worker.id
                    for worker in _find_workers(self._teams.list_agents(run.id))
                    if worker.status not in {"failed", "canceled"}
                ]
                if not approvers:
                    return True
                # Checked here rather than per reviewer: the ordinal scheme is
                # what cannot express approver 100, and finding that out inside
                # the review loop would first open a revision and spend a
                # hundred real model calls.
                _require_keyable_approvers(approvers)
                revision = self._teams.create_plan_revision(
                    run.id, cycle_id, [task.id for task in tasks], approvers
                )
            if revision is None:
                await self._abandon_negotiation(run, leader, cycle_id, tasks)
                return False

            labels = _plan_labels(tasks)
            self._teams.append_message(
                run.id,
                leader.id,
                None,
                "plan_proposed",
                f"Plan revision {revision.revision} with {len(tasks)} tasks.",
                {"revision": revision.revision, "labels": sorted(labels)},
                cycle_id=cycle_id,
            )

            reviewed = self._teams.plan_reviews(revision.id)
            for index, agent_id in enumerate(revision.required_approver_agent_ids):
                if agent_id in reviewed:
                    continue  # already answered; re-asking can flip an approval
                review = await self._review_plan(
                    run, revision, agent_id, index, labels, cycle_id
                )
                if review is None:
                    break  # unparsable after repair: not consent, stop asking
                self._teams.append_message(
                    run.id,
                    agent_id,
                    leader.id,
                    "plan_reviewed",
                    review.decision,
                    {
                        "revision": revision.revision,
                        "objections": [asdict(o) for o in review.objections],
                    },
                    cycle_id=cycle_id,
                )
                if review.decision == "object":
                    break  # the revision is already dead

            verdict = verdict_for(
                revision.required_approver_agent_ids,
                self._teams.plan_reviews(revision.id),
            )
            if verdict == "approved":
                self._teams.set_plan_revision_status(revision.id, "approved")
                return True

            # "waiting" reaches here only when a review would not parse, which
            # is not consent -- so it is treated the same as an objection.
            if next_revision(revision.revision) is None:
                # Out of budget. This revision is abandoned rather than
                # superseded: nothing is going to replace it. The same rule
                # create_plan_revision enforces is asked here, one step
                # earlier, so the run does not spend a replan it cannot use.
                await self._abandon_negotiation(run, leader, cycle_id, tasks)
                return False
            self._teams.set_plan_revision_status(revision.id, "superseded")
            for task in tasks:
                if task.status == "pending":
                    self._teams.set_task_status(task.id, "canceled")
            await self._replan_after_objections(run, leader, revision, cycle_id)

    async def _review_plan(
        self,
        run: TeamRun,
        revision: TeamPlanRevision,
        agent_id: str,
        approver_index: int,
        labels: dict[str, TeamTask],
        cycle_id: str,
    ) -> PlanReview | None:
        """Ask one owner to review the plan. None means it could not be read.

        The verdict is recorded and the operation closed by a single effect, so
        there is no state in which the run holds a review nobody can see. A
        resume re-enters an already-applied operation and replays the stored
        verdict instead of asking the model a question it already answered.
        """
        agent = self._teams.get_agent(agent_id)
        messages = self._plan_review_messages(run, agent, labels, cycle_id)
        spec = _operation_spec(
            run,
            cycle_id,
            agent,
            "cycle_plan_review",
            _plan_review_ordinal(revision.revision, approver_index),
            messages,
        )

        def parse(response: ModelResponse) -> ValidatedOperationResult:
            return _validated_plan_review(response, frozenset(labels))

        repair = self._open_review_repair(cycle_id, spec)
        try:
            if repair is None:
                operation = await self._invoke_with_repair(
                    spec,
                    agent,
                    messages,
                    parse,
                    repair_messages=_plan_review_repair_messages(messages),
                )
            else:
                # A restart caught the repair round. The repair reuses this
                # ordinal under its own stage name, so reserving the base spec
                # first would find an open operation whose key differs and
                # raise "Cycle already has an open model operation" -- on this
                # resume and on every resume after it, with nothing ever
                # draining the operation. Enter at the repair instead.
                operation = await self._repair_operation(
                    run.id,
                    cycle_id,
                    agent,
                    "cycle_plan_review",
                    repair,
                    parse,
                    repair_messages=_plan_review_repair_messages(messages),
                )
        except InvalidOperationResult:
            return None
        review = self._model_effects.apply_plan_review(operation.id, revision.id)
        # The reviewer keeps the session it reviewed in, the same way every
        # effect in TeamModelEffectService promotes its actor's session. The
        # reviewer is the agent that will later be asked to do this work, so
        # the alternative -- dropping it -- would open one upstream session per
        # reviewer per revision that is never written back and never reused,
        # and would cost the worker the context in which it read the plan.
        if operation.upstream_session_id is not None:
            self._teams.set_agent_session(agent_id, operation.upstream_session_id)
        return review

    def _open_review_repair(
        self, cycle_id: str, spec: OperationSpec
    ) -> TeamModelOperation | None:
        """The failed review this resume has to re-enter through its repair.

        Returns the *base* operation, which is what _repair_operation needs;
        None when nothing is open or what is open is this review's own base
        operation, which reserving handles by itself.
        """
        operation = self._operations.get_open_for_cycle(cycle_id)
        if (
            operation is None
            or operation.stage != repair_stage_for("cycle_plan_review")
            or operation.stage_ordinal != spec.stage_ordinal
        ):
            return None
        failed = self._operations.get_by_key(spec.operation_key)
        if failed is None or failed.status != "failed":
            return None
        return failed

    def _plan_review_messages(
        self,
        run: TeamRun,
        agent: TeamAgent,
        labels: dict[str, TeamTask],
        cycle_id: str,
    ) -> list[dict[str, object]]:
        names = {
            member.id: member.name
            for member in self._teams.list_agents(run.id)
        }
        plan_block = "\n".join(
            f"{label} "
            f"[{_plan_owner_label(task, agent.id, names)}] "
            f"{task.title} — {task.description}"
            for label, task in labels.items()
        )
        prompt = PLAN_REVIEW_PROMPT.format(
            agent_label=agent.name,
            goal=self._goal_context(run, cycle_id),
            plan_block=plan_block,
        )
        return [{"role": "user", "content": prompt}]

    async def _replan_after_objections(
        self,
        run: TeamRun,
        leader: TeamAgent,
        revision: TeamPlanRevision,
        cycle_id: str,
    ) -> None:
        """Plan again, with the reasons the last plan was refused."""
        leader_agent = self._teams.get_agent(leader.id)
        prompt = (
            self._planning_prompt(run, leader_agent, cycle_id)
            + self._objection_block(revision)
        )
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        member_ids = {
            member.id for member in _find_workers(self._teams.list_agents(run.id))
        }

        def validate_plan(response: ModelResponse) -> ValidatedOperationResult:
            return _validated_task_plan(
                response,
                allowed_owner_agent_ids=member_ids,
            )

        ordinal, repair_ordinal = _replan_ordinals(revision.revision)
        spec = _operation_spec(
            run,
            cycle_id,
            leader_agent,
            "cycle_planning",
            ordinal,
            messages,
        )
        operation = await self._invoke_with_repair(
            spec,
            leader_agent,
            messages,
            validate_plan,
            repair_messages=_planning_repair_messages(messages),
            repair_ordinal=repair_ordinal,
        )
        await self._apply_plan_operation(run, operation)

    async def _resume_interrupted_replan(
        self,
        run: TeamRun,
        leader: TeamAgent,
        cycle_id: str,
    ) -> None:
        """Finish a replan a restart caught mid-flight.

        A replan runs as cycle_planning at ordinal 20 or 30, which
        _recover_open_operation's planning branch cannot read: it takes
        planning to mean ordinal 0, with 1 or 2 for a repair, so it refuses an
        interrupted replan with "Open planning operation requires its source
        request". The request is not missing -- it is the superseded revision's
        objections, which are on the ledger -- so it is reissued here, before
        the loop decides whether a revision needs creating.

        Reissuing means calling _replan_after_objections again. It builds the
        same operation key, so the ledger returns this very operation: a
        prepared one is invoked with its repair round intact, and a completed
        one is applied as it stands. Nothing is asked twice.
        """
        operation = self._operations.get_open_for_cycle(cycle_id)
        if operation is None or operation.stage not in {
            "cycle_planning",
            "cycle_planning_repair",
        }:
            return
        superseded = _replan_source_revision(
            operation.stage, operation.stage_ordinal
        )
        if superseded is None:
            return  # the first plan, which _invoke_plan_with_repair recovers
        if operation.stage != "cycle_planning" or operation.status not in {
            "prepared",
            "completed",
        }:
            # An interrupted call, or a repair round whose own source response
            # is not on this path. Neither can be re-asked from here, but the
            # message should at least name the replan instead of describing it
            # as a first plan with a missing request.
            raise OperationConflict(
                f"Replan of plan revision {superseded} was interrupted "
                f"({operation.stage} {operation.status}) and cannot be "
                "reissued from here"
            )
        revision = next(
            (
                candidate
                for candidate in self._teams.list_plan_revisions(
                    run.id, cycle_id
                )
                if candidate.revision == superseded
            ),
            None,
        )
        if revision is None:
            raise OperationConflict(
                "Open replan operation has no superseded plan revision"
            )
        await self._replan_after_objections(run, leader, revision, cycle_id)

    def _objection_block(self, revision: TeamPlanRevision) -> str:
        names = {
            member.id: member.name
            for member in self._teams.list_agents(revision.team_run_id)
        }
        lines = [
            f"- {objection['task_ref']} ({objection['kind']}), raised by "
            f"{names.get(agent_id, agent_id)}: {objection['detail']}"
            for agent_id, objections in (
                self._teams.plan_review_objections(revision.id).items()
            )
            for objection in objections
        ]
        if not lines:
            # A revision can also die because a reviewer's answer could not be
            # read. Saying so beats replanning with no explanation at all.
            lines = [
                "- One owner could not return a usable review of the plan."
            ]
        return (
            f"\n\nRevision {revision.revision} of this plan was refused by the "
            "agents who own it. Produce a new complete plan that resolves every "
            "point below. Do not simply restate the previous plan.\n"
            + "\n".join(lines)
        )

    async def _abandon_negotiation(
        self,
        run: TeamRun,
        leader: TeamAgent,
        cycle_id: str,
        tasks: list[TeamTask],
    ) -> None:
        """End the run without executing an unapproved plan.

        The status is set, not derived: cycle_execution_disposition would call a
        run whose required tasks are canceled `failed`, and the design requires
        completed_with_failures with a reason the operator can act on.

        Everything else here is what every other terminal path in this class
        does. Nobody failed -- the team refused a plan, which is the feature
        working -- so the agents settle as `completed`, and the run is packaged
        like any other finished run so the delivery and UI layers read "nothing
        was produced" rather than "the package is missing".
        """
        for task in tasks:
            if task.status == "pending":
                self._teams.set_task_status(task.id, "canceled")
        active = self._teams.get_active_plan_revision(run.id, cycle_id)
        if active is not None:
            self._teams.set_plan_revision_status(active.id, "abandoned")
        for agent in self._teams.list_agents(run.id):
            if agent.status == "running":
                self._teams.set_agent_status(agent.id, "completed")
        self._teams.set_cycle_status(cycle_id, "completed_with_failures")
        run = self._teams.set_run_status(
            run.id,
            "completed_with_failures",
            error_message="collaboration_plan_approval_incomplete",
        )
        self._package_results(run, leader, cycle_id)
        await self._publish(
            {"type": "team.run.completed", "team_run_id": run.id}
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
                        "cycle_contest",
                        "cycle_contest_repair",
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
        guard = _AcceptanceReviewGuard(task, messages)

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
            guard.parse,
            repair_messages=guard.repair_messages,
            on_exhausted=lambda failed: self._escalate_unparsable_lead_output(
                run,
                task.cycle_id,
                task,
                "acceptance_lead",
                leader_agent,
                failed,
                cause=guard.refusal,
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
        guard = _AcceptanceReviewGuard(task, messages, outcome, acceptance)
        response = await model.complete(messages)
        if response.upstream_session_id:
            self._teams.set_agent_session(
                leader_agent.id, response.upstream_session_id
            )
        # This path has no ledger operation and no repair seam: its own
        # `except ValueError` below is the retry, reused here so a futile
        # retry_worker resolution gets the same second chance an unparseable
        # response already gets.
        try:
            resolution = _parse_acceptance_review_resolution(response.content)
            guard.refuse_if_futile(resolution)
            return resolution
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
            resolution = _parse_acceptance_review_resolution(retry.content)
            guard.refuse_if_futile(resolution)
            return resolution

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
            tasks = self._live_plan_tasks(
                run, cycle_id, self._teams.list_tasks(run.id, cycle_id)
            )
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

    def _live_plan_tasks(
        self,
        run: TeamRun,
        cycle_id: str | None,
        tasks: list[TeamTask],
    ) -> list[TeamTask]:
        """The cycle's tasks with every discarded proposal dropped.

        A superseded revision leaves its tasks behind as canceled rows, and
        cycle_execution_disposition reads a canceled *required* task as
        terminal `failed`. So a negotiation that worked -- a plan every owner
        approved, every task of which completed -- reported the run as failed,
        because the plan nobody agreed to was still being counted.

        Only tasks a discarded revision proposed are dropped, and only when no
        surviving revision also lists them. Work added to the cycle afterwards
        sits on no revision at all and keeps deciding the outcome, which is
        what add_work's own failures depend on.
        """
        if cycle_id is None:
            return tasks
        discarded = discarded_task_ids(
            (revision.status, revision.task_ids)
            for revision in self._teams.list_plan_revisions(run.id, cycle_id)
        )
        if not discarded:
            return tasks
        return [task for task in tasks if task.id not in discarded]

    def _plan_awaits_negotiation(self, run: TeamRun, cycle_id: str) -> bool:
        """Whether this cycle's plan still has no approval behind it.

        Asked of the stored revisions, because that is the only record a
        restarted process has. Anything other than an approved revision --
        none at all, one still awaiting approval, or a negotiation that ended
        without one -- means execution must not start.
        """
        return not any(
            revision.status == "approved"
            for revision in self._teams.list_plan_revisions(run.id, cycle_id)
        )

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
        open_operation: TeamModelOperation | None = None
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
            if cycle_id is not None and open_operation is not None and (
                open_operation.stage
                in {"cycle_contest", "cycle_contest_repair"}
            ):
                # Not wrapped here: the success paths out of this helper
                # return through resume() or settle_contest(), which close
                # collaboration themselves -- wrapping again would record the
                # same pending count twice.
                return await self._resume_zero_task_contest(run, cycle_id)
            # start() closes collaboration on its own way out.
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
                return self._close_collaboration(run)
            # Negotiation runs after the tasks exist, so the empty-task
            # delegation to start() above never covers it: without this, a
            # restart mid-negotiation walked straight into execution with an
            # unapproved plan and reported success. The same three conditions
            # start() applies, plus the stored revisions -- the question is
            # whether this cycle's plan was ever approved, not whether this
            # process happened to ask.
            if (
                run.plan_negotiation_enabled
                and cycle_id is not None
                and run.run_mode == "plan_and_execute"
                and self._plan_awaits_negotiation(run, cycle_id)
            ):
                if not await self._negotiate_plan(run, leader, workers, cycle_id):
                    # Settled already, and settled explicitly:
                    # _execute_and_synthesize would re-derive `failed` from the
                    # canceled tasks and lose the reason code.
                    return self._close_collaboration(self._teams.get_team_run(run.id))
                run = self._teams.get_team_run(run.id)
            return self._close_collaboration(
                await self._execute_and_synthesize(run, leader, workers, cycle_id)
            )
        except asyncio.CancelledError:
            if run is not None:
                self._settle_canceled(run, cycle_id)
            raise
        except UnparsableLeadOutput:
            # The escalation already published the decision request and moved the
            # run to waiting_for_user. Return that state rather than failing.
            return self._close_collaboration(self._teams.get_team_run(run.id))
        except (ProviderOperationWaiting, AmbiguousModelOperation):
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._settle_failed(run, error, cycle_id)
            await self._publish({"type": "team.run.failed", "team_run_id": run.id, "error": error})
            return self._close_collaboration(run)

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

    async def adjudicate_contest(
        self, team_run_id: str, cycle_id: str, objection: str
    ) -> ContestOutcome:
        run = self._teams.get_team_run(team_run_id)
        # A contest is the cycle doing work, so it owns its own activation the
        # way start/resume do -- callers (Task 9's orchestrator) invoke this
        # before resume(), so nothing else has put the cycle or the run into an
        # active status yet. Activation below is unconditional otherwise, so
        # these four statuses have to be refused first, mirroring add_work's
        # API-level guard (api/team_runs.py) exactly: a run that never started,
        # or that is already paused for an unrelated decision or recovery,
        # must not be forced to "running" and stepped over. Every other
        # terminal status (e.g. a settled previous cycle) is reopenable, per
        # that same guard's own comment. waiting_for_user/interrupted are
        # believed unreachable here today -- team_cycles._pause_cycle never
        # resolves a paused cycle's request out of "dispatching", and
        # claim_next refuses to claim a new request while one is still
        # dispatching for the run, so a distinct queued cycle should never
        # coexist with a run paused by a different one. This is defensive
        # insurance against that wiring changing, not a path exercised today.
        if run.status in {"draft", "interrupted", "waiting_for_user", "canceled"}:
            raise OperationConflict(
                f"Team run status '{run.status}' cannot be contested"
            )
        self._activate_cycle(cycle_id)
        run = self._teams.set_run_status(run.id, "running")
        leader = _find_leader(self._teams.list_agents(run.id))
        leader_agent = self._teams.get_agent(leader.id)
        messages = self._contest_messages(run, leader_agent, cycle_id, objection)
        # Counted rather than hard-coded, but 0 is the only value it can take
        # today: an objection is enqueued as its own cycle request and so always
        # gets a fresh cycle, which cannot already hold a cycle_contest. The
        # count exists so that a second contest on one cycle -- if the queue
        # ever allows it -- gets its own ordinal instead of colliding.
        ordinal = sum(
            1
            for operation in self._operations.list_for_cycle(cycle_id)
            if operation.stage == "cycle_contest"
        )
        try:
            operation = await self._invoke_with_repair(
                _operation_spec(
                    run,
                    cycle_id,
                    leader_agent,
                    "cycle_contest",
                    ordinal,
                    messages,
                ),
                leader_agent,
                messages,
                _validated_contest_verdict,
                repair_messages=_contest_repair_messages,
            )
            return await self._apply_contest_outcome(run, cycle_id, operation.id)
        except asyncio.CancelledError:
            # Every other cycle path settles what it activated before the
            # cancel propagates; without this the cycle and run this method
            # just moved to "running" are left there for the dispatcher to
            # relabel "interrupted" instead of "canceled".
            self._settle_canceled(run, cycle_id)
            raise

    async def settle_contest(self, team_run_id: str, cycle_id: str) -> TeamRun:
        """Close a contest cycle that produced no work.

        A verdict with no tasks ends the cycle it opened, but nothing else in
        the system would: there is no execution to synthesize and no failure to
        settle, so the cycle would stay `running` forever and its request would
        stay `dispatching`, blocking every later request for the run.

        Only a `reject` reaches the close below. `ask_back` has already moved
        the cycle to `waiting_for_user` through the decision request it raised,
        and that pause is the whole point of the verdict -- completing the cycle
        here would destroy it one call after it was created. The cycle status
        the verdict left behind is what tells the two apart.
        """
        run = self._teams.get_team_run(team_run_id)
        if self._teams.get_cycle(cycle_id).status != "running":
            return self._close_collaboration(run)
        leader = _find_leader(self._teams.list_agents(run.id))
        self._teams.set_agent_status(leader.id, "completed")
        self._teams.set_cycle_status(cycle_id, "completed")
        run = self._teams.set_run_status(run.id, "completed")
        await self._publish(
            {"type": "team.run.completed", "team_run_id": run.id}
        )
        return self._close_collaboration(run)

    async def _resume_zero_task_contest(
        self, run: TeamRun, cycle_id: str
    ) -> TeamRun:
        """Finish a crash-recovered contest whose cycle owns no tasks.

        resume() would otherwise take its zero-task shortcut into start(),
        which opens a cycle_planning operation while the recovered
        cycle_contest is still open -- the ledger refuses two open operations
        for one cycle, so the cycle could never move again. Recovering the
        contest operation applies the verdict the crash lost, and from there
        this is the same fork the orchestrator takes on a live verdict.
        """
        leader: TeamAgent | None = None
        try:
            self._activate_cycle(cycle_id)
            leader = _find_leader(self._teams.list_agents(run.id))
            run = self._teams.set_run_status(run.id, "running")
            # The leader is deliberately left "pending". adjudicate_contest
            # never sets an agent status, so "pending" is what a contest looks
            # like live, and that is the one state
            # _validate_active_source accepts for cycle_contest -- setting
            # "running" here made a provider failure during recovery raise the
            # OperationConflict that parking exists to avoid, failing the cycle
            # and losing the objection. Two paths, one truth about what the
            # leader's status means while it rules. settle_contest and
            # publish_decision_request both already work from "pending".
            recovery = await self._recover_open_operation(run, leader, cycle_id)
            if recovery is None or not isinstance(
                recovery.result, ContestOutcome
            ):
                raise OperationConflict(
                    "Contest recovery did not produce a verdict"
                )
            if recovery.result.tasks:
                return await self.resume(run.id, cycle_id)
            return await self.settle_contest(run.id, cycle_id)
        except asyncio.CancelledError:
            self._settle_canceled(run, cycle_id)
            raise
        except (ProviderOperationWaiting, AmbiguousModelOperation):
            raise
        except Exception as exc:  # noqa: BLE001
            error = redact_text(exc) or type(exc).__name__
            run = self._settle_failed(run, error, cycle_id)
            await self._publish(
                {
                    "type": "team.run.failed",
                    "team_run_id": run.id,
                    "error": error,
                }
            )
            return run

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


def _plan_under_review(tasks: list[TeamTask]) -> list[TeamTask]:
    """The plan a reviewer is shown.

    A replan leaves the superseded revision's tasks behind as canceled rows.
    They are not part of the plan any more, so they are neither shown to a
    reviewer nor listed on the next revision.
    """
    return [task for task in tasks if task.status != "canceled"]


def _plan_labels(tasks: list[TeamTask]) -> dict[str, TeamTask]:
    """Name every task in the plan under review, and map the name back.

    Numbered from 1 by position in *this* revision, not by plan_ordinal.
    plan_ordinal continues across the cycle, so a replan would show T-02/T-03
    and then T-04/T-05 -- and PLAN_REVIEW_PROMPT carries a literal "T-01" in
    its schema example. A model that echoes the example is right on revision 1
    and unparsable on every revision after it, which spends the revision
    budget on a defect in the prompt. Position-based labels keep the example a
    real label in every round.
    """
    return {task_label(index): task for index, task in enumerate(tasks, start=1)}


def _plan_owner_label(
    task: TeamTask, reviewer_id: str, names: dict[str, str]
) -> str:
    if task.owner_agent_id == reviewer_id:
        return "YOURS"
    if task.owner_agent_id is None:
        return "unassigned"
    return names.get(task.owner_agent_id, task.owner_agent_id)


# One ledger key per reviewer per revision. The ledger refuses a second
# reservation under a key already bound to another request, and the only part
# of the key a review can vary is the ordinal: task_id must name a real task
# row, which a revision is not. The revision is the high digits and the
# reviewer's position in the revision's stored approver list is the low ones,
# so the number is the same after a restart.
_PLAN_REVIEW_ORDINAL_STRIDE = 100


def _require_keyable_approvers(approver_ids: list[str]) -> None:
    if len(approver_ids) > _PLAN_REVIEW_ORDINAL_STRIDE:
        raise ValueError("too many plan approvers to key a review operation")


def _plan_review_ordinal(revision: int, approver_index: int) -> int:
    return revision * _PLAN_REVIEW_ORDINAL_STRIDE + approver_index


def _replan_ordinals(superseded_revision: int) -> tuple[int, int]:
    """Where a replan sits in the cycle_planning ordinal space.

    A replan has to run as cycle_planning because that is the only stage the
    effect service will apply a plan from, so it needs ordinals that cannot
    collide with the first plan (cycle_planning 0, cycle_planning_repair 1) or
    with add-work's repair (cycle_planning_repair 2).
    """
    base = 10 * (superseded_revision + 1)
    return base, base + 1


def _replan_source_revision(stage: str, stage_ordinal: int) -> int | None:
    """Which revision an open planning operation is replanning, if any.

    The inverse of _replan_ordinals. The first plan (cycle_planning 0,
    cycle_planning_repair 1) and add-work's repair (cycle_planning_repair 2)
    are not replans and return None, so they stay with the recovery that
    already knows how to rebuild them.
    """
    base = stage_ordinal - 1 if stage == "cycle_planning_repair" else stage_ordinal
    if base < 10 or base % 10 != 0:
        return None
    return base // 10 - 1


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


def _undeclared_retry_repair_messages(
    messages: list[dict[str, object]],
    extra_paths: frozenset[str],
) -> list[dict[str, object]]:
    """Send a futile retry_worker resolution back to the leader by name.

    The generic _repair_messages prompt says only "could not be parsed", which
    is true at the ledger level (both land as invalid_structured_output) but
    misleading here -- the resolution parsed fine, it just cannot clear the
    rejection. Naming the extras gives the leader the one fact it is missing.
    """
    correction = (
        "Your resolution cannot clear this rejection. These paths are counted "
        "against the task and the contract does not list them -- declared by "
        "this outcome, or left in the workspace by an earlier round:\n"
        + "\n".join(f"- {path}" for path in sorted(extra_paths))
        + "\n\nTo keep them, return revise_acceptance with required_outputs "
        "extended to include every one. To have the worker remove them, return "
        "retry_worker with an instruction that names each path to delete. "
        "retry_worker without those paths leaves the contract unchanged and the "
        "same rejection returns."
    )
    return [{"role": "user", "content": f"{messages[0]['content']}\n\n{correction}"}]


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


def _contest_repair_messages(
    reason_code: str | None,
) -> list[dict[str, object]]:
    """Name the rules a verdict actually fails.

    The generic prompt says "re-emit only the previous final result", which is
    exactly wrong for the failure this stage sees most: a verdict missing
    `reason`. Re-emitting the same object cannot satisfy the validator, so the
    repair burns its one attempt. Every rule the verdict validator enforces is
    restated here instead.
    """
    error = reason_code or "invalid_structured_output"
    return [
        {
            "role": "user",
            "content": (
                "Your previous verdict could not be accepted.\n"
                f"Error: {error}.\n\n"
                "Do not repeat the adjudication and do not modify files. "
                "Send the same ruling again as one raw JSON object with "
                'these keys: kind, reason, tasks, question, supersedes.\n'
                '- kind is exactly one of "amend", "partial", "reject", '
                '"ask_back".\n'
                "- reason is required for every kind, including reject and "
                "ask_back. A verdict without a reason is rejected.\n"
                "- amend and partial carry at least one task; reject and "
                "ask_back carry none.\n"
                "- question is required for ask_back and only for ask_back.\n"
                "- every supersedes entry needs a task that corrects the "
                "document it names; a supersedes entry without a task is "
                "rejected.\n"
                "No explanations, no Markdown, no code fences."
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


def _contest_history_text(messages: list[TeamMessage]) -> str:
    history = [message for message in messages if message.kind == "plan_adjudication"]
    if not history:
        return "(none)"
    return "\n".join(f"- {message.content}" for message in history)


def _validated_contest_verdict(response: ModelResponse) -> ValidatedOperationResult:
    stripped = normalize_json_envelope(response.content)
    if stripped.startswith("```"):
        raise ValueError("Contest verdict response must not use code fences")
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("Contest verdict response must be a JSON object")
    return ValidatedOperationResult("contest_verdict", payload)


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


def _validated_plan_review(
    response: ModelResponse,
    allowed_labels: frozenset[str],
) -> ValidatedOperationResult:
    review = parse_plan_review(response.content, allowed_labels)
    return ValidatedOperationResult(
        "plan_review",
        {
            "decision": review.decision,
            "objections": [asdict(objection) for objection in review.objections],
        },
    )


def _plan_review_repair_messages(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": (
                f"{messages[0]['content']}\n{_PLAN_REVIEW_REPAIR_INSTRUCTION}"
            ),
        }
    ]


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
