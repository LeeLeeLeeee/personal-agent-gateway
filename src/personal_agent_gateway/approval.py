from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

ApprovalStatus = Literal["pending", "approved", "denied"]


@dataclass(frozen=True)
class ShellApproval:
    id: str
    command: str
    status: ApprovalStatus


class ApprovalStore:
    def __init__(self) -> None:
        self._approvals: dict[str, ShellApproval] = {}

    def create(self, command: str) -> ShellApproval:
        approval = ShellApproval(id=uuid4().hex, command=command, status="pending")
        self._approvals[approval.id] = approval
        return approval

    def restore_pending(self, approval_id: str, command: str) -> ShellApproval:
        existing = self._approvals.get(approval_id)
        if existing is not None:
            return existing

        approval = ShellApproval(id=approval_id, command=command, status="pending")
        self._approvals[approval.id] = approval
        return approval

    def get(self, approval_id: str) -> ShellApproval:
        return self._approvals[approval_id]

    def approve(self, approval_id: str) -> ShellApproval:
        approval = self.get(approval_id)
        approved = ShellApproval(
            id=approval.id,
            command=approval.command,
            status="approved",
        )
        self._approvals[approval_id] = approved
        return approved

    def deny(self, approval_id: str) -> ShellApproval:
        approval = self.get(approval_id)
        denied = ShellApproval(
            id=approval.id,
            command=approval.command,
            status="denied",
        )
        self._approvals[approval_id] = denied
        return denied

    def pending(self) -> list[ShellApproval]:
        return [
            approval
            for approval in self._approvals.values()
            if approval.status == "pending"
        ]
