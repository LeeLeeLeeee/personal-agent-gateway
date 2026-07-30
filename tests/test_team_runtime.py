import json
from dataclasses import dataclass

import pytest

from personal_agent_gateway.archive import ArchiveService
from personal_agent_gateway.db import Database
from personal_agent_gateway.events import EventBus
from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.personas import PersonaService
from personal_agent_gateway.team_cycles import TeamCycleService
from personal_agent_gateway.team_outcomes import TaskOutcome
from personal_agent_gateway.team_runtime import (
    WORKER_PROMPT,
    TeamRuntime,
    _parse_acceptance_review_resolution,
    _parse_task_plan,
    _rules_block,
    _task_delta,
    _terminal_status,
)
from personal_agent_gateway.teams import TaskAcceptance, TeamRunService


@dataclass
class FakeModel:
    content: str
    normalize_worker: bool = True

    async def complete(self, messages):
        content = _complete_plan_fixture(self.content)
        if self.normalize_worker and _is_worker_prompt(messages):
            content = _complete_worker_fixture(content)
        return ModelResponse(content=content, tool_calls=[])


@dataclass
class ScriptedModel:
    """호출마다 responses에서 순서대로 반환. 소진되면 마지막 값 반복."""
    responses: list
    normalize_worker: bool = True

    def __post_init__(self):
        self._calls = 0
        self._is_worker = False
        self.messages = []

    async def complete(self, messages):
        self.messages.append(messages)
        self._is_worker = self._is_worker or _is_worker_prompt(messages)
        idx = min(self._calls, len(self.responses) - 1)
        self._calls += 1
        value = self.responses[idx]
        if isinstance(value, Exception):
            raise value
        content = _complete_plan_fixture(value)
        if self.normalize_worker and self._is_worker:
            content = _complete_worker_fixture(content)
        return ModelResponse(
            content=content,
            tool_calls=[],
            upstream_session_id=f"sess-{self._calls}",
        )


