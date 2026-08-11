from personal_agent_gateway.team_build_evidence import (
    run_build_evidence,
    task_build_evidence,
)
from personal_agent_gateway.teams import (
    RequiredVerification,
    TaskAcceptance,
    TeamTask,
)


def _fill_team_task_defaults(values: dict) -> dict:
    defaults = {
        "cycle_id": None,
        "plan_ordinal": 0,
        "retry_of_task_id": None,
        "result": None,
        "error_message": None,
        "created_at": "2026-08-12T00:00:00+00:00",
        "updated_at": "2026-08-12T00:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "acceptance_recovery_attempts": 0,
    }
    return {**defaults, **values}


def _task(tmp_path, **overrides):
    """A TeamTask is frozen with many fields this report ignores, so build one
    here rather than dragging a whole runtime fixture into a pure-function test.
    """
    base = dict(
        id="t1",
        team_run_id="r1",
        title="Study backend",
        description="",
        owner_agent_id=None,
        status="completed",
        required=True,
        acceptance=TaskAcceptance(
            ("promised.md",), (RequiredVerification("has-export"),)
        ),
        outcome={"deliverables": [{"path": "promised.md"}]},
        acceptance_result={
            "evidence": {
                "verifications": {
                    "has-export": {"mode": "attested", "status": "passed"}
                },
                "attested_only": True,
            }
        },
    )
    base.update(overrides)
    return TeamTask(**_fill_team_task_defaults(base))


def test_evidence_reports_both_directions_of_the_promise(tmp_path):
    """A rejected task's story is the difference between what its contract asked
    for and what the worker declared. Run 699c1915's task 8 promised four files
    and declared seven, and nothing in the UI said so."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    (tmp_path / "extra.md").write_text("x", encoding="utf-8")
    task = _task(
        tmp_path,
        acceptance=TaskAcceptance(("promised.md", "forgotten.md"), ()),
        outcome={
            "deliverables": [{"path": "promised.md"}, {"path": "extra.md"}]
        },
    )

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["promised"] == ["forgotten.md", "promised.md"]
    assert evidence["declared"] == ["extra.md", "promised.md"]
    assert evidence["undeclared_promises"] == ["forgotten.md"]
    assert evidence["extra_declarations"] == ["extra.md"]
    assert evidence["missing_files"] == []


def test_a_declared_file_that_is_not_there_is_reported_missing(tmp_path):
    task = _task(tmp_path, outcome={"deliverables": [{"path": "ghost.md"}]})

    assert task_build_evidence(task, tmp_path)["missing_files"] == ["ghost.md"]


def test_a_sensitive_file_that_is_present_is_not_reported_missing(tmp_path):
    """safe_workspace_file refuses .env by name, not by absence. Reporting a file
    that is plainly there as missing would make the screen lie."""
    (tmp_path / ".env.example").write_text("KEY=", encoding="utf-8")
    task = _task(
        tmp_path, outcome={"deliverables": [{"path": ".env.example"}]}
    )

    assert task_build_evidence(task, tmp_path)["missing_files"] == []


def test_a_path_escaping_the_workspace_counts_as_missing(tmp_path):
    """safe_workspace_file returns None for an escape, and the report must not
    turn that into a file that exists somewhere else on the machine."""
    task = _task(tmp_path, outcome={"deliverables": [{"path": "../outside.md"}]})

    assert task_build_evidence(task, tmp_path)["missing_files"] == ["../outside.md"]


def test_verification_mode_is_carried_through_unchanged(tmp_path):
    """The distinction between a check the gate ran and the worker's own word is
    the whole point; the report must not collapse them."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    task = _task(
        tmp_path,
        acceptance_result={
            "evidence": {
                "verifications": {
                    "ran": {"mode": "verified", "status": "passed"},
                    "claimed": {"mode": "attested", "status": "passed"},
                },
                "attested_only": False,
            }
        },
    )

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["verifications"] == [
        {"name": "claimed", "mode": "attested", "status": "passed"},
        {"name": "ran", "mode": "verified", "status": "passed"},
    ]
    assert evidence["worker_asserted_only"] is False


def test_a_task_with_no_outcome_yet_reports_empty_rather_than_raising(tmp_path):
    task = _task(tmp_path, status="in_progress", outcome=None, acceptance_result=None)

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["declared"] == []
    assert evidence["verifications"] == []
    assert evidence["worker_asserted_only"] is False


def test_run_rollup_counts_what_rests_on_the_workers_word(tmp_path):
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    asserted = _task(tmp_path)
    inspected = _task(
        tmp_path,
        id="t2",
        acceptance_result={
            "evidence": {
                "verifications": {"ran": {"mode": "verified", "status": "passed"}},
                "attested_only": False,
            }
        },
    )
    ghost = _task(tmp_path, id="t3", outcome={"deliverables": [{"path": "ghost.md"}]})

    rollup = run_build_evidence([asserted, inspected, ghost], tmp_path)

    assert rollup == {
        "task_count": 3,
        "worker_asserted_only_count": 2,
        "missing_file_count": 1,
    }
