from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_agent_gateway.lmg_client import ProviderExecutionCapabilities
from personal_agent_gateway.space_policies import SpacePolicy

WorkspaceMode = Literal["isolated", "worktree", "full_access"]
NetworkMode = Literal["unspecified", "denied", "required"]


@dataclass(frozen=True)
class ExecutionRequirements:
    source_roots: tuple[Path, ...]
    requires_sources: bool
    workspace_mode: WorkspaceMode
    workspace_root: Path | None
    network: NetworkMode


@dataclass(frozen=True)
class CompiledExecution:
    workspace_root: Path
    read_roots: tuple[Path, ...]
    sandbox: str
    permission_mode: str
    approval_policy: str
    network: NetworkMode
    input_manifest_path: Path | None
    input_manifest_sha256: str | None


class ExecutionContractError(RuntimeError):
    def __init__(self, code: str, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.code = code


def compile_execution(
    requirements: ExecutionRequirements,
    policy: SpacePolicy,
    capabilities: ProviderExecutionCapabilities,
    staging,
) -> CompiledExecution:
    if requirements.network not in capabilities.network_modes:
        raise ExecutionContractError(
            "unsupported_execution_capability",
            "The selected provider does not support the required network mode",
        )

    requested_sandbox = (
        "danger-full-access"
        if requirements.workspace_mode == "full_access"
        else "workspace-write"
    )
    sandbox = requested_sandbox if capabilities.sandbox_modes else ""
    if sandbox and sandbox not in capabilities.sandbox_modes:
        raise ExecutionContractError(
            "unsupported_execution_capability",
            "The selected provider does not support the required sandbox mode",
        )
    requested_permission = (
        "bypassPermissions"
        if requirements.workspace_mode == "full_access"
        else "acceptEdits"
    )
    permission_mode = requested_permission if capabilities.permission_modes else ""
    if permission_mode and permission_mode not in capabilities.permission_modes:
        raise ExecutionContractError(
            "unsupported_execution_capability",
            "The selected provider does not support the required permission mode",
        )

    if requirements.workspace_mode != "isolated":
        if requirements.workspace_root is None:
            raise ExecutionContractError(
                "invalid_execution_path",
                "A direct workspace mode requires a workspace root",
            )
        workspace_root = requirements.workspace_root.resolve()
        return CompiledExecution(
            workspace_root=workspace_root,
            read_roots=(workspace_root,) if requirements.requires_sources else (),
            sandbox=sandbox,
            permission_mode=permission_mode,
            approval_policy="never" if sandbox else "",
            network=requirements.network,
            input_manifest_path=None,
            input_manifest_sha256=None,
        )

    configured_workspace = requirements.workspace_root or getattr(
        staging,
        "workspace_root",
        None,
    )
    if configured_workspace is None:
        raise ExecutionContractError(
            "invalid_execution_path",
            "An isolated execution requires a workspace root",
        )
    workspace_root = Path(configured_workspace).resolve()
    if not requirements.requires_sources:
        return CompiledExecution(
            workspace_root=workspace_root,
            read_roots=(),
            sandbox=sandbox,
            permission_mode=permission_mode,
            approval_policy="never" if sandbox else "",
            network=requirements.network,
            input_manifest_path=None,
            input_manifest_sha256=None,
        )
    if policy.read_mode == "all":
        return CompiledExecution(
            workspace_root=workspace_root,
            read_roots=(),
            sandbox=sandbox,
            permission_mode=permission_mode,
            approval_policy="never" if sandbox else "",
            network=requirements.network,
            input_manifest_path=None,
            input_manifest_sha256=None,
        )
    if policy.read_mode == "home":
        raise ExecutionContractError(
            "source_scope_requires_selection",
            "Select a bounded source directory for isolated execution",
        )
    roots = tuple(path.resolve() for path in requirements.source_roots)
    if policy.read_mode != "selected" or not roots:
        raise ExecutionContractError(
            "source_scope_requires_selection",
            "Select a source directory for this execution",
        )
    selected_root = Path(policy.read_path).resolve() if policy.read_path else None
    if roots != (selected_root,):
        raise ExecutionContractError(
            "source_scope_requires_selection",
            "The selected source directory does not match the frozen policy",
        )
    try:
        staged = staging.stage(roots, workspace_root)
    except Exception as exc:
        raise ExecutionContractError(
            "source_staging_failed",
            "The selected source could not be staged",
        ) from exc
    return CompiledExecution(
        workspace_root=workspace_root,
        read_roots=tuple(Path(path).resolve() for path in staged.read_roots),
        sandbox=sandbox,
        permission_mode=permission_mode,
        approval_policy="never" if sandbox else "",
        network=requirements.network,
        input_manifest_path=Path(staged.manifest_path).resolve(),
        input_manifest_sha256=staged.manifest_sha256,
    )