def _complete_plan_fixture(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not isinstance(parsed, list) or any(
        not isinstance(item, dict)
        or "title" not in item
        or "description" not in item
        for item in parsed
    ):
        return value
    for item in parsed:
        item.setdefault("owner_agent_id", None)
        item.setdefault("required", True)
        item.setdefault(
            "acceptance",
            {
                "required_outputs": [],
                "required_verifications": ["worker-result"],
            },
        )
    return json.dumps(parsed)


def _is_worker_prompt(messages) -> bool:
    return any(
        "CONCRETE ASSIGNMENT" in str(message.get("content", ""))
        for message in messages
    )


def _complete_worker_fixture(value):
    if not isinstance(value, str) or '"needs_info"' in value:
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and set(parsed) == {
        "status",
        "summary",
        "reason_code",
        "deliverables",
        "verifications",
    }:
        return value
    return json.dumps(
        {
            "status": "completed",
            "summary": value,
            "reason_code": None,
            "deliverables": [],
            "verifications": [
                {
                    "name": "worker-result",
                    "status": "passed",
                    "evidence": "test fixture response",
                }
            ],
        }
    )


def _factory_by_role(
    leader_responses,
    worker_responses,
    *,
    normalize_worker=True,
):
    from personal_agent_gateway.teams import TeamAgent
    models = {}
    def factory(agent: TeamAgent, _cycle_id: str | None = None):
        if agent.id not in models:
            responses = leader_responses if agent.role == "leader" else worker_responses
            models[agent.id] = ScriptedModel(
                list(responses),
                normalize_worker=normalize_worker if agent.role != "leader" else True,
            )
        return models[agent.id]
    return factory


def test_worker_prompt_presents_a_complete_concrete_assignment() -> None:
    prompt = WORKER_PROMPT.format(
        persona_snapshot_json="{}",
        goal="Summarize the mail",
        task_title="Read mail context",
        task_description="Read CYCLES/cycle-1/MAIL_CONTEXT.md",
    )

    assert "Perform the concrete assignment below now" in prompt
    assert "Do not ask the user what work to do" in prompt
    assert "Read CYCLES/cycle-1/MAIL_CONTEXT.md" in prompt
    assert "changed files" not in prompt
    assert '"deliverables"' in prompt
    assert '"verifications"' in prompt
    assert "final response must contain only" in prompt


def test_worker_prompt_uses_cycle_space_instead_of_run_space(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    cycle_policy = dict(cycle.space_policy or {})
    cycle_policy["read_mode"] = "all"
    db.execute(
        "update team_run_cycles set space_policy_snapshot_json = ? where id = ?",
        (json.dumps(cycle_policy), cycle.id),
    )
    task = teams.create_task(
        run.id,
        "Inspect",
        "Inspect the source",
        cycle_id=cycle.id,
    )
    worker_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.role == "member"
    )
    runtime = TeamRuntime(teams, lambda _agent: FakeModel("done"))

    prompt = runtime._worker_prompt(run, worker_agent, task)

    assert "SPACE POLICY (frozen at cycle start):" in prompt
    assert "- Read scope: all" in prompt
    assert "- Read scope: none" not in prompt
    assert "- Write mode: isolated" in prompt


def test_worker_prompt_without_cycle_keeps_run_space(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    task = teams.create_task(run.id, "Inspect", "Inspect the source")
    worker_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.role == "member"
    )
    runtime = TeamRuntime(teams, lambda _agent: FakeModel("done"))

    prompt = runtime._worker_prompt(run, worker_agent, task)

    assert "SPACE POLICY (frozen at run start):" in prompt
    assert "- Read scope: none" in prompt
    assert "- Write mode: isolated" in prompt


@pytest.mark.asyncio
async def test_add_work_passes_cycle_space_to_leader_model_factory(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [],
        "planning_only",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    seen_cycle_ids: list[str | None] = []

    def model_factory(_agent, cycle_id=None):
        seen_cycle_ids.append(cycle_id)
        return FakeModel('[{"title":"Inspect","description":"Inspect the source"}]')

    runtime = TeamRuntime(teams, model_factory)

    await runtime.add_work(run.id, "Inspect the source", cycle.id)

    assert seen_cycle_ids == [cycle.id]


@pytest.mark.asyncio
async def test_worker_final_response_is_parsed_as_task_outcome(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(run.id, "T", "D")
    model = FakeModel(
        json.dumps(
            {
                "status": "completed",
                "summary": "Done",
                "reason_code": None,
                "deliverables": [],
                "verifications": [
                    {"name": "review", "status": "passed", "evidence": "checked"}
                ],
            }
        )
    )
    runtime = TeamRuntime(teams, lambda _agent: model)

    outcome = await runtime._run_task(run, leader_agent, worker_agent, task)

    assert isinstance(outcome, TaskOutcome)
    assert outcome.status == "completed"
    assert outcome.summary == "Done"


@pytest.mark.asyncio
async def test_fenced_worker_outcome_reaches_normal_acceptance_path(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(
        run.id,
        "Inspect",
        "Inspect dashboard",
        acceptance=TaskAcceptance((), ("pytest",)),
    )
    payload = json.dumps(
        {
            "status": "completed",
            "summary": "Done",
            "reason_code": None,
            "deliverables": [],
            "verifications": [
                {
                    "name": "pytest",
                    "status": "passed",
                    "evidence": "tests passed",
                }
            ],
        }
    )
    runtime = TeamRuntime(
        teams,
        lambda _agent: FakeModel(
            f"```json\n{payload}\n```",
            normalize_worker=False,
        ),
    )

    outcome = await runtime._run_task(
        run,
        leader_agent,
        worker_agent,
        task,
    )

    assert outcome.status == "completed"
    assert outcome.reason_code is None
    assert outcome.verifications[0].name == "pytest"


@pytest.mark.asyncio
async def test_worker_prose_becomes_invalid_task_outcome(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    leader_agent, worker_agent = teams.list_agents(run.id)
    task = teams.create_task(run.id, "T", "D")
    runtime = TeamRuntime(
        teams,
        lambda _agent: FakeModel(
            "권한이 없어 실패했습니다.",
            normalize_worker=False,
        ),
    )

    outcome = await runtime._run_task(run, leader_agent, worker_agent, task)

    assert isinstance(outcome, TaskOutcome)
    assert outcome.status == "blocked"
    assert outcome.reason_code == "invalid_task_outcome"
    assert outcome.summary == "권한이 없어 실패했습니다."


@pytest.mark.asyncio
async def test_worker_prose_cannot_complete_team_run(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    worker = personas.create_persona("Worker", "worker", "d", [], [])
    run = teams.create_team_run(
        "goal",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    plan = '[{"title":"T","description":"D"}]'
    runtime = TeamRuntime(
        teams,
        _factory_by_role(
            [plan, "Everything is done."],
            ["I could not inspect files."],
            normalize_worker=False,
        ),
    )

    result = await runtime.start(run.id)

    assert result.status == "blocked"
    task = teams.list_tasks(run.id)[0]
    assert task.status == "blocked"
    assert task.outcome is not None
    assert task.outcome["reason_code"] == "invalid_task_outcome"
    assert task.acceptance_result is not None
    assert task.acceptance_result["accepted"] is False


@pytest.mark.parametrize(
    ("required_status", "optional_status", "expected"),
    [
        ("failed", "completed", "failed"),
        ("blocked", "completed", "blocked"),
        ("completed", "failed", "completed_with_failures"),
        ("completed", "blocked", "completed_with_failures"),
        ("completed", "completed", "completed"),
    ],
)
def test_terminal_status_respects_required_tasks(
    tmp_path,
    required_status,
    optional_status,
    expected,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    required = teams.create_task(
        run.id,
        "required",
        "D",
        required=True,
        acceptance=TaskAcceptance((), ("review",)),
    )
    optional = teams.create_task(
        run.id,
        "optional",
        "D",
        required=False,
        acceptance=TaskAcceptance((), ("review",)),
    )
    teams.set_task_status(required.id, required_status)
    teams.set_task_status(optional.id, optional_status)

    assert _terminal_status(teams.list_tasks(run.id)) == expected


def test_task_plan_requires_and_returns_immutable_acceptance() -> None:
    tasks = _parse_task_plan(
        json.dumps(
            [
                {
                    "title": "Create D3 guide",
                    "description": "Write the integrated guide.",
                    "owner_agent_id": "worker-1",
                    "required": True,
                    "acceptance": {
                        "required_outputs": ["outputs/d3-guide.md"],
                        "required_verifications": ["markdown-link-check"],
                    },
                }
            ]
        )
    )

    assert tasks == [
        {
            "title": "Create D3 guide",
            "description": "Write the integrated guide.",
            "owner_agent_id": "worker-1",
            "required": True,
            "acceptance": TaskAcceptance(
                required_outputs=("outputs/d3-guide.md",),
                required_verifications=("markdown-link-check",),
            ),
        }
    ]


def test_acceptance_review_resolution_parses_worker_retry() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "retry_worker",
                    "instruction": "Remove the undeclared deliverable and resubmit.",
                    "reason": "The contract declares no output.",
                }
            }
        )
    )

    assert resolution.kind == "retry_worker"
    assert resolution.reason == "The contract declares no output."
    assert resolution.instruction == "Remove the undeclared deliverable and resubmit."
    assert resolution.acceptance is None
    assert resolution.decision is None
    assert resolution.reason_code is None


def test_acceptance_review_resolution_parses_revised_acceptance() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "revise_acceptance",
                    "acceptance": {
                        "required_outputs": ["docs/knowledge/d3-review.md"],
                        "required_verifications": ["source-check"],
                    },
                    "instruction": "Resubmit the document under the revised contract.",
                    "reason": "The task goal requires a reusable draft.",
                }
            }
        )
    )

    assert resolution.kind == "revise_acceptance"
    assert resolution.reason == "The task goal requires a reusable draft."
    assert resolution.instruction == "Resubmit the document under the revised contract."
    assert resolution.acceptance == TaskAcceptance(
        required_outputs=("docs/knowledge/d3-review.md",),
        required_verifications=("source-check",),
    )
    assert resolution.decision is None
    assert resolution.reason_code is None


