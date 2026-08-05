import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Generic, Literal, TypeVar
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
_LMG_PROTOCOL_VERSIONS = frozenset({"2.0", "2.1"})
_LOGGER = logging.getLogger(__name__)
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


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


class LMGProtocolMismatch(RuntimeError):
    """Raised when LMG cannot supply a usable execution protocol snapshot."""


@dataclass(frozen=True)
class ProviderExecutionCapabilities:
    resume: bool
    external_read_only_roots: bool
    network_modes: tuple[str, ...]
    sandbox_modes: tuple[str, ...]
    permission_modes: tuple[str, ...]


@dataclass(frozen=True)
class ProviderReadiness:
    ready: bool
    error_code: str | None


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
    sleep: Callable[[float], None] = time.sleep,
    retry_delays: tuple[float, ...] = (0.5, 1.5),
) -> LmgQueryResult[dict[str, object]]:
    for attempt in range(len(retry_delays) + 1):
        result = _fetch_capabilities_once(config, transport=transport)
        _LOGGER.info("attempt=%d status=%s", attempt + 1, result.status)
        if result.status not in {"unreachable", "not_ready"}:
            return result
        if attempt == len(retry_delays):
            return result
        sleep(retry_delays[attempt])
    raise AssertionError("unreachable retry state")


