import pytest

from personal_agent_gateway.run_state import SessionAlreadyRunningError, SessionRunRegistry


def test_finish_ignores_stale_request_id_for_same_session() -> None:
    registry = SessionRunRegistry()
    registry.start("session-1", "request-1")

    registry.finish("session-1", "request-2")

    assert registry.is_running("session-1") is True


def test_delete_if_idle_runs_callback_for_idle_session() -> None:
    registry = SessionRunRegistry()
    called = False

    def delete_session() -> bool:
        nonlocal called
        called = True
        return True

    assert registry.delete_if_idle("session-1", delete_session) is True
    assert called is True


def test_delete_if_idle_rejects_running_session_without_callback() -> None:
    registry = SessionRunRegistry()
    registry.start("session-1", "request-1")
    called = False

    def delete_session() -> bool:
        nonlocal called
        called = True
        return True

    with pytest.raises(SessionAlreadyRunningError):
        registry.delete_if_idle("session-1", delete_session)

    assert called is False


def test_start_if_exists_checks_existence_and_marks_running_atomically() -> None:
    registry = SessionRunRegistry()

    registry.start_if_exists("session-1", "request-1", lambda: True)

    assert registry.is_running("session-1") is True


def test_start_if_exists_rejects_deleted_session_without_marking_running() -> None:
    registry = SessionRunRegistry()

    assert registry.start_if_exists("session-1", "request-1", lambda: False) is False
    assert registry.is_running("session-1") is False
