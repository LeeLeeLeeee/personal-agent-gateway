import subprocess
import sys
from pathlib import Path

import pytest

from personal_agent_gateway.approval import ApprovalStore
from personal_agent_gateway.tools import ToolError, WorkspaceTools


def make_tools(root: Path) -> WorkspaceTools:
    return WorkspaceTools(root=root, approvals=ApprovalStore())


def write_file_command(filename: str, content: str) -> str:
    code = (
        "from pathlib import Path; "
        f"Path({filename!r}).write_text({content!r}, encoding='utf-8')"
    )
    return f'"{sys.executable}" -c "{code}"'


def test_fs_list_lists_only_workspace_entries(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("inside", encoding="utf-8")
    (workspace / "nested").mkdir()
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    tools = make_tools(workspace)

    assert tools.fs_list(".") == ["file.txt", "nested"]


def test_fs_read_reads_file_inside_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("inside", encoding="utf-8")
    tools = make_tools(workspace)

    assert tools.fs_read("file.txt") == "inside"


def test_fs_read_rejects_outside_path_with_similar_string_prefix(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "workspace-outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    tools = make_tools(workspace)

    with pytest.raises(ToolError):
        tools.fs_read("../workspace-outside/outside.txt")


def test_shell_request_creates_pending_approval_without_executing(tmp_path: Path) -> None:
    tools = make_tools(tmp_path)

    pending = tools.shell_request("printf executed > requested.txt")

    assert pending.command == "printf executed > requested.txt"
    assert pending.status == "pending"
    assert tools.approvals.get(pending.id).command == "printf executed > requested.txt"
    assert tools.approvals.get(pending.id).status == "pending"
    assert not (tmp_path / "requested.txt").exists()


def test_approve_shell_executes_exactly_one_approved_command(tmp_path: Path) -> None:
    tools = make_tools(tmp_path)
    command = write_file_command("ran.txt", "executed")
    pending = tools.shell_request(command)

    result = tools.approve_shell(pending.id)

    assert result.approval_id == pending.id
    assert result.command == command
    assert result.exit_code == 0
    assert (tmp_path / "ran.txt").read_text(encoding="utf-8") == "executed"
    with pytest.raises(ToolError, match="not pending"):
        tools.approve_shell(pending.id)


def test_approve_shell_timeout_raises_tool_error_and_keeps_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = make_tools(tmp_path)
    pending = tools.shell_request("sleep 10")

    def raise_timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="sleep 10", timeout=60)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    with pytest.raises(ToolError, match="Shell command failed"):
        tools.approve_shell(pending.id)

    assert tools.approvals.get(pending.id).status == "pending"


def test_deny_shell_records_denial_and_does_not_execute(tmp_path: Path) -> None:
    tools = make_tools(tmp_path)
    pending = tools.shell_request("printf denied > denied.txt")

    denied = tools.deny_shell(pending.id)

    assert denied.id == pending.id
    assert denied.command == "printf denied > denied.txt"
    assert denied.status == "denied"
    assert not (tmp_path / "denied.txt").exists()
