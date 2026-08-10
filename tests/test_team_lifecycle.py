from dataclasses import dataclass

import pytest

from personal_agent_gateway.team_lifecycle import (
    TERMINAL_TASK_STATUSES,
    WAITING_TASK_STATUSES,
    LifecycleIntegrityError,
    can_transition,
    cycle_execution_disposition,
    require_transition,
    task_dependency_disposition,
)


@dataclass(frozen=True)
class TaskState:
    id: str
    status: str
    required: bool = True


def test_task_terminal_and_waiting_sets_are_unambiguous():
    assert WAITING_TASK_STATUSES == {"waiting_for_user", "waiting_for_provider"}
    assert TERMINAL_TASK_STATUSES == {
        "completed",
        "skipped",
        "blocked",
        "failed",
        "canceled",
    }
    assert WAITING_TASK_STATUSES.isdisjoint(TERMINAL_TASK_STATUSES)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("pending", "in_progress"),
        ("pending", "skipped"),
        ("in_progress", "waiting_for_user"),
        ("in_progress", "waiting_for_provider"),
        ("waiting_for_user", "pending"),
        ("waiting_for_provider", "in_progress"),
        ("in_progress", "completed"),
        ("pending", "pending"),
    ],
)
def test_allowed_task_transitions(source, target):
    assert can_transition("task", source, target)
    require_transition("task", source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("pending", "completed"),
        ("waiting_for_user", "in_progress"),
        ("completed", "pending"),
        ("skipped", "pending"),
    ],
)
def test_illegal_task_transitions_include_context(source, target):
    assert not can_transition("task", source, target)
    with pytest.raises(
        LifecycleIntegrityError,
        match=rf"task.*{source}.*{target}",
    ):
        require_transition("task", source, target)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "ready"),
        (["completed", "completed"], "ready"),
        (["pending"], "waiting"),
        (["in_progress"], "waiting"),
        (["waiting_for_user"], "waiting"),
        (["waiting_for_provider"], "waiting"),
        (["failed"], "skip"),
        (["blocked"], "skip"),
        (["canceled"], "skip"),
        (["skipped"], "skip"),
    ],
)
def test_dependency_disposition(statuses, expected):
    assert task_dependency_disposition(statuses) == expected


def test_execution_disposition_is_incomplete_for_active_task():
    disposition = cycle_execution_disposition(
        [TaskState("task-1", "pending")],
        {},
    )

    assert disposition.kind == "incomplete"
    assert disposition.terminal_status is None


def test_required_skipped_task_preserves_transitive_failed_root_cause():
    tasks = [
        TaskState("root", "failed", required=False),
        TaskState("middle", "skipped", required=False),
        TaskState("required", "skipped"),
    ]

    disposition = cycle_execution_disposition(
        tasks,
        {"middle": ["root"], "required": ["middle"]},
    )

    assert disposition.terminal_status == "failed"


def test_skipped_task_without_unsuccessful_root_is_integrity_error():
    with pytest.raises(LifecycleIntegrityError, match="required"):
        cycle_execution_disposition(
            [TaskState("required", "skipped")],
            {},
        )
