import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from personal_agent_gateway.model_client import ModelResponse
from personal_agent_gateway.remote_model_client import RemoteRunError
from personal_agent_gateway.team_model_operations import (
    OperationConflict,
    TeamModelOperation,
    TeamModelOperationService,
    ValidatedOperationResult,
)

_SAFE_ADMISSION_CODES = {
    "provider_unavailable",
    "provider_not_ready",
    "capacity_exceeded",
}
_RETRY_DELAYS = (0.5, 1.5)
_MAX_ATTEMPTS = 3


class OperationRemoteClient(Protocol):
    async def complete_operation(
        self,
        messages: list[dict[str, object]],
        *,
        consumer_run_id: str,
    ) -> ModelResponse: ...


class _OperationInvocationError(RuntimeError):
    def __init__(
        self,
        operation_id: str,
        consumer_run_id: str,
        reason_code: str,
    ) -> None:
        super().__init__(reason_code)
        self.operation_id = operation_id
        self.consumer_run_id = consumer_run_id
        self.reason_code = reason_code


class ProviderOperationUnavailable(_OperationInvocationError):
    pass


class AmbiguousModelOperation(_OperationInvocationError):
    pass


class InvalidOperationResult(_OperationInvocationError):
    pass


class TeamModelInvoker:
    def __init__(
        self,
        operations: TeamModelOperationService,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._operations = operations
        self._sleep = sleep

    async def invoke(
        self,
        operation: TeamModelOperation,
        client: OperationRemoteClient,
        messages: list[dict[str, object]],
        parser: Callable[[ModelResponse], ValidatedOperationResult],
    ) -> TeamModelOperation:
        current = self._operations.get(operation.id)
        if current.status == "completed":
            return current
        if current.status != "prepared":
            raise OperationConflict(
                f"Operation status {current.status} cannot start a model call"
            )

        for attempt_index in range(_MAX_ATTEMPTS):
            consumer_run_id = str(uuid.uuid4())
            invoking = self._operations.begin_attempt(current.id, consumer_run_id)
            try:
                response = await client.complete_operation(
                    messages,
                    consumer_run_id=consumer_run_id,
                )
            except RemoteRunError as exc:
                if exc.pre_stream and exc.code in _SAFE_ADMISSION_CODES:
                    if invoking.attempts == _MAX_ATTEMPTS:
                        self._operations.record_invoking_reason(
                            invoking.id,
                            invoking.version,
                            exc.code,
                        )
                        raise ProviderOperationUnavailable(
                            invoking.id,
                            consumer_run_id,
                            exc.code,
                        ) from exc
                    current = self._operations.prepare_retry(
                        invoking.id,
                        invoking.version,
                        exc.code,
                    )
                    await self._sleep(_RETRY_DELAYS[attempt_index])
                    continue
                raise AmbiguousModelOperation(
                    invoking.id,
                    consumer_run_id,
                    exc.code,
                ) from exc

            try:
                result = parser(response)
            except Exception as exc:
                self._operations.mark_failed(
                    invoking.id,
                    invoking.version,
                    "invalid_structured_output",
                    upstream_session_id=response.upstream_session_id,
                )
                raise InvalidOperationResult(
                    invoking.id,
                    consumer_run_id,
                    "invalid_structured_output",
                ) from exc

            try:
                return self._operations.complete(
                    invoking.id,
                    invoking.version,
                    result,
                    upstream_session_id=response.upstream_session_id,
                )
            except OperationConflict as exc:
                self._operations.mark_failed(
                    invoking.id,
                    invoking.version,
                    "invalid_structured_output",
                    upstream_session_id=response.upstream_session_id,
                )
                raise InvalidOperationResult(
                    invoking.id,
                    consumer_run_id,
                    "invalid_structured_output",
                ) from exc

        raise AssertionError("retry loop exited without a result")
