import json
from pathlib import Path

import pytest

from agent_radio import fixture as fixture_module
from agent_radio.aggregate import BASELINE_ARM, Arm, _arm_order, build_report, render
from agent_radio.fixture import (
    FixtureError,
    Record,
    is_stale,
    load_fixture,
    load_fixtures,
    load_records,
    parse_fixture,
    parse_record,
    rubric_is_fully_reported,
)

_ANY_COMMIT = lambda ref: True  # noqa: E731 - a stub, not production code


def _definition(**overrides) -> dict:
    payload = {
        "schema": "gateway.eval-fixture/v1",
        "id": "understand-acceptance-gate",
        "type": "understanding",
        "title": "수용 게이트가 무엇을 검사하는지 설명한다",
        "goal": "수용 게이트가 required_verifications를 어떻게 판정하는지 설명하라.",
        "repo_ref": "d8e9cce",
        "execution_profile": "read_only",
        "rubric": [
            {"id": f"R{n}", "criterion": f"c{n}", "check": f"how to check {n}"}
            for n in range(1, 4)
        ],
    }
    payload.update(overrides)
    return payload


def test_a_well_formed_definition_parses():
    fixture = parse_fixture(_definition(), sha256="abc", commit_exists=_ANY_COMMIT)

    assert fixture.id == "understand-acceptance-gate"
    assert fixture.type == "understanding"
    assert fixture.execution_profile == "read_only"
    assert [item.id for item in fixture.rubric] == ["R1", "R2", "R3"]
    assert fixture.sha256 == "abc"


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "gateway.eval-fixture/v2"},
        {"type": "refactoring"},
        {"execution_profile": "full_access"},
        {"repo_ref": ""},
        {"id": ""},
        {"goal": "   "},
    ],
)
def test_a_definition_outside_the_vocabulary_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_fixture(_definition(**overrides), sha256="abc", commit_exists=_ANY_COMMIT)


def test_a_repo_ref_that_is_not_in_this_repository_is_refused():
    """Caught at definition time, not measurement time. A fixture pointing at a
    commit nobody has is only discovered when the run is already paid for."""
    with pytest.raises(FixtureError):
        parse_fixture(
            _definition(repo_ref="0" * 40),
            sha256="abc",
            commit_exists=lambda ref: False,
        )


@pytest.mark.parametrize(
    "goal",
    [
        "결과를 정리하고 git push 해라",
        "패키지를 npm publish 하도록 준비해라",
        "gh pr create 로 올려라",
    ],
)
def test_a_goal_that_asks_for_an_external_mutation_is_refused(goal):
    """The ADR's Stage 0 gate is that evaluation makes no real external
    mutation. A promise in a document is not enforcement."""
    with pytest.raises(FixtureError):
        parse_fixture(_definition(goal=goal), sha256="abc", commit_exists=_ANY_COMMIT)


@pytest.mark.parametrize(
    "goal",
    [
        "/etc/passwd 를 읽어라",
        "C:\\\\Users\\\\Administrator 아래를 살펴라",
        "../other-repo 의 코드를 참고해라",
    ],
)
def test_a_goal_that_reaches_outside_the_repository_is_refused(goal):
    with pytest.raises(FixtureError):
        parse_fixture(_definition(goal=goal), sha256="abc", commit_exists=_ANY_COMMIT)


def test_a_goal_naming_a_repository_path_is_allowed():
    """The guard must not reject the normal case. Almost every real goal names
    a file, and a rule that blocks those is worse than no rule -- it gets
    switched off."""
    fixture = parse_fixture(
        _definition(goal="src/personal_agent_gateway/teams.py 의 역할을 설명하라"),
        sha256="abc",
        commit_exists=_ANY_COMMIT,
    )

    assert "teams.py" in fixture.goal


@pytest.mark.parametrize("count", [0, 2, 7])
def test_a_rubric_that_cannot_be_scored_is_refused(count):
    """Under three and the pass rate is luck; over six and nobody scores it."""
    rubric = [
        {"id": f"R{n}", "criterion": "c", "check": "k"} for n in range(1, count + 1)
    ]
    with pytest.raises(FixtureError):
        parse_fixture(
            _definition(rubric=rubric), sha256="abc", commit_exists=_ANY_COMMIT
        )