def test_acceptance_review_resolution_parses_user_question() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "ask_user",
                    "topic": "publication scope",
                    "question": "Should this be published?",
                    "why_needed": "The goal is ambiguous.",
                    "options": [],
                    "recommended_option_id": None,
                    "blocking_scope": "task",
                }
            }
        )
    )

    assert resolution.kind == "ask_user"
    assert resolution.reason == "The goal is ambiguous."
    assert resolution.instruction is None
    assert resolution.acceptance is None
    assert resolution.decision == {
        "kind": "ask_user",
        "topic": "publication scope",
        "question": "Should this be published?",
        "why_needed": "The goal is ambiguous.",
        "options": [],
        "recommended_option_id": None,
        "blocking_scope": "task",
    }
    assert resolution.reason_code is None


def test_acceptance_review_resolution_parses_terminal_failure() -> None:
    resolution = _parse_acceptance_review_resolution(
        json.dumps(
            {
                "resolution": {
                    "kind": "fail",
                    "reason_code": "unrecoverable_contract",
                    "summary": "The request conflicts with frozen rules.",
                }
            }
        )
    )

    assert resolution.kind == "fail"
    assert resolution.reason == "The request conflicts with frozen rules."
    assert resolution.instruction is None
    assert resolution.acceptance is None
    assert resolution.decision is None
    assert resolution.reason_code == "unrecoverable_contract"


def _ask_user_review_resolution(**updates: object) -> dict[str, object]:
    resolution: dict[str, object] = {
        "kind": "ask_user",
        "topic": "publication scope",
        "question": "Should this be published?",
        "why_needed": "The goal is ambiguous.",
        "options": [
            {
                "id": "publish",
                "label": "Publish",
                "impact": "Makes the draft public.",
            }
        ],
        "recommended_option_id": "publish",
        "blocking_scope": "task",
    }
    resolution.update(updates)
    return resolution


