import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from time import sleep

import pytest

from personal_agent_gateway.agents import AgentRegistry, CliProbeResult, probe_cli
from personal_agent_gateway.config import AppConfig
from personal_agent_gateway.lmg_client import LmgQueryResult, ProviderExecutionCapabilities


def make_config(tmp_path: Path) -> AppConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AppConfig(
        workspace_root=workspace,
        session_dir=tmp_path / "sessions",
        codex_binary="codex-test",
        claude_binary="claude-test",
    )


def _agent_protocol_payload(*, snapshot_status: str = "fresh"):
    return {
        "protocol_version": "2.0",
        "schema_version": 1,
        "detected_at": "2026-07-30T00:00:00Z",
        "snapshot_status": snapshot_status,
        "refresh_error_code": None,
        "gateway_status": "ready",
        "admission_status": "ready",
        "providers": {
            "codex": {
                "available": True,
                "ready": True,
                "readiness_error": None,
                "models": [{"id": "default", "label": "Default"}],
                "execution": {
                    "resume": True,
                    "external_read_only_roots": False,
                    "network_modes": ["unspecified", "denied", "required"],
                    "sandbox_modes": ["read-only", "workspace-write"],
                    "permission_modes": [],
                },
            }
        },
    }


def test_registry_lists_codex_and_claude_with_safe_defaults(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    registry = AgentRegistry(
        config,
        probe=lambda binary: CliProbeResult(
            available=binary == "codex-test",
            error=None if binary == "codex-test" else "not found",
        ),
    )

    catalog = registry.catalog()

    assert [agent.id for agent in catalog] == ["codex", "claude"]
    codex = catalog[0]
    claude = catalog[1]
    assert codex.available is False
    assert codex.availability_error == "unavailable"
    assert codex.execution_capabilities is None
    assert codex.binary == "codex-test"
    assert codex.default_model == "default"
    assert codex.defaults["sandbox"] == "workspace-write"
    assert claude.available is False
    assert claude.availability_error == "not found"
    assert claude.defaults["effort"] == "medium"
    assert codex.models == ["default", "gpt-5.5", "gpt-5.4"]
    assert any(option.name == "effort" and option.choices == ["low", "medium", "high", "xhigh"] for option in codex.options_schema)
    assert codex.defaults["effort"] == "high"
    assert claude.models == ["default", "best", "sonnet", "opus", "haiku", "sonnet[1m]", "opus[1m]", "opusplan"]
    assert "fable" not in claude.models


def test_registry_accepts_curated_model_presets_only(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    assert registry.validate_config(
        "codex", "gpt-5.5", {}, require_available=False
    )["model"] == "gpt-5.5"
    assert registry.validate_config(
        "claude", "opusplan", {}, require_available=False
    )["model"] == "opusplan"

    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config(
            "codex", "codex-5.5", {}, require_available=False
        )

    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config("claude", "fable", {}, require_available=False)


def test_registry_rejects_unknown_agent_model_and_option(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    with pytest.raises(ValueError, match="Unknown agent"):
        registry.validate_config("missing", "default", {})

    with pytest.raises(ValueError, match="Unsupported model"):
        registry.validate_config("codex", "not-listed", {}, require_available=False)

    with pytest.raises(ValueError, match="Unsupported option"):
        registry.validate_config(
            "codex",
            "default",
            {"not_allowed": True},
            require_available=False,
        )


def test_registry_accepts_supported_provider_options(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    assert registry.validate_config(
        "claude",
        "sonnet",
        {"effort": "high", "permission_mode": "manual"},
        require_available=False,
    ) == {
        "agent_id": "claude",
        "model": "sonnet",
        "options": {"effort": "high", "permission_mode": "manual"},
    }


def test_probe_cli_returns_timeout_result_for_hung_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex-test", "--help"], timeout=5)

    monkeypatch.setattr("personal_agent_gateway.agents.subprocess.run", fake_run)

    assert probe_cli("codex-test") == CliProbeResult(False, "probe timed out")


def test_registry_rejects_invalid_option_choice(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
    )

    with pytest.raises(ValueError, match="Unsupported option value"):
        registry.validate_config(
            "codex",
            "default",
            {"sandbox": "invalid-choice"},
            require_available=False,
        )


def test_registry_rejects_unavailable_agent_for_new_config(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda binary: CliProbeResult(binary == "codex-test", "not found on PATH"),
    )

    with pytest.raises(ValueError, match="Agent unavailable"):
        registry.validate_config("claude", "sonnet", {})

    assert registry.validate_config(
        "claude",
        "sonnet",
        {},
        require_available=False,
    ) == {
        "agent_id": "claude",
        "model": "sonnet",
        "options": {},
    }


def test_registry_uses_detected_models_and_model_specific_efforts(tmp_path: Path) -> None:
    detected = {
        "providers": {
            "codex": {
                "available": True,
                "ready": True,
                "execution": {
                    "resume": True,
                    "external_read_only_roots": False,
                    "network_modes": ["unspecified", "denied", "required"],
                    "sandbox_modes": ["read-only", "workspace-write"],
                    "permission_modes": [],
                },
                "version": "codex-cli test",
                "source": ["cli_help", "models_cache"],
                "models": [
                    {
                        "id": "default",
                        "label": "Default (gpt-new)",
                        "efforts": ["low", "high"],
                        "default_effort": "high",
                    },
                    {
                        "id": "gpt-new",
                        "label": "GPT New",
                        "efforts": ["low"],
                        "default_effort": "low",
                    },
                ],
                "options": {
                    "sandbox": ["read-only", "workspace-write"],
                    "approval_policy": ["on-request", "never"],
                    "profile": ["review"],
                },
                "defaults": {"model": "default", "effort": "high"},
            }
        }
    }
    registry = AgentRegistry(
        make_config(tmp_path),
        probe=lambda _binary: CliProbeResult(True, None),
        capability_loader=lambda _config: detected,
    )

    codex = registry.get("codex")

    assert codex.models == ["default", "gpt-new"]
    assert codex.model_options[1].label == "GPT New"
    assert codex.model_options[1].efforts == ["low"]
    assert codex.version == "codex-cli test"
    assert codex.capability_source == ["cli_help", "models_cache"]
    assert codex.execution_capabilities == ProviderExecutionCapabilities(
        resume=True,
        external_read_only_roots=False,
        network_modes=("unspecified", "denied", "required"),
        sandbox_modes=("read-only", "workspace-write"),
        permission_modes=(),
    )
    assert codex.ready is True
    assert codex.readiness_error is None
    assert next(option for option in codex.options_schema if option.name == "profile").choices == [
        "review"
    ]
    assert registry.validate_config("codex", "gpt-new", {"effort": "low"})["model"] == "gpt-new"
    with pytest.raises(ValueError, match="Unsupported effort"):
        registry.validate_config("codex", "gpt-new", {"effort": "high"})


def test_registry_keeps_not_ready_provider_capabilities_visible(tmp_path: Path) -> None:
    payload = _agent_protocol_payload()
    payload["gateway_status"] = "not_ready"
    payload["admission_status"] = "not_ready"
    provider = payload["providers"]["codex"]
    provider["ready"] = False
    provider["readiness_error"] = "provider_not_ready"
    provider["models"] = [{"id": "gpt-detected", "label": "GPT Detected"}]
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: LmgQueryResult(
            data=payload,
            status="not_ready",
        ),
    )

    codex = registry.get("codex")

    assert codex.models == ["gpt-detected"]
    assert codex.available is True
    assert codex.execution_capabilities is not None
    assert codex.ready is False
    assert codex.readiness_error == "provider_not_ready"
    assert codex.snapshot_status == "fresh"


@pytest.mark.parametrize(
    "status",
    ["unreachable", "unauthorized", "protocol_error", "not_ready"],
)
def test_registry_reports_gateway_results_without_probing_local_cli(
    tmp_path: Path,
    status: str,
) -> None:
    payload = _agent_protocol_payload()
    payload["gateway_status"] = "not_ready"
    payload["admission_status"] = "not_ready"
    provider = payload["providers"]["codex"]
    provider["ready"] = False
    provider["readiness_error"] = "provider_not_ready"
    provider["models"] = [{"id": "gpt-detected", "label": "GPT Detected"}]

    def unexpected_probe(_binary: str) -> CliProbeResult:
        raise AssertionError("PAG must not probe a CLI owned by LMG")

    registry = AgentRegistry(
        make_config(tmp_path),
        probe=unexpected_probe,
        capability_loader=lambda _config: LmgQueryResult(
            data=payload if status == "not_ready" else None,
            status=status,
        ),
    )

    codex = registry.get("codex")

    expected_model = "gpt-detected" if status == "not_ready" else "default"
    assert expected_model in codex.models
    if status == "not_ready":
        assert codex.available is True
        assert codex.readiness_error == "provider_not_ready"
        assert codex.snapshot_status == "fresh"
    else:
        assert codex.available is False
        assert codex.readiness_error == f"gateway_{status}"
        assert codex.snapshot_status == "unavailable"


def test_registry_recovers_after_short_negative_cache_ttl(tmp_path: Path) -> None:
    now = [0.0]
    status = ["not_ready"]
    payload = _agent_protocol_payload()
    payload["providers"]["codex"]["models"] = [
        {"id": "gpt-detected", "label": "GPT Detected"}
    ]
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: LmgQueryResult(
            data=payload if status[0] == "ready" else None,
            status=status[0],
        ),
        failure_ttl_seconds=2,
        clock=lambda: now[0],
    )

    assert registry.get("codex").available is False
    status[0] = "ready"
    now[0] = 1
    assert registry.get("codex").available is False
    now[0] = 2
    assert registry.get("codex").available is True


def test_registry_keeps_last_good_capability_when_refresh_is_unreachable(
    tmp_path: Path,
) -> None:
    results = iter(
        [
            LmgQueryResult(
                data=_agent_protocol_payload(snapshot_status="fresh"),
                status="ready",
            ),
            LmgQueryResult(data=None, status="unreachable", message="offline"),
        ]
    )
    clock = [0.0]
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: next(results),
        cache_ttl_seconds=1,
        failure_ttl_seconds=2,
        clock=lambda: clock[0],
    )

    first = registry.get("codex")
    clock[0] = 2.0
    stale = registry.get("codex")

    assert first.execution_capabilities == stale.execution_capabilities
    assert stale.snapshot_status == "stale"
    assert stale.ready is False
    assert stale.readiness_error == "gateway_unreachable"


def test_registry_hard_protocol_failure_is_not_recoverable(tmp_path: Path) -> None:
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: LmgQueryResult(
            data=None,
            status="protocol_error",
            message="bad protocol",
        ),
    )

    descriptor = registry.get("codex")

    assert descriptor.snapshot_status == "unavailable"
    assert descriptor.readiness_error == "gateway_protocol_error"
    assert descriptor.execution_capabilities is None


