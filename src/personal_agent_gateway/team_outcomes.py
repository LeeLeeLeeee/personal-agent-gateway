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
    status: VerificationStatus | None
    evidence: str
    checked: bool = True


@dataclass(frozen=True)
class Mention:
    to: str
    text: str

    def __post_init__(self) -> None:
        # A note is one short line by design. A break lets a body forge extra
        # radio lines or a whole competing SPACE POLICY block ahead of the real
        # one once rendered -- refused here, at the one place every Mention
        # comes into being (whether via _parse_mentions or directly), rather
        # than stripped or escaped, since a mangled version of a forgery
        # attempt serves no one.
        #
        # `splitlines` is the check because it is Python's own answer to what a
        # line break is: it also breaks on U+2028 (LINE SEPARATOR), U+2029
        # (PARAGRAPH SEPARATOR), U+0085, \x0b and \x0c, every one of which a
        # `"\n" in text` test lets through -- and json carries a literal U+2028
        # through untouched, so a model needs no escape to emit one. Rejoining
        # with nothing must give the text back, which holds exactly when the
        # text carries no break at all; counting lines alone would let a
        # trailing break past.
        if "".join(self.text.splitlines()) != self.text:
            raise TaskOutcomeError()


@dataclass(frozen=True)
class TaskOutcome:
    status: TaskOutcomeStatus
    summary: str
    reason_code: str | None
    deliverables: tuple[Deliverable, ...]
    verifications: tuple[VerificationEvidence, ...]
    # Default keeps existing constructor calls working; a later task drops this
    # from the stored payload, so it must stay easy to omit.
    mentions: tuple[Mention, ...] = ()
    # One reason per note the parse refused. Notes are auxiliary, so a
    # malformed one is dropped instead of voiding the worker's finished task --
    # but dropping it silently is the loss this whole channel exists to
    # prevent, and the place that records a refusal (`_store_mentions`) is on
    # the far side of the ledger from the place that discovers one. So the
    # discovery rides on the outcome, which is the one thing that crosses.
    mention_refusals: tuple[str, ...] = ()


class TaskOutcomeError(ValueError):
    def __init__(self, code: str = "invalid_task_outcome") -> None:
        self.code = code
        super().__init__("Worker final response is not a valid TaskOutcome")


_OUTCOME_KEYS = frozenset(
    {"status", "summary", "reason_code", "deliverables", "verifications"}
)

# Why a note was refused. Our own words, never the model's: the note itself is
# not repeated into the ledger, only the fact that one was turned away.
MENTION_REFUSED_LINE_BREAK = "line_break"
MENTION_REFUSED_MALFORMED = "malformed"
_MENTION_REFUSALS = frozenset({MENTION_REFUSED_LINE_BREAK, MENTION_REFUSED_MALFORMED})


def parse_task_outcome(content: str) -> TaskOutcome:
    stripped = normalize_json_envelope(content)
    if not stripped or stripped.startswith("```"):
        raise TaskOutcomeError()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise TaskOutcomeError() from exc
    if not isinstance(raw, dict) or set(raw) not in (
        _OUTCOME_KEYS,
        _OUTCOME_KEYS | {"mentions"},
        # The shape asdict(TaskOutcome) produces, which is what the ledger
        # stores and hands back for re-parsing. Accepted so a stored outcome
        # round-trips; `mention_refusals` is not documented to any model.
        _OUTCOME_KEYS | {"mentions", "mention_refusals"},
    ):
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
    mentions, refusals = _parse_mentions(raw.get("mentions", []))
    return TaskOutcome(
        status=status,
        summary=summary.strip(),
        reason_code=reason_code.strip() if isinstance(reason_code, str) else None,
        deliverables=deliverables,
        verifications=verifications,
        mentions=mentions,
        mention_refusals=(
            _parse_mention_refusals(raw.get("mention_refusals", [])) + refusals
        ),
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
        if not isinstance(raw, dict) or set(raw) not in (
            {"name", "status", "evidence"},
            {"name", "checked", "status", "evidence"},
        ):
            raise TaskOutcomeError()
        name = raw["name"]
        status = raw["status"]
        evidence = raw["evidence"]
        checked = raw.get("checked", True)
        if not isinstance(checked, bool):
            raise TaskOutcomeError()
        # checked and status carry different facts, so the two have to agree:
        # a check that ran has a result, and one that did not cannot have one.
        if checked and status not in {"passed", "failed"}:
            raise TaskOutcomeError()
        if not checked and status is not None:
            raise TaskOutcomeError()
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(evidence, str)
            or not evidence.strip()
        ):
            raise TaskOutcomeError()
        normalized_name = name.strip()
        if normalized_name in names:
            raise TaskOutcomeError()
        names.add(normalized_name)
        verifications.append(
            VerificationEvidence(
                normalized_name, status, evidence.strip(), checked=checked
            )
        )
    return tuple(verifications)


def _parse_mentions(value: object) -> tuple[tuple[Mention, ...], tuple[str, ...]]:
    """The notes that stand, and one reason for each one turned away.

    A malformed note is dropped, never raised. Raising voids the whole outcome
    over a field that is not the worker's work -- and then costs a repair round
    whose prompt does not even ask the field back, so the note vanishes with
    nothing recorded. A bad recipient label already degrades and leaves the
    task standing; a malformed body is the same kind of fault and ends the same
    way. The reasons travel on the outcome so `_store_mentions` can write the
    refusal down in that same shape.
    """
    if not isinstance(value, list):
        return (), (MENTION_REFUSED_MALFORMED,)
    mentions: list[Mention] = []
    refusals: list[str] = []
    for raw in value:
        if (
            not isinstance(raw, dict)
            or set(raw) != {"to", "text"}
            or not isinstance(raw["to"], str)
            or not raw["to"].strip()
            or not isinstance(raw["text"], str)
            or not raw["text"].strip()
        ):
            refusals.append(MENTION_REFUSED_MALFORMED)
            continue
        try:
            # Mention refuses a line break at construction and keeps refusing
            # it: that invariant is what makes "no path can render a forged
            # line" structural rather than an audit of every asdict(outcome)
            # site. So the break is caught here, not pre-checked and allowed
            # through a relaxed dataclass.
            mentions.append(Mention(raw["to"].strip(), raw["text"].strip()))
        except TaskOutcomeError:
            refusals.append(MENTION_REFUSED_LINE_BREAK)
    return tuple(mentions), tuple(refusals)


def _parse_mention_refusals(value: object) -> tuple[str, ...]:
    """Refusals an earlier parse of this same outcome already recorded.

    Anything unrecognised is normalised to the generic reason rather than
    refused: raising here would void the outcome, which is the very failure
    this field exists to prevent.
    """
    if not isinstance(value, list):
        return (MENTION_REFUSED_MALFORMED,)
    return tuple(
        reason
        if isinstance(reason, str) and reason in _MENTION_REFUSALS
        else MENTION_REFUSED_MALFORMED
        for reason in value
    )


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