def test_acceptance_review_resolution_rejects_unknown_outer_fields() -> None:
    content = json.dumps(
        {
            "resolution": _ask_user_review_resolution(),
            "unexpected": "not allowed",
        }
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    "options",
    [None, {}, "not-a-list"],
)
def test_acceptance_review_resolution_rejects_non_list_user_options(
    options: object,
) -> None:
    content = json.dumps(
        {"resolution": _ask_user_review_resolution(options=options)}
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    "option",
    [
        "not-an-object",
        {"id": "publish", "label": "Publish"},
        {
            "id": "publish",
            "label": "Publish",
            "impact": "Public.",
            "unexpected": "not allowed",
        },
        {"id": "", "label": "Publish", "impact": "Public."},
        {"id": 1, "label": "Publish", "impact": "Public."},
        {"id": "publish", "label": "", "impact": "Public."},
        {"id": "publish", "label": None, "impact": "Public."},
        {"id": "publish", "label": "Publish", "impact": None},
    ],
)
def test_acceptance_review_resolution_rejects_malformed_user_option(
    option: object,
) -> None:
    content = json.dumps(
        {"resolution": _ask_user_review_resolution(options=[option])}
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("topic", ""),
        ("topic", None),
        ("question", "   "),
        ("question", 1),
        ("why_needed", ""),
        ("why_needed", None),
    ],
)
def test_acceptance_review_resolution_rejects_invalid_user_text_fields(
    field: str,
    value: object,
) -> None:
    content = json.dumps(
        {"resolution": _ask_user_review_resolution(**{field: value})}
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize("recommended", ["", "   ", 1, []])
def test_acceptance_review_resolution_rejects_invalid_recommended_option(
    recommended: object,
) -> None:
    content = json.dumps(
        {
            "resolution": _ask_user_review_resolution(
                recommended_option_id=recommended
            )
        }
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize("blocking_scope", ["cycle", "", None, 1])
def test_acceptance_review_resolution_rejects_invalid_blocking_scope(
    blocking_scope: object,
) -> None:
    content = json.dumps(
        {
            "resolution": _ask_user_review_resolution(
                blocking_scope=blocking_scope
            )
        }
    )

    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(content)


@pytest.mark.parametrize(
    "resolution",
    [
        {"kind": "retry_worker", "instruction": "Retry.", "reason": ""},
        {
            "kind": "revise_acceptance",
            "instruction": "Retry.",
            "reason": "Missing contract.",
            "acceptance": {
                "required_outputs": [],
                "required_verifications": [],
            },
        },
        {
            "kind": "revise_acceptance",
            "instruction": "Retry.",
            "reason": "Duplicate output.",
            "acceptance": {
                "required_outputs": ["docs/review.md", "docs/review.md"],
                "required_verifications": [],
            },
        },
        {
            "kind": "revise_acceptance",
            "instruction": "Retry.",
            "reason": "Unsafe output.",
            "acceptance": {
                "required_outputs": ["../outside.md"],
                "required_verifications": [],
            },
        },
        {
            "kind": "ask_user",
            "topic": "scope",
            "question": "Publish it?",
            "why_needed": "The goal is ambiguous.",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
            "unexpected": "not allowed",
        },
        {
            "kind": "ask_user",
            "topic": "scope",
            "question": "Publish it?",
            "why_needed": "",
            "options": [],
            "recommended_option_id": None,
            "blocking_scope": "task",
        },
        {"kind": "approve", "reason": "No rejection remains."},
    ],
)
def test_acceptance_review_resolution_rejects_invalid_lead_decisions(
    resolution: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Invalid acceptance review resolution"):
        _parse_acceptance_review_resolution(json.dumps({"resolution": resolution}))


def test_task_plan_accepts_one_outer_json_fence() -> None:
    tasks = _parse_task_plan(
        """```json
[{
  "title": "Create D3 guide",
  "description": "Write the integrated guide.",
  "owner_agent_id": null,
  "required": true,
  "acceptance": {
    "required_outputs": ["outputs/d3-guide.md"],
    "required_verifications": ["markdown-link-check"]
  }
}]
```"""
    )

    assert tasks[0]["title"] == "Create D3 guide"
    assert tasks[0]["acceptance"] == TaskAcceptance(
        required_outputs=("outputs/d3-guide.md",),
        required_verifications=("markdown-link-check",),
    )


@pytest.mark.parametrize(
    "payload",
    [
        "before\n```json\n[]\n```",
        "```json\n[]\n```\nafter",
        "```JSON\n[]\n```",
        "```json\n[{\n```",
    ],
)
def test_task_plan_rejects_ambiguous_json_envelopes(payload: str) -> None:
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _parse_task_plan(payload)


@pytest.mark.parametrize(
    "task",
    [
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "acceptance": {
                "required_outputs": [],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": ["C:/absolute.txt"],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": ["outputs/../secret.txt"],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": ["outputs/a.txt", "outputs/a.txt"],
                "required_verifications": ["pytest"],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": [],
                "required_verifications": [""],
            },
        },
        {
            "title": "T",
            "description": "D",
            "owner_agent_id": None,
            "required": True,
            "acceptance": {
                "required_outputs": [],
                "required_verifications": ["pytest"],
            },
            "unexpected": True,
        },
    ],
)
def test_task_plan_rejects_incomplete_or_unsafe_acceptance(task) -> None:
    with pytest.raises(ValueError):
        _parse_task_plan(json.dumps([task]))


def test_cycle_objective_replaces_blank_triggered_run_goal(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    cycles = TeamCycleService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace", cycle_service=cycles)
    leader = personas.create_persona("Lead", "Planning", "Plans", [], [])
    run = teams.create_team_run(
        "",
        leader.id,
        [],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    request = cycles.enqueue_request(
        run.id,
        "manual",
        "manual-1",
        "Review the new release",
        previous_cycle_id=None,
    )
    cycles.claim_next(run.id)
    cycle = teams.create_cycle(
        run.id, "manual", request.source_id, request_id=request.id
    )
    runtime = TeamRuntime(teams, lambda _agent: FakeModel("[]"))

    assert runtime._goal_context(run, cycle.id) == "Review the new release"


def test_task_delta_keeps_cycle_id(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "Planning", "Plans", [], [])
    run = teams.create_team_run(
        "Plan",
        leader.id,
        [],
        "planning_only",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    task = teams.create_task(run.id, "Inspect", "Inspect dashboard", cycle_id=cycle.id)

    assert _task_delta(task)["cycle_id"] == cycle.id


@pytest.mark.asyncio
async def test_planning_only_creates_tasks_and_completes_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel(
            '[{"title":"Define schema","description":"Add team tables"},'
            '{"title":"Design UI","description":"Add team screens"}]'
        ),
    )

    completed = await runtime.start(run.id)

    assert completed.status == "completed"
    assert [task.title for task in teams.list_tasks(run.id)] == ["Define schema", "Design UI"]
    assert "Planning completed" in teams.list_messages(run.id)[-1].content
    leader_agent = teams.list_agents(run.id)[0]
    assert leader_agent.status == "completed"


@pytest.mark.asyncio
async def test_planning_failure_fails_run_and_settles_leader(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel("not json at all"),
    )

    failed = await runtime.start(run.id)

    assert failed.status == "failed"
    assert failed.error_message
    assert teams.list_agents(run.id)[0].status == "failed"


@pytest.mark.asyncio
async def test_runtime_failure_redacts_environment_secret_from_state_and_event(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    bus = EventBus()
    monkeypatch.setenv("OPENAI_API_KEY", "backend-secret")
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: ScriptedModel(
            [RuntimeError("backend leaked backend-secret")]
        ),
        event_bus=bus,
    )

    failed = await runtime.start(run.id)

    assert "backend-secret" not in (failed.error_message or "")
    assert "[redacted]" in (failed.error_message or "")
    assert "backend-secret" not in str(bus.recent())


@pytest.mark.asyncio
async def test_plan_and_execute_assigns_tasks_to_workers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    backend = personas.create_persona("Backend", "Development", "Builds APIs.", ["Build"], [])
    worker = personas.create_persona("QA Tester", "Quality", "Checks work.", ["Test"], [])
    run = teams.create_team_run(
        "Build teams", leader.id, [backend.id, worker.id], "plan_and_execute", 1
    )
    qa_agent = next(agent for agent in teams.list_agents(run.id) if agent.persona_id == worker.id)

    responses = iter([
        json.dumps([{
            "title": "Verify API",
            "description": "Check team run endpoints",
            "owner_agent_id": qa_agent.id,
        }]),
        "Verified API behavior. No files changed. Evidence: tests passed.",
        "Summary: API endpoints verified successfully.",
    ])
    runtime = TeamRuntime(teams=teams, model_factory=lambda _agent: FakeModel(next(responses)))

    completed = await runtime.start(run.id)

    tasks = teams.list_tasks(run.id)
    messages = teams.list_messages(run.id)
    assert completed.status == "completed"
    assert tasks[0].status == "completed"
    assert tasks[0].owner_agent_id == qa_agent.id
    assert "Verified API behavior" in tasks[0].result
    assert any(message.kind == "agent_output" for message in messages)


@pytest.mark.asyncio
async def test_add_work_keeps_leader_selected_owner(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("Lead", "Planning", "Plans", ["Assign"], [])
    frontend = personas.create_persona(
        "Frontend", "Frontend development", "Builds UI", ["Implement React UI"], []
    )
    database = personas.create_persona(
        "Database", "Database development", "Builds schema", ["Design schema"], []
    )
    run = teams.create_team_run(
        "Improve dashboard",
        leader.id,
        [frontend.id, database.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-1")
    frontend_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.persona_id == frontend.id
    )
    plan = json.dumps([{
        "title": "Build dashboard widget",
        "description": "Implement the React widget",
        "owner_agent_id": frontend_agent.id,
    }])
    runtime = TeamRuntime(teams, lambda _agent, _cycle_id=None: FakeModel(plan))

    tasks = await runtime.add_work(run.id, "Add a dashboard widget", cycle.id)

    assert tasks[0].owner_agent_id == frontend_agent.id


@pytest.mark.asyncio
async def test_plan_and_execute_with_no_workers_fails_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "plan_and_execute", 1)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel(
            '[{"title":"Verify API","description":"Check team run endpoints"}]'
        ),
    )

    result = await runtime.start(run.id)

    assert result.status == "failed"
    assert result.error_message and "worker" in result.error_message
    assert result.status != "completed"


@pytest.mark.asyncio
async def test_team_runtime_publishes_team_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    leader = personas.create_persona("Tech Lead", "Planning", "Plans work.", ["Plan"], [])
    run = teams.create_team_run("Build teams", leader.id, [], "planning_only", 1)
    bus = EventBus()
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel('[{"title":"Define schema","description":"Add tables"}]'),
        event_bus=bus,
    )

    await runtime.start(run.id)

    event_types = [event["type"] for event in bus.recent()]
    assert "team.run.started" in event_types
    assert "team.task.created" in event_types
    assert "team.run.completed" in event_types


@pytest.mark.asyncio
async def test_partial_failure_yields_completed_with_failures(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = (
        '[{"title":"T1","description":"d1"},'
        '{"title":"T2","description":"d2","required":false}]'
    )
    # 워커: T1 성공, T2 예외
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "summary"], ["ok result", RuntimeError("boom")]),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed_with_failures"
    tasks = teams.list_tasks(run.id)
    assert {t.title: t.status for t in tasks} == {"T1": "completed", "T2": "failed"}


@pytest.mark.asyncio
async def test_all_workers_fail_yields_failed(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "summary"], [RuntimeError("boom")]),
    )
    result = await runtime.start(run.id)
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_worker_query_consumes_round_and_reinvokes(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')

    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [plan, "use schema X"],
            [
                'Working...\n```json\n{"needs_info":{"topic":"schema","question":"what schema?"}}\n```',
                "final result using schema X",
            ],
        ),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.rounds_used == 1
    agent = [a for a in teams.list_agents(run.id) if a.role == "member"][0]
    assert agent.reinvocations == 1
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert "query" in kinds and "answer" in kinds


