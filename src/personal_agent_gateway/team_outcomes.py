import json
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from personal_agent_gateway.team_structured_output import normalize_json_envelope

TaskOutcomeStatus = Literal["completed", "blocked", "failed"]
VerificationStatus = Literal["passed", "failed"]


@dataclass(frozen=True)
class Deliverable:
    path: str
    kind: str


@dataclass(frozen=True)
class VerificationEvidence:
    name: str
    status: VerificationStatus
    evidence: str


@dataclass(frozen=True)
class TaskOutcome:
    status: TaskOutcomeStatus
    summary: str
    reason_code: str | None
    deliverables: tuple[Deliverable, ...]
    verifications: tuple[VerificationEvidence, ...]


class TaskOutcomeError(ValueError):
    def __init__(self, code: str = "invalid_task_outcome") -> None:
        self.code = code
        super().__init__("Worker final response is not a valid TaskOutcome")


def parse_task_outcome(content: str) -> TaskOutcome:
    stripped = normalize_json_envelope(content)
    if not stripped or stripped.startswith("```"):
        raise TaskOutcomeError()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise TaskOutcomeError() from exc
    if not isinstance(raw, dict) or set(raw) != {
        "status",
        "summary",
        "reason_code",
        "deliverables",
        "verifications",
    }:
        raise TaskOutcomeError()

    status = raw["status"]
    summary = raw["summary"]
    reason_code = raw["reason_code"]
    if status not in {"completed", "blocked", "failed"}:
        raise TaskOutcomeError()
    if not isinstance(summary, str) or not summary.strip():
        raise TaskOutcomeError()
    if reason_code is not None and (
        not isinstance(reason_code, str) or not reason_code.strip()
    ):
        raise TaskOutcomeError()

    deliverables = _parse_deliverables(raw["deliverables"])
    verifications = _parse_verifications(raw["verifications"])
    return TaskOutcome(
        status=status,
        summary=summary.strip(),
        reason_code=reason_code.strip() if isinstance(reason_code, str) else None,
        deliverables=deliverables,
        verifications=verifications,
    )


def _parse_deliverables(value: object) -> tuple[Deliverable, ...]:
    if not isinstance(value, list):
        raise TaskOutcomeError()
    deliverables: list[Deliverable] = []
    paths: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"path", "kind"}:
            raise TaskOutcomeError()
        path = raw["path"]
        kind = raw["kind"]
        if (
            not isinstance(path, str)
            or not _safe_relative_path(path)
            or not isinstance(kind, str)
            or not kind.strip()
        ):
            raise TaskOutcomeError()
        normalized_path = path.strip()
        if normalized_path in paths:
            raise TaskOutcomeError()
        paths.add(normalized_path)
        deliverables.append(Deliverable(normalized_path, kind.strip()))
    return tuple(deliverables)


def _parse_verifications(value: object) -> tuple[VerificationEvidence, ...]:
    if not isinstance(value, list):
        raise TaskOutcomeError()
    verifications: list[VerificationEvidence] = []
    names: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"name", "status", "evidence"}:
            raise TaskOutcomeError()
        name = raw["name"]
        status = raw["status"]
        evidence = raw["evidence"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or status not in {"passed", "failed"}
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            raise TaskOutcomeError()
        normalized_name = name.strip()
        if normalized_name in names:
            raise TaskOutcomeError()
        names.add(normalized_name)
        verifications.append(
            VerificationEvidence(normalized_name, status, evidence.strip())
        )
    return tuple(verifications)


def _safe_relative_path(value: str) -> bool:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        value.strip() not in {"", "."}
        and not posix.is_absolute()
        and not windows.is_absolute()
        and ".." not in posix.parts
        and ".." not in windows.parts
    )
