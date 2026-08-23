from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

# How many assignments may hold a provider call at once, whatever the roster
# size. A ceiling and not just the worker count, because the limit that
# actually binds is downstream: LMG admits a bounded number of concurrent runs
# per provider (LMG_CODEX_CONCURRENT_RUNS and its siblings, two by default) and
# queues the rest. Starting more calls than the gateway will run does not
# finish the cycle sooner -- it converts run time into queue time, which
# wall_ms cannot tell apart, and a call that waits long enough comes back
# ambiguous.
#
# Three rather than two: a team big enough to want concurrency usually has one
# assignment that outlasts the others, and the third slot is what keeps the
# roster busy while it finishes. Raising this past what LMG admits is the one
# change here that makes things worse rather than better.
MAX_CONCURRENT_WORKERS = 3


TeamRunStatus = Literal[
    "draft",
    "planning",
    "running",
    "summarizing",
    "completed",
    "completed_with_failures",
    "blocked",
    "failed",
    "canceled",
    "interrupted",
    "waiting_for_user",
    "waiting_for_provider",
]
CycleStatus = Literal[
    "queued",
    "running",
    "waiting_for_provider",
    "waiting_for_user",
    "interrupted",
    "completed",
    "completed_with_failures",
    "blocked",
    "failed",
    "canceled",
]
TaskStatus = Literal[
    "pending",
    "in_progress",
    "waiting_for_user",
    "waiting_for_provider",
    "completed",
    "skipped",
    "blocked",
    "failed",
    "canceled",
]
TerminalCycleStatus = Literal[
    "completed",
    "completed_with_failures",
    "blocked",
    "failed",
    "canceled",
]
DependencyDisposition = Literal["ready", "waiting", "skip"]

TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "completed_with_failures", "blocked", "failed", "canceled"}
)
TERMINAL_CYCLE_STATUSES = frozenset(
    {"completed", "completed_with_failures", "blocked", "failed", "canceled"}
)
TERMINAL_TASK_STATUSES = frozenset(
    {"completed", "skipped", "blocked", "failed", "canceled"}
)
WAITING_TASK_STATUSES = frozenset({"waiting_for_user", "waiting_for_provider"})

_TASK_TRANSITIONS = {
    "pending": frozenset({"in_progress", "skipped", "canceled"}),
    "in_progress": frozenset(
        {
            "waiting_for_user",
            "waiting_for_provider",
            "completed",
            "blocked",
            "failed",
            "canceled",
        }
    ),
    "waiting_for_user": frozenset({"pending", "canceled"}),
    "waiting_for_provider": frozenset({"in_progress", "failed", "canceled"}),
    "completed": frozenset(),
    "skipped": frozenset(),
    "blocked": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
}
_TRANSITIONS = {"task": _TASK_TRANSITIONS}


class LifecycleIntegrityError(ValueError):
    pass


class TaskLifecycleState(Protocol):
    id: str
    status: str
    required: bool


@dataclass(frozen=True)
class ExecutionDisposition:
    kind: Literal[
        "incomplete", "waiting_for_user", "waiting_for_provider", "terminal"
    ]
    terminal_status: TerminalCycleStatus | None = None


def can_transition(entity: str, source: str, target: str) -> bool:
    transitions = _TRANSITIONS.get(entity)
    if transitions is None or source not in transitions:
        return False
    return source == target or target in transitions[source]


def require_transition(entity: str, source: str, target: str) -> None:
    if can_transition(entity, source, target):
        return
    raise LifecycleIntegrityError(
        f"Illegal {entity} lifecycle transition: {source} -> {target}"
    )


def task_dependency_disposition(
    prerequisite_statuses: Sequence[str],
) -> DependencyDisposition:
    if all(status == "completed" for status in prerequisite_statuses):
        return "ready"
    if any(
        status in TERMINAL_TASK_STATUSES and status != "completed"
        for status in prerequisite_statuses
    ):
        return "skip"
    return "waiting"


def cycle_execution_disposition(
    tasks: Sequence[TaskLifecycleState],
    dependencies: Mapping[str, Sequence[str]],
    *,
    active_decision: bool = False,
    open_provider_operation: bool = False,
) -> ExecutionDisposition:
    if active_decision:
        return ExecutionDisposition("waiting_for_user")
    if open_provider_operation:
        return ExecutionDisposition("waiting_for_provider")
    if not tasks:
        return ExecutionDisposition("terminal", "failed")

    tasks_by_id = {task.id: task for task in tasks}
    active = [task.id for task in tasks if task.status not in TERMINAL_TASK_STATUSES]
    if active:
        return ExecutionDisposition("incomplete")

    required = [task for task in tasks if task.required]
    optional = [task for task in tasks if not task.required]
    if not required:
        has_optional_issue = any(
            task.status != "completed" for task in optional
        )
        status: TerminalCycleStatus = (
            "completed_with_failures" if has_optional_issue else "completed"
        )
        return ExecutionDisposition("terminal", status)

    required_causes = {
        _required_terminal_cause(task.id, tasks_by_id, dependencies, frozenset())
        for task in required
        if task.status != "completed"
    }
    if "failed" in required_causes or "canceled" in required_causes:
        return ExecutionDisposition("terminal", "failed")
    if "blocked" in required_causes:
        return ExecutionDisposition("terminal", "blocked")
    if required_causes:
        unresolved = sorted(
            task.id for task in required if task.status != "completed"
        )
        raise LifecycleIntegrityError(
            f"Required tasks have unresolved terminal states: {unresolved}"
        )

    has_optional_issue = any(task.status != "completed" for task in optional)
    status = "completed_with_failures" if has_optional_issue else "completed"
    return ExecutionDisposition("terminal", status)


def _required_terminal_cause(
    task_id: str,
    tasks_by_id: Mapping[str, TaskLifecycleState],
    dependencies: Mapping[str, Sequence[str]],
    visiting: frozenset[str],
) -> str:
    if task_id in visiting:
        involved = sorted((*visiting, task_id))
        raise LifecycleIntegrityError(f"Task dependency cycle: {involved}")
    task = tasks_by_id.get(task_id)
    if task is None:
        raise LifecycleIntegrityError(f"Unknown task dependency: {task_id}")
    if task.status != "skipped":
        return task.status

    prerequisite_ids = dependencies.get(task_id, ())
    if not prerequisite_ids:
        raise LifecycleIntegrityError(
            f"Skipped task has no terminal prerequisite: {task_id}"
        )
    next_visiting = visiting | {task_id}
    causes = [
        _required_terminal_cause(
            prerequisite_id,
            tasks_by_id,
            dependencies,
            next_visiting,
        )
        for prerequisite_id in prerequisite_ids
    ]
    for cause in ("failed", "canceled", "blocked"):
        if cause in causes:
            return cause
    raise LifecycleIntegrityError(
        f"Skipped task has no unsuccessful root prerequisite: {task_id}"
    )