def _fetch_capabilities_once(
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
    failure = _capability_http_failure(response.status_code)
    if failure is not None:
        return failure
    try:
        payload = response.json()
    except ValueError:
        return _query_failure("protocol_error")
    if not _valid_capabilities(payload):
        return _query_failure("protocol_error")
    gateway_status = payload.get("gateway_status")
    if gateway_status not in {"ready", "not_ready"}:
        return _query_failure("protocol_error")
    return LmgQueryResult(
        data={str(key): value for key, value in payload.items()},
        status=gateway_status,
        message=_STATUS_MESSAGES.get(gateway_status),
    )


def fetch_execution_capabilities(
    config: AppConfig,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, ProviderExecutionCapabilities]:
    result = fetch_capabilities(config, transport=transport, sleep=sleep)
    if result.status != "ready" or result.data is None:
        raise LMGProtocolMismatch(result.message or "LMG execution protocol is unavailable")
    providers = result.data["providers"]
    assert isinstance(providers, dict)
    parsed: dict[str, ProviderExecutionCapabilities] = {}
    for provider_id, provider in providers.items():
        assert isinstance(provider_id, str)
        capability = parse_provider_execution_capabilities(provider)
        parsed[provider_id] = capability
    return parsed


def _valid_capabilities(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    protocol_version = payload.get("protocol_version")
    if (
        not isinstance(protocol_version, str)
        or protocol_version not in _LMG_PROTOCOL_VERSIONS
    ):
        return False
    if type(payload.get("schema_version")) is not int:
        return False
    if payload["schema_version"] != 1:
        return False
    detected_at = payload.get("detected_at")
    if not isinstance(detected_at, str) or not detected_at:
        return False
    if payload.get("snapshot_status") not in {"fresh", "stale"}:
        return False
    if payload.get("admission_status") not in {"ready", "not_ready"}:
        return False
    if payload.get("gateway_status") not in {"ready", "not_ready"}:
        return False
    refresh_error_code = payload.get("refresh_error_code")
    if refresh_error_code is not None and not isinstance(refresh_error_code, str):
        return False
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return False
    for provider_id, capabilities in providers.items():
        if not isinstance(provider_id, str) or not provider_id:
            return False
        if not _valid_provider_capabilities(capabilities):
            return False
    return True


def _valid_provider_capabilities(capabilities: object) -> bool:
    if not isinstance(capabilities, dict):
        return False
    if not isinstance(capabilities.get("available"), bool):
        return False
    try:
        parse_provider_execution_capabilities(capabilities)
        parse_provider_readiness(capabilities)
    except LMGProtocolMismatch:
        return False
    for key in ("version", "error"):
        value = capabilities.get(key)
        if value is not None and not isinstance(value, str):
            return False
    for key in ("options", "defaults", "usage"):
        value = capabilities.get(key)
        if value is not None and not isinstance(value, dict):
            return False
    source = capabilities.get("source")
    if source is not None and (
        not isinstance(source, list)
        or not all(isinstance(item, str) for item in source)
    ):
        return False
    models = capabilities.get("models")
    if models is None:
        return True
    if not isinstance(models, list):
        return False
    return all(
        isinstance(model, dict)
        and isinstance(model.get("id"), str)
        and bool(model["id"])
        for model in models
    )


def parse_provider_execution_capabilities(
    provider: object,
) -> ProviderExecutionCapabilities:
    if not isinstance(provider, dict):
        raise LMGProtocolMismatch("LMG provider capability data is malformed")
    execution = provider.get("execution")
    if not isinstance(execution, dict):
        raise LMGProtocolMismatch("LMG provider execution capabilities are missing")
    resume = execution.get("resume")
    external_roots = execution.get("external_read_only_roots")
    if not isinstance(resume, bool) or not isinstance(external_roots, bool):
        raise LMGProtocolMismatch("LMG provider execution flags are malformed")
    collections: dict[str, tuple[str, ...]] = {}
    for key in ("network_modes", "sandbox_modes", "permission_modes"):
        value = execution.get(key)
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise LMGProtocolMismatch(f"LMG provider {key} are malformed")
        collections[key] = tuple(dict.fromkeys(value))
    if not collections["network_modes"]:
        raise LMGProtocolMismatch("LMG provider network modes are missing")
    return ProviderExecutionCapabilities(
        resume=resume,
        external_read_only_roots=external_roots,
        network_modes=collections["network_modes"],
        sandbox_modes=collections["sandbox_modes"],
        permission_modes=collections["permission_modes"],
    )


def parse_provider_readiness(provider: object) -> ProviderReadiness:
    if not isinstance(provider, dict):
        raise LMGProtocolMismatch("LMG provider readiness data is malformed")
    ready = provider.get("ready")
    error_code = provider.get("readiness_error")
    if not isinstance(ready, bool):
        raise LMGProtocolMismatch("LMG provider readiness is missing")
    if error_code is not None and not isinstance(error_code, str):
        raise LMGProtocolMismatch("LMG provider readiness error is malformed")
    return ProviderReadiness(ready=ready, error_code=error_code)


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


def fetch_usage(
    config: AppConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> LmgQueryResult[dict[str, object]]:
    """Fetch and strictly validate the LMG account-limit snapshot."""
    url = f"{config.lmg_base_url.rstrip('/')}/v1/usage"
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
    if not _valid_usage_report(payload):
        return _query_failure("protocol_error")
    return LmgQueryResult(
        data={str(key): value for key, value in payload.items()},
        status="ready",
    )


def _valid_usage_report(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    collected_at = payload.get("collected_at")
    providers = payload.get("providers")
    return (
        isinstance(collected_at, str)
        and _is_rfc3339(collected_at)
        and isinstance(providers, list)
        and all(_valid_usage_provider(provider) for provider in providers)
    )


def _valid_usage_provider(provider: object) -> bool:
    if not isinstance(provider, dict):
        return False
    provider_id = provider.get("provider")
    status = provider.get("status")
    rate_limits = provider.get("rate_limits")
    return (
        isinstance(provider_id, str)
        and bool(provider_id.strip())
        and isinstance(status, str)
        and status in {"ok", "unconfirmed", "unavailable"}
        and isinstance(rate_limits, list)
        and all(is_valid_rate_limit(rate_limit) for rate_limit in rate_limits)
    )


def is_valid_rate_limit(rate_limit: object) -> bool:
    if not isinstance(rate_limit, dict):
        return False
    window_minutes = rate_limit.get("window_minutes")
    used_percent = rate_limit.get("used_percent")
    resets_at = rate_limit.get("resets_at")
    return (
        isinstance(window_minutes, int)
        and not isinstance(window_minutes, bool)
        and window_minutes > 0
        and isinstance(used_percent, (int, float))
        and not isinstance(used_percent, bool)
        and math.isfinite(used_percent)
        and 0 <= used_percent <= 100
        and (resets_at is None or (isinstance(resets_at, str) and _is_rfc3339(resets_at)))
    )


def _is_rfc3339(value: str) -> bool:
    if not _RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


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


def _capability_http_failure(status_code: int) -> LmgQueryResult | None:
    if status_code == 401:
        return _query_failure("unauthorized")
    if status_code == 503:
        return _query_failure("not_ready")
    if status_code in {502, 504}:
        return _query_failure("unreachable")
    if status_code < 200 or status_code >= 300:
        return _query_failure("protocol_error")
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