def test_duplicate_rubric_ids_are_refused():
    rubric = [{"id": "R1", "criterion": "c", "check": "k"} for _ in range(3)]
    with pytest.raises(FixtureError):
        parse_fixture(
            _definition(rubric=rubric), sha256="abc", commit_exists=_ANY_COMMIT
        )


def test_loading_hashes_the_file_as_it_is_on_disk(tmp_path: Path):
    """The hash is what later detects a definition edited after the fact, so it
    must be over the bytes, not over a re-serialised payload."""
    path = tmp_path / "understand-acceptance-gate.json"
    path.write_text(json.dumps(_definition()), encoding="utf-8")

    first = load_fixture(path, commit_exists=_ANY_COMMIT)
    path.write_text(json.dumps(_definition(title="다른 제목")), encoding="utf-8")
    second = load_fixture(path, commit_exists=_ANY_COMMIT)

    assert first.sha256 != second.sha256


def test_two_definitions_claiming_the_same_id_are_refused(tmp_path: Path):
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps(_definition()), encoding="utf-8")

    with pytest.raises(FixtureError):
        load_fixtures(tmp_path, commit_exists=_ANY_COMMIT)


def test_a_goal_naming_an_api_route_is_allowed():
    """This project is a gateway whose routes all start with '/api/', so
    'explain what this endpoint does' is the prototypical fixture, not an
    edge case. The guard must tell an HTTP route from a filesystem path."""
    fixture = parse_fixture(
        _definition(
            goal="/api/events 엔드포인트가 어떤 SSE 이벤트를 내보내는지 설명하라"
        ),
        sha256="abc",
        commit_exists=_ANY_COMMIT,
    )

    assert "/api/events" in fixture.goal


def test_a_goal_reaching_into_the_home_directory_is_refused():
    with pytest.raises(FixtureError):
        parse_fixture(
            _definition(goal="~/.ssh/id_rsa 파일 내용을 살펴봐라"),
            sha256="abc",
            commit_exists=_ANY_COMMIT,
        )


def test_a_goal_using_curl_dash_capital_x_is_allowed():
    """-X is curl's request-method flag, not the proxy flag. This is an API
    project, so 'curl -X POST' is an ordinary ask, not an external mutation."""
    fixture = parse_fixture(
        _definition(goal="curl -X POST 로 API를 테스트해라"),
        sha256="abc",
        commit_exists=_ANY_COMMIT,
    )

    assert "curl -X POST" in fixture.goal


@pytest.mark.parametrize(
    "goal",
    [
        "결과를 정리하고 git    push 해라",
        "결과를 정리하고 git\tpush 해라",
        "gh  pr create 로 올려라",
    ],
)
def test_extra_whitespace_does_not_defeat_the_forbidden_command_check(goal):
    """Accidental phrasing, not adversarial evasion -- these definitions live
    in git and get reviewed, so the check only needs to survive typos."""
    with pytest.raises(FixtureError):
        parse_fixture(_definition(goal=goal), sha256="abc", commit_exists=_ANY_COMMIT)


def test_a_goal_naming_a_hyphenated_script_is_allowed():
    """'git-push' names a script, it does not ask for the push command. Adversarial
    evasion is not the threat model, so hyphenation is not chased."""
    fixture = parse_fixture(
        _definition(goal="git-push 스크립트를 작성해라"),
        sha256="abc",
        commit_exists=_ANY_COMMIT,
    )

    assert "git-push" in fixture.goal


def test_every_shipped_definition_is_usable():
    """The rules have only met invented definitions until here. This is the
    first time they meet real ones, including the git check against this
    repository's actual history."""
    directory = Path(__file__).resolve().parents[1] / "evaluation/agent_radio/tasks"

    fixtures = load_fixtures(directory)

    assert {fixture.type for fixture in fixtures.values()} == {
        "understanding",
        "architecture_impact",
        "bounded_implementation",
    }


