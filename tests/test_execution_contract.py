from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_agent_gateway.execution_contract import (
    ExecutionContractError,
    ExecutionRequirements,
    compile_execution,
)
from personal_agent_gateway.lmg_client import ProviderExecutionCapabilities
from personal_agent_gateway.space_policies import SpacePolicy


def _policy(read_mode: str, read_path: Path | None, write_mode: str = "isolated"):
    return SpacePolicy(
        scope="global",
        scope_id="",
        read_mode=read_mode,
        read_path=str(read_path) if read_path else None,
        write_mode=write_mode,
        workspace_path=None,
        created_at="",
        updated_at="",
    )


def _capabilities(**overrides):
    values = {
        "resume": True,
        "external_read_only_roots": False,
        "network_modes": ("unspecified", "denied", "required"),
        "sandbox_modes": ("read-only", "workspace-write", "danger-full-access"),
        "permission_modes": (),
    }
    values.update(overrides)
    return ProviderExecutionCapabilities(**values)


class FakeStaging:
    def __init__(self, workspace_root: Path, *, fail: bool = False):
        self.workspace_root = workspace_root
        self.fail = fail
        self.calls = []

    def stage(self, roots, workspace_root):
        self.calls.append((roots, workspace_root))
        if self.fail:
            raise OSError("copy failed")
        inputs = workspace_root / "_inputs"
        return SimpleNamespace(
            read_roots=(inputs,),
            manifest_path=inputs / "manifest.json",
            manifest_sha256="manifest-hash",
        )


def _requirements(
    *,
    source_roots=(),
    requires_sources=True,
    workspace_mode="isolated",
    workspace_root=None,
    network="unspecified",
    permission_mode="",
):
    return ExecutionRequirements(
        source_roots=tuple(source_roots),
        requires_sources=requires_sources,
        workspace_mode=workspace_mode,
        workspace_root=workspace_root,
        network=network,
        permission_mode=permission_mode,
    )


def test_compile_execution_ignores_transient_provider_readiness(tmp_path: Path) -> None:
    capabilities = ProviderExecutionCapabilities(
        resume=True,
        external_read_only_roots=False,
        network_modes=("unspecified",),
        sandbox_modes=("workspace-write",),
        permission_modes=(),
    )

    compiled = compile_execution(
        ExecutionRequirements(
            source_roots=(),
            requires_sources=False,
            workspace_mode="isolated",
            workspace_root=tmp_path,
            network="unspecified",
        ),
        _policy("none", None),
        capabilities,
        FakeStaging(tmp_path),
    )

    assert compiled.workspace_root == tmp_path.resolve()


def test_home_read_with_isolated_write_requires_selection(tmp_path: Path) -> None:
    with pytest.raises(ExecutionContractError) as error:
        compile_execution(
            _requirements(),
            _policy("home", None),
            _capabilities(),
            FakeStaging(tmp_path / "run"),
        )

    assert error.value.code == "source_scope_requires_selection"


def test_all_read_with_isolated_write_uses_unstaged_workspace(tmp_path: Path) -> None:
    staging = FakeStaging(tmp_path / "run")

    compiled = compile_execution(
        _requirements(requires_sources=True),
        _policy("all", None),
        _capabilities(),
        staging,
    )

    assert compiled.workspace_root == (tmp_path / "run").resolve()
    assert compiled.read_roots == ()
    assert compiled.input_manifest_path is None
    assert staging.calls == []


def test_no_source_isolated_execution_uses_empty_workspace(tmp_path: Path) -> None:
    staging = FakeStaging(tmp_path / "run")

    compiled = compile_execution(
        _requirements(requires_sources=False),
        _policy("none", None),
        _capabilities(),
        staging,
    )

    assert compiled.workspace_root == (tmp_path / "run").resolve()
    assert compiled.read_roots == ()
    assert compiled.input_manifest_path is None
    assert staging.calls == []


def test_selected_external_root_is_staged_without_omission(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    staging = FakeStaging(tmp_path / "run")

    compiled = compile_execution(
        _requirements(source_roots=(source,)),
        _policy("selected", source),
        _capabilities(),
        staging,
    )

    assert staging.calls == [((source.resolve(),), (tmp_path / "run").resolve())]
    assert compiled.read_roots == ((tmp_path / "run").resolve() / "_inputs",)
    assert compiled.input_manifest_sha256 == "manifest-hash"


def test_selected_root_mismatch_is_not_silently_substituted(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    other = tmp_path / "other"
    selected.mkdir()
    other.mkdir()

    with pytest.raises(ExecutionContractError) as error:
        compile_execution(
            _requirements(source_roots=(other,)),
            _policy("selected", selected),
            _capabilities(),
            FakeStaging(tmp_path / "run"),
        )

    assert error.value.code == "source_scope_requires_selection"


def test_staging_failure_has_stable_code(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ExecutionContractError) as error:
        compile_execution(
            _requirements(source_roots=(source,)),
            _policy("selected", source),
            _capabilities(),
            FakeStaging(tmp_path / "run", fail=True),
        )

    assert error.value.code == "source_staging_failed"
    assert "copy failed" not in str(error.value)


def test_required_network_must_be_supported(tmp_path: Path) -> None:
    with pytest.raises(ExecutionContractError) as error:
        compile_execution(
            _requirements(requires_sources=False, network="required"),
            _policy("none", None),
            _capabilities(network_modes=("unspecified",)),
            FakeStaging(tmp_path / "run"),
        )

    assert error.value.code == "unsupported_execution_capability"


def test_persona_permission_mode_is_preserved_for_supported_provider(
    tmp_path: Path,
) -> None:
    compiled = compile_execution(
        _requirements(requires_sources=False, permission_mode="plan"),
        _policy("none", None),
        _capabilities(permission_modes=("default", "acceptEdits", "plan")),
        FakeStaging(tmp_path / "run"),
    )

    assert compiled.permission_mode == "plan"


def test_unsupported_persona_permission_mode_fails_before_execution(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExecutionContractError) as error:
        compile_execution(
            _requirements(requires_sources=False, permission_mode="bypassPermissions"),
            _policy("none", None),
            _capabilities(permission_modes=("default", "acceptEdits", "plan")),
            FakeStaging(tmp_path / "run"),
        )

    assert error.value.code == "unsupported_execution_capability"


@pytest.mark.parametrize("mode", ["worktree", "full_access"])
def test_direct_workspace_modes_do_not_copy_sources(tmp_path: Path, mode: str) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    staging = FakeStaging(tmp_path / "run")

    compiled = compile_execution(
        _requirements(
            source_roots=(workspace,),
            workspace_mode=mode,
            workspace_root=workspace,
        ),
        _policy("home", workspace, mode),
        _capabilities(),
        staging,
    )

    assert compiled.workspace_root == workspace.resolve()
    assert compiled.read_roots == (workspace.resolve(),)
    assert staging.calls == []
