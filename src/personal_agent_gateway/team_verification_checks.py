import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from personal_agent_gateway.file_safety import is_sensitive_file

MAX_CHECK_BYTES = 1_000_000
MAX_PATTERN_LENGTH = 200
FILE_MATCHES_SEARCH_CAP = 64_000
CHECK_TYPES = (
    "file_nonempty",
    "file_contains",
    "file_matches",
    "json_parses",
    "command_succeeds",
)
_VALUE_TYPES = {"file_contains"}
_PATTERN_TYPES = {"file_matches"}
# 파일을 가리키지 않는 유일한 검사. 나머지 넷은 "이 파일이 어떠한가" 를 묻고
# 이것은 "이것이 도는가" 를 묻는다.
_COMMAND_TYPES = {"command_succeeds"}
COMMAND_TIMEOUT_SECONDS = 120
MAX_COMMAND_EVIDENCE = 2_000

# `command_succeeds` 는 모델이 쓴 명령을 서버가 셸로 실행한다. 이 게이트웨이는
# 이미 에이전트에게 `shell.run` 을 주고 있으므로 새로 열리는 종류의 권한은
# 아니지만, 실행 시점이 다르다: 에이전트의 셸은 사람이 지시한 일을 하다 불리고,
# 이 검사는 판정할 때 자동으로, 재시도마다 다시 돈다. 파괴적인 명령 하나가
# 여러 번 실행될 수 있다는 뜻이다. 정규식과 같은 근거로 받아들인 위험이다 --
# 명령 작성자는 자기 일감을 판정하는 리드이고, 여기는 로컬 단일 사용자다.
#
# `file_matches` compiles a model-authored regex and searches model-authored
# text with Python's `re`, which has no execution timeout. Capping the
# pattern length and the searched text bounds the worst case, but does not
# eliminate it: even a short pattern (e.g. `(\w+\s?)+$`) can still backtrack
# exponentially against a crafted input and stall the caller. This is an
# accepted residual risk for this vocabulary — the pattern author (a model
# operating on its own task) is inside the trust boundary, and this is a
# local, single-user gateway.


@dataclass(frozen=True)
class VerificationCheck:
    type: str
    path: str
    value: str = ""
    pattern: str = ""
    command: str = ""


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    evidence: str


def parse_verification_check(value: object) -> VerificationCheck:
    if not isinstance(value, dict):
        raise ValueError(  # noqa: TRY004
            "Verification check must be an object"
        )
    check_type = value.get("type")
    if check_type not in CHECK_TYPES:
        raise ValueError(f"Unknown verification check type: {check_type!r}")
    if check_type in _COMMAND_TYPES:
        command = value.get("command")
        if not isinstance(command, str) or not command.strip():
            msg = f"Verification check {check_type} requires a command"
            raise ValueError(msg)
        if set(value) != {"type", "command"}:
            raise ValueError('Verification check fields must be exactly ["command", "type"]')
        return VerificationCheck(type=check_type, path="", command=command.strip())
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
    if check_type in _VALUE_TYPES | _PATTERN_TYPES and (
        not isinstance(detail, str) or not detail.strip()
    ):
        msg = f"Verification check {check_type} requires a non-empty value"
        raise ValueError(msg)
    if check_type in _PATTERN_TYPES and len(detail) > MAX_PATTERN_LENGTH:
        msg = (
            f"Verification check pattern must be at most {MAX_PATTERN_LENGTH} "
            "characters"
        )
        raise ValueError(msg)
    return VerificationCheck(
        type=check_type,
        path=path.strip(),
        value=detail if check_type in _VALUE_TYPES else "",
        pattern=detail if check_type in _PATTERN_TYPES else "",
    )


def verification_check_payload(check: VerificationCheck) -> dict[str, str]:
    if check.type in _COMMAND_TYPES:
        return {"type": check.type, "command": check.command}
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
    check: VerificationCheck,
    workspace: Path,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
) -> CheckResult:
    if check.type in _COMMAND_TYPES:
        return _run_command_check(check, workspace, timeout_seconds)
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
        truncated = len(text) > FILE_MATCHES_SEARCH_CAP
        search_text = text[:FILE_MATCHES_SEARCH_CAP] if truncated else text
        passed = pattern.search(search_text) is not None
        evidence = (
            f"file_matches: {check.path} "
            f"{'matched' if passed else 'did not match'}"
        )
        if truncated:
            evidence += f" (search truncated to {FILE_MATCHES_SEARCH_CAP} bytes)"
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


def _kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """자식까지 죽인다.

    process.kill() 만으로는 부족하다. shell=True 는 셸을 하나 띄우고 명령은
    그 자식이 되는데, 셸만 죽이면 손자가 살아남아 파이프를 잡고 있다. 그러면
    출력을 읽는 쪽이 명령이 제 수명을 다할 때까지 기다리게 되어, 제한 시간이
    있으나 마나가 된다 -- 실측에서 1초 제한에 30초를 기다렸다.
    """
    if os.name == "nt":
        subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def _run_command_check(
    check: VerificationCheck, workspace: Path, timeout_seconds: int
) -> CheckResult:
    """명령을 작업 폴더에서 돌리고 종료 코드로 판정한다.

    제한 시간이 있어야 하는 이유: 파일 검사는 즉시 끝나므로 이 함수의 다른
    갈래에는 그 위험이 없었다. 끝나지 않는 명령 하나는 판정을 영원히 붙잡고,
    그 사이 런은 아무 상태도 아닌 채로 남는다.

    출력은 잘라서 싣는다. 근거는 기록에 저장되고 리드의 다음 프롬프트로도
    가므로, 통째로 실으면 둘 다 넘친다. 다만 종료 코드와 끝부분은 남긴다 --
    실패한 명령이 마지막에 찍는 것이 대개 이유다.
    """
    kwargs: dict[str, object] = {}
    if os.name != "nt":
        # 프로세스 그룹을 따로 만들어야 손자까지 한 번에 죽일 수 있다.
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(  # noqa: S602 - 위 주석의 신뢰 경계 참조
            check.command,
            shell=True,
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
    except OSError as exc:
        return CheckResult(False, f"command_succeeds: 실행할 수 없습니다: {exc}")
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        # 죽인 뒤에도 파이프를 비워야 한다. 안 그러면 이 함수가 반환한 뒤에도
        # 핸들이 남는다. 트리를 죽였으므로 이 대기는 곧 끝난다.
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        msg = (
            f"command_succeeds: {check.command} 가 제한 시간 "
            f"{timeout_seconds}초 안에 끝나지 않았습니다"
        )
        return CheckResult(False, msg)
    output = "\n".join(
        stream.decode(errors="replace").strip()
        for stream in (stdout, stderr)
        if stream
    ).strip()
    if len(output) > MAX_COMMAND_EVIDENCE:
        output = "...(앞부분 생략)...\n" + output[-MAX_COMMAND_EVIDENCE:]
    passed = process.returncode == 0
    head = f"command_succeeds: {check.command} -> exit {process.returncode}"
    return CheckResult(passed, f"{head}\n{output}" if output else head)
