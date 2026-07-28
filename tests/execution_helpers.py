from personal_agent_gateway.agents import AgentRegistry, CliProbeResult


def ready_agent_registry(config):
    return AgentRegistry(
        config,
        probe=lambda _binary: CliProbeResult(True, None),
        capability_loader=lambda _config: {
            "protocol_version": "2.0",
            "schema_version": 1,
            "gateway_status": "ready",
            "providers": {
                "codex": {
                    "available": True,
                    "ready": True,
                    "models": [{"id": "default"}, {"id": "gpt-5.5"}],
                    "options": {
                        "sandbox": [
                            "read-only",
                            "workspace-write",
                            "danger-full-access",
                        ],
                        "approval_policy": ["untrusted", "on-request", "never"],
                        "profile": ["local-dev"],
                    },
                    "execution": {
                        "resume": True,
                        "external_read_only_roots": False,
                        "network_modes": ["unspecified", "denied", "required"],
                        "sandbox_modes": [
                            "read-only",
                            "workspace-write",
                            "danger-full-access",
                        ],
                        "permission_modes": [],
                    },
                },
                "claude": {
                    "available": True,
                    "ready": True,
                    "models": [{"id": "sonnet"}],
                    "options": {
                        "permission_mode": ["default", "acceptEdits", "plan"],
                        "agent": ["reviewer"],
                    },
                    "execution": {
                        "resume": True,
                        "external_read_only_roots": False,
                        "network_modes": ["unspecified"],
                        "sandbox_modes": [],
                        "permission_modes": ["default", "acceptEdits", "plan"],
                    },
                },
            },
        },
    )