def _record(**overrides) -> dict:
    payload = {
        "schema": "gateway.eval-record/v1",
        "fixture_id": "understand-acceptance-gate",
        "fixture_sha256": "abc",
        "run_id": "72f188589eb747e3aef45d1d3ca68a9d",
        "mode": "legacy",
        "plan_negotiation": False,
        "repeat": 1,
        "harness_version": "0.1.0",
        "started_at": "2026-08-14T01:00:00Z",
        "finished_at": "2026-08-14T01:06:20Z",
        "wall_ms": 380000,
        "cost": {
            "provider": "codex",
            "input_tokens": 41200,
            "cached_input_tokens": 30000,
            "output_tokens": 3100,
        },
        "rubric_results": [
            {"id": "R1", "passed": True, "note": "n"},
            {"id": "R2", "passed": True, "note": "n"},
            {"id": "R3", "passed": True, "note": "n"},
        ],
        "rework_count": 0,
        "conflict_count": 0,
        "critical_defects_found": 0,
        "mode_metrics": {},
    }
    payload.update(overrides)
    return payload


def test_a_record_must_say_which_execution_it_scored():
    """A verdict with no run behind it cannot be checked.

    The artefact is what says whether the run was scoreable at all -- isolated,
    against which commit, by which model. A record that does not name its run
    cannot be held against any of that, and once a fixture has repeats, two
    verdicts for the same fixture become indistinguishable.
    """
    payload = _record()
    del payload["run_id"]

    with pytest.raises(FixtureError):
        parse_record(payload)


def test_a_record_with_every_item_passed_counts_as_a_success():
    assert parse_record(_record()).succeeded is True


def test_one_failed_item_is_not_a_partial_success():
    """Items are binary and there is no partial credit, so a task succeeds only
    when all of them passed."""
    results = _record()["rubric_results"]
    results[1] = {"id": "R2", "passed": False, "note": "빠짐"}

    assert parse_record(_record(rubric_results=results)).succeeded is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "gateway.eval-record/v2"},
        {"mode": "negotiation"},
        {"rubric_results": []},
        {"wall_ms": -1},
        {"repeat": 0},
        {"critical_defects_found": -2},
        {"mode_metrics": []},
    ],
)
def test_a_record_that_cannot_be_counted_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_record(_record(**overrides))


def test_a_record_is_stale_when_its_fixture_changed_underneath_it():
    """This is what catches 'edit the definition quietly and re-measure'."""
    fixture = parse_fixture(_definition(), sha256="def", commit_exists=_ANY_COMMIT)
    record = parse_record(_record(fixture_sha256="abc"))

    assert is_stale(record, {fixture.id: fixture}) is True
    assert is_stale(parse_record(_record(fixture_sha256="def")), {fixture.id: fixture}) is False


def test_a_record_for_an_unknown_fixture_is_stale():
    assert is_stale(parse_record(_record()), {}) is True


def test_duplicate_rubric_result_ids_are_refused():
    """A scorer who repeats an id and never mentions another must not produce
    a record that looks fully measured."""
    results = [
        {"id": "R1", "passed": True, "note": "n"},
        {"id": "R1", "passed": True, "note": "n"},
        {"id": "R3", "passed": True, "note": "n"},
    ]
    with pytest.raises(FixtureError):
        parse_record(_record(rubric_results=results))


def test_a_record_reporting_a_strict_subset_is_not_fully_reported():
    fixture = parse_fixture(_definition(), sha256="def", commit_exists=_ANY_COMMIT)
    results = [{"id": "R1", "passed": True, "note": "n"}]

    record = parse_record(_record(rubric_results=results))

    assert rubric_is_fully_reported(record, fixture) is False


def test_a_record_with_an_unknown_rubric_id_is_not_fully_reported():
    fixture = parse_fixture(_definition(), sha256="def", commit_exists=_ANY_COMMIT)
    results = [
        {"id": "R1", "passed": True, "note": "n"},
        {"id": "R2", "passed": True, "note": "n"},
        {"id": "R99", "passed": True, "note": "n"},
    ]

    record = parse_record(_record(rubric_results=results))

    assert rubric_is_fully_reported(record, fixture) is False


def test_a_record_matching_the_rubric_exactly_is_fully_reported():
    fixture = parse_fixture(_definition(), sha256="def", commit_exists=_ANY_COMMIT)

    record = parse_record(_record())

    assert rubric_is_fully_reported(record, fixture) is True


