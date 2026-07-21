import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from personal_agent_gateway.approval import ApprovalStore, ShellApproval


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class ShellResult:
    approval_id: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PendingShellCommand:
    id: str
    command: str
    status: Literal["pending"]


class WorkspaceTools:
    def __init__(
        self,
        root: Path,
        approvals: ApprovalStore,
        read_roots: list[Path] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.read_roots = [path.resolve() for path in (read_roots or [])]
        self.approvals = approvals

    def fs_list(self, relative_path: str) -> list[str]:
        path = self._resolve_read_path(relative_path)
        return sorted(child.name for child in path.iterdir())

    def fs_read(self, relative_path: str) -> str:
        path = self._resolve_read_path(relative_path)
        return path.read_text(encoding="utf-8")

    def shell_request(self, command: str) -> PendingShellCommand:
        approval = self.approvals.create(command)
        return PendingShellCommand(
            id=approval.id,
            command=approval.command,
            status="pending",
        )

    def approve_shell(self, approval_id: str) -> ShellResult:
        approval = self.approvals.get(approval_id)
        if approval.status != "pending":
            raise ToolError(f"Approval {approval_id} is not pending")

        try:
            result = subprocess.run(
                approval.command,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=60,
                shell=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolError(f"Shell command failed: {approval.command}") from exc

        approved = self.approvals.approve(approval_id)
        return ShellResult(
            approval_id=approved.id,
            command=approved.command,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def deny_shell(self, approval_id: str) -> ShellApproval:
        approval = self.approvals.get(approval_id)
        if approval.status != "pending":
            raise ToolError(f"Approval {approval_id} is not pending")
        return self.approvals.deny(approval_id)

    def _resolve_workspace_path(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ToolError(f"Path escapes workspace: {relative_path}") from exc
        return path

    def _resolve_read_path(self, value: str) -> Path:
        requested = Path(value).expanduser()
        path = requested.resolve() if requested.is_absolute() else (self.root / requested).resolve()
        allowed_roots = [self.root, *self.read_roots]
        if not any(path == root or root in path.parents for root in allowed_roots):
            raise ToolError(f"Path escapes readable SPACE: {value}")
        return path