def test_registry_without_last_good_snapshot_keeps_retryable_failure_unavailable(
    tmp_path: Path,
) -> None:
    results = iter(
        [
            LmgQueryResult(data=None, status="protocol_error", message="bad protocol"),
            LmgQueryResult(data=None, status="unreachable", message="offline"),
        ]
    )
    clock = [0.0]
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: next(results),
        failure_ttl_seconds=1,
        clock=lambda: clock[0],
    )

    hard_failure = registry.get("codex")
    clock[0] = 2.0
    retryable_failure = registry.get("codex")

    assert hard_failure.snapshot_status == "unavailable"
    assert retryable_failure.snapshot_status == "unavailable"
    assert retryable_failure.readiness_error == "gateway_unreachable"
    assert retryable_failure.execution_capabilities is None


def test_registry_preserves_last_good_snapshot_across_hard_failure(
    tmp_path: Path,
) -> None:
    results = iter(
        [
            LmgQueryResult(data=_agent_protocol_payload(), status="ready"),
            LmgQueryResult(data=None, status="protocol_error", message="bad protocol"),
            LmgQueryResult(data=None, status="unreachable", message="offline"),
        ]
    )
    clock = [0.0]
    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=lambda _config: next(results),
        cache_ttl_seconds=1,
        failure_ttl_seconds=1,
        clock=lambda: clock[0],
    )

    good = registry.get("codex")
    clock[0] = 2.0
    hard_failure = registry.get("codex")
    clock[0] = 4.0
    recovered_stale = registry.get("codex")

    assert good.execution_capabilities is not None
    assert hard_failure.snapshot_status == "unavailable"
    assert hard_failure.execution_capabilities is None
    assert recovered_stale.snapshot_status == "stale"
    assert recovered_stale.readiness_error == "gateway_unreachable"
    assert recovered_stale.execution_capabilities == good.execution_capabilities