@pytest.mark.asyncio
async def test_user_decisions_batch_after_independent_tasks_and_resume_with_answers(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    bus = EventBus()
    plan = (
        '[{"title":"Deploy","description":"choose target"},'
        '{"title":"Notify","description":"choose audience"}]'
    )
    needs_target = (
        '```json\n{"needs_info":{"topic":"target","question":"Where deploy?"}}\n```'
    )
    needs_audience = (
        '```json\n{"needs_info":{"topic":"audience","question":"Who gets notified?"}}\n```'
    )
    ask_target = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "target",
                "question": "Where deploy?",
                "why_needed": "Changes configuration.",
                "options": [{"id": "staging", "label": "Staging", "impact": "Safer."}],
                "recommended_option_id": "staging",
                "blocking_scope": "task",
            }
        }
    )
    ask_audience = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "audience",
                "question": "Who gets notified?",
                "why_needed": "Changes recipients.",
                "options": [],
                "recommended_option_id": None,
                "blocking_scope": "task",
            }
        }
    )
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [plan, ask_target, ask_audience, "All decisions applied."],
            [needs_target, needs_audience, "deployed to staging", "notified release team"],
        ),
        event_bus=bus,
    )

    waiting = await runtime.start(run.id)

    assert waiting.status == "waiting_for_user"
    request = teams.get_active_decision_request(run.id)
    assert request is not None
    assert request.status == "awaiting_user"
    assert [item["id"] for item in request.items] == ["Q-001", "Q-002"]
    assert [task.status for task in teams.list_tasks(run.id)] == ["blocked", "blocked"]
    assert "team.run.input_requested" in [event["type"] for event in bus.recent()]
    assert "synthesis" not in [message.kind for message in teams.list_messages(run.id)]

    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "staging", "Q-002": "release team"},
    )
    messages = teams.list_messages(run.id)
    query_ids = {message.content: message.id for message in messages if message.kind == "query"}
    user_answers = {
        message.metadata["query_id"]: message.content
        for message in messages
        if message.kind == "answer" and message.metadata.get("source") == "user_decision"
    }
    assert user_answers == {
        query_ids["Where deploy?"]: "staging",
        query_ids["Who gets notified?"]: "release team",
    }
    completed = await runtime.resume(run.id)

    assert completed.status == "completed"
    assert completed.summary == "All decisions applied."
    assert [task.result for task in teams.list_tasks(run.id)] == [
        "deployed to staging",
        "notified release team",
    ]


@pytest.mark.asyncio
async def test_leader_can_request_user_decision_during_planning_and_resume(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("Deploy the service", leader.id, [member.id], "plan_and_execute", 1)
    ask_environment = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "deployment environment",
                "question": "Deploy to staging or production?",
                "why_needed": "The target changes the execution plan.",
                "options": [
                    {"id": "staging", "label": "Staging", "impact": "Lower risk."},
                    {"id": "production", "label": "Production", "impact": "User-facing."},
                ],
                "recommended_option_id": "staging",
                "blocking_scope": "run",
            }
        }
    )
    plan = '[{"title":"Deploy staging","description":"Deploy to staging"}]'
    leader_model = ScriptedModel([ask_environment, plan, "Deployment completed."])
    worker_model = ScriptedModel(["deployed"])
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    waiting = await runtime.start(run.id)

    assert waiting.status == "waiting_for_user"
    assert teams.list_tasks(run.id) == []
    request = teams.get_active_decision_request(run.id)
    assert request is not None
    assert request.items[0]["stage"] == "planning"
    assert request.items[0]["blocking_task_ids"] == []

    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "staging"},
    )
    completed = await runtime.resume(run.id)

    assert completed.status == "completed"
    assert completed.summary == "Deployment completed."
    assert [task.title for task in teams.list_tasks(run.id)] == ["Deploy staging"]
    assert "Q: Deploy to staging or production?\nA: staging" in (
        leader_model.messages[1][0]["content"]
    )


@pytest.mark.asyncio
async def test_leader_can_request_user_decision_before_final_synthesis(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("Prepare release report", leader.id, [member.id], "plan_and_execute", 1)
    ask_detail = json.dumps(
        {
            "resolution": {
                "kind": "ask_user",
                "topic": "report detail",
                "question": "Should the final report include internal diagnostics?",
                "why_needed": "This changes the final report content.",
                "options": [
                    {"id": "omit", "label": "Omit", "impact": "Concise report."},
                    {"id": "include", "label": "Include", "impact": "More detail."},
                ],
                "recommended_option_id": "omit",
                "blocking_scope": "run",
            }
        }
    )
    leader_model = ScriptedModel([
        '[{"title":"Collect results","description":"Collect release results"}]',
        ask_detail,
        "Final report without internal diagnostics.",
    ])
    worker_model = ScriptedModel(["results collected"])
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda agent: leader_model if agent.role == "leader" else worker_model,
    )

    waiting = await runtime.start(run.id)

    assert waiting.status == "waiting_for_user"
    request = teams.get_active_decision_request(run.id)
    assert request is not None
    assert request.items[0]["stage"] == "synthesis"
    assert [task.status for task in teams.list_tasks(run.id)] == ["completed"]

    teams.answer_decision_request(
        run.id,
        request.id,
        request.revision,
        {"Q-001": "omit"},
    )
    completed = await runtime.resume(run.id)

    assert completed.status == "completed"
    assert completed.summary == "Final report without internal diagnostics."
    assert [message.kind for message in teams.list_messages(run.id)].count("agent_output") == 1
    assert "Q: Should the final report include internal diagnostics?\nA: omit" in (
        leader_model.messages[2][0]["content"]
    )


