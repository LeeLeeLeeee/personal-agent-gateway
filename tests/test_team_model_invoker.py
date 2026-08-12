import asyncio
import json

import pytest

from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.remote_model_client import (
    RemoteRunAbortedError,
    RemoteRunFailedError,
)
from personal_agent_gateway.team_model_invoker import (
    AmbiguousModelOperation,
    InvalidOperationResult,
    ProviderOperationUnavailable,
    TeamModelInvoker,
)
from personal_agent_gateway.team_model_operations import (
    OperationConflict,
    OperationSpec,
    TeamModelOperationService,
    ValidatedOperationResult,
)
from team_cycle_helpers import make_cycle_services, make_queued_cycle


def make_operation_service_and_spec(tmp_path, *, upstream_session_id=None):
    db, teams, cycles, run = make_cycle_services(tmp_path, "triggered")
    cycle = make_queued_cycle(teams, cycles, run)
    agent = teams.get_agent(run.leader_agent_id)
    service = TeamModelOperationService(
        db,
        result_validators={
            "cycle_planning": {
                "test": lambda payload: set(payload) == {"ok"}
                and isinstance(payload["ok"], bool)
            }
        },
    )
    return service, OperationSpec(
        operation_key=f"{cycle.id}:planning:0",
        team_run_id=run.id,
        cycle_id=cycle.id,
        task_id=None,
        agent_id=agent.id,
        provider=agent.backend,
        stage="cycle_planning",
        stage_ordinal=0,
        request_digest="a" * 64,
        upstream_session_id=upstream_session_id,
    )


class RecordingOperationClient:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.consumer_run_ids = []

    async def complete_operation(self, messages, *, consumer_run_id):
        self.calls += 1
        self.consumer_run_ids.append(consumer_run_id)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


async def record_delay(delays, delay):
    delays.append(delay)


def parse_test_result(response):
    return ValidatedOperationResult("test", json.loads(response.content))


