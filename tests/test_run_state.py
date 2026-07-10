import asyncio

import pytest

from personal_agent_gateway.run_state import (
    SessionAlreadyRunningError,
    SessionRunRegistry,
    TeamRunRegistry,
)


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


def test_interrupt_cancels_attached_task() -> None:
    registry = SessionRunRegistry()

    async def scenario() -> bool:
        registry.start("session-1", "request-1")

        async def worker() -> None:
            await asyncio.sleep(60)

        task = asyncio.ensure_future(worker())
        registry.attach_task("session-1", "request-1", task)
        assert registry.interrupt("session-1") is True
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(scenario()) is True


def test_interrupt_returns_false_without_task() -> None:
    registry = SessionRunRegistry()
    registry.start("session-1", "request-1")
    assert registry.interrupt("session-1") is False


def test_attach_task_ignores_mismatched_request_id() -> None:
    registry = SessionRunRegistry()

    async def scenario() -> bool:
        registry.start("session-1", "request-1")

        async def worker() -> None:
            await asyncio.sleep(60)

        task = asyncio.ensure_future(worker())
        registry.attach_task("session-1", "other-request", task)
        result = registry.interrupt("session-1")
        task.cancel()
        return result

    assert asyncio.run(scenario()) is False


@pytest.mark.asyncio
async def test_register_cancel_finish():
    registry = TeamRunRegistry()

    async def sleeper():
        await asyncio.sleep(60)

    task = asyncio.create_task(sleeper())
    registry.register("run-1", task)
    assert registry.is_running("run-1") is True

    assert registry.cancel("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task

    registry.finish("run-1")
    assert registry.is_running("run-1") is False
    assert registry.cancel("run-1") is False