@pytest.mark.asyncio
async def test_budget_exhausted_rejects_and_best_effort(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    # 예산 0으로 생성 → 즉시 거절 경로
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1, rounds_budget=0)
    plan = '[{"title":"T1","description":"d1"}]'
    needs = 'x\n```json\n{"needs_info":{"topic":"t","question":"q"}}\n```'

    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan], [needs, "best effort final"]),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.rounds_used == 0
    task = teams.list_tasks(run.id)[0]
    assert task.result == "best effort final"
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert "answer" not in kinds  # 중재 없음


@pytest.mark.asyncio
async def test_synthesis_summary_from_leader(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"T1","description":"d1"}]'
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "SYNTHESIZED SUMMARY"], ["result"]),
    )
    result = await runtime.start(run.id)
    assert result.summary == "SYNTHESIZED SUMMARY"
    assert [m.kind for m in teams.list_messages(run.id)].count("synthesis") == 1


@pytest.mark.asyncio
async def test_reinvocation_cap_rejects_after_three(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    # 예산은 넉넉하게 잡아서(캡이 아니라 예산이) 걸림돌이 되지 않도록 한다.
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1, rounds_budget=10)
    plan = '[{"title":"T1","description":"d1"}]'
    needs_q1 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q1?"}}\n```'
    needs_q2 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q2?"}}\n```'
    needs_q3 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q3?"}}\n```'
    needs_q4 = 'x\n```json\n{"needs_info":{"topic":"t","question":"q4?"}}\n```'

    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [plan, "answer1", "answer2", "answer3"],
            [needs_q1, needs_q2, needs_q3, needs_q4, "final result after cap"],
        ),
    )
    result = await runtime.start(run.id)

    assert result.status == "completed"
    # 3번의 중재만 예산을 소비한다 (4번째 needs_info는 캡에 막혀 거절된다).
    assert result.rounds_used == 3
    agent = [a for a in teams.list_agents(run.id) if a.role == "member"][0]
    assert agent.reinvocations == 3
    task = teams.list_tasks(run.id)[0]
    assert task.result == "final result after cap"
    kinds = [m.kind for m in teams.list_messages(run.id)]
    assert kinds.count("query") == 3
    assert kinds.count("answer") == 3


@pytest.mark.asyncio
async def test_cancel_settles_run_and_task(tmp_path):
    import asyncio
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')
    started = asyncio.Event()

    class HangingModel:
        def __init__(self, role): self.role = role
        async def complete(self, messages):
            from personal_agent_gateway.model_client import ModelResponse
            if self.role == "leader":
                return ModelResponse(content=plan, tool_calls=[], upstream_session_id="s")
            started.set()
            await asyncio.sleep(60)  # 워커 실행 중 매달림

    runtime = TeamRuntime(teams=teams, model_factory=lambda a: HangingModel(a.role))
    task = asyncio.create_task(runtime.start(run.id))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert teams.get_team_run(run.id).status == "canceled"
    canceled_task = teams.list_tasks(run.id)[0]
    canceled_worker = [agent for agent in teams.list_agents(run.id) if agent.role == "member"][0]
    assert canceled_task.status == "canceled"
    assert canceled_task.owner_agent_id == canceled_worker.id
    assert canceled_worker.current_task_id is None


@pytest.mark.asyncio
async def test_runtime_publishes_task_and_agent_assignment_deltas(tmp_path):
    import asyncio
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)
    plan = '[{"title":"Visible task","description":"d"}]'
    started = asyncio.Event()
    release = asyncio.Event()

    class GatedWorkerModel:
        async def complete(self, _messages):
            started.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            return ModelResponse(
                content=_complete_worker_fixture("done"),
                tool_calls=[],
            )

    leader_model = ScriptedModel([plan, "summary"])
    bus = EventBus()
    runtime = TeamRuntime(
        teams,
        lambda agent: leader_model if agent.role == "leader" else GatedWorkerModel(),
        bus,
    )
    running = asyncio.create_task(runtime.start(run.id))
    await asyncio.wait_for(started.wait(), timeout=2)

    task = teams.list_tasks(run.id)[0]
    worker = [agent for agent in teams.list_agents(run.id) if agent.role == "member"][0]
    assert teams.get_team_run(run.id).status == "running"
    assert task.owner_agent_id == worker.id
    assert worker.current_task_id == task.id
    assigned = [event for event in bus.recent() if event["type"] == "team.task.updated"][-1]
    assert assigned["task"]["owner_agent_id"] == worker.id
    assert assigned["agent"]["current_task_id"] == task.id

    release.set()
    await running
    event_types = [event["type"] for event in bus.recent()]
    assert "team.run.executing" in event_types
    assert "team.run.summarizing" in event_types


@pytest.mark.asyncio
async def test_execute_drains_task_added_during_execution(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.model_client import ModelResponse
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    state = {"injected": False}
    models = {}

    def factory(agent):
        if agent.role == "leader":
            if agent.id not in models:
                models[agent.id] = ScriptedModel([plan, "summary"])
            return models[agent.id]

        class WorkerModel:
            async def complete(self, messages):
                if not state["injected"]:
                    state["injected"] = True
                    teams.create_task(run.id, "T2", "d2")
                return ModelResponse(
                    content=_complete_worker_fixture("did it"),
                    tool_calls=[],
                )

        return WorkerModel()

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }


