import pytest

from personal_agent_gateway.remote_model_client import (
    RemoteRunFailedError,
    RemoteRunProtocolError,
    _decode_event,
    _terminal_result,
)


@pytest.mark.parametrize(
    "kind",
    ["message.snapshot", "output.completed", "run.retrying"],
)
def test_decode_event_accepts_extended_kinds(kind: str) -> None:
    event = _decode_event(kind, '{"kind": "%s", "run_id": "run-1"}' % kind)

    assert event["kind"] == kind


def test_decode_event_still_rejects_unknown_kind() -> None:
    with pytest.raises(RemoteRunProtocolError):
        _decode_event("message.bogus", '{"kind": "message.bogus", "run_id": "run-1"}')


def test_decode_event_passes_through_unknown_fields() -> None:
    event = _decode_event(
        "tool.activity",
        '{"kind": "tool.activity", "run_id": "run-1", "subagent": {"parent_tool_call_id": "t1"}}',
    )

    assert event["subagent"] == {"parent_tool_call_id": "t1"}


@pytest.mark.parametrize(
    "code",
    ["provider_auth_required", "provider_rate_limited"],
)
def test_terminal_result_accepts_extended_failure_codes(code: str) -> None:
    terminal = {
        "kind": "run.failed",
        "run_id": "run-1",
        "error": "boom",
        "error_code": code,
    }

    with pytest.raises(RemoteRunFailedError) as raised:
        _terminal_result(terminal, None)

    assert raised.value.code == code


def test_terminal_result_still_rejects_unknown_failure_code() -> None:
    terminal = {
        "kind": "run.failed",
        "run_id": "run-1",
        "error": "boom",
        "error_code": "provider_made_up",
    }

    with pytest.raises(RemoteRunProtocolError):
        _terminal_result(terminal, None)
