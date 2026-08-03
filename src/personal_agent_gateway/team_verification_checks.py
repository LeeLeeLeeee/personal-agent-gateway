import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from personal_agent_gateway.file_safety import is_sensitive_file

MAX_CHECK_BYTES = 1_000_000
CHECK_TYPES = ("file_nonempty", "file_contains", "file_matches", "json_parses")
_VALUE_TYPES = {"file_contains"}
_PATTERN_TYPES = {"file_matches"}


@dataclass(frozen=True)
class VerificationCheck:
    type: str
    path: str
    value: str = ""
    pattern: str = ""


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    evidence: str


def parse_verification_check(value: object) -> VerificationCheck:
    if not isinstance(value, dict):
        raise ValueError("Verification check must be an object")
    check_type = value.get("type")
    if check_type not in CHECK_TYPES:
        raise ValueError(f"Unknown verification check type: {check_type!r}")
    path = value.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Verification check requires a path")
    if not _safe_relative(path.strip()):
        raise ValueError("Verification check path must be relative and bounded")
    expected = {"type", "path"}
    if check_type in _VALUE_TYPES:
        expected.add("value")
    if check_type in _PATTERN_TYPES:
        expected.add("pattern")
    if set(value) != expected:
        raise ValueError(f"Verification check fields must be exactly {sorted(expected)}")
    detail = ""
    if check_type in _VALUE_TYPES:
        detail = value["value"]
    elif check_type in _PATTERN_TYPES:
        detail = value["pattern"]
    if check_type in _VALUE_TYPES | _PATTERN_TYPES:
        if not isinstance(detail, str) or not detail.strip():
            msg = f"Verification check {check_type} requires a non-empty value"
            raise ValueError(msg)
    return VerificationCheck(
        type=check_type,
        path=path.strip(),
        value=detail if check_type in _VALUE_TYPES else "",
        pattern=detail if check_type in _PATTERN_TYPES else "",
    )


def verification_check_payload(check: VerificationCheck) -> dict[str, str]:
    payload = {"type": check.type, "path": check.path}
    if check.type in _VALUE_TYPES:
        payload["value"] = check.value
    if check.type in _PATTERN_TYPES:
        payload["pattern"] = check.pattern
    return payload


def safe_workspace_file(workspace: Path, relative_path: str) -> Path | None:
    root = workspace.resolve()
    candidate = root / relative_path
    current = candidate
    while current != root:
        if current.is_symlink():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
        if root not in current.parents and current != root:
            break
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    if is_sensitive_file(candidate.name):
        return None
    return candidate


def run_verification_check(
    check: VerificationCheck, workspace: Path
) -> CheckResult:
    resolved = safe_workspace_file(workspace, check.path)
    if resolved is None:
        msg = f"{check.type}: {check.path} is missing or not readable"
        return CheckResult(False, msg)
    try:
        size = resolved.stat().st_size
        if size > MAX_CHECK_BYTES:
            msg = (
                f"{check.type}: {check.path} is too large ({size} bytes)"
            )
            return CheckResult(False, msg)
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        msg = f"{check.type}: {check.path} could not be read: {exc}"
        return CheckResult(False, msg)
    if check.type == "file_nonempty":
        passed = bool(text.strip())
        evidence = (
            f"file_nonempty: {check.path} {'has' if passed else 'has no'} content"
        )
        return CheckResult(passed, evidence)
    if check.type == "file_contains":
        passed = check.value in text
        evidence = (
            f"file_contains: {check.path} "
            f"{'contains' if passed else 'lacks'} the value"
        )
        return CheckResult(passed, evidence)
    if check.type == "file_matches":
        try:
            pattern = re.compile(check.pattern, re.MULTILINE)
        except re.error as exc:
            msg = f"file_matches: pattern did not compile: {exc}"
            return CheckResult(False, msg)
        passed = pattern.search(text) is not None
        evidence = (
            f"file_matches: {check.path} "
            f"{'matched' if passed else 'did not match'}"
        )
        return CheckResult(passed, evidence)
    if check.type == "json_parses":
        try:
            json.loads(text)
        except ValueError as exc:
            msg = f"json_parses: {check.path} is not valid JSON: {exc}"
            return CheckResult(False, msg)
        return CheckResult(True, f"json_parses: {check.path} parsed")
    msg = f"unknown verification check type: {check.type!r}"
    return CheckResult(False, msg)


def _safe_relative(value: str) -> bool:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        value not in {"", "."}
        and not posix.is_absolute()
        and not windows.is_absolute()
        and ".." not in posix.parts
        and ".." not in windows.parts
    )