@pytest.mark.asyncio
async def test_task_added_during_synthesis_is_executed_before_terminal(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.model_client import ModelResponse
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = _complete_plan_fixture('[{"title":"T1","description":"d1"}]')
    models = {}

    def factory(agent):
        if agent.role == "leader":
            if agent.id not in models:
                class LeaderModel:
                    def __init__(self): self.calls = 0
                    async def complete(self, messages):
                        self.calls += 1
                        if self.calls == 1:
                            return ModelResponse(content=plan, tool_calls=[])
                        if self.calls == 2:
                            # First synthesis pass: user work lands mid-synthesis.
                            teams.create_task(run.id, "T2", "d2")
                            return ModelResponse(content="interim", tool_calls=[])
                        return ModelResponse(content="final summary", tool_calls=[])
                models[agent.id] = LeaderModel()
            return models[agent.id]
        return FakeModel("worker done")

    runtime = TeamRuntime(teams=teams, model_factory=factory)
    result = await runtime.start(run.id)

    assert result.status == "completed"
    assert result.summary == "final summary"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }


@pytest.mark.asyncio
async def test_resume_runs_added_tasks_on_terminal_run(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    plan = '[{"title":"T1","description":"d1"}]'
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role([plan, "summary1", "summary2"], ["r1", "r2"]),
    )
    first = await runtime.start(run.id)
    assert first.status == "completed"

    # Simulate add-work having created a new pending task, then reopen.
    teams.create_task(run.id, "T2", "d2")
    resumed = await runtime.resume(run.id)

    assert resumed.status == "completed"
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "T1": "completed",
        "T2": "completed",
    }


@pytest.mark.asyncio
async def test_resume_restarts_planning_when_interrupted_before_tasks_exist(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [], "planning_only", 1)
    teams.set_run_status(run.id, "planning")
    teams.interrupt_active_runs()
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda _agent: FakeModel('[{"title":"T1","description":"d1"}]'),
    )

    resumed = await runtime.resume(run.id)

    assert resumed.status == "completed"
    assert [task.title for task in teams.list_tasks(run.id)] == ["T1"]


@pytest.mark.asyncio
async def test_resume_prefers_worker_that_was_running_before_interruption(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    finished_worker = personas.create_persona("W1", "planning", "d", [], [])
    interrupted_worker = personas.create_persona("W2", "developer", "d", [], [])
    run = teams.create_team_run(
        "goal", leader.id, [finished_worker.id, interrupted_worker.id], "plan_and_execute", 2
    )
    leader_agent, first_worker, second_worker = teams.list_agents(run.id)
    task = teams.create_task(run.id, "current", "d")
    teams.set_agent_status(first_worker.id, "completed")
    teams.set_agent_status(second_worker.id, "running")
    teams.set_task_status(task.id, "in_progress")
    teams.set_run_status(run.id, "running")
    teams.interrupt_active_runs()
    worker_calls = []

    def factory(agent):
        if agent.id == leader_agent.id:
            return FakeModel("summary")
        worker_calls.append(agent.name)
        return FakeModel("done")

    resumed = await TeamRuntime(teams=teams, model_factory=factory).resume(run.id)

    assert resumed.status == "completed"
    assert worker_calls[0] == "W2"


@pytest.mark.asyncio
async def test_add_work_creates_pending_tasks_from_instruction(tmp_path):
    from personal_agent_gateway.db import Database
    from personal_agent_gateway.personas import PersonaService
    from personal_agent_gateway.teams import TeamRunService

    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path)
    leader = personas.create_persona("L", "lead", "d", [], [])
    member = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run("goal", leader.id, [member.id], "plan_and_execute", 1)

    decomposition = '[{"title":"Extra A","description":"da"},{"title":"Extra B","description":"db"}]'
    runtime = TeamRuntime(teams=teams, model_factory=lambda _agent: FakeModel(decomposition))

    created = await runtime.add_work(run.id, "please also do A and B")

    assert [task.title for task in created] == ["Extra A", "Extra B"]
    assert {t.title: t.status for t in teams.list_tasks(run.id)} == {
        "Extra A": "pending",
        "Extra B": "pending",
    }
    assert any(m.kind == "plan_note" for m in teams.list_messages(run.id))


@pytest.mark.asyncio
async def test_continuous_cycle_with_fenced_plan_creates_tasks_and_resumes(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "manual-fenced-plan")
    fenced_plan = """```json
[{
  "title": "Process request",
  "description": "Produce the requested result.",
  "owner_agent_id": null,
  "required": true,
  "acceptance": {
    "required_outputs": [],
    "required_verifications": ["worker-result"]
  }
}]
```"""
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [fenced_plan, "cycle summary"],
            ["worker result"],
        ),
    )

    created = await runtime.add_work(run.id, "process request", cycle.id)
    completed = await runtime.resume(run.id, cycle.id)

    assert [task.title for task in created] == ["Process request"]
    assert completed.status == "completed"
    assert teams.get_cycle(cycle.id).status == "completed"


@pytest.mark.asyncio
async def test_continuous_run_executes_and_synthesizes_each_cycle_in_isolation(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    first_cycle = teams.create_cycle(run.id, "hook", "hook-run-1")
    second_cycle = teams.create_cycle(run.id, "hook", "hook-run-2")
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            [
                '[{"title":"Mail 1","description":"d1"}]',
                "summary-1",
                '[{"title":"Mail 2","description":"d2"}]',
                "summary-2",
            ],
            ["result-1", "result-2"],
        ),
    )

    await runtime.add_work(run.id, "first mail", first_cycle.id)
    await runtime.resume(run.id, first_cycle.id)
    await runtime.add_work(run.id, "second mail", second_cycle.id)
    completed = await runtime.resume(run.id, second_cycle.id)

    assert completed.status == "completed"
    assert [task.title for task in teams.list_tasks(run.id, first_cycle.id)] == [
        "Mail 1"
    ]
    assert [task.title for task in teams.list_tasks(run.id, second_cycle.id)] == [
        "Mail 2"
    ]
    assert [
        message.content
        for message in teams.list_messages(run.id, first_cycle.id)
        if message.kind == "synthesis"
    ] == ["summary-1"]
    assert [
        message.content
        for message in teams.list_messages(run.id, second_cycle.id)
        if message.kind == "synthesis"
    ] == ["summary-2"]
    assert teams.get_cycle(first_cycle.id).summary == "summary-1"
    assert teams.get_cycle(second_cycle.id).summary == "summary-2"


