from pathlib import Path

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


def test_a_symlink_at_the_declared_path_counts_as_missing(tmp_path, monkeypatch):
    """safe_workspace_file refuses a symlink outright, without following it.
    Creating a real symlink needs privileges this environment does not have,
    so the symlink is simulated instead, for the one declared path only: it
    reports as a symlink, resolving it lands on a real, readable file
    actually created inside the workspace, and it reports as a file --
    exactly what a real symlink to that target would do at the OS level.

    The declared path is itself named `.env` so the assertion actually
    depends on the short-circuit under test. The rescue formula below only
    ever answers "not missing" when the *declared* name looks sensitive; a
    non-sensitive declared name (e.g. `link.md`) stays "missing" whether or
    not the symlink is followed, because `is_sensitive_file` is checked
    against that unresolved name, not the resolved target's. So a symlink
    named plainly cannot distinguish the fix from a regression -- only a
    sensitively-named symlink pointing at an ordinary target can, and that is
    precisely the shape of the vulnerability the short-circuit closes: a
    symlink at `.env` resolving through to a real, ordinary file must still
    read as refused, not rescued.
    """
    real_target = tmp_path / "real.md"
    real_target.write_text("x", encoding="utf-8")
    link_path = tmp_path / ".env"

    real_is_symlink = Path.is_symlink
    real_resolve = Path.resolve
    real_is_file = Path.is_file

    def fake_is_symlink(self):
        if self == link_path:
            return True
        return real_is_symlink(self)

    def fake_resolve(self, *args, **kwargs):
        if self == link_path:
            return real_target
        return real_resolve(self, *args, **kwargs)

    def fake_is_file(self):
        if self == link_path:
            return True
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    monkeypatch.setattr(Path, "is_file", fake_is_file)

    task = _task(tmp_path, outcome={"deliverables": [{"path": ".env"}]})

    assert task_build_evidence(task, tmp_path)["missing_files"] == [".env"]


def test_an_absolute_path_to_a_real_file_outside_the_workspace_counts_as_missing(
    tmp_path_factory,
):
    """pathlib's `/` discards the workspace root entirely when the joined
    path is itself absolute, so an absolute declared path that matches a
    real file on the host must still be reported missing -- the report must
    not read the host filesystem as if it were the workspace."""
    workspace = tmp_path_factory.mktemp("workspace")
    outside_file = tmp_path_factory.mktemp("outside") / "real.md"
    outside_file.write_text("x", encoding="utf-8")
    task = _task(
        workspace, outcome={"deliverables": [{"path": str(outside_file)}]}
    )

    assert task_build_evidence(task, workspace)["missing_files"] == [
        str(outside_file)
    ]


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


def test_evidence_reports_what_the_worker_could_not_check(tmp_path):
    """The label carries the distinction the gate now records: a check the gate ran,
    a check the worker asserted, and a check nobody confirmed."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    task = _task(
        tmp_path,
        acceptance_result={
            "evidence": {
                "verifications": {
                    "ran": {"mode": "verified", "status": "passed"},
                    "typecheck": {"mode": "unverified", "status": "unknown"},
                },
                "attested_only": False,
                "unverified": ["typecheck"],
            }
        },
    )

    evidence = task_build_evidence(task, tmp_path)

    assert evidence["unverified"] == ["typecheck"]
    assert {"name": "typecheck", "mode": "unverified", "status": "unknown"} in (
        evidence["verifications"]
    )


def test_the_rollup_counts_tasks_with_something_unconfirmed(tmp_path):
    """This is the number that moves when work goes unchecked. attested_only does
    not: it is true only when a task had zero runnable checks, so it reads 0 for a
    run where every check was a file read."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    clean = _task(tmp_path, id="t1")
    unconfirmed = _task(
        tmp_path,
        id="t2",
        acceptance_result={
            "evidence": {"verifications": {}, "attested_only": False, "unverified": ["typecheck"]}
        },
    )

    # run_build_evidence takes the already-computed per-task reports, not the
    # tasks, and takes no workspace -- recomputing here doubled the filesystem
    # work on a polled endpoint.
    rollup = run_build_evidence(
        [task_build_evidence(task, tmp_path) for task in (clean, unconfirmed)]
    )

    assert rollup["unverified_task_count"] == 1


def test_an_acceptance_result_without_the_key_reports_none(tmp_path):
    """Every stored acceptance result predates this key."""
    (tmp_path / "promised.md").write_text("x", encoding="utf-8")
    task = _task(tmp_path)

    assert task_build_evidence(task, tmp_path)["unverified"] == []


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

    rollup = run_build_evidence(
        [
            task_build_evidence(task, tmp_path)
            for task in (asserted, inspected, ghost)
        ]
    )

    assert rollup == {
        "task_count": 3,
        "worker_asserted_only_count": 2,
        "missing_file_count": 1,
        "unverified_task_count": 0,
        # ghost.md was declared, so its promise is met on paper; promised.md is
        # promised and declared by the other two.
        "undeclared_promise_count": 1,
    }


def test_an_anchored_declared_path_is_refused_without_touching_the_disk(
    tmp_path, monkeypatch
):
    """The declared path is model output. A UNC path made /detail open an SMB
    connection to a host the model named, on every poll -- pathlib discards the
    workspace root for any anchored path, and the containment check that would
    have caught it ran after the stat."""
    def forbidden(*args, **kwargs):
        raise AssertionError("_is_missing touched the filesystem")

    for name in ("is_symlink", "resolve", "is_file", "stat"):
        monkeypatch.setattr(Path, name, forbidden)

    task = _task(
        tmp_path,
        outcome={
            "deliverables": [
                {"path": "//10.0.0.1/share/x"},
                {"path": "C:/Windows/System32/drivers/etc/hosts"},
                {"path": "/etc/passwd"},
            ]
        },
    )

    assert task_build_evidence(task, tmp_path)["missing_files"] == [
        "//10.0.0.1/share/x",
        "/etc/passwd",
        "C:/Windows/System32/drivers/etc/hosts",
    ]


def test_a_run_that_produced_nothing_does_not_read_as_clean(tmp_path):
    """Every other count in the rollup is about what a task reported, so a task
    that reported nothing at all scored zero on all of them -- a run whose plan
    was never agreed and whose tasks were all canceled looked identical to a
    clean one."""
    task = _task(tmp_path, status="canceled", outcome=None, acceptance_result=None)

    rollup = run_build_evidence([task_build_evidence(task, tmp_path)])

    assert rollup["undeclared_promise_count"] == 1
    assert rollup["missing_file_count"] == 0
    assert rollup["unverified_task_count"] == 0
