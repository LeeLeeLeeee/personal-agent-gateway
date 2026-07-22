from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import BaseModel

from personal_agent_gateway.agents import AgentDescriptor, AgentRegistry

# 확정된 로컬 사용량 소스가 아직 없다. artifacts/dashboard-usage-spec.md §3.2 참고:
# weekly_limit/used/reset_at 을 로컬에서 안정적으로 얻을 수 있는 CLI 명령·파일이
# 검증되지 않았다. 검증 전까지 값을 추정하지 않고 None 으로 노출한다(스펙 §4).
_UNCONFIRMED_NOTE = "로컬에서 확정된 사용량 소스가 없어 한도·사용량을 수집하지 못했습니다."


_COLLECTION_FAILURE_NOTE = "사용량 정보 수집에 실패했습니다."


class ProviderUsage(BaseModel):
    provider: str
    label: str
    available: bool
    availability_error: str | None = None
    version: str = ""
    model: str = ""
    weekly_limit: int | None = None
    used: int | None = None
    remaining: int | None = None
    reset_at: str | None = None
    usage_status: str
    usage_source: str | None = None
    note: str | None = None


class UsageReport(BaseModel):
    detected_at: str
    providers: list[ProviderUsage]


UsageReader = Callable[[AgentDescriptor], dict[str, object]]


def _read_usage(_descriptor: AgentDescriptor) -> dict[str, object]:
    """provider 별 사용량/한도를 수집하는 확장 지점.

    현재는 검증된 로컬 소스가 없어 항상 빈 결과를 반환한다
    (artifacts/dashboard-usage-spec.md §3.2, §4). 소스가 확정되면
    weekly_limit/used/remaining/reset_at/source 키를 채워 반환하도록 확장한다.
    값을 추정하거나 임의로 채우지 않는다.
    """
    return {}


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _provider_usage(descriptor: AgentDescriptor, reader: UsageReader) -> ProviderUsage:
    if not descriptor.available:
        return ProviderUsage(
            provider=descriptor.id,
            label=descriptor.label,
            available=False,
            availability_error=descriptor.availability_error,
            version=descriptor.version,
            model=descriptor.default_model,
            usage_status="unavailable",
            note=descriptor.availability_error,
        )

    try:
        collected = reader(descriptor)
    except Exception as exc:
        return ProviderUsage(
            provider=descriptor.id,
            label=descriptor.label,
            available=True,
            version=descriptor.version,
            model=descriptor.default_model,
            usage_status="unconfirmed",
            note=f"{_COLLECTION_FAILURE_NOTE} {exc}",
        )
    weekly_limit = _int_or_none(collected.get("weekly_limit"))
    used = _int_or_none(collected.get("used"))
    remaining = _int_or_none(collected.get("remaining"))
    if remaining is None and weekly_limit is not None and used is not None:
        remaining = max(weekly_limit - used, 0)
    reset_at = collected.get("reset_at")
    reset_at = reset_at if isinstance(reset_at, str) and reset_at else None

    fields = [weekly_limit, used, remaining, reset_at]
    present = [field for field in fields if field is not None]
    if not present:
        status = "unconfirmed"
    elif None in fields:
        status = "partial"
    else:
        status = "ok"

    source = collected.get("source")
    return ProviderUsage(
        provider=descriptor.id,
        label=descriptor.label,
        available=True,
        version=descriptor.version,
        model=descriptor.default_model,
        weekly_limit=weekly_limit,
        used=used,
        remaining=remaining,
        reset_at=reset_at,
        usage_status=status,
        usage_source=source if isinstance(source, str) and source else None,
        note=None if present else _UNCONFIRMED_NOTE,
    )


def build_usage_report(
    descriptors: list[AgentDescriptor],
    detected_at: str,
    reader: UsageReader = _read_usage,
) -> UsageReport:
    return UsageReport(
        detected_at=detected_at,
        providers=[_provider_usage(descriptor, reader) for descriptor in descriptors],
    )


def collect_local_agent_usage(
    registry: AgentRegistry,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    reader: UsageReader = _read_usage,
) -> UsageReport:
    return build_usage_report(
        registry.catalog(),
        detected_at=now().isoformat(),
        reader=reader,
    )
