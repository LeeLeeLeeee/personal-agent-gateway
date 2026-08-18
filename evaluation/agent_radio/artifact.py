"""What one execution left behind.

Deliberately not a record: a record carries a human's verdict, and this
carries only what happened. Keeping them apart is what stops the aggregator's
counts sliding from "measured" to "attempted".

Pure, like fixture.py -- it imports nothing from personal_agent_gateway, so the
shape can be reasoned about without standing up a runtime.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_radio.fixture import (
    EXECUTION_PROFILES,
    MODES,
    FixtureError,
    _required_text,
)

ARTIFACT_SCHEMA = "gateway.eval-run/v1"
IMPLEMENTED_MODES = frozenset({"legacy"})
# The product's terminal statuses that mean "the Team produced an answer".
# `completed_with_failures` is one of them: the product writes it when every
# *required* task completed and some *optional* task did not, and it carries a
# real summary and no error. Excluding it would throw away a gradeable answer
# because one optional task was flaky -- with a real model that is an ordinary
# event, so it would quietly eat a share of every measurement.
ANSWERING_RUN_STATUSES = frozenset({"completed", "completed_with_failures"})


@dataclass(frozen=True)
class RunArtifact:
    run_id: str
    fixture_id: str
    fixture_sha256: str
    mode: str
    # See Record.plan_negotiation. Recorded here because it is a fact about what
    # the run did, not a verdict about it.
    plan_negotiation: bool
    execution_profile: str
    backend: str
    model: str
    # The commit the run was allowed to read. Required for the same reason
    # `backend` is: the source is half of what a run is, and two answers about
    # different trees are not two measurements of the same thing.
    source_commit: str
    # What actually answered, as opposed to `model`, which is what was asked
    # for. "default" is an alias the local provider configuration resolves, so
    # the same request runs a different model whenever that configuration
    # changes, and nothing in the request records the difference. Null when the
    # provider keeps no recoverable record of it -- an unknown is reported as an
    # unknown rather than backfilled with the alias.
    resolved_model: str | None
    # The reasoning effort the provider actually used. Not merely unresolved like
    # the model alias -- never requested at all, so the provider's own record is
    # the only one there is. Two runs at different efforts are not two samples of
    # the same configuration, however identical the request looked.
    resolved_effort: str | None
    # What the run cost, from the provider's transcript. LMG reports usage per
    # account, which cannot be attributed to one run, so this is the only place
    # a per-run number exists. Null rather than zero when unrecoverable: a run
    # averaged in as free would make every mode look cheaper than it is.
    input_tokens: int | None
    # The part of input_tokens the provider served from cache. Kept apart because
    # the two answer different questions: total input says how much context was
    # carried, fresh input (total minus this) says how much was newly processed.
    # A run with more turns over a growing context inflates the first without
    # spending proportionally more on the second.
    cached_input_tokens: int | None
    output_tokens: int | None
    started_at: str
    finished_at: str
    wall_ms: int
    run_status: str
    summary: str | None
    workspace_path: str
    repository_unchanged: bool
    error: str | None

    @property
    def scoreable(self) -> bool:
        """Whether a human should grade this at all.

        Three ways an execution produces nothing gradeable: it did not reach a
        status that carries an answer, it produced no answer -- a summary that
        is empty or only whitespace counts as no answer -- or it changed the
        repository. The last one applies whatever the execution profile:
        bounded_write is licensed to write to its own isolated workspace, never
        to the repository, so a bounded_write run that mutated the repository
        broke isolation exactly as badly as a read_only run would.
        """
        if self.run_status not in ANSWERING_RUN_STATUSES:
            return False
        if not self.summary or not self.summary.strip():
            return False
        if not self.repository_unchanged:
            return False
        return True


def parse_artifact(payload: dict) -> RunArtifact:
    if not isinstance(payload, dict):
        raise FixtureError("artefact is not an object")
    if payload.get("schema") != ARTIFACT_SCHEMA:
        raise FixtureError(f"unknown artefact schema: {payload.get('schema')!r}")
    mode = _required_text(payload, "mode")
    if mode not in MODES:
        raise FixtureError(f"unknown mode: {mode!r}")
    if mode not in IMPLEMENTED_MODES:
        raise FixtureError(f"mode is not implemented by the runner: {mode!r}")
    profile = _required_text(payload, "execution_profile")
    if profile not in EXECUTION_PROFILES:
        raise FixtureError(f"unknown execution profile: {profile!r}")
    negotiation = payload.get("plan_negotiation")
    if not isinstance(negotiation, bool):
        raise FixtureError("plan_negotiation must be a boolean")
    unchanged = payload.get("repository_unchanged")
    if not isinstance(unchanged, bool):
        raise FixtureError("repository_unchanged must be a boolean")
    wall_ms = payload.get("wall_ms")
    if not isinstance(wall_ms, int) or isinstance(wall_ms, bool) or wall_ms < 0:
        raise FixtureError("wall_ms must be a non-negative integer")
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise FixtureError("summary must be a string or null")
    error = payload.get("error")
    if error is not None and not isinstance(error, str):
        raise FixtureError("error must be a string or null")
    resolved_model = _recovered_text(payload, "resolved_model")
    resolved_effort = _recovered_text(payload, "resolved_effort")
    input_tokens = _recovered_count(payload, "input_tokens")
    cached_input_tokens = _recovered_count(payload, "cached_input_tokens")
    output_tokens = _recovered_count(payload, "output_tokens")
    if (
        input_tokens is not None
        and cached_input_tokens is not None
        and cached_input_tokens > input_tokens
    ):
        raise FixtureError("cached_input_tokens cannot exceed input_tokens")
    return RunArtifact(
        run_id=_required_text(payload, "run_id"),
        fixture_id=_required_text(payload, "fixture_id"),
        fixture_sha256=_required_text(payload, "fixture_sha256"),
        mode=mode,
        plan_negotiation=negotiation,
        execution_profile=profile,
        # Which provider and which model produced the answer. Required, not
        # optional: once the measurement is paid for, an artefact that does not
        # name what produced it cannot be compared with anything.
        backend=_required_text(payload, "backend"),
        model=_required_text(payload, "model"),
        source_commit=_required_text(payload, "source_commit"),
        resolved_model=resolved_model,
        resolved_effort=resolved_effort,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        started_at=_required_text(payload, "started_at"),
        finished_at=_required_text(payload, "finished_at"),
        wall_ms=wall_ms,
        run_status=_required_text(payload, "run_status"),
        summary=summary,
        workspace_path=_required_text(payload, "workspace_path"),
        repository_unchanged=unchanged,
        error=error,
    )


def _recovered_text(payload: dict, key: str) -> str | None:
    """A fact the provider may not have kept, as text.

    Absent is refused separately from null. A key that can be left out lets
    "nobody recorded this" pass as "this artefact predates the question", and
    those are different states that would both read as no data. An empty string
    is refused for the mirror reason: it claims a blank answer where null claims
    no answer.
    """
    if key not in payload:
        raise FixtureError(f"{key} is missing")
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FixtureError(f"{key} must be a non-empty string or null")
    return value


def _recovered_count(payload: dict, key: str) -> int | None:
    """A fact the provider may not have kept, as a count.

    Zero is a legitimate measurement and null is the absence of one, so they
    are kept distinct: a run recorded as costing zero tokens would be averaged
    in as free.
    """
    if key not in payload:
        raise FixtureError(f"{key} is missing")
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FixtureError(f"{key} must be a non-negative integer or null")
    return value


def write_artifact(directory: Path, artifact: RunArtifact) -> Path:
    """Write one artefact, refusing to overwrite.

    An artefact describes one execution. Overwriting loses the execution it
    described, and nothing else in the system records that it happened.

    run_id comes from a later task and cannot be trusted here -- this is the
    last place it is still a string rather than part of a path, so it is the
    last chance to refuse one that would escape the directory it was given.
    """
    if any(fragment in artifact.run_id for fragment in ("/", "\\", "..")):
        raise FixtureError(f"run_id is not a safe filename: {artifact.run_id!r}")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise FixtureError(f"{directory} is not a directory") from exc
    path = directory / f"{artifact.run_id}.json"
    if path.exists():
        raise FixtureError(f"an artefact for {artifact.run_id} already exists")
    payload = {"schema": ARTIFACT_SCHEMA, **asdict(artifact)}
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise FixtureError(f"could not write artefact for {artifact.run_id}") from exc
    return path


def load_artifacts(directory: Path) -> list[RunArtifact]:
    artifacts: list[RunArtifact] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_bytes())
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name} is not JSON") from exc
        artifacts.append(parse_artifact(payload))
    return artifacts