@pytest.mark.parametrize("status", ["ready", "unreachable"])
def test_registry_ttl_starts_after_slow_loader_completes(
    tmp_path: Path,
    status: str,
) -> None:
    clock = [0.0]
    calls = 0

    def load(_config):
        nonlocal calls
        calls += 1
        clock[0] = 10.0
        return LmgQueryResult(
            data=_agent_protocol_payload() if status == "ready" else None,
            status=status,
        )

    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=load,
        cache_ttl_seconds=5,
        failure_ttl_seconds=5,
        clock=lambda: clock[0],
    )

    registry.catalog()
    registry.catalog()

    assert calls == 1


def test_concurrent_refresh_keeps_newer_successful_catalog(tmp_path: Path) -> None:
    clock = [0.0]
    refresh_started = Event()
    release_refresh = Event()
    calls_lock = Lock()
    calls = 0
    initial = _agent_protocol_payload()
    refreshed = _agent_protocol_payload()
    refreshed["detected_at"] = "2026-07-30T00:01:00Z"
    refreshed["providers"]["codex"]["models"] = [
        {"id": "gpt-refreshed", "label": "GPT Refreshed"}
    ]

    def load(_config):
        nonlocal calls
        with calls_lock:
            calls += 1
            call = calls
        if call == 1:
            return LmgQueryResult(data=initial, status="ready")
        if call == 2:
            refresh_started.set()
            release_refresh.wait(timeout=1)
            return LmgQueryResult(data=refreshed, status="ready")
        release_refresh.wait(timeout=1)
        sleep(0.05)
        return LmgQueryResult(data=None, status="unreachable", message="offline")

    registry = AgentRegistry(
        make_config(tmp_path),
        capability_loader=load,
        cache_ttl_seconds=1,
        clock=lambda: clock[0],
    )
    registry.catalog()
    clock[0] = 2.0

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(registry.catalog) for _ in range(2)]
        assert refresh_started.wait(timeout=1)
        release_refresh.set()
        catalogs = [future.result(timeout=1) for future in futures]

    assert calls == 2
    assert all(catalog[0].models == ["gpt-refreshed"] for catalog in catalogs)
    assert registry.get("codex").snapshot_status == "fresh"
    assert registry.get("codex").detected_at == "2026-07-30T00:01:00Z"