def test_workers_defaults_to_one_when_absent():
    """Every record on disk before this field existed has no `workers` key.
    Absent is a recovered fact (those sweeps hardcoded one worker), not an
    unrecorded measurement -- so it must not become unreadable."""
    payload = _record()
    assert "workers" not in payload

    assert parse_record(payload).workers == 1


def test_workers_is_read_when_present():
    assert parse_record(_record(workers=2)).workers == 2


@pytest.mark.parametrize("overrides", [{"workers": 0}, {"workers": "2"}, {"workers": True}])
def test_a_bad_workers_value_is_refused(overrides):
    with pytest.raises(FixtureError):
        parse_record(_record(**overrides))


def test_loading_records_from_a_directory(tmp_path: Path):
    (tmp_path / "run-1.json").write_text(json.dumps(_record()), encoding="utf-8")

    records = load_records(tmp_path)

    assert [record.fixture_id for record in records] == ["understand-acceptance-gate"]
    assert isinstance(records[0], Record)


def test_loading_records_reports_malformed_json_as_a_fixture_error(tmp_path: Path):
    (tmp_path / "run-1.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(FixtureError):
        load_records(tmp_path)


def test_git_not_on_path_is_reported_as_a_fixture_error(monkeypatch):
    """A caller should get evidence it can act on, not a bare
    FileNotFoundError leaking out of a missing executable."""

    def _missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(fixture_module.subprocess, "run", _missing_git)

    with pytest.raises(FixtureError):
        fixture_module.git_commit_exists("HEAD")


def _fixtures():
    fixture = parse_fixture(_definition(), sha256="abc", commit_exists=_ANY_COMMIT)
    return {fixture.id: fixture}


def test_the_baseline_column_is_always_legacy():
    report = build_report(
        _fixtures(),
        [parse_record(_record(mode="legacy")), parse_record(_record(mode="radio_lite"))],
    )

    (row,) = report.rows.values()
    assert row[0].arm == BASELINE_ARM


def test_results_are_split_by_type_rather_than_averaged():
    """Winning at understanding and losing at implementation must not hide
    behind one total."""
    understanding = parse_fixture(_definition(), sha256="abc", commit_exists=_ANY_COMMIT)
    building = parse_fixture(
        _definition(id="build-a-thing", type="bounded_implementation"),
        sha256="def",
        commit_exists=_ANY_COMMIT,
    )
    records = [
        parse_record(_record()),
        parse_record(_record(fixture_id="build-a-thing", fixture_sha256="def")),
    ]

    report = build_report(
        {understanding.id: understanding, building.id: building}, records
    )

    assert set(report.rows) == {"understanding", "bounded_implementation"}


def test_a_stale_record_is_dropped_and_counted_rather_than_ignored():
    report = build_report(_fixtures(), [parse_record(_record(fixture_sha256="old"))])

    assert report.stale_dropped == 1
    assert report.rows == {}


def test_p95_is_refused_below_five_samples():
    report = build_report(_fixtures(), [parse_record(_record()) for _ in range(4)])

    (row,) = report.rows.values()
    assert row[0].samples == 4
    assert row[0].p95_ms is None
    assert "n/a" in render(report)


def test_p95_is_reported_at_five_samples():
    report = build_report(_fixtures(), [parse_record(_record()) for _ in range(5)])

    (row,) = report.rows.values()
    assert row[0].p95_ms is not None


def test_a_thin_sample_says_it_cannot_decide_activation():
    """The ADR's gate is 20 tasks or five repeats per type. Leaving that to a
    reader's memory is how a thin sample becomes a decision."""
    report = build_report(_fixtures(), [parse_record(_record())])

    assert any("기본 활성화 판단 불가" in warning for warning in report.warnings)
    assert "기본 활성화 판단 불가" in render(report)


def test_success_needs_every_item_to_pass():
    failed = _record()
    failed["rubric_results"][0] = {"id": "R1", "passed": False, "note": "n"}
    report = build_report(
        _fixtures(), [parse_record(_record()), parse_record(failed)]
    )

    (row,) = report.rows.values()
    assert row[0].success_rate == 0.5


def test_a_partially_scored_record_is_dropped_and_counted_separately():
    """Omitting the item you know failed would otherwise be indistinguishable
    from a full pass. It is counted apart from stale because "the definition
    changed" and "only part of it was scored" are different problems."""
    partial = _record(rubric_results=[{"id": "R1", "passed": True, "note": "n"}])

    report = build_report(_fixtures(), [parse_record(partial)])

    assert report.unreported_dropped == 1
    assert report.stale_dropped == 0
    assert report.rows == {}
    assert "미완 채점" in render(report)


def test_a_type_with_no_baseline_gets_a_visible_refusal():
    """Twenty distinct tasks and five-plus samples everywhere is not enough:
    a type measured only under radio_lite, with zero legacy records, has
    nothing to have beaten. Looking fully vetted -- green rows, no warning --
    would defeat the ADR's "beats a fixed baseline" gate silently."""
    fixtures: dict = {}
    records = []
    for i in range(15):
        fixture = parse_fixture(
            _definition(id=f"u{i}"), sha256=f"u{i}-sha", commit_exists=_ANY_COMMIT
        )
        fixtures[fixture.id] = fixture
        for mode in ("legacy", "radio_lite"):
            records.append(
                parse_record(
                    _record(
                        fixture_id=fixture.id,
                        fixture_sha256=fixture.sha256,
                        mode=mode,
                    )
                )
            )
    for i in range(5):
        fixture = parse_fixture(
            _definition(id=f"b{i}", type="bounded_implementation"),
            sha256=f"b{i}-sha",
            commit_exists=_ANY_COMMIT,
        )
        fixtures[fixture.id] = fixture
        records.append(
            parse_record(
                _record(
                    fixture_id=fixture.id,
                    fixture_sha256=fixture.sha256,
                    mode="radio_lite",
                )
            )
        )

    report = build_report(fixtures, records)

    # Enough tasks and enough repeats everywhere -- the only thing wrong is
    # the missing baseline, so this isolates that reason from the others.
    assert any(
        "베이스라인 없음" in warning and "bounded_implementation" in warning
        for warning in report.warnings
    )
    assert not any("반복 부족" in warning for warning in report.warnings)
    rendered = render(report)
    assert "베이스라인 없음: bounded_implementation" in rendered


def test_defect_rework_and_cost_columns_are_reported_per_task():
    """Raw sums compare wrongly across groups with different n -- 2 defects
    over 4 samples looks better than 3 over 10 at a glance -- and every ADR
    criterion using these numbers is a per-task comparison against the
    baseline."""
    records = [
        parse_record(
            _record(
                critical_defects_found=1,
                rework_count=2,
                cost={"provider": "codex", "input_tokens": 100, "output_tokens": 50},
            )
        ),
        parse_record(
            _record(
                critical_defects_found=3,
                rework_count=0,
                cost={"provider": "codex", "input_tokens": 300, "output_tokens": 50},
            )
        ),
    ]

    report = build_report(_fixtures(), records)

    (row,) = report.rows.values()
    cell = row[0]
    assert cell.samples == 2
    assert cell.critical_defects_per_task == 2.0
    assert cell.rework_per_task == 1.0
    assert cell.cost_tokens_per_task == 250.0
    assert "defects/task" in render(report)
    assert "rework/task" in render(report)
    # Named for what it holds: freshly processed tokens, not carried context.
    assert "신규토큰/task" in render(report)


def test_repeats_of_a_single_task_show_up_as_one_task_not_five():
    """The repeats-per-type gate counts records, not distinct fixtures, so one
    task run five times clears it. This does not invent a new threshold --
    it makes the shape of the sample visible so a reader can judge it."""
    report = build_report(_fixtures(), [parse_record(_record()) for _ in range(5)])

    (row,) = report.rows.values()
    assert row[0].samples == 5
    assert row[0].tasks == 1
    assert "| arm | n | tasks |" in render(report)


def test_different_worker_counts_do_not_pool_into_one_cell():
    """legacy@2 vs radio_lite@2 is the controlled comparison two-worker
    support exists to make possible. A one-worker and a two-worker run of the
    same mode must not silently average into a single row."""
    report = build_report(
        _fixtures(),
        [
            parse_record(_record(workers=1)),
            parse_record(_record(workers=2)),
        ],
    )

    (row,) = report.rows.values()
    assert len(row) == 2
    assert {cell.samples for cell in row} == {1, 1}


def test_a_two_worker_comparison_reads_against_its_own_baseline():
    """`legacy@2 vs radio_lite@2` is the comparison the workers axis exists to
    enable, so it has to be able to read as met. A baseline pinned at one worker
    made it permanently unmeetable -- every two-worker group reported
    "베이스라인 없음" while holding a legacy arm -- and did not disclose that it
    meant no *one-worker* legacy arm."""
    fixtures: dict = {}
    records = []
    for i in range(20):
        fixture = parse_fixture(
            _definition(id=f"u{i}"), sha256=f"u{i}-sha", commit_exists=_ANY_COMMIT
        )
        fixtures[fixture.id] = fixture
        for mode in ("legacy", "radio_lite"):
            records.append(
                parse_record(
                    _record(
                        fixture_id=fixture.id,
                        fixture_sha256=fixture.sha256,
                        mode=mode,
                        workers=2,
                    )
                )
            )

    report = build_report(fixtures, records)

    assert report.warnings == ()
    assert "베이스라인 없음" not in render(report)
    (row,) = report.rows.values()
    assert row[0].arm == Arm("legacy", False, workers=2)


def test_a_missing_baseline_names_the_worker_count_it_is_missing_at():
    """"베이스라인 없음" on a two-worker group means no *two-worker* legacy arm.
    A reader who cannot see which one is missing looks for the wrong records --
    and a one-worker legacy arm sitting right there in the same group makes the
    bare message read as simply wrong."""
    report = build_report(
        _fixtures(),
        [
            parse_record(_record(mode="legacy", workers=1)),
            parse_record(_record(mode="radio_lite", workers=2)),
        ],
    )

    assert any("legacy@2" in warning for warning in report.warnings)
    assert "legacy@2" in render(report)


def test_each_worker_count_is_ordered_from_its_own_baseline():
    """Baseline-first is dead weight if the promotion can only ever fire for
    one worker count; a synthetic mode sorting before 'legacy' is what proves
    it fires at all."""
    baseline_two = Arm("legacy", False, workers=2)
    other_two = Arm("aardvark", False, workers=2)

    assert _arm_order({other_two: [], baseline_two: []}) == [baseline_two, other_two]
    assert _arm_order({other_two: [], BASELINE_ARM: [], baseline_two: []}) == [
        BASELINE_ARM,
        baseline_two,
        other_two,
    ]


def test_the_rubric_tells_a_grader_to_write_the_worker_count():
    """Nothing propagates artifact.workers into a hand-authored record, and
    parse_record tolerates its absence by design, so the only thing standing
    between a two-worker record and the one-worker cell is the instruction."""
    rubric = (
        Path(__file__).resolve().parents[1]
        / "evaluation"
        / "agent_radio"
        / "rubric.md"
    ).read_text(encoding="utf-8")

    assert "workers" in rubric
    # Naming the field is not enough: the tolerance is what makes omitting it
    # silent, so the procedure has to say that omitting it pools the record.
    assert "1워커" in rubric


def test_a_two_worker_arm_is_labelled_distinctly():
    assert Arm("legacy", False, workers=2).label == "legacy@2"
    assert Arm("legacy", False, workers=1).label == "legacy"


def test_arm_order_puts_the_baseline_first_even_against_the_alphabet():
    """Every mode in the real vocabulary already sorts after 'legacy', so a
    test built only from real modes would still pass if the baseline-first
    promotion in _arm_order were deleted outright. A synthetic mode name that
    sorts before 'legacy' is what actually exercises the promotion."""
    other = Arm("aardvark", False)

    assert _arm_order({other: [], BASELINE_ARM: []}) == [BASELINE_ARM, other]


def test_legacy_that_negotiated_is_not_the_baseline():
    """The baseline is legacy with the feature off.

    If a negotiated legacy run could stand in as the baseline, Stage 1 would be
    compared against itself and its gate -- "improves on legacy" -- would read
    as met no matter what the numbers said.
    """
    negotiated = Arm("legacy", True)

    assert negotiated != BASELINE_ARM
    assert _arm_order({negotiated: []}) == [negotiated]