@pytest.mark.asyncio
async def test_invoker_retries_only_safe_admission_with_same_operation(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    client = RecordingOperationClient(
        [
            RemoteRunFailedError("provider_not_ready", "not_ready", pre_stream=True),
            RemoteRunFailedError("capacity_exceeded", "busy", pre_stream=True),
            ModelResponse(content='{"ok":true}', tool_calls=[]),
        ]
    )
    delays = []
    invoker = TeamModelInvoker(
        service,
        sleep=lambda delay: record_delay(delays, delay),
    )
    reserved = service.reserve(spec)

    operation = await invoker.invoke(
        reserved,
        client,
        [{"role": "user", "content": "work"}],
        parse_test_result,
    )

    assert operation.status == "completed"
    assert operation.attempts == 3
    assert len(set(client.consumer_run_ids)) == 3
    assert service.get(operation.id).id == operation.id
    assert delays == [0.5, 1.5]


@pytest.mark.asyncio
async def test_invoker_never_replays_ambiguous_read_timeout(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    client = RecordingOperationClient([RemoteRunAbortedError("run_timeout", "timeout")])
    reserved = service.reserve(spec)

    with pytest.raises(AmbiguousModelOperation) as raised:
        await TeamModelInvoker(service).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    assert client.calls == 1
    operation = service.get(raised.value.operation_id)
    assert operation.status == "invoking"
    assert operation.consumer_run_id == raised.value.consumer_run_id


@pytest.mark.asyncio
async def test_invoker_maps_response_session_conflict_to_ambiguous_identity(tmp_path):
    service, spec = make_operation_service_and_spec(
        tmp_path,
        upstream_session_id="existing-session",
    )
    client = RecordingOperationClient(
        [
            ModelResponse(
                content='{"ok":true}',
                tool_calls=[],
                upstream_session_id="different-session",
            )
        ]
    )
    reserved = service.reserve(spec)

    with pytest.raises(AmbiguousModelOperation) as raised:
        await TeamModelInvoker(service).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    assert client.calls == 1
    operation = service.get(raised.value.operation_id)
    assert operation.status == "invoking"
    assert operation.consumer_run_id == raised.value.consumer_run_id
    assert operation.upstream_session_id == "existing-session"


@pytest.mark.asyncio
async def test_invoker_maps_parser_failure_with_session_conflict_to_ambiguous(tmp_path):
    service, spec = make_operation_service_and_spec(
        tmp_path,
        upstream_session_id="existing-session",
    )
    client = RecordingOperationClient(
        [
            ModelResponse(
                content="not-json",
                tool_calls=[],
                upstream_session_id="different-session",
            )
        ]
    )
    reserved = service.reserve(spec)

    with pytest.raises(AmbiguousModelOperation) as raised:
        await TeamModelInvoker(service).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    assert client.calls == 1
    operation = service.get(raised.value.operation_id)
    assert operation.status == "invoking"
    assert operation.consumer_run_id == raised.value.consumer_run_id
    assert operation.upstream_session_id == "existing-session"


@pytest.mark.asyncio
async def test_invoker_maps_result_validation_with_session_conflict_to_ambiguous(
    tmp_path,
):
    service, spec = make_operation_service_and_spec(
        tmp_path,
        upstream_session_id="existing-session",
    )
    client = RecordingOperationClient(
        [
            ModelResponse(
                content='{"ok":true}',
                tool_calls=[],
                upstream_session_id="different-session",
            )
        ]
    )
    reserved = service.reserve(spec)

    with pytest.raises(AmbiguousModelOperation) as raised:
        await TeamModelInvoker(service).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            lambda response: ValidatedOperationResult(
                "unregistered",
                json.loads(response.content),
            ),
        )

    assert client.calls == 1
    operation = service.get(raised.value.operation_id)
    assert operation.status == "invoking"
    assert operation.consumer_run_id == raised.value.consumer_run_id
    assert operation.upstream_session_id == "existing-session"


@pytest.mark.asyncio
async def test_invoker_leaves_exhausted_safe_admission_invoking(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    client = RecordingOperationClient(
        [RemoteRunFailedError("provider_not_ready", "not_ready", pre_stream=True)] * 3
    )
    reserved = service.reserve(spec)

    with pytest.raises(ProviderOperationUnavailable) as raised:
        await TeamModelInvoker(service, sleep=lambda _: asyncio.sleep(0)).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    operation = service.get(raised.value.operation_id)
    assert operation.status == "invoking"
    assert operation.attempts == 3
    assert raised.value.reason_code == "provider_not_ready"
    assert client.calls == 3


@pytest.mark.asyncio
async def test_invoker_parks_again_instead_of_overrunning_the_retry_delays(tmp_path):
    """An operation that already spent its attempts must park, not crash.

    attempts accumulates for the life of an operation -- begin_attempt only ever
    increments it and no transition resets it -- so an operation that was parked
    at the cap and later claimed back re-enters invoke with attempts already at
    _MAX_ATTEMPTS. The exhaustion guard compared attempts == _MAX_ATTEMPTS, which
    can never be true once it is past the cap, so the loop ran to its third pass
    and indexed _RETRY_DELAYS (two entries) at 2.
    """
    service, spec = make_operation_service_and_spec(tmp_path)
    reserved = service.reserve(spec)
    with service._db.connection() as connection:
        connection.execute(
            "update team_model_operations set attempts = ? where id = ?",
            (3, reserved.id),
        )
    reserved = service.get(reserved.id)
    assert reserved.status == "prepared" and reserved.attempts == 3
    client = RecordingOperationClient(
        [RemoteRunFailedError("provider_not_ready", "not_ready", pre_stream=True)] * 3
    )

    with pytest.raises(ProviderOperationUnavailable) as raised:
        await TeamModelInvoker(service, sleep=lambda _: asyncio.sleep(0)).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    assert raised.value.reason_code == "provider_not_ready"
    assert service.get(raised.value.operation_id).status == "invoking"
    # It gives up on the first failure rather than burning two more provider
    # calls it was never entitled to.
    assert client.calls == 1


@pytest.mark.asyncio
async def test_invoker_marks_parser_failure_without_agent_session_mutation(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    client = RecordingOperationClient(
        [
            ModelResponse(
                content="not-json",
                tool_calls=[],
                upstream_session_id="response-session",
            )
        ]
    )
    reserved = service.reserve(spec)

    with pytest.raises(InvalidOperationResult) as raised:
        await TeamModelInvoker(service).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    operation = service.get(raised.value.operation_id)
    assert operation.status == "failed"
    assert operation.reason_code == "invalid_structured_output"
    assert operation.upstream_session_id == "response-session"
    agent = service._db.fetchone(
        "select upstream_session_id from team_agents where id = ?",
        (operation.agent_id,),
    )
    assert agent is not None
    assert agent["upstream_session_id"] is None


@pytest.mark.asyncio
async def test_invoker_marks_result_validation_failure_invalid(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    client = RecordingOperationClient(
        [ModelResponse(content='{"ok":true}', tool_calls=[])]
    )
    reserved = service.reserve(spec)

    with pytest.raises(InvalidOperationResult) as raised:
        await TeamModelInvoker(service).invoke(
            reserved,
            client,
            [{"role": "user", "content": "work"}],
            lambda response: ValidatedOperationResult(
                "unregistered",
                json.loads(response.content),
            ),
        )

    operation = service.get(raised.value.operation_id)
    assert operation.status == "failed"
    assert operation.reason_code == "invalid_structured_output"


@pytest.mark.asyncio
async def test_invoker_returns_completed_operation_without_a_model_call(tmp_path):
    service, spec = make_operation_service_and_spec(tmp_path)
    reserved = service.reserve(spec)
    invoking = service.begin_attempt(reserved.id, "consumer-1")
    completed = service.complete(
        invoking.id,
        invoking.version,
        ValidatedOperationResult("test", {"ok": True}),
    )
    client = RecordingOperationClient([])

    result = await TeamModelInvoker(service).invoke(
        completed,
        client,
        [{"role": "user", "content": "work"}],
        parse_test_result,
    )

    assert result == completed
    assert client.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["applied", "ambiguous", "waiting_for_provider"])
async def test_invoker_rejects_terminal_or_recovery_owned_operations(tmp_path, status):
    service, spec = make_operation_service_and_spec(tmp_path)
    reserved = service.reserve(spec)
    service._db.execute(
        "update team_model_operations set status = ? where id = ?",
        (status, reserved.id),
    )
    operation = service.get(reserved.id)
    client = RecordingOperationClient([])

    with pytest.raises(OperationConflict):
        await TeamModelInvoker(service).invoke(
            operation,
            client,
            [{"role": "user", "content": "work"}],
            parse_test_result,
        )

    assert client.calls == 0