@pytest.mark.asyncio
async def test_previous_cycle_summary_is_only_added_to_leader_instruction(
    tmp_path,
):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    cycle = teams.create_cycle(run.id, "manual", "client-1")
    leader_model = ScriptedModel(
        [
            '[{"title":"New work","description":"process the next item"}]',
            "done",
        ]
    )
    worker_model = ScriptedModel(["worker result"])
    runtime = TeamRuntime(
        teams,
        lambda agent, _cycle_id=None: (
            leader_model
            if agent.role == "leader"
            else worker_model
        ),
    )
    instruction = (
        "next work\n\nPREVIOUS CYCLE SUMMARY\nprevious result"
    )

    await runtime.add_work(run.id, instruction, cycle.id)
    await runtime.resume(run.id, cycle.id)

    assert "PREVIOUS CYCLE SUMMARY" in (
        leader_model.messages[0][0]["content"]
    )
    assert "PREVIOUS CYCLE SUMMARY" not in (
        worker_model.messages[0][0]["content"]
    )


@pytest.mark.asyncio
async def test_continuous_run_uses_cycle_round_budget_instead_of_run_total(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, tmp_path / "workspace")
    leader = personas.create_persona("L", "lead", "d", [], [])
    worker = personas.create_persona("W", "work", "d", [], [])
    run = teams.create_team_run(
        "mailbox",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
        lifecycle_mode="continuous",
        execution_policy="triggered",
    )
    exhausted_cycle = teams.create_cycle(
        run.id, "hook", "hook-run-1", rounds_budget=1
    )
    active_cycle = teams.create_cycle(
        run.id, "hook", "hook-run-2", rounds_budget=1
    )
    teams.increment_cycle_rounds_used(exhausted_cycle.id)
    teams.create_task(run.id, "Mail 2", "d", cycle_id=active_cycle.id)
    runtime = TeamRuntime(
        teams=teams,
        model_factory=_factory_by_role(
            ['{"resolution":{"kind":"answer","answer":"continue"}}', "summary"],
            [
                '```json\n{"needs_info":{"topic":"scope","question":"Which?"}}\n```',
                "done",
            ],
        ),
    )

    completed = await runtime.resume(run.id, active_cycle.id)

    assert completed.status == "completed"
    assert teams.get_cycle(exhausted_cycle.id).rounds_used == 1
    assert teams.get_cycle(active_cycle.id).rounds_used == 1
    assert teams.get_team_run(run.id).rounds_used == 0
    assert teams.list_tasks(run.id, active_cycle.id)[0].result == "done"


def test_rules_block_empty_when_no_snapshot():
    assert _rules_block(None, include_persona_baseline=True) == ""


def test_rules_block_marks_required_and_guideline():
    snapshot = {
        "global": {"personality": "global voice",
                   "rules": [{"level": "REQUIRED", "text": "no destructive writes"}]},
        "team": {"personality": "team voice",
                 "rules": [{"level": "GUIDELINE", "text": "prefer CRF"}]},
        "persona_baseline": {"personality": "persona voice",
                             "rules": [{"level": "REQUIRED", "text": "cite paths"}]},
    }
    block = _rules_block(snapshot, include_persona_baseline=True)
    assert "global voice" in block
    assert "team voice" in block
    assert "persona voice" in block
    assert "MUST: no destructive writes" in block
    assert "SHOULD: prefer CRF" in block
    assert "MUST: cite paths" in block


def test_rules_block_excludes_persona_baseline_for_leader():
    snapshot = {
        "global": {"personality": "", "rules": []},
        "team": None,
        "persona_baseline": {"personality": "persona voice",
                             "rules": [{"level": "REQUIRED", "text": "cite paths"}]},
    }
    block = _rules_block(snapshot, include_persona_baseline=False)
    assert "persona voice" not in block
    assert "cite paths" not in block


@pytest.mark.asyncio
async def test_team_runtime_uses_archive_and_routes_knowledge_gap_to_library(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    db.initialize()
    personas = PersonaService(db)
    teams = TeamRunService(db, personas, workspace)
    archive = ArchiveService(db)
    leader = personas.create_persona("Lead", "Planning", "Plans work", [], [])
    worker = personas.create_persona("QA", "Quality", "Verifies releases", [], [])
    archive.publish_entry(
        actor_type="user",
        kind="checklist",
        title="Release verification",
        summary="Checks required before a release.",
        content_markdown="Run the smoke suite and attach the test report.",
        tags=["release", "verification"],
        source_urls=[],
        persona_ids=[],
    )
    run = teams.create_team_run(
        "Verify the release",
        leader.id,
        [worker.id],
        "plan_and_execute",
        1,
    )
    worker_agent = next(
        agent for agent in teams.list_agents(run.id) if agent.persona_id == worker.id
    )
    plan = json.dumps(
        [
            {
                "title": "Verify release",
                "description": "Use the release checklist.",
                "owner_agent_id": worker_agent.id,
            }
        ]
    )
    leader_model = ScriptedModel([plan, "Release verification completed."])
    worker_model = ScriptedModel(
        [
            (
                "Smoke suite passed."
                '<knowledge_request>{"title":"Rollback verification",'
                '"reason":"No reusable rollback check exists.",'
                '"suggested_outline":["Trigger rollback","Verify recovery"],'
                '"source_hints":["release runbook"]}</knowledge_request>'
            )
        ]
    )
    runtime = TeamRuntime(
        teams=teams,
        model_factory=lambda agent: (
            leader_model if agent.role == "leader" else worker_model
        ),
        archive_service=archive,
    )

    completed = await runtime.start(run.id)

    assert completed.status == "completed"
    assert "Release verification" in worker_model.messages[0][0]["content"]
    task = teams.list_tasks(run.id)[0]
    assert "<knowledge_request>" not in (task.result or "")
    assert "Library에 요청되었습니다" in (task.result or "")
    requests = archive.list_requests()
    assert len(requests) == 1
    assert requests[0].requested_by_persona_id == worker.id
    assert requests[0].team_run_id == run.id
    assert len(archive.list_entries()) == 1
