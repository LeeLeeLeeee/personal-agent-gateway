from dataclasses import dataclass
from typing import Generic, Literal, TypeVar
from urllib.parse import quote

import httpx

from personal_agent_gateway.config import AppConfig

LmgStatus = Literal[
    "ready",
    "unreachable",
    "unauthorized",
    "not_ready",
    "protocol_error",
]
T = TypeVar("T")


@dataclass(frozen=True)
class LmgQueryResult(Generic[T]):
    data: T | None
    status: LmgStatus
    message: str | None = None


class LMGQueryError(RuntimeError):
    """Raised when an authoritative LMG query cannot be completed."""

    def __init__(self, code: str, message: str | None = None) -> None:
        if message is None:
            message = code
            code = "query_failed"
        super().__init__(message)
        self.code = code


class LMGDeleteError(LMGQueryError):
    """Raised when a linked LMG session cannot be deleted safely."""


_SESSION_REQUIRED_NONEMPTY_STRINGS = ("provider", "upstream_id")
_SESSION_REQUIRED_STRINGS = (
    "model",
    "workspace_root",
    "created_at",
    "last_run_at",
)
_SESSION_OPTIONAL_STRINGS = (
    "consumer",
    "consumer_session_id",
    "consumer_run_id",
    "consumer_context_fingerprint",
    "storage_path",
)


def _lmg_headers(config: AppConfig) -> dict[str, str]:
    if config.lmg_local_token is None:
        return {}
    return {"Authorization": f"Bearer {config.lmg_local_token}"}


def fetch_capabilities(
    config,
    *,
    transport: httpx.BaseTransport | None = None,
) -> LmgQueryResult[dict[str, object]]:
    url = f"{config.lmg_base_url.rstrip('/')}/v1/models"
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            response = client.get(url, headers=_lmg_headers(config))
    except httpx.HTTPError:
        return _query_failure("unreachable")
    failure = _http_failure(response.status_code)
    if failure is not None:
        return failure
    try:
        payload = response.json()
    except ValueError:
        return _query_failure("protocol_error")
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("providers"), dict)
    ):
        return _query_failure("protocol_error")
    gateway_status = payload.get("gateway_status")
    if gateway_status not in {"ready", "not_ready"}:
        return _query_failure("protocol_error")
    return LmgQueryResult(
        data={str(key): value for key, value in payload.items()},
        status=gateway_status,
        message=_STATUS_MESSAGES.get(gateway_status),
    )


def fetch_sessions(
    config,
    *,
    transport: httpx.BaseTransport | None = None,
) -> LmgQueryResult[list[dict[str, object]]]:
    url = f"{config.lmg_base_url.rstrip('/')}/v1/sessions"
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            response = client.get(url, headers=_lmg_headers(config))
    except httpx.HTTPError:
        return _query_failure("unreachable")
    failure = _http_failure(response.status_code)
    if failure is not None:
        return failure
    try:
        payload = response.json()
    except ValueError:
        return _query_failure("protocol_error")
    if not _valid_session_rows(payload):
        return _query_failure("protocol_error")
    return LmgQueryResult(data=payload, status="ready")


def fetch_sessions_strict(
    config,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, object]]:
    """Fetch LMG sessions without converting an unavailable LMG into an empty list."""
    result = fetch_sessions(config, transport=transport)
    if result.status != "ready" or result.data is None:
        raise LMGQueryError(result.status, result.message or "LMG query failed")
    return result.data


def _valid_session_rows(payload: object) -> bool:
    if not isinstance(payload, list):
        return False
    upstream_ids: set[str] = set()
    for row in payload:
        if not _valid_session_row(row):
            return False
        upstream_id = row["upstream_id"]
        if upstream_id in upstream_ids:
            return False
        upstream_ids.add(upstream_id)
    return True


def _valid_session_row(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for field in _SESSION_REQUIRED_NONEMPTY_STRINGS:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
    for field in _SESSION_REQUIRED_STRINGS:
        if field not in row or not isinstance(row[field], str):
            return False
    for field in _SESSION_OPTIONAL_STRINGS:
        value = row.get(field)
        if field in row and value is not None and not isinstance(value, str):
            return False
    size_bytes = row.get("size_bytes")
    return (
        "size_bytes" in row
        and isinstance(size_bytes, int)
        and not isinstance(size_bytes, bool)
        and size_bytes >= 0
    )


def delete_session(
    config,
    upstream_session_id: str,
    *,
    provider: str,
    consumer_session_id: str,
    timeout_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    encoded_session_id = quote(upstream_session_id, safe="")
    url = f"{config.lmg_base_url.rstrip('/')}/v1/sessions/{encoded_session_id}"
    params = {
        "provider": provider,
        "consumer": "personal-agent-gateway",
        "consumer_session_id": consumer_session_id,
    }
    try:
        with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
            response = client.delete(
                url,
                headers=_lmg_headers(config),
                params=params,
            )
        if response.status_code == 404:
            return True
    except httpx.HTTPError as exc:
        raise LMGDeleteError(
            "unreachable",
            "Local model gateway session deletion failed",
        ) from exc
    if response.is_success:
        return True
    code = _safe_error_code(response)
    if response.status_code == 409 and code in {
        "session_identity_conflict",
        "session_busy",
        "storage_metadata_stale",
    }:
        raise LMGDeleteError(code, "Local model gateway rejected session deletion")
    raise LMGDeleteError(
        "delete_failed",
        "Local model gateway session deletion failed",
    )


_STATUS_MESSAGES: dict[LmgStatus, str | None] = {
    "ready": None,
    "unreachable": "로컬 모델 게이트웨이에 연결할 수 없습니다.",
    "unauthorized": "로컬 모델 게이트웨이 인증에 실패했습니다.",
    "not_ready": "로컬 모델 게이트웨이가 준비되지 않았습니다.",
    "protocol_error": "로컬 모델 게이트웨이 응답 형식이 올바르지 않습니다.",
}


def _query_failure(status: LmgStatus) -> LmgQueryResult:
    return LmgQueryResult(data=None, status=status, message=_STATUS_MESSAGES[status])


def _http_failure(status_code: int) -> LmgQueryResult | None:
    if status_code == 401:
        return _query_failure("unauthorized")
    if status_code == 503:
        return _query_failure("not_ready")
    if status_code < 200 or status_code >= 300:
        return _query_failure("unreachable")
    return None


def _safe_error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    code = payload.get("code")
    return code if isinstance(code, str) else ""
