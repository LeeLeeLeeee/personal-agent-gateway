import json
from pathlib import Path

import pytest

from agent_radio import fixture as fixture_module
from agent_radio.fixture import (
    FixtureError,
    load_fixture,
    load_fixtures,
    parse_fixture,
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


def test_git_not_on_path_is_reported_as_a_fixture_error(monkeypatch):
    """A caller should get evidence it can act on, not a bare
    FileNotFoundError leaking out of a missing executable."""

    def _missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(fixture_module.subprocess, "run", _missing_git)

    with pytest.raises(FixtureError):
        fixture_module.git_commit_exists("HEAD")
