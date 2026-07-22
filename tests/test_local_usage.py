from datetime import datetime, timezone

from personal_agent_gateway.agents import AgentDescriptor
from personal_agent_gateway.local_usage import build_usage_report, collect_local_agent_usage


def make_descriptor(agent_id, *, available=True, error=None):
    return AgentDescriptor(
        id=agent_id,
        label=f"{agent_id} label",
        binary=f"{agent_id}-bin",
        available=available,
        availability_error=error,
        models=["default"],
        model_options=[],
        default_model="default",
        options_schema=[],
        defaults={},
        version="1.2.3",
        capability_source=["fallback"],
    )


def test_available_provider_without_source_reports_unconfirmed():
    report = build_usage_report([make_descriptor("claude")], detected_at="t")

    provider = report.providers[0]
    assert provider.usage_status == "unconfirmed"
    assert provider.weekly_limit is None
    assert provider.used is None
    assert provider.remaining is None
    assert provider.reset_at is None
    assert provider.note is not None
    assert provider.version == "1.2.3"
    assert provider.model == "default"


def test_unavailable_provider_reports_unavailable_and_error():
    report = build_usage_report(
        [make_descriptor("codex", available=False, error="not found on PATH")],
        detected_at="t",
    )

    provider = report.providers[0]
    assert provider.available is False
    assert provider.usage_status == "unavailable"
    assert provider.availability_error == "not found on PATH"
    assert provider.note == "not found on PATH"


def test_remaining_is_derived_when_limit_and_used_present():
    def reader(_descriptor):
        return {"weekly_limit": 1000, "used": 720, "source": "test-source"}

    report = build_usage_report([make_descriptor("claude")], detected_at="t", reader=reader)

    provider = report.providers[0]
    assert provider.remaining == 280
    assert provider.reset_at is None
    assert provider.usage_status == "partial"
    assert provider.usage_source == "test-source"
    assert provider.note is None


def test_collection_failure_reports_unconfirmed_provider_without_breaking_report():
    def reader(_descriptor):
        raise RuntimeError("usage endpoint unavailable")

    report = build_usage_report([make_descriptor("codex")], detected_at="t", reader=reader)

    provider = report.providers[0]
    assert provider.available is True
    assert provider.usage_status == "unconfirmed"
    assert provider.weekly_limit is None
    assert provider.note is not None
    assert "usage endpoint unavailable" in provider.note


def test_status_ok_when_all_fields_present():
    def reader(_descriptor):
        return {
            "weekly_limit": 1000,
            "used": 720,
            "reset_at": "2026-07-24T09:00:00Z",
            "source": "test-source",
        }

    report = build_usage_report([make_descriptor("codex")], detected_at="t", reader=reader)

    assert report.providers[0].usage_status == "ok"
    assert report.providers[0].remaining == 280


class _FakeRegistry:
    def __init__(self, descriptors):
        self._descriptors = descriptors

    def catalog(self):
        return self._descriptors


def test_collect_uses_registry_catalog_and_timestamp():
    registry = _FakeRegistry([make_descriptor("codex"), make_descriptor("claude")])
    fixed = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)

    report = collect_local_agent_usage(registry, now=lambda: fixed)

    assert report.detected_at == fixed.isoformat()
    assert [provider.provider for provider in report.providers] == ["codex", "claude"]
