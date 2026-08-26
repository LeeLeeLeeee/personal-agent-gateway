import json
import shutil
import sqlite3
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from personal_agent_gateway.db import Database
from personal_agent_gateway.pagination import decode_cursor, encode_cursor
from personal_agent_gateway.personas import Persona, PersonaService
from personal_agent_gateway.space_policies import (
    SpacePolicyService,
    TeamSpaceManager,
    policy_from_snapshot,
    policy_json,
)
from personal_agent_gateway.team_lifecycle import (
    MAX_CONCURRENT_WORKERS,
    TERMINAL_CYCLE_STATUSES,
    TERMINAL_RUN_STATUSES,
    CycleStatus,
    TaskStatus,
    TeamRunStatus,
)
from personal_agent_gateway.team_plan_negotiation import next_revision
from personal_agent_gateway.team_verification_checks import (
    VerificationCheck,
    parse_verification_check,
    verification_check_payload,
)
from personal_agent_gateway.team_workspace_inheritance import inherit_workspace

if TYPE_CHECKING:
    from personal_agent_gateway.team_cycles import ExecutionPolicy, TeamCycleService


RunMode = Literal["planning_only", "plan_and_execute", "review_only"]
LifecycleMode = Literal["standard", "continuous"]
AgentStatus = Literal["pending", "running", "waiting", "completed", "failed", "canceled"]
DecisionRequestStatus = Literal["collecting", "awaiting_user", "resolved", "canceled"]
# 두 번은 이 저장소가 실제로 내는 크기의 일감에 짧았다. 실측에서 리드가 두
# 번째 심사에 "거의 다 왔다, 마지막 시도에서 이것만 닫아라" 라고 쓴 채로
# 한도가 끝났다 -- 남은 한 칸이 무엇인지 알고 있는데 시도할 자리가 없었다.
#
# 시도를 늘리는 것만으로는 닫히지 않을 구멍에 네 번을 쓰게 될 뿐이다. 그래서
# ACCEPTANCE_REVIEW_PROMPT 가 매 회차에 무엇이 막고 있는지와 남은 시도로
# 닫히는지를 먼저 답하게 하고, 아니면 지금 fail 하도록 요구한다. 둘은 같이
# 움직여야 한다.
ACCEPTANCE_RECOVERY_CAP = 4

_ACTIVE_RUN_STATUSES = {"planning", "running", "summarizing", "waiting_for_provider"}
_PROVIDER_WAIT_SOURCE_RUN_STATUSES = {"planning", "running", "summarizing"}


@dataclass(frozen=True)
class TeamRun:
    id: str
    goal: str
    status: TeamRunStatus
    run_mode: RunMode
    lifecycle_mode: LifecycleMode
    leader_agent_id: str | None
    max_workers: int
    rounds_budget: int
    rounds_used: int
    workspace_root: str
    summary: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    team_id: str | None = None
    rules_snapshot: dict | None = None
    execution_policy: Literal["auto", "triggered"] | None = None
    working_root: str | None = None
    artifact_root: str | None = None
    worktree_branch: str | None = None
    space_policy: dict | None = None
    parent_team_run_id: str | None = None
    plan_negotiation_enabled: bool = False
    pause_requested_at: str | None = None


@dataclass(frozen=True)
class TeamRunCycle:
    id: str
    team_run_id: str
    sequence: int
    source_type: str
    source_id: str
    status: CycleStatus
    rounds_budget: int
    rounds_used: int
    summary: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    request_id: str | None = None
    rules_snapshot: dict | None = None
    execution_metadata: dict[str, object] | None = None
    space_policy: dict | None = None


@dataclass(frozen=True)
class TeamCycleInputArtifact:
    artifact_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    created_at: str


@dataclass(frozen=True)
class TeamTaskInputArtifact:
    artifact_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    staged_path: str
    created_at: str


@dataclass(frozen=True)
class TeamTaskDependency:
    task_id: str
    depends_on_task_id: str


@dataclass(frozen=True)
class ProviderRecoveryClaim:
    team_run_id: str
    cycle_id: str
    task_id: str | None
    operation_id: str | None = None


@dataclass(frozen=True)
class TeamAgent:
    id: str
    team_run_id: str
    name: str
    role: str
    persona_id: str
    persona_snapshot: dict[str, object]
    backend: str
    model: str
    status: AgentStatus
    workspace_path: str | None
    current_task_id: str | None
    reinvocations: int
    upstream_session_id: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class RequiredVerification:
    name: str
    check: VerificationCheck | None = None


@dataclass(frozen=True)
class TaskAcceptance:
    required_outputs: tuple[str, ...]
    required_verifications: tuple[RequiredVerification, ...]


@dataclass(frozen=True)
class TeamTask:
    id: str
    team_run_id: str
    title: str
    description: str
    owner_agent_id: str | None
    status: TaskStatus
    required: bool
    acceptance: TaskAcceptance
    outcome: dict[str, object] | None
    acceptance_result: dict[str, object] | None
    result: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    cycle_id: str | None = None
    retry_of_task_id: str | None = None
    acceptance_recovery_attempts: int = 0
    plan_ordinal: int = 0


@dataclass(frozen=True)
class TeamMessage:
    id: str
    team_run_id: str
    sender_agent_id: str | None
    recipient_agent_id: str | None
    kind: str
    content: str
    metadata: dict[str, object]
    created_at: str
    cycle_id: str | None = None


@dataclass(frozen=True)
class TeamDecisionRequest:
    id: str
    team_run_id: str
    status: DecisionRequestStatus
    revision: int
    items: list[dict[str, object]]
    answers: dict[str, str]
    file_path: str
    created_at: str
    published_at: str | None
    answered_at: str | None
    updated_at: str
    cycle_id: str | None = None


@dataclass(frozen=True)
class TeamPlanRevision:
    id: str
    team_run_id: str
    cycle_id: str | None
    revision: int
    status: str
    task_ids: tuple[str, ...]
    required_approver_agent_ids: tuple[str, ...]
    created_at: str
    decided_at: str | None


class TeamRunService:
    def __init__(
        self,
        db: Database,
        personas: PersonaService,
        workspace_root: Path,
        cycle_service: "TeamCycleService | None" = None,
        space_policies: SpacePolicyService | None = None,
        space_manager: TeamSpaceManager | None = None,
        concurrent_workers: bool = False,
    ) -> None:
        # Whether execution may overlap assignments. The run list reports the
        # concurrency a run will actually get, and that is not a property of
        # the run row: the same row executes sequentially or overlapped
        # depending on how the gateway is configured now.
        self._concurrent_workers = concurrent_workers
        self._db = db
        self._personas = personas
        self._workspace_root = workspace_root
        self._cycle_service = cycle_service
        self._space_policies = space_policies or SpacePolicyService(db)
        self._space_policies.seed_defaults()
        self._space_manager = space_manager or TeamSpaceManager()

    def effective_workers(self, configured: int) -> int:
        """How many assignments this run will actually overlap.

        One when concurrency is off, whatever the roster size -- that was the
        only answer before and is still the answer for an operator who has not
        turned it on. Otherwise the roster bounded by the executor's ceiling,
        because a bigger roster does not buy more overlap and saying it does
        would be a promise the executor breaks.
        """
        if not self._concurrent_workers:
            return 1
        return max(1, min(int(configured or 1), MAX_CONCURRENT_WORKERS))

    def create_team_run(
        self,
        goal: str,
        leader_persona_id: str,
        member_persona_ids: list[str],
        run_mode: RunMode,
        max_workers: int,
        rounds_budget: int = 8,
        team_id: str | None = None,
        rules_snapshot_json: str | None = None,
        lifecycle_mode: LifecycleMode = "standard",
        execution_policy: "ExecutionPolicy | None" = None,
        auto_repeat_count: int | None = None,
        auto_interval_seconds: int | None = None,
        parent_team_run_id: str | None = None,
        plan_negotiation: bool = False,
    ) -> TeamRun:
        if (
            lifecycle_mode == "continuous"
            and execution_policy not in {"auto", "triggered"}
        ):
            raise ValueError("Continuous Team Run requires an execution policy")
        if execution_policy == "auto":
            if not auto_repeat_count or auto_repeat_count < 1:
                raise ValueError("AUTO repeat count must be positive")
            if not auto_interval_seconds or auto_interval_seconds < 60:
                raise ValueError("AUTO interval must be at least 60 seconds")
            if self._cycle_service is None:
                raise RuntimeError("AUTO Team Run requires a cycle service")
        elif auto_repeat_count is not None or auto_interval_seconds is not None:
            raise ValueError("TRIGGERED Team Run does not accept AUTO settings")

        parent_run = None
        if parent_team_run_id:
            parent_run = self.get_team_run(parent_team_run_id)
            if parent_run.status not in TERMINAL_RUN_STATUSES:
                raise ValueError("Parent Team Run must be terminal before inheritance")
            if not parent_run.working_root:
                raise ValueError("Parent Team Run has no working workspace")

        team_run_id = uuid4().hex
        now = _now()
        effective_space = self._space_policies.resolve(
            team_id=team_id,
            persona_id=leader_persona_id,
        )
        if parent_run and effective_space.policy.write_mode != "isolated":
            raise ValueError("Workspace inheritance requires isolated target SPACE")
        workspace_root_path = self._workspace_root / team_run_id
        workspace_root_path.mkdir(parents=True)
        workspace_root = str(workspace_root_path)
        prepared_space = None
        try:
            prepared_space = self._space_manager.prepare(
                team_run_id,
                workspace_root_path,
                effective_space.policy,
            )
            if parent_run:
                inherit_workspace(
                    Path(parent_run.working_root),
                    prepared_space.working_root,
                    prepared_space.artifact_root / "workspace-inheritance.json",
                    parent_run.id,
                )
            with self._db.connection() as connection:
                connection.execute("begin immediate")
                connection.execute(
                    """
                    insert into team_runs (
                        id, parent_team_run_id, goal, status, run_mode, lifecycle_mode, execution_policy,
                        leader_agent_id, max_workers, rounds_budget, rounds_used,
                        workspace_root, working_root, artifact_root, worktree_branch,
                        space_policy_snapshot_json, summary, error_message, created_at,
                        started_at, finished_at, updated_at, team_id, rules_snapshot_json,
                        plan_negotiation_enabled
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        team_run_id,
                        parent_run.id if parent_run else None,
                        goal,
                        "draft",
                        run_mode,
                        lifecycle_mode,
                        execution_policy,
                        None,
                        max_workers,
                        rounds_budget,
                        0,
                        workspace_root,
                        str(prepared_space.working_root),
                        str(prepared_space.artifact_root),
                        prepared_space.worktree_branch,
                        policy_json(effective_space.policy),
                        None,
                        None,
                        now,
                        None,
                        None,
                        now,
                        team_id,
                        rules_snapshot_json,
                        1 if plan_negotiation else 0,
                    ),
                )
                leader_agent = self._insert_agent(
                    connection,
                    team_run_id,
                    leader_persona_id,
                    "leader",
                    _now(),
                    str(prepared_space.working_root),
                )
                for member_persona_id in member_persona_ids:
                    self._insert_agent(
                        connection,
                        team_run_id,
                        member_persona_id,
                        "member",
                        _now(),
                        str(prepared_space.working_root),
                    )
                connection.execute(
                    "update team_runs set leader_agent_id = ?, updated_at = ? where id = ?",
                    (leader_agent.id, now, team_run_id),
                )
                if execution_policy == "auto":
                    self._cycle_service.initialize_auto_series(
                        connection,
                        team_run_id,
                        target_slots=auto_repeat_count,
                        interval_seconds=auto_interval_seconds,
                        now=now,
                    )
        except Exception:
            if prepared_space is not None:
                self._space_manager.cleanup(
                    workspace_root_path,
                    effective_space.policy,
                    prepared_space.working_root,
                    prepared_space.worktree_branch,
                )
            elif workspace_root_path.exists():
                shutil.rmtree(workspace_root_path)
            raise
        return self.get_team_run(team_run_id)

    def create_team_run_from_team(
        self,
        team_service,
        rule_set_service,
        team_id: str,
        goal: str,
        run_mode: RunMode,
        max_workers: int,
        rounds_budget: int = 8,
        lifecycle_mode: LifecycleMode = "standard",
        execution_policy: "ExecutionPolicy | None" = None,
        auto_repeat_count: int | None = None,
        auto_interval_seconds: int | None = None,
        parent_team_run_id: str | None = None,
        plan_negotiation: bool = False,
    ) -> TeamRun:
        team = team_service.get_team(team_id)
        snapshot = rule_set_service.snapshot_for_team(team_id)
        if snapshot.get("team") is not None:
            snapshot["team"]["name"] = team.name
        return self.create_team_run(
            goal=goal,
            leader_persona_id=team.leader_persona_id,
            member_persona_ids=list(team.member_persona_ids),
            run_mode=run_mode,
            max_workers=max_workers,
            rounds_budget=rounds_budget,
            team_id=team_id,
            rules_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            lifecycle_mode=lifecycle_mode,
            execution_policy=execution_policy,
            auto_repeat_count=auto_repeat_count,
            auto_interval_seconds=auto_interval_seconds,
            parent_team_run_id=parent_team_run_id,
            plan_negotiation=plan_negotiation,
        )

    def get_team_run(self, team_run_id: str) -> TeamRun:
        row = self._db.fetchone("select * from team_runs where id = ?", (team_run_id,))
        if row is None:
            raise KeyError(f"Team run not found: {team_run_id}")
        return _team_run_from_row(row)

    def create_cycle(
        self,
        team_run_id: str,
        source_type: str,
        source_id: str,
        rounds_budget: int | None = None,
        request_id: str | None = None,
    ) -> TeamRunCycle:
        run = self.get_team_run(team_run_id)
        if run.lifecycle_mode != "continuous":
            raise ValueError("Cycles require a continuous team run")

        with self._db.connection() as connection:
            connection.execute("begin immediate")
            if request_id is not None:
                request = connection.execute(
                    "select * from team_cycle_requests where id = ?",
                    (request_id,),
                ).fetchone()
                if request is None:
                    raise KeyError(f"Team cycle request not found: {request_id}")
                if request["team_run_id"] != team_run_id:
                    raise ValueError("Cycle request belongs to a different team run")
                existing = connection.execute(
                    "select * from team_run_cycles where request_id = ?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    return _team_run_cycle_from_row(existing)
            normalized_source_type = source_type.strip()
            normalized_source_id = source_id.strip()
            if not normalized_source_type or not normalized_source_id:
                raise ValueError("Cycle source type and source id are required")
            normalized_budget = (
                run.rounds_budget if rounds_budget is None else rounds_budget
            )
            if normalized_budget < 1:
                raise ValueError("Cycle rounds budget must be positive")
            if request_id is not None:
                if request["status"] != "dispatching":
                    raise ValueError("Cycle request must be dispatching")
                if (
                    request["source_type"] != normalized_source_type
                    or request["source_id"] != normalized_source_id
                ):
                    raise ValueError("Cycle source does not match the cycle request")
            existing = connection.execute(
                """
                select * from team_run_cycles
                where team_run_id = ? and source_type = ? and source_id = ?
                """,
                (team_run_id, normalized_source_type, normalized_source_id),
            ).fetchone()
            if existing is not None:
                if request_id is not None and existing["request_id"] != request_id:
                    raise ValueError("Cycle source is linked to a different request")
                return _team_run_cycle_from_row(existing)
            row = connection.execute(
                "select coalesce(max(sequence), 0) + 1 as next from team_run_cycles "
                "where team_run_id = ?",
                (team_run_id,),
            ).fetchone()
            sequence = int(row["next"])
            cycle_id = uuid4().hex
            now = _now()
            space_policy_snapshot_json = self._space_policy_snapshot_for_cycle(run)
            connection.execute(
                """
                insert into team_run_cycles (
                    id, team_run_id, request_id, sequence, source_type, source_id, status,
                    rounds_budget, rounds_used, space_policy_snapshot_json, summary,
                    error_message, created_at, started_at, finished_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, 'queued', ?, 0, ?, null, null, ?, null, null, ?)
                """,
                (
                    cycle_id,
                    team_run_id,
                    request_id,
                    sequence,
                    normalized_source_type,
                    normalized_source_id,
                    normalized_budget,
                    space_policy_snapshot_json,
                    now,
                    now,
                ),
            )
            if request_id is not None:
                connection.execute(
                    """
                    insert into team_cycle_input_artifacts (
                        cycle_id, artifact_id, relative_path, sha256,
                        size_bytes, created_at
                    )
                    select ?, artifact_id, relative_path, sha256, size_bytes, created_at
                    from team_cycle_request_input_artifacts
                    where cycle_request_id = ?
                    order by rowid asc
                    """,
                    (cycle_id, request_id),
                )
        return self.get_cycle(cycle_id)

    def get_cycle_for_request(self, request_id: str) -> TeamRunCycle | None:
        row = self._db.fetchone(
            "select * from team_run_cycles where request_id = ?", (request_id,)
        )
        return _team_run_cycle_from_row(row) if row is not None else None

    def get_cycle(self, cycle_id: str) -> TeamRunCycle:
        row = self._db.fetchone(
            "select * from team_run_cycles where id = ?", (cycle_id,)
        )
        if row is None:
            raise KeyError(f"Team run cycle not found: {cycle_id}")
        return _team_run_cycle_from_row(row)

    def list_cycle_input_artifacts(
        self,
        cycle_id: str,
    ) -> list[TeamCycleInputArtifact]:
        rows = self._db.fetchall(
            """
            select artifact_id, relative_path, sha256, size_bytes, created_at
            from team_cycle_input_artifacts
            where cycle_id = ?
            order by rowid asc
            """,
            (cycle_id,),
        )
        return [
            TeamCycleInputArtifact(
                artifact_id=row["artifact_id"],
                relative_path=row["relative_path"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_task_input_artifacts(
        self,
        task_id: str,
    ) -> list[TeamTaskInputArtifact]:
        rows = self._db.fetchall(
            """
            select artifact_id, relative_path, sha256, size_bytes, staged_path, created_at
            from team_task_input_artifacts
            where task_id = ?
            order by rowid asc
            """,
            (task_id,),
        )
        return [
            TeamTaskInputArtifact(
                artifact_id=row["artifact_id"],
                relative_path=row["relative_path"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                staged_path=row["staged_path"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_cycle_objective(self, cycle_id: str) -> str | None:
        row = self._db.fetchone(
            """
            select request.instruction from team_run_cycles cycle
            left join team_cycle_requests request on request.id = cycle.request_id
            where cycle.id = ?
            """,
            (cycle_id,),
        )
        if row is None:
            raise KeyError(f"Team run cycle not found: {cycle_id}")
        objective = str(row["instruction"] or "").strip()
        return objective or None

    def list_cycles(self, team_run_id: str) -> list[TeamRunCycle]:
        self.get_team_run(team_run_id)
        return [
            _team_run_cycle_from_row(row)
            for row in self._db.fetchall(
                "select * from team_run_cycles where team_run_id = ? order by sequence asc",
                (team_run_id,),
            )
        ]

    def list_source_cycles(self, source_type: str) -> list[TeamRunCycle]:
        return [
            _team_run_cycle_from_row(row)
            for row in self._db.fetchall(
                """
                select * from team_run_cycles
                where source_type = ?
                order by created_at asc, id asc
                """,
                (source_type,),
            )
        ]

    def get_cycle_for_source(
        self,
        source_type: str,
        source_id: str,
    ) -> TeamRunCycle | None:
        row = self._db.fetchone(
            """
            select * from team_run_cycles
            where source_type = ? and source_id = ?
            order by created_at asc, id asc limit 1
            """,
            (source_type, source_id),
        )
        return _team_run_cycle_from_row(row) if row is not None else None

    def increment_cycle_rounds_used(self, cycle_id: str) -> TeamRunCycle:
        self.get_cycle(cycle_id)
        self._db.execute(
            "update team_run_cycles set rounds_used = rounds_used + 1, updated_at = ? "
            "where id = ?",
            (_now(), cycle_id),
        )
        return self.get_cycle(cycle_id)

    def set_cycle_execution_metadata(
        self,
        cycle_id: str,
        metadata: dict[str, object],
    ) -> TeamRunCycle:
        self.get_cycle(cycle_id)
        self._db.execute(
            """
            update team_run_cycles
            set execution_metadata_json = ?, updated_at = ?
            where id = ?
            """,
            (
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                _now(),
                cycle_id,
            ),
        )
        return self.get_cycle(cycle_id)

    def set_cycle_effective_instruction(
        self,
        cycle_id: str,
        instruction: str,
        output_contract_id: str | None = None,
    ) -> TeamRunCycle:
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("Cycle effective instruction is required")
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Team run cycle not found: {cycle_id}")
            metadata = _execution_metadata_object(row["execution_metadata_json"])
            semantic_source = metadata.get("semantic_source", {})
            if not isinstance(semantic_source, dict):
                raise ValueError("Cycle semantic source metadata is invalid")
            existing = semantic_source.get("effective_instruction")
            if existing is not None and existing != instruction:
                raise ValueError("Cycle effective instruction is immutable")
            metadata["semantic_source"] = {
                **semantic_source,
                "effective_instruction": instruction,
                "output_contract_id": output_contract_id,
            }
            cursor = connection.execute(
                """
                update team_run_cycles
                set execution_metadata_json = ?, updated_at = ? where id = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    now,
                    cycle_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Team run cycle changed before metadata update")
            updated = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            return _team_run_cycle_from_row(updated)

    def get_cycle_effective_instruction(self, cycle_id: str) -> str | None:
        row = self._db.fetchone(
            "select execution_metadata_json from team_run_cycles where id = ?",
            (cycle_id,),
        )
        if row is None:
            raise KeyError(f"Team run cycle not found: {cycle_id}")
        metadata = _execution_metadata_object(row["execution_metadata_json"])
        semantic_source = metadata.get("semantic_source", {})
        if not isinstance(semantic_source, dict):
            raise ValueError("Cycle semantic source metadata is invalid")
        instruction = semantic_source.get("effective_instruction")
        if instruction is None:
            return None
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("Cycle effective instruction metadata is invalid")
        return instruction

    def get_cycle_output_contract_id(self, cycle_id: str) -> str | None:
        row = self._db.fetchone(
            "select execution_metadata_json from team_run_cycles where id = ?",
            (cycle_id,),
        )
        if row is None:
            raise KeyError(f"Team run cycle not found: {cycle_id}")
        metadata = _execution_metadata_object(row["execution_metadata_json"])
        semantic_source = metadata.get("semantic_source", {})
        if not isinstance(semantic_source, dict):
            raise ValueError("Cycle semantic source metadata is invalid")
        contract_id = semantic_source.get("output_contract_id")
        if contract_id is None:
            return None
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise ValueError("Cycle output contract metadata is invalid")
        return contract_id

    def set_cycle_provider_capabilities(
        self,
        cycle_id: str,
        snapshots: dict[str, object],
    ) -> TeamRunCycle:
        if not isinstance(snapshots, dict):
            raise ValueError("Provider capabilities must be an object")
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Team run cycle not found: {cycle_id}")
            metadata = _execution_metadata_object(row["execution_metadata_json"])
            metadata["provider_capabilities"] = snapshots
            cursor = connection.execute(
                """
                update team_run_cycles
                set execution_metadata_json = ?, updated_at = ? where id = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    now,
                    cycle_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Team run cycle changed before metadata update")
            updated = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            return _team_run_cycle_from_row(updated)

    def set_cycle_agent_execution_metadata(
        self,
        cycle_id: str,
        agent_id: str,
        metadata: dict[str, object],
    ) -> TeamRunCycle:
        if not isinstance(metadata, dict):
            raise ValueError("Agent execution metadata must be an object")
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Team run cycle not found: {cycle_id}")
            agent = connection.execute(
                "select team_run_id from team_agents where id = ?",
                (agent_id,),
            ).fetchone()
            if agent is None or agent["team_run_id"] != row["team_run_id"]:
                raise ValueError("Agent does not belong to the cycle team run")
            execution_metadata = _execution_metadata_object(
                row["execution_metadata_json"]
            )
            agents = execution_metadata.get("agents", {})
            if not isinstance(agents, dict):
                raise ValueError("Cycle agent execution metadata is invalid")
            execution_metadata["agents"] = {**agents, agent_id: metadata}
            cursor = connection.execute(
                """
                update team_run_cycles
                set execution_metadata_json = ?, updated_at = ? where id = ?
                """,
                (
                    json.dumps(
                        execution_metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                    cycle_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Team run cycle changed before metadata update")
            updated = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            return _team_run_cycle_from_row(updated)

    def mark_waiting_for_provider(
        self,
        cycle_id: str,
        *,
        provider: str,
        reason_code: str,
        attempts: int,
        task_id: str | None,
        agent_id: str | None,
        now: datetime,
    ) -> TeamRunCycle:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            cycle = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            if cycle is None:
                raise KeyError(f"Team run cycle not found: {cycle_id}")
            run = connection.execute(
                "select * from team_runs where id = ?",
                (cycle["team_run_id"],),
            ).fetchone()
            if run is None:
                raise ValueError("Team run and cycle are not active for provider wait")
            active_execution_source = (
                cycle["status"] == "running"
                and run["status"] in _PROVIDER_WAIT_SOURCE_RUN_STATUSES
            )
            active_execution_wait = active_execution_source and agent_id is not None
            preplanning_freeze_wait = (
                cycle["status"] == "queued"
                and run["status"] == "draft"
                and cycle["request_id"] is not None
                and task_id is None
                and agent_id is None
                and _preplanning_source_is_pristine(run, cycle)
            )
            if not active_execution_wait and not preplanning_freeze_wait:
                if active_execution_source and agent_id is None:
                    raise ValueError(
                        "Provider wait omitted the current task and agent"
                    )
                raise ValueError("Team run and cycle are not active for provider wait")
            if cycle["request_id"] is not None:
                request = connection.execute(
                    "select * from team_cycle_requests where id = ?",
                    (cycle["request_id"],),
                ).fetchone()
                if (
                    request is None
                    or request["team_run_id"] != cycle["team_run_id"]
                    or request["source_type"] != cycle["source_type"]
                    or request["source_id"] != cycle["source_id"]
                    or request["status"] != "dispatching"
                ):
                    raise ValueError(
                        "Team cycle request is not dispatching for provider wait"
                    )
            if preplanning_freeze_wait:
                cycle_task = connection.execute(
                    """
                    select id from team_tasks
                    where team_run_id = ? and cycle_id = ?
                    limit 1
                    """,
                    (cycle["team_run_id"], cycle_id),
                ).fetchone()
                agent_rows = connection.execute(
                    "select * from team_agents where team_run_id = ?",
                    (cycle["team_run_id"],),
                ).fetchall()
                if (
                    cycle_task is not None
                    or not _preplanning_agents_are_pristine(
                        agent_rows,
                        run["leader_agent_id"],
                    )
                ):
                    raise ValueError(
                        "Team run and cycle are not pristine for provider wait"
                    )
            if task_id is not None and agent_id is None:
                raise ValueError("Provider wait task has no current task agent")
            if task_id is None and not preplanning_freeze_wait:
                current_task = connection.execute(
                    """
                    select id from team_tasks
                    where team_run_id = ? and cycle_id = ? and status = 'in_progress'
                    limit 1
                    """,
                    (cycle["team_run_id"], cycle_id),
                ).fetchone()
                if current_task is not None:
                    raise ValueError(
                        "Provider wait omitted the current task and agent"
                    )
            if agent_id is None and not preplanning_freeze_wait:
                current_agent = connection.execute(
                    """
                    select id from team_agents
                    where team_run_id = ? and status = 'running'
                    limit 1
                    """,
                    (cycle["team_run_id"],),
                ).fetchone()
                if current_agent is not None:
                    raise ValueError(
                        "Provider wait omitted the current task and agent"
                    )
            task = None
            if task_id is not None:
                task = connection.execute(
                    "select * from team_tasks where id = ?",
                    (task_id,),
                ).fetchone()
                if (
                    run["status"] != "running"
                    or task is None
                    or task["team_run_id"] != cycle["team_run_id"]
                    or task["cycle_id"] != cycle_id
                    or task["status"] != "in_progress"
                    or task["owner_agent_id"] != agent_id
                ):
                    raise ValueError(
                        "Team task is not the current task for provider wait"
                    )
            agent = None
            if agent_id is not None:
                agent = connection.execute(
                    "select * from team_agents where id = ?",
                    (agent_id,),
                ).fetchone()
                if (
                    agent is None
                    or agent["team_run_id"] != cycle["team_run_id"]
                    or agent["status"] != "running"
                    or agent["current_task_id"] != task_id
                    or (task_id is None and run["leader_agent_id"] != agent_id)
                ):
                    raise ValueError(
                        "Team agent does not own the current task for provider wait"
                    )
            stored_metadata = (
                json.loads(cycle["execution_metadata_json"])
                if cycle["execution_metadata_json"]
                else {}
            )
            metadata = stored_metadata if isinstance(stored_metadata, dict) else {}
            metadata["provider_recovery"] = {
                "provider": provider,
                "task_id": task_id,
                "agent_id": agent_id,
                "reason_code": reason_code,
                "attempts": attempts,
                "first_failed_at": timestamp,
                "next_retry_at": _timestamp(now + timedelta(seconds=30)),
                "warning_visible_at": _timestamp(now + timedelta(seconds=120)),
            }
            cursor = connection.execute(
                """
                update team_run_cycles
                set status = 'waiting_for_provider',
                    execution_metadata_json = ?, finished_at = null, updated_at = ?
                where id = ? and status = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    cycle_id,
                    cycle["status"],
                ),
            )
            _require_one_updated(cursor, "Provider wait cycle update was stale")
            cursor = connection.execute(
                """
                update team_runs
                set status = 'waiting_for_provider',
                    finished_at = null, updated_at = ?
                where id = ? and status = ?
                """,
                (timestamp, cycle["team_run_id"], run["status"]),
            )
            _require_one_updated(cursor, "Provider wait run update was stale")
            if task_id is not None:
                cursor = connection.execute(
                    """
                    update team_tasks
                    set status = 'waiting_for_provider',
                        finished_at = null, updated_at = ?
                    where id = ? and team_run_id = ? and cycle_id = ?
                      and owner_agent_id = ? and status = 'in_progress'
                    """,
                    (
                        timestamp,
                        task_id,
                        cycle["team_run_id"],
                        cycle_id,
                        agent_id,
                    ),
                )
                _require_one_updated(cursor, "Provider wait task update was stale")
            if agent_id is not None:
                cursor = connection.execute(
                    """
                    update team_agents
                    set status = 'waiting', finished_at = null, updated_at = ?
                    where id = ? and team_run_id = ? and status = 'running'
                      and current_task_id is ?
                    """,
                    (timestamp, agent_id, cycle["team_run_id"], task_id),
                )
                _require_one_updated(cursor, "Provider wait agent update was stale")
            row = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            return _team_run_cycle_from_row(row)

    def list_waiting_provider_cycles(self) -> list[TeamRunCycle]:
        return [
            _team_run_cycle_from_row(row)
            for row in self._db.fetchall(
                """
                select * from team_run_cycles
                where status = 'waiting_for_provider'
                order by created_at asc, id asc
                """
            )
        ]

    def claim_provider_recovery(
        self,
        cycle_id: str,
        now: datetime,
    ) -> ProviderRecoveryClaim | None:
        timestamp = _timestamp(now)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            cursor = connection.execute(
                """
                update team_run_cycles
                set status = 'running', updated_at = ?
                where id = ? and status = 'waiting_for_provider'
                """,
                (timestamp, cycle_id),
            )
            if cursor.rowcount != 1:
                return None

            cycle = connection.execute(
                "select * from team_run_cycles where id = ?",
                (cycle_id,),
            ).fetchone()
            try:
                stored_metadata = (
                    json.loads(cycle["execution_metadata_json"])
                    if cycle["execution_metadata_json"]
                    else {}
                )
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid provider recovery metadata") from exc
            metadata, task_id, agent_id = _validated_provider_recovery_metadata(
                stored_metadata
            )
            preplanning_recovery = task_id is None and agent_id is None
            run = connection.execute(
                "select * from team_runs where id = ?",
                (cycle["team_run_id"],),
            ).fetchone()
            if run is None or run["status"] != "waiting_for_provider":
                raise ValueError("Invalid provider recovery related state")
            if preplanning_recovery and cycle["request_id"] is None:
                raise ValueError("Invalid provider recovery related state")
            if cycle["request_id"] is not None:
                request = connection.execute(
                    "select * from team_cycle_requests where id = ?",
                    (cycle["request_id"],),
                ).fetchone()
                if (
                    request is None
                    or request["team_run_id"] != cycle["team_run_id"]
                    or request["source_type"] != cycle["source_type"]
                    or request["source_id"] != cycle["source_id"]
                    or request["status"] != "dispatching"
                ):
                    raise ValueError("Invalid provider recovery related state")
            if preplanning_recovery:
                cycle_task = connection.execute(
                    """
                    select id from team_tasks
                    where team_run_id = ? and cycle_id = ?
                    limit 1
                    """,
                    (cycle["team_run_id"], cycle_id),
                ).fetchone()
                agent_rows = connection.execute(
                    "select * from team_agents where team_run_id = ?",
                    (cycle["team_run_id"],),
                ).fetchall()
                if (
                    not _preplanning_source_is_pristine(run, cycle)
                    or cycle_task is not None
                    or not _preplanning_agents_are_pristine(
                        agent_rows,
                        run["leader_agent_id"],
                    )
                ):
                    raise ValueError("Invalid provider recovery related state")
            elif task_id is None:
                waiting_task = connection.execute(
                    """
                    select id from team_tasks
                    where team_run_id = ? and cycle_id = ?
                      and status = 'waiting_for_provider'
                    limit 1
                    """,
                    (cycle["team_run_id"], cycle_id),
                ).fetchone()
                if waiting_task is not None:
                    raise ValueError("Invalid provider recovery related state")
            if agent_id is None and not preplanning_recovery:
                waiting_agent = connection.execute(
                    """
                    select id from team_agents
                    where team_run_id = ? and status = 'waiting'
                    limit 1
                    """,
                    (cycle["team_run_id"],),
                ).fetchone()
                if waiting_agent is not None:
                    raise ValueError("Invalid provider recovery related state")
            task = None
            if task_id is not None:
                task = connection.execute(
                    "select * from team_tasks where id = ?",
                    (task_id,),
                ).fetchone()
                if (
                    agent_id is None
                    or task is None
                    or task["team_run_id"] != cycle["team_run_id"]
                    or task["cycle_id"] != cycle_id
                    or task["owner_agent_id"] != agent_id
                    or task["status"] != "waiting_for_provider"
                ):
                    raise ValueError("Invalid provider recovery related state")
            agent = None
            if agent_id is not None:
                agent = connection.execute(
                    "select * from team_agents where id = ?",
                    (agent_id,),
                ).fetchone()
                if (
                    agent is None
                    or agent["team_run_id"] != cycle["team_run_id"]
                    or agent["status"] != "waiting"
                    or agent["current_task_id"] != task_id
                    or (task_id is None and run["leader_agent_id"] != agent_id)
                ):
                    raise ValueError("Invalid provider recovery related state")
            if task_id is not None:
                cursor = connection.execute(
                    """
                    update team_tasks
                    set status = 'pending', result = null, error_message = null,
                        started_at = null, finished_at = null, updated_at = ?
                    where id = ? and team_run_id = ? and cycle_id = ?
                      and owner_agent_id = ? and status = 'waiting_for_provider'
                    """,
                    (
                        timestamp,
                        task_id,
                        cycle["team_run_id"],
                        cycle_id,
                        agent_id,
                    ),
                )
                _require_one_updated(cursor, "Provider recovery task update was stale")
            if agent_id is not None:
                cursor = connection.execute(
                    """
                    update team_agents
                    set status = 'pending', current_task_id = null,
                        finished_at = null, updated_at = ?
                    where id = ? and team_run_id = ? and status = 'waiting'
                      and current_task_id is ?
                    """,
                    (timestamp, agent_id, cycle["team_run_id"], task_id),
                )
                _require_one_updated(cursor, "Provider recovery agent update was stale")
            cursor = connection.execute(
                """
                update team_runs
                set status = 'running', error_message = null,
                    finished_at = null, updated_at = ?
                where id = ? and status = 'waiting_for_provider'
                """,
                (timestamp, cycle["team_run_id"]),
            )
            _require_one_updated(cursor, "Provider recovery run update was stale")
            metadata.pop("provider_recovery", None)
            cursor = connection.execute(
                """
                update team_run_cycles
                set execution_metadata_json = ?
                where id = ? and status = 'running'
                """,
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    cycle_id,
                ),
            )
            _require_one_updated(cursor, "Provider recovery metadata update was stale")
            return ProviderRecoveryClaim(
                cycle["team_run_id"],
                cycle_id,
                task_id,
            )

    def set_cycle_status(
        self,
        cycle_id: str,
        status: CycleStatus,
        summary: str | None = None,
        error_message: str | None = None,
    ) -> TeamRunCycle:
        self.get_cycle(cycle_id)
        started_at = _now() if status == "running" else None
        finished_at = (
            _now()
            if status
            in {"completed", "completed_with_failures", "blocked", "failed", "canceled"}
            else None
        )
        self._db.execute(
            """
            update team_run_cycles
            set status = ?, summary = ?, error_message = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at), updated_at = ?
            where id = ?
            """,
            (
                status,
                summary,
                error_message,
                started_at,
                finished_at,
                _now(),
                cycle_id,
            ),
        )
        return self.get_cycle(cycle_id)

    def delete_team_run(self, team_run_id: str) -> None:
        run = self.get_team_run(team_run_id)
        workspace_root = Path(run.workspace_root).resolve()
        expected_workspace_root = (self._workspace_root.resolve() / team_run_id).resolve()
        if workspace_root != expected_workspace_root:
            raise ValueError("Team workspace is outside the configured workspace root")
        self._space_manager.cleanup(
            workspace_root,
            policy_from_snapshot(run.space_policy),
            Path(run.working_root).resolve() if run.working_root else None,
            run.worktree_branch,
        )
        # team_agents / team_tasks / team_messages cascade via foreign keys
        self._db.execute("delete from team_runs where id = ?", (team_run_id,))

    def _space_policy_snapshot_for_cycle(self, run: TeamRun) -> str:
        if run.team_id:
            return policy_json(
                self._space_policies.resolve(team_id=run.team_id).policy
            )
        if run.space_policy:
            return json.dumps(run.space_policy, ensure_ascii=False, sort_keys=True)
        raise RuntimeError("Team run has no SPACE policy")

    def list_team_runs(self) -> list[TeamRun]:
        return [
            _team_run_from_row(row)
            for row in self._db.fetchall("select * from team_runs order by created_at desc")
        ]

    def list_team_runs_enriched(self) -> list[dict[str, object]]:
        return self._enrich_runs(self.list_team_runs())

    def page_team_runs_enriched(
        self, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[dict[str, object]], str | None]:
        clauses: list[str] = []
        parameters: list[object] = []
        if cursor:
            created_at, team_run_id = decode_cursor(cursor, 2)
            if not isinstance(created_at, str) or not isinstance(team_run_id, str):
                raise ValueError("Invalid cursor")
            clauses.append("(created_at < ? or (created_at = ? and id < ?))")
            parameters.extend((created_at, created_at, team_run_id))
        where = f"where {' and '.join(clauses)}" if clauses else ""
        normalized_limit = max(1, min(limit, 200))
        rows = self._db.fetchall(
            f"select * from team_runs {where} "
            "order by created_at desc, id desc limit ?",
            (*parameters, normalized_limit + 1),
        )
        has_more = len(rows) > normalized_limit
        selected = rows[:normalized_limit]
        runs = [_team_run_from_row(row) for row in selected]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = encode_cursor(last["created_at"], last["id"])
        return self._enrich_runs(runs), next_cursor

    def _enrich_runs(self, runs: list[TeamRun]) -> list[dict[str, object]]:
        if not runs:
            return []
        run_ids = [run.id for run in runs]
        placeholders = ", ".join("?" for _ in run_ids)
        agent_rows = self._db.fetchall(
            f"select * from team_agents where team_run_id in ({placeholders}) "
            "order by created_at asc, id asc",
            run_ids,
        )
        count_rows = self._db.fetchall(
            f"select team_run_id, cycle_id, status, count(*) as total from team_tasks "
            f"where team_run_id in ({placeholders}) group by team_run_id, cycle_id, status",
            run_ids,
        )
        cycle_rows = self._db.fetchall(
            f"""
            select cycle.*, request.instruction as request_instruction
            from team_run_cycles cycle
            left join team_cycle_requests request on request.id = cycle.request_id
            where cycle.team_run_id in ({placeholders})
            order by cycle.team_run_id, cycle.sequence desc
            """,
            run_ids,
        )
        pending_request_rows = self._db.fetchall(
            f"""
            select * from team_cycle_requests
            where team_run_id in ({placeholders})
              and status in ('queued', 'dispatching')
            order by team_run_id,
              case status when 'dispatching' then 0 else 1 end,
              created_at desc, rowid desc
            """,
            run_ids,
        )
        series_rows = self._db.fetchall(
            f"""
            select * from team_run_auto_series
            where team_run_id in ({placeholders})
            order by team_run_id, series_number desc
            """,
            run_ids,
        )
        agents_by_run: dict[str, list[TeamAgent]] = {run_id: [] for run_id in run_ids}
        for row in agent_rows:
            agent = _team_agent_from_row(row)
            agents_by_run[agent.team_run_id].append(agent)
        counts_by_run: dict[str, dict[str, int]] = {run_id: {} for run_id in run_ids}
        counts_by_cycle: dict[tuple[str, str], dict[str, int]] = {}
        for row in count_rows:
            run_counts = counts_by_run[row["team_run_id"]]
            run_counts[row["status"]] = run_counts.get(row["status"], 0) + int(row["total"])
            if row["cycle_id"] is not None:
                cycle_counts = counts_by_cycle.setdefault(
                    (row["team_run_id"], row["cycle_id"]), {}
                )
                cycle_counts[row["status"]] = int(row["total"])
        latest_cycle_by_run: dict[str, sqlite3.Row] = {}
        cycle_count_by_run = {run_id: 0 for run_id in run_ids}
        for row in cycle_rows:
            cycle_count_by_run[row["team_run_id"]] += 1
            latest_cycle_by_run.setdefault(row["team_run_id"], row)
        pending_request_by_run: dict[str, sqlite3.Row] = {}
        for row in pending_request_rows:
            pending_request_by_run.setdefault(row["team_run_id"], row)
        latest_series_by_run: dict[str, sqlite3.Row] = {}
        for row in series_rows:
            latest_series_by_run.setdefault(row["team_run_id"], row)

        result: list[dict[str, object]] = []
        for run in runs:
            agents = agents_by_run[run.id]
            leader = next((a for a in agents if a.role == "leader"), None)
            members = [a for a in agents if a.role != "leader"]
            latest_cycle = latest_cycle_by_run.get(run.id)
            pending_request = pending_request_by_run.get(run.id)
            latest_series = latest_series_by_run.get(run.id)
            lifetime_counts = counts_by_run[run.id]
            counts = (
                counts_by_cycle.get((run.id, latest_cycle["id"]), {})
                if latest_cycle is not None and run.lifecycle_mode == "continuous"
                else lifetime_counts
            )
            team_snapshot = (run.rules_snapshot or {}).get("team")
            team_name = (
                str(team_snapshot.get("name") or "").strip() or None
                if isinstance(team_snapshot, dict)
                else None
            )
            objective = _current_objective(run, pending_request, latest_cycle)
            display_status = _team_run_display_status(
                run, pending_request, latest_cycle, latest_series
            )
            activity_times = [run.updated_at]
            for row in (pending_request, latest_cycle, latest_series):
                if row is not None and row["updated_at"]:
                    activity_times.append(row["updated_at"])
            result.append(
                {
                    "id": run.id,
                    "goal": run.goal,
                    "status": run.status,
                    "run_mode": run.run_mode,
                    "lifecycle_mode": run.lifecycle_mode,
                    # Both of these used to be hardcoded to 1 / "sequential",
                    # and they were true then: tasks ran one at a time however
                    # many workers the run was configured with. Concurrency
                    # makes the constant a lie, and the field the UI shows as
                    # the run's parallelism would have kept reading 1 while
                    # three assignments ran at once. Effective, not configured:
                    # a roster of eight still overlaps at most
                    # MAX_CONCURRENT_WORKERS, and reporting eight would promise
                    # parallelism the executor will not deliver.
                    "max_workers": self.effective_workers(run.max_workers),
                    "configured_max_workers": run.max_workers,
                    "execution_mode": (
                        "concurrent"
                        if self.effective_workers(run.max_workers) > 1
                        else "sequential"
                    ),
                    "team_id": run.team_id,
                    "parent_team_run_id": run.parent_team_run_id,
                    "team_name": team_name,
                    "display_status": display_status,
                    "current_objective": objective,
                    "cycle_count": cycle_count_by_run[run.id],
                    "latest_cycle": (
                        {
                            "id": latest_cycle["id"],
                            "sequence": latest_cycle["sequence"],
                            "status": latest_cycle["status"],
                            "source_type": latest_cycle["source_type"],
                            "objective": latest_cycle["request_instruction"],
                            "updated_at": latest_cycle["updated_at"],
                        }
                        if latest_cycle is not None
                        else None
                    ),
                    "pending_request": (
                        {
                            "id": pending_request["id"],
                            "status": pending_request["status"],
                            "source_type": pending_request["source_type"],
                            "slot_ordinal": pending_request["slot_ordinal"],
                            "objective": pending_request["instruction"],
                            "updated_at": pending_request["updated_at"],
                        }
                        if pending_request is not None
                        else None
                    ),
                    "auto_series": (
                        {
                            "status": latest_series["status"],
                            "target_slots": latest_series["target_slots"],
                            "settled_slots": latest_series["settled_slots"],
                            "next_run_at": latest_series["next_run_at"],
                        }
                        if latest_series is not None
                        else None
                    ),
                    "last_activity_at": max(activity_times),
                    "created_at": run.created_at,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "updated_at": run.updated_at,
                    "leader_name": leader.name if leader else None,
                    "leader": (
                        {
                            "name": leader.name,
                            "avatar": leader.persona_snapshot.get("avatar", ""),
                            "initials": _initials(leader.name),
                        }
                        if leader else None
                    ),
                    "members": [
                        {
                            "name": agent.name,
                            "avatar": agent.persona_snapshot.get("avatar", ""),
                            "initials": _initials(agent.name),
                        }
                        for agent in members
                    ],
                    "task_counts": counts,
                    "task_total": sum(counts.values()),
                    "task_done": counts.get("completed", 0),
                    "elapsed_seconds": _elapsed_seconds(run.started_at, run.finished_at),
                    "lifetime_task_total": sum(lifetime_counts.values()),
                }
            )
        return result

    def interrupt_active_runs(self) -> list[TeamRun]:
        run_ids = [
            row["id"]
            for row in self._db.fetchall(
                """
                select run.id from team_runs run
                where run.status in ('planning', 'running', 'summarizing')
                  and not exists (
                      select 1 from team_model_operations operation
                      where operation.team_run_id = run.id
                        and operation.status in (
                            'prepared', 'invoking', 'completed',
                            'waiting_for_provider', 'ambiguous'
                        )
                  )
                """
            )
        ]
        return [self.interrupt_run(team_run_id) for team_run_id in run_ids]

    def interrupt_run(self, team_run_id: str, include_canceled: bool = False) -> TeamRun:
        now = _now()
        with self._db.connection() as connection:
            run = connection.execute(
                "select status from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            allowed = set(_ACTIVE_RUN_STATUSES)
            if include_canceled:
                allowed.add("canceled")
            if run["status"] not in allowed:
                return self.get_team_run(team_run_id)

            task_statuses = ("in_progress", "canceled") if include_canceled else ("in_progress",)
            placeholders = ", ".join("?" for _ in task_statuses)
            task_rows = connection.execute(
                f"select id from team_tasks where team_run_id = ? and status in ({placeholders})",
                (team_run_id, *task_statuses),
            ).fetchall()
            requeued_task_ids = [row["id"] for row in task_rows]
            connection.execute(
                f"""
                update team_tasks
                set status = 'pending', result = null, error_message = null,
                    started_at = null, finished_at = null, updated_at = ?
                where team_run_id = ? and status in ({placeholders})
                """,
                (now, team_run_id, *task_statuses),
            )

            agent_statuses = ("running", "canceled") if include_canceled else ("running",)
            agent_placeholders = ", ".join("?" for _ in agent_statuses)
            connection.execute(
                f"""
                update team_agents
                set status = 'pending', current_task_id = null,
                    finished_at = null, updated_at = ?
                where team_run_id = ? and status in ({agent_placeholders})
                """,
                (now, team_run_id, *agent_statuses),
            )
            connection.execute(
                """
                update team_runs
                set status = 'interrupted', error_message = null,
                    finished_at = null, updated_at = ?
                where id = ?
                """,
                (now, team_run_id),
            )
            cycle_statuses = ("running", "canceled") if include_canceled else ("running",)
            cycle_placeholders = ", ".join("?" for _ in cycle_statuses)
            active_cycle = connection.execute(
                f"""
                select id from team_run_cycles
                where team_run_id = ? and status in ({cycle_placeholders})
                order by sequence asc limit 1
                """,
                (team_run_id, *cycle_statuses),
            ).fetchone()
            cycle_id = active_cycle["id"] if active_cycle is not None else None
            if cycle_id is not None:
                connection.execute(
                    """
                    update team_run_cycles
                    set status = 'interrupted', error_message = null,
                        finished_at = null, updated_at = ? where id = ?
                    """,
                    (now, cycle_id),
                )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    cycle_id,
                    "system_interrupted",
                    "Gateway execution stopped. Resume is required.",
                    json.dumps(
                        {
                            "previous_status": run["status"],
                            "requeued_task_ids": requeued_task_ids,
                        },
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        return self.get_team_run(team_run_id)

    def list_agents(self, team_run_id: str) -> list[TeamAgent]:
        self.get_team_run(team_run_id)
        return [
            _team_agent_from_row(row)
            for row in self._db.fetchall(
                "select * from team_agents where team_run_id = ? order by created_at asc, id asc",
                (team_run_id,),
            )
        ]

    def set_agent_status(self, agent_id: str, status: AgentStatus) -> TeamAgent:
        self._get_agent(agent_id)
        started_at = _now() if status == "running" else None
        finished_at = _now() if status in ("completed", "failed", "canceled") else None
        self._db.execute(
            """
            update team_agents
            set status = ?,
                current_task_id = case
                    when ? in ('completed', 'failed', 'canceled') then null
                    else current_task_id
                end,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                updated_at = ?
            where id = ?
            """,
            (status, status, started_at, finished_at, _now(), agent_id),
        )
        return self._get_agent(agent_id)

    def get_agent(self, agent_id: str) -> TeamAgent:
        return self._get_agent(agent_id)

    def get_task(self, task_id: str) -> TeamTask:
        return self._get_task(task_id)

    def set_agent_session(self, agent_id: str, upstream_session_id: str | None) -> TeamAgent:
        self._get_agent(agent_id)
        self._db.execute(
            "update team_agents set upstream_session_id = ?, updated_at = ? where id = ?",
            (upstream_session_id, _now(), agent_id),
        )
        return self._get_agent(agent_id)

    def increment_agent_reinvocations(self, agent_id: str) -> TeamAgent:
        self._get_agent(agent_id)
        self._db.execute(
            "update team_agents set reinvocations = reinvocations + 1, updated_at = ? where id = ?",
            (_now(), agent_id),
        )
        return self._get_agent(agent_id)

    def reset_agent_reinvocations(self, team_run_id: str) -> None:
        self.get_team_run(team_run_id)
        self._db.execute(
            "update team_agents set reinvocations = 0, updated_at = ? "
            "where team_run_id = ?",
            (_now(), team_run_id),
        )

    def reset_agents_for_new_cycle(self, team_run_id: str) -> None:
        # "waiting" is terminal for an agent whose task ended blocked, so it has
        # to be cleared here too; "blocked" is an illegal status that pre-fix
        # rows may still carry. Only "pending"/"running" survive a new cycle.
        self.get_team_run(team_run_id)
        self._db.execute(
            """
            update team_agents
            set status = 'pending', current_task_id = null,
                finished_at = null, updated_at = ?
            where team_run_id = ?
              and status in (
                  'completed', 'failed', 'canceled', 'waiting', 'blocked'
              )
            """,
            (_now(), team_run_id),
        )

    def increment_rounds_used(self, team_run_id: str) -> TeamRun:
        self.get_team_run(team_run_id)
        self._db.execute(
            "update team_runs set rounds_used = rounds_used + 1, updated_at = ? where id = ?",
            (_now(), team_run_id),
        )
        return self.get_team_run(team_run_id)

    def create_task(
        self,
        team_run_id: str,
        title: str,
        description: str,
        owner_agent_id: str | None = None,
        cycle_id: str | None = None,
        *,
        required: bool = True,
        acceptance: TaskAcceptance | None = None,
    ) -> TeamTask:
        self.get_team_run(team_run_id)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
        task_id = uuid4().hex
        now = _now()
        effective_acceptance = acceptance or TaskAcceptance((), ())
        self._db.execute(
            """
            insert into team_tasks (
                id, team_run_id, cycle_id, title, description, owner_agent_id, status,
                required, acceptance_json, outcome_json, acceptance_result_json,
                result, error_message, created_at, updated_at, started_at, finished_at,
                plan_ordinal
            )
            values (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, null, null, ?, ?, ?, ?, ?, ?,
                (
                    select coalesce(max(existing.plan_ordinal), -1) + 1
                    from team_tasks existing
                    where existing.team_run_id = ? and existing.cycle_id is ?
                )
            )
            """,
            (
                task_id,
                team_run_id,
                cycle_id,
                title,
                description,
                owner_agent_id,
                "pending",
                int(required),
                _task_acceptance_json(effective_acceptance),
                None,
                None,
                now,
                now,
                None,
                None,
                team_run_id,
                cycle_id,
            ),
        )
        return self._get_task(task_id)

    def list_tasks(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> list[TeamTask]:
        self.get_team_run(team_run_id)
        where = "team_run_id = ?"
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            where += " and cycle_id = ?"
            parameters += (cycle_id,)
        return [
            _team_task_from_row(row)
            for row in self._db.fetchall(
                f"select * from team_tasks where {where} "
                "order by created_at asc, plan_ordinal asc, id asc",
                parameters,
            )
        ]

    def add_task_dependencies(
        self,
        task_id: str,
        prerequisite_ids: list[str],
    ) -> None:
        if len(set(prerequisite_ids)) != len(prerequisite_ids):
            raise ValueError("Task dependencies must be unique")
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            task = connection.execute(
                "select team_run_id, cycle_id from team_tasks where id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise KeyError(f"Team task not found: {task_id}")
            for prerequisite_id in prerequisite_ids:
                if prerequisite_id == task_id:
                    raise ValueError("Task cannot depend on itself")
                prerequisite = connection.execute(
                    "select team_run_id, cycle_id from team_tasks where id = ?",
                    (prerequisite_id,),
                ).fetchone()
                if prerequisite is None:
                    raise KeyError(f"Prerequisite task not found: {prerequisite_id}")
                if (
                    prerequisite["team_run_id"] != task["team_run_id"]
                    or prerequisite["cycle_id"] != task["cycle_id"]
                ):
                    raise ValueError("Task dependency must belong to the same cycle")
                connection.execute(
                    """
                    insert into team_task_dependencies (task_id, depends_on_task_id)
                    values (?, ?)
                    """,
                    (task_id, prerequisite_id),
                )

    def list_task_dependencies(self, task_id: str) -> list[TeamTaskDependency]:
        return [
            TeamTaskDependency(
                task_id=row["task_id"],
                depends_on_task_id=row["depends_on_task_id"],
            )
            for row in self._db.fetchall(
                """
                select task_id, depends_on_task_id
                from team_task_dependencies where task_id = ? order by rowid asc
                """,
                (task_id,),
            )
        ]

    def list_task_dependency_map(
        self,
        team_run_id: str,
        cycle_id: str | None = None,
    ) -> dict[str, list[str]]:
        self.get_team_run(team_run_id)
        cycle_clause = "" if cycle_id is None else "and task.cycle_id = ?"
        parameters: tuple[object, ...] = (
            (team_run_id,) if cycle_id is None else (team_run_id, cycle_id)
        )
        rows = self._db.fetchall(
            f"""
            select dependency.task_id, dependency.depends_on_task_id
            from team_task_dependencies dependency
            join team_tasks task on task.id = dependency.task_id
            where task.team_run_id = ? {cycle_clause}
            order by dependency.rowid asc
            """,
            parameters,
        )
        dependency_map: dict[str, list[str]] = {}
        for row in rows:
            dependency_map.setdefault(row["task_id"], []).append(
                row["depends_on_task_id"]
            )
        return dependency_map

    def list_dependency_ready_tasks(
        self,
        team_run_id: str,
        cycle_id: str | None,
    ) -> list[TeamTask]:
        self.get_team_run(team_run_id)
        rows = self._db.fetchall(
            """
            select task.* from team_tasks task
            where task.team_run_id = ? and task.cycle_id is ?
              and task.status = 'pending'
              and not exists (
                  select 1 from team_task_dependencies dependency
                  join team_tasks prerequisite
                    on prerequisite.id = dependency.depends_on_task_id
                  where dependency.task_id = task.id
                    and prerequisite.status != 'completed'
              )
            order by task.created_at asc, task.plan_ordinal asc, task.id asc
            """,
            (team_run_id, cycle_id),
        )
        return [_team_task_from_row(row) for row in rows]

    def skip_pending_dependency_failures(
        self,
        team_run_id: str,
        cycle_id: str | None,
    ) -> list[TeamTask]:
        skipped_ids: list[str] = []
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            while True:
                rows = connection.execute(
                    """
                    select task.id from team_tasks task
                    where task.team_run_id = ? and task.cycle_id is ?
                      and task.status = 'pending'
                      and exists (
                          select 1 from team_task_dependencies dependency
                          join team_tasks prerequisite
                            on prerequisite.id = dependency.depends_on_task_id
                          where dependency.task_id = task.id
                            and prerequisite.status in (
                                'failed', 'blocked', 'canceled', 'skipped'
                            )
                      )
                    order by task.created_at asc, task.plan_ordinal asc, task.id asc
                    """,
                    (team_run_id, cycle_id),
                ).fetchall()
                if not rows:
                    break
                now = _now()
                for row in rows:
                    connection.execute(
                        """
                        update team_tasks
                        set status = 'skipped', error_message = 'skipped_by_dependency',
                            finished_at = ?, updated_at = ? where id = ?
                        """,
                        (now, now, row["id"]),
                    )
                    skipped_ids.append(row["id"])
            return [
                self._task_from_connection(connection, task_id)
                for task_id in skipped_ids
            ]

    def retry_failed_task(
        self,
        team_run_id: str,
        task_id: str,
        rules_snapshot_json: str | None = None,
    ) -> tuple[TeamRun, TeamTask, TeamRunCycle | None]:
        now = _now()
        retry_cycle_id: str | None = None
        retry_task_id = uuid4().hex
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            run = connection.execute(
                "select * from team_runs where id = ?",
                (team_run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            if run["status"] not in {"completed_with_failures", "failed"}:
                raise ValueError("Only failed terminal team runs can retry tasks")

            task = connection.execute(
                "select * from team_tasks "
                "where id = ? and team_run_id = ?",
                (task_id, team_run_id),
            ).fetchone()
            if task is None:
                raise KeyError(f"Team task not found: {task_id}")
            if task["status"] != "failed":
                raise ValueError("Only failed tasks can be retried")
            existing_retry = connection.execute(
                "select id from team_tasks where retry_of_task_id = ?", (task_id,)
            ).fetchone()
            if existing_retry is not None:
                raise ValueError("Failed task already has a retry task")

            if run["lifecycle_mode"] == "continuous":
                sequence_row = connection.execute(
                    "select coalesce(max(sequence), 0) + 1 as next "
                    "from team_run_cycles where team_run_id = ?",
                    (team_run_id,),
                ).fetchone()
                retry_cycle_id = uuid4().hex
                space_policy_snapshot_json = self._space_policy_snapshot_for_cycle(
                    _team_run_from_row(run)
                )
                connection.execute(
                    """
                    insert into team_run_cycles (
                        id, team_run_id, request_id, sequence, source_type, source_id,
                        status, rounds_budget, rounds_used, rules_snapshot_json,
                        space_policy_snapshot_json, summary, error_message, created_at,
                        started_at, finished_at, updated_at
                    ) values (?, ?, null, ?, 'task_retry', ?, 'interrupted', ?, 0, ?, ?,
                              null, null, ?, null, null, ?)
                    """,
                    (
                        retry_cycle_id,
                        team_run_id,
                        int(sequence_row["next"]),
                        task_id,
                        int(run["rounds_budget"]),
                        rules_snapshot_json,
                        space_policy_snapshot_json,
                        now,
                        now,
                    ),
                )

            connection.execute(
                """
                insert into team_tasks (
                    id, team_run_id, cycle_id, retry_of_task_id, title, description,
                    owner_agent_id, status, required, acceptance_json, outcome_json,
                    acceptance_result_json, result, error_message, created_at,
                    updated_at, started_at, finished_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, null, null, null,
                          null, ?, ?, null, null)
                """,
                (
                    retry_task_id,
                    team_run_id,
                    retry_cycle_id,
                    task_id,
                    task["title"],
                    _retry_description(connection, task),
                    task["owner_agent_id"],
                    task["required"],
                    task["acceptance_json"],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                update team_runs
                set status = 'interrupted', summary = null, error_message = null,
                    finished_at = null, updated_at = ?
                where id = ?
                """,
                (now, team_run_id),
            )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    retry_cycle_id,
                    "system_task_retried",
                    "Retry task created in a new cycle. Resume is required.",
                    json.dumps(
                        {
                            "original_cycle_id": task["cycle_id"],
                            "original_task_id": task_id,
                            "retry_cycle_id": retry_cycle_id,
                            "retry_task_id": retry_task_id,
                            "previous_error": task["error_message"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        retry_cycle = self.get_cycle(retry_cycle_id) if retry_cycle_id else None
        return self.get_team_run(team_run_id), self._get_task(retry_task_id), retry_cycle

    def set_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: str | None = None,
        error_message: str | None = None,
    ) -> TeamTask:
        self._get_task(task_id)
        started_at = _now() if status == "in_progress" else None
        finished_at = (
            _now()
            if status in ("blocked", "completed", "failed", "canceled")
            else None
        )
        self._db.execute(
            """
            update team_tasks
            set status = ?,
                result = ?,
                error_message = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                updated_at = ?
            where id = ?
            """,
            (status, result, error_message, started_at, finished_at, _now(), task_id),
        )
        return self._get_task(task_id)

    def record_task_outcome(
        self,
        task_id: str,
        outcome: dict[str, object],
        acceptance_result: dict[str, object],
    ) -> TeamTask:
        self._get_task(task_id)
        self._db.execute(
            """
            update team_tasks
            set outcome_json = ?, acceptance_result_json = ?, updated_at = ?
            where id = ?
            """,
            (
                json.dumps(outcome, ensure_ascii=False, sort_keys=True),
                json.dumps(acceptance_result, ensure_ascii=False, sort_keys=True),
                _now(),
                task_id,
            ),
        )
        return self._get_task(task_id)

    def record_acceptance_review(
        self,
        task_id: str,
        leader_agent_id: str,
        worker_agent_id: str,
        *,
        action: Literal["retry_worker", "revise_acceptance", "ask_user", "fail"],
        reason_code: str,
        reason: str,
        instruction: str | None,
        acceptance_after: TaskAcceptance | None,
        rejected_deliverables: tuple[str, ...],
        rejected_verifications: tuple[str, ...],
    ) -> TeamTask:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            task = connection.execute(
                "select * from team_tasks where id = ?", (task_id,)
            ).fetchone()
            leader = connection.execute(
                "select team_run_id from team_agents where id = ?", (leader_agent_id,)
            ).fetchone()
            worker = connection.execute(
                "select team_run_id from team_agents where id = ?", (worker_agent_id,)
            ).fetchone()
            if task is None or leader is None or worker is None:
                raise KeyError("Team task or agent not found")
            if (
                task["team_run_id"] != leader["team_run_id"]
                or task["team_run_id"] != worker["team_run_id"]
            ):
                raise ValueError("Task and agents belong to different team runs")
            if task["status"] != "in_progress":
                raise ValueError("Task is not in progress")
            if action == "revise_acceptance" and acceptance_after is None:
                raise ValueError("acceptance_after is required for revise_acceptance")
            if action != "revise_acceptance" and acceptance_after is not None:
                raise ValueError("acceptance_after is only allowed for revise_acceptance")
            if acceptance_after is not None:
                _validate_task_acceptance(acceptance_after)

            current_attempts = int(task["acceptance_recovery_attempts"])
            consumes_attempt = action in {"retry_worker", "revise_acceptance"}
            if consumes_attempt and current_attempts >= ACCEPTANCE_RECOVERY_CAP:
                raise ValueError("Acceptance recovery limit reached")

            next_attempts = current_attempts + int(consumes_attempt)
            acceptance_json = (
                _task_acceptance_json(acceptance_after)
                if acceptance_after is not None
                else task["acceptance_json"]
            )
            if consumes_attempt:
                connection.execute(
                    """
                    update team_tasks
                    set acceptance_recovery_attempts = ?, acceptance_json = ?, updated_at = ?
                    where id = ?
                    """,
                    (next_attempts, acceptance_json, now, task_id),
                )
            metadata = _acceptance_review_metadata(
                task_id=task_id,
                attempt=current_attempts + 1,
                reason_code=reason_code,
                action=action,
                reason=reason,
                instruction=instruction,
                acceptance_before=json.loads(task["acceptance_json"]),
                acceptance_after=(
                    json.loads(_task_acceptance_json(acceptance_after))
                    if acceptance_after is not None
                    else None
                ),
                rejected_deliverables=rejected_deliverables,
                rejected_verifications=rejected_verifications,
            )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, 'acceptance_review', ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    task["team_run_id"],
                    task["cycle_id"],
                    leader_agent_id,
                    worker_agent_id,
                    instruction or reason,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        return self._get_task(task_id)

    def start_task(self, task_id: str, agent_id: str) -> tuple[TeamTask, TeamAgent]:
        now = _now()
        with self._db.connection() as connection:
            task = connection.execute(
                "select team_run_id from team_tasks where id = ?", (task_id,)
            ).fetchone()
            agent = connection.execute(
                "select team_run_id from team_agents where id = ?", (agent_id,)
            ).fetchone()
            if task is None or agent is None:
                raise KeyError("Team task or agent not found")
            if task["team_run_id"] != agent["team_run_id"]:
                raise ValueError("Task and agent belong to different team runs")
            connection.execute(
                """
                update team_tasks
                set owner_agent_id = ?, status = 'in_progress', result = null,
                    error_message = null, started_at = ?, finished_at = null, updated_at = ?
                where id = ?
                """,
                (agent_id, now, now, task_id),
            )
            connection.execute(
                """
                update team_agents
                set status = 'running', current_task_id = ?,
                    started_at = coalesce(started_at, ?), finished_at = null, updated_at = ?
                where id = ?
                """,
                (task_id, now, now, agent_id),
            )
        return self._get_task(task_id), self._get_agent(agent_id)

    def finish_task(
        self,
        task_id: str,
        agent_id: str,
        status: Literal["completed", "blocked", "failed", "canceled"],
        result: str | None = None,
        error_message: str | None = None,
    ) -> tuple[TeamTask, TeamAgent]:
        now = _now()
        with self._db.connection() as connection:
            task = connection.execute(
                "select team_run_id, owner_agent_id from team_tasks where id = ?", (task_id,)
            ).fetchone()
            agent = connection.execute(
                "select team_run_id from team_agents where id = ?", (agent_id,)
            ).fetchone()
            if task is None or agent is None:
                raise KeyError("Team task or agent not found")
            if task["team_run_id"] != agent["team_run_id"]:
                raise ValueError("Task and agent belong to different team runs")
            connection.execute(
                """
                update team_tasks
                set status = ?, result = ?, error_message = ?, finished_at = ?, updated_at = ?
                where id = ?
                """,
                (status, result, error_message, now, now, task_id),
            )
            # "blocked" is a task status, not an agent status. Park the agent as
            # "waiting", matching how a blocked task's agent is recorded elsewhere.
            agent_status: AgentStatus = "waiting" if status == "blocked" else status
            connection.execute(
                """
                update team_agents
                set status = ?, current_task_id = null, finished_at = ?, updated_at = ?
                where id = ?
                """,
                (agent_status, now, now, agent_id),
            )
        return self._get_task(task_id), self._get_agent(agent_id)

    def get_active_decision_request(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> TeamDecisionRequest | None:
        self.get_team_run(team_run_id)
        cycle_clause = ""
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            cycle_clause = "and cycle_id = ?"
            parameters += (cycle_id,)
        row = self._db.fetchone(
            f"""
            select * from team_decision_requests
            where team_run_id = ? {cycle_clause}
              and status in ('collecting', 'awaiting_user')
            order by created_at desc limit 1
            """,
            parameters,
        )
        return _team_decision_request_from_row(row) if row is not None else None

    def list_decision_requests(self, team_run_id: str) -> list[TeamDecisionRequest]:
        self.get_team_run(team_run_id)
        return [
            _team_decision_request_from_row(row)
            for row in self._db.fetchall(
                """
                select * from team_decision_requests
                where team_run_id = ? order by created_at asc, id asc
                """,
                (team_run_id,),
            )
        ]

    def reconcile_lifecycle(
        self,
        protected_cycle_ids: set[str],
    ) -> list[str]:
        now = _now()
        repaired_cycle_ids: list[str] = []
        projected_run_ids: set[str] = set()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            requests = connection.execute(
                """
                select * from team_decision_requests
                where status in ('collecting', 'awaiting_user')
                order by created_at asc, id asc
                """
            ).fetchall()
            for request in requests:
                cycle_id = request["cycle_id"]
                if cycle_id in protected_cycle_ids:
                    continue
                items = json.loads(request["items_json"])
                if not items:
                    continue
                team_run_id = request["team_run_id"]
                blocking_task_ids = _decision_blocking_task_ids(items)
                linked_agent_ids = self._validate_decision_blockers(
                    connection,
                    team_run_id,
                    cycle_id,
                    blocking_task_ids,
                    allow_legacy_blocked=True,
                )
                run = connection.execute(
                    "select status, leader_agent_id from team_runs where id = ?",
                    (team_run_id,),
                ).fetchone()
                if run is None:
                    raise KeyError(f"Team run not found: {team_run_id}")
                if _decision_has_run_scope(items) and run["leader_agent_id"]:
                    linked_agent_ids.add(run["leader_agent_id"])

                changed = False
                if request["status"] == "collecting":
                    cursor = connection.execute(
                        """
                        update team_decision_requests
                        set status = 'awaiting_user', revision = revision + 1,
                            published_at = coalesce(published_at, ?), updated_at = ?
                        where id = ? and status = 'collecting'
                        """,
                        (now, now, request["id"]),
                    )
                    _require_one_updated(
                        cursor,
                        "Decision request changed during lifecycle reconciliation",
                    )
                    changed = True

                message = connection.execute(
                    """
                    select id from team_messages
                    where team_run_id = ? and kind = 'user_decision_requested'
                      and json_extract(metadata_json, '$.request_id') = ?
                    limit 1
                    """,
                    (team_run_id, request["id"]),
                ).fetchone()
                if message is None:
                    connection.execute(
                        """
                        insert into team_messages (
                            id, team_run_id, cycle_id, sender_agent_id,
                            recipient_agent_id, kind, content, metadata_json,
                            created_at
                        ) values (?, ?, ?, null, null,
                            'user_decision_requested', ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            team_run_id,
                            cycle_id,
                            f"User input requested for {len(items)} decision(s).",
                            json.dumps(
                                {
                                    "request_id": request["id"],
                                    "question_count": len(items),
                                },
                                sort_keys=True,
                            ),
                            now,
                        ),
                    )
                    changed = True

                if blocking_task_ids:
                    placeholders = ", ".join("?" for _ in blocking_task_ids)
                    cursor = connection.execute(
                        f"""
                        update team_tasks
                        set status = 'waiting_for_user', error_message = null,
                            finished_at = null, updated_at = ?
                        where team_run_id = ? and status = 'blocked'
                          and id in ({placeholders})
                        """,
                        (now, team_run_id, *sorted(blocking_task_ids)),
                    )
                    changed = changed or cursor.rowcount > 0

                if run["status"] not in TERMINAL_RUN_STATUSES:
                    cursor = connection.execute(
                        """
                        update team_runs
                        set status = 'waiting_for_user', error_message = null,
                            finished_at = null, updated_at = ?
                        where id = ? and status != 'waiting_for_user'
                        """,
                        (now, team_run_id),
                    )
                    changed = changed or cursor.rowcount > 0

                cycle = None
                if cycle_id is not None:
                    cycle = connection.execute(
                        "select status, request_id from team_run_cycles where id = ?",
                        (cycle_id,),
                    ).fetchone()
                    if cycle is None:
                        raise ValueError("Decision request cycle does not exist")
                    if cycle["status"] not in TERMINAL_CYCLE_STATUSES:
                        cursor = connection.execute(
                            """
                            update team_run_cycles
                            set status = 'waiting_for_user', error_message = null,
                                finished_at = null, updated_at = ?
                            where id = ? and status != 'waiting_for_user'
                            """,
                            (now, cycle_id),
                        )
                        changed = changed or cursor.rowcount > 0

                if linked_agent_ids:
                    placeholders = ", ".join("?" for _ in linked_agent_ids)
                    cursor = connection.execute(
                        f"""
                        update team_agents
                        set status = 'waiting', current_task_id = null,
                            finished_at = null, updated_at = ?
                        where team_run_id = ? and status in ('pending', 'running')
                          and id in ({placeholders})
                        """,
                        (now, team_run_id, *sorted(linked_agent_ids)),
                    )
                    changed = changed or cursor.rowcount > 0

                if cycle is not None and cycle["request_id"] is not None:
                    cycle_request = connection.execute(
                        """
                        select auto_series_id from team_cycle_requests where id = ?
                        """,
                        (cycle["request_id"],),
                    ).fetchone()
                    if cycle_request is None:
                        raise ValueError("Decision cycle request does not exist")
                    series_id = cycle_request["auto_series_id"]
                    if series_id is not None:
                        series = connection.execute(
                            """
                            select status, next_run_at, pause_reason,
                                paused_cycle_id, completed_at
                            from team_run_auto_series where id = ?
                            """,
                            (series_id,),
                        ).fetchone()
                        if series is None:
                            raise ValueError("Decision cycle auto-series does not exist")
                        expected_series_state = (
                            "paused_user",
                            None,
                            "waiting_for_user",
                            cycle_id,
                            None,
                        )
                        current_series_state = tuple(series)
                        if (
                            series["status"] not in {"canceled", "auto_completed"}
                            and current_series_state != expected_series_state
                        ):
                            connection.execute(
                                """
                                update team_run_auto_series
                                set status = 'paused_user', next_run_at = null,
                                    pause_reason = 'waiting_for_user',
                                    paused_cycle_id = ?, completed_at = null,
                                    updated_at = ?
                                where id = ?
                                """,
                                (cycle_id, now, series_id),
                            )
                            changed = True

                projected_run_ids.add(team_run_id)
                if changed and cycle_id is not None:
                    repaired_cycle_ids.append(cycle_id)

        for team_run_id in projected_run_ids:
            self._project_decisions_safely(team_run_id)
        return repaired_cycle_ids

    def defer_task_for_user_decision(
        self,
        task_id: str,
        agent_id: str,
        decision: dict[str, object],
    ) -> TeamDecisionRequest:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            task = connection.execute(
                """
                select team_run_id, cycle_id, status, owner_agent_id
                from team_tasks where id = ?
                """,
                (task_id,),
            ).fetchone()
            agent = connection.execute(
                "select team_run_id from team_agents where id = ?", (agent_id,)
            ).fetchone()
            if task is None or agent is None:
                raise KeyError("Team task or agent not found")
            if task["team_run_id"] != agent["team_run_id"]:
                raise ValueError("Task and agent belong to different team runs")
            if task["owner_agent_id"] != agent_id:
                raise ValueError("Agent does not own the task")
            if task["status"] != "in_progress":
                raise ValueError("Only in-progress tasks can wait for a user decision")

            team_run_id = task["team_run_id"]
            cycle_id = task["cycle_id"]
            request_id = self._append_decision_item(
                connection,
                team_run_id,
                cycle_id,
                decision,
                now,
                blocking_task_id=task_id,
                stage="task",
            )
            cursor = connection.execute(
                """
                update team_tasks
                set status = 'waiting_for_user', result = null, error_message = null,
                    finished_at = null, updated_at = ? where id = ?
                    and status = 'in_progress'
                """,
                (now, task_id),
            )
            _require_one_updated(cursor, "Team task changed before user decision defer")
            connection.execute(
                """
                update team_agents
                set status = 'waiting', current_task_id = null, finished_at = null, updated_at = ?
                where id = ?
                """,
                (now, agent_id),
            )
        request = self._get_decision_request(request_id)
        self._project_decisions_safely(team_run_id)
        return request

    def defer_run_for_user_decision(
        self,
        team_run_id: str,
        decision: dict[str, object],
        *,
        stage: Literal["planning", "synthesis"],
        cycle_id: str | None = None,
    ) -> TeamDecisionRequest:
        self.get_team_run(team_run_id)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            request_id = self._append_decision_item(
                connection,
                team_run_id,
                cycle_id,
                decision,
                now,
                blocking_task_id=None,
                stage=stage,
            )
        request = self._get_decision_request(request_id)
        self._project_decisions_safely(team_run_id)
        return request

    def _append_decision_item(
        self,
        connection,
        team_run_id: str,
        cycle_id: str | None,
        decision: dict[str, object],
        now: str,
        *,
        blocking_task_id: str | None,
        stage: Literal["task", "planning", "synthesis"],
    ) -> str:
        cycle_clause = "cycle_id is null" if cycle_id is None else "cycle_id = ?"
        parameters: tuple[object, ...] = (
            (team_run_id,) if cycle_id is None else (team_run_id, cycle_id)
        )
        row = connection.execute(
            f"""
            select * from team_decision_requests
            where team_run_id = ? and {cycle_clause}
              and status in ('collecting', 'awaiting_user')
            order by created_at desc limit 1
            """,
            parameters,
        ).fetchone()
        if row is not None and row["status"] != "collecting":
            raise ValueError("Decision request is already awaiting user input")

        if row is None:
            request_id = uuid4().hex
            items: list[dict[str, object]] = []
            revision = 0
            connection.execute(
                """
                insert into team_decision_requests (
                    id, team_run_id, cycle_id, status, revision, items_json, answers_json,
                    file_path, created_at, published_at, answered_at, updated_at
                ) values (?, ?, ?, 'collecting', 0, '[]', '{}', 'USER_DECISIONS.md', ?, null, null, ?)
                """,
                (request_id, team_run_id, cycle_id, now, now),
            )
        else:
            request_id = row["id"]
            items = json.loads(row["items_json"])
            revision = row["revision"]

        topic = str(decision.get("topic") or "").strip()
        question = str(decision.get("question") or "").strip()
        if not question:
            raise ValueError("User decision requires a question")
        duplicate = next(
            (
                item
                for item in items
                if item.get("topic") == topic and item.get("question") == question
            ),
            None,
        )
        query_message_id = decision.get("query_message_id")
        if duplicate is not None:
            blocking_ids = list(duplicate.get("blocking_task_ids") or [])
            if blocking_task_id is not None and blocking_task_id not in blocking_ids:
                blocking_ids.append(blocking_task_id)
            duplicate["blocking_task_ids"] = blocking_ids
            query_ids = list(duplicate.get("query_message_ids") or [])
            if isinstance(query_message_id, str) and query_message_id not in query_ids:
                query_ids.append(query_message_id)
            duplicate["query_message_ids"] = query_ids
        else:
            items.append(
                {
                    "id": f"Q-{len(items) + 1:03d}",
                    "stage": stage,
                    "topic": topic,
                    "question": question,
                    "why_needed": str(decision.get("why_needed") or "").strip(),
                    "options": list(decision.get("options") or []),
                    "recommended_option_id": decision.get("recommended_option_id"),
                    "blocking_scope": (
                        "run"
                        if blocking_task_id is None or decision.get("blocking_scope") == "run"
                        else "task"
                    ),
                    "blocking_task_ids": (
                        [blocking_task_id] if blocking_task_id is not None else []
                    ),
                    "query_message_ids": (
                        [query_message_id] if isinstance(query_message_id, str) else []
                    ),
                }
            )

        connection.execute(
            """
            update team_decision_requests
            set items_json = ?, revision = ?, updated_at = ? where id = ?
            """,
            (
                json.dumps(items, ensure_ascii=False, sort_keys=True),
                revision + 1,
                now,
                request_id,
            ),
        )
        return request_id

    def _validate_decision_blockers(
        self,
        connection: sqlite3.Connection,
        team_run_id: str,
        cycle_id: str | None,
        blocking_task_ids: set[str],
        *,
        allow_legacy_blocked: bool,
    ) -> set[str]:
        if not blocking_task_ids:
            return set()
        placeholders = ", ".join("?" for _ in blocking_task_ids)
        rows = connection.execute(
            f"""
            select id, cycle_id, status, owner_agent_id
            from team_tasks
            where team_run_id = ? and id in ({placeholders})
            """,
            (team_run_id, *sorted(blocking_task_ids)),
        ).fetchall()
        if {row["id"] for row in rows} != blocking_task_ids:
            raise ValueError("Decision request references an unknown blocking task")
        allowed_statuses = {"waiting_for_user"}
        if allow_legacy_blocked:
            allowed_statuses.add("blocked")
        for row in rows:
            if row["cycle_id"] != cycle_id:
                raise ValueError("Decision blocking task belongs to another cycle")
            if row["status"] not in allowed_statuses:
                raise ValueError("Decision blocking task is not waiting for user input")
        return {
            row["owner_agent_id"]
            for row in rows
            if isinstance(row["owner_agent_id"], str)
        }

    def _set_linked_agent_status(
        self,
        connection: sqlite3.Connection,
        team_run_id: str,
        agent_ids: set[str],
        *,
        source: str,
        target: str,
        now: str,
        finished_at: str | None = None,
    ) -> None:
        if not agent_ids:
            return
        placeholders = ", ".join("?" for _ in agent_ids)
        connection.execute(
            f"""
            update team_agents
            set status = ?, current_task_id = null, finished_at = ?, updated_at = ?
            where team_run_id = ? and status = ? and id in ({placeholders})
            """,
            (
                target,
                finished_at,
                now,
                team_run_id,
                source,
                *sorted(agent_ids),
            ),
        )

    def _set_decision_series_state(
        self,
        connection: sqlite3.Connection,
        cycle_request_id: str | None,
        *,
        status: str,
        pause_reason: str | None,
        paused_cycle_id: str | None,
        now: str,
        expected_status: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        if cycle_request_id is None:
            return
        request = connection.execute(
            "select auto_series_id from team_cycle_requests where id = ?",
            (cycle_request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Decision cycle request does not exist")
        series_id = request["auto_series_id"]
        if series_id is None:
            return
        series = connection.execute(
            "select status from team_run_auto_series where id = ?",
            (series_id,),
        ).fetchone()
        if series is None:
            raise ValueError("Decision cycle auto-series does not exist")
        if expected_status is not None and series["status"] != expected_status:
            raise ValueError("Decision cycle auto-series has stale state")
        if expected_status is None and series["status"] not in {
            "running",
            "waiting_interval",
            "paused_user",
        }:
            raise ValueError("Decision cycle auto-series is not active")
        connection.execute(
            """
            update team_run_auto_series
            set status = ?, next_run_at = null, pause_reason = ?,
                paused_cycle_id = ?, completed_at = ?, updated_at = ?
            where id = ?
            """,
            (
                status,
                pause_reason,
                paused_cycle_id,
                completed_at,
                now,
                series_id,
            ),
        )

    def consume_acceptance_attempt(self, task_id: str) -> TeamTask:
        """Count an acceptance round that produced no verdict.

        An unparseable review still used a round: the operation key for that
        attempt is taken and cannot be re-invoked, and team_model_effects
        requires the operation ordinal to equal attempts + 1. Without this the
        retry after the operator answers would re-enter the same attempt and hit
        the failed operation.

        This increments without a ceiling on purpose -- the ceiling is enforced by
        the caller. _escalate_unparsable_lead_output refuses to pause once
        attempts reach ACCEPTANCE_RECOVERY_CAP, and that check is what bounds a
        model returning unparseable reviews. An earlier version of this docstring
        claimed the cap did the bounding by itself; it did not, and the run asked
        the operator the same question forever.
        """
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            connection.execute(
                "update team_tasks "
                "set acceptance_recovery_attempts = acceptance_recovery_attempts + 1, "
                "updated_at = ? where id = ?",
                (now, task_id),
            )
        return self.get_task(task_id)

    def raise_system_decision(
        self,
        team_run_id: str,
        cycle_id: str | None,
        *,
        topic: str,
        question: str,
    ) -> TeamDecisionRequest:
        """Pause the run on a question the system asked, not an agent.

        The item carries no blocking task on purpose. answer_decision_request
        resets every blocking task to pending and clears its result, which would
        throw away the outcome the pause exists to preserve. The pause comes from
        publishing, not from the blocking relationship.
        """
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            self._append_decision_item(
                connection,
                team_run_id,
                cycle_id,
                {"topic": topic, "question": question, "options": []},
                now,
                blocking_task_id=None,
                stage="task",
            )
        return self.publish_decision_request(team_run_id, cycle_id)

    def publish_decision_request(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> TeamDecisionRequest:
        now = _now()
        cycle_clause = ""
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            cycle_clause = "and cycle_id = ?"
            parameters += (cycle_id,)
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                f"""
                select * from team_decision_requests
                where team_run_id = ? {cycle_clause} and status = 'collecting'
                order by created_at desc limit 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                raise ValueError("No collecting decision request")
            items = json.loads(row["items_json"])
            if not items:
                raise ValueError("Cannot publish an empty decision request")
            blocking_task_ids = _decision_blocking_task_ids(items)
            linked_agent_ids = self._validate_decision_blockers(
                connection,
                team_run_id,
                row["cycle_id"],
                blocking_task_ids,
                allow_legacy_blocked=True,
            )
            run = connection.execute(
                "select status, leader_agent_id from team_runs where id = ?",
                (team_run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            if run["status"] not in _PROVIDER_WAIT_SOURCE_RUN_STATUSES:
                raise ValueError("Team run is not active for a user decision")
            if _decision_has_run_scope(items) and run["leader_agent_id"]:
                linked_agent_ids.add(run["leader_agent_id"])
            cycle = None
            if row["cycle_id"] is not None:
                cycle = connection.execute(
                    "select status, request_id from team_run_cycles where id = ?",
                    (row["cycle_id"],),
                ).fetchone()
                if cycle is None or cycle["status"] != "running":
                    raise ValueError("Team run cycle is not active for a user decision")

            cursor = connection.execute(
                """
                update team_decision_requests
                set status = 'awaiting_user', revision = revision + 1,
                    published_at = ?, updated_at = ? where id = ? and status = 'collecting'
                """,
                (now, now, row["id"]),
            )
            _require_one_updated(cursor, "Decision request changed before publish")
            cursor = connection.execute(
                """
                update team_runs
                set status = 'waiting_for_user', error_message = null,
                    finished_at = null, updated_at = ? where id = ?
                    and status in ('planning', 'running', 'summarizing')
                """,
                (now, team_run_id),
            )
            _require_one_updated(cursor, "Team run changed before decision publish")
            if cycle is not None:
                cursor = connection.execute(
                    """
                    update team_run_cycles
                    set status = 'waiting_for_user', error_message = null,
                        finished_at = null, updated_at = ?
                    where id = ? and status = 'running'
                    """,
                    (now, row["cycle_id"]),
                )
                _require_one_updated(cursor, "Team cycle changed before decision publish")
                self._set_decision_series_state(
                    connection,
                    cycle["request_id"],
                    status="paused_user",
                    pause_reason="waiting_for_user",
                    paused_cycle_id=row["cycle_id"],
                    now=now,
                )
            self._set_linked_agent_status(
                connection,
                team_run_id,
                linked_agent_ids,
                source="running",
                target="waiting",
                now=now,
            )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, 'user_decision_requested', ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    row["cycle_id"],
                    f"User input requested for {len(items)} decision(s).",
                    json.dumps(
                        {"request_id": row["id"], "question_count": len(items)},
                        sort_keys=True,
                    ),
                    now,
                ),
            )
        request = self._get_decision_request(row["id"])
        self._project_decisions_safely(team_run_id)
        return request

    def answer_decision_request(
        self,
        team_run_id: str,
        request_id: str,
        revision: int,
        answers: dict[str, str],
    ) -> tuple[TeamRun, TeamDecisionRequest]:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            run = connection.execute(
                "select status, leader_agent_id from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            row = connection.execute(
                "select * from team_decision_requests where id = ? and team_run_id = ?",
                (request_id, team_run_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Decision request not found: {request_id}")
            if run["status"] != "waiting_for_user" or row["status"] != "awaiting_user":
                raise ValueError("Decision request is no longer awaiting user input")
            cycle = None
            if row["cycle_id"] is not None:
                cycle = connection.execute(
                    "select status, request_id from team_run_cycles where id = ?",
                    (row["cycle_id"],),
                ).fetchone()
                if cycle is None or cycle["status"] != "waiting_for_user":
                    raise ValueError("Decision cycle is no longer awaiting user input")
            if row["revision"] != revision:
                raise ValueError("Decision request revision is stale")
            items = json.loads(row["items_json"])
            required_ids = {item["id"] for item in items}
            normalized = {
                key: value.strip()
                for key, value in answers.items()
                if key in required_ids and isinstance(value, str) and value.strip()
            }
            if set(normalized) != required_ids:
                raise ValueError("Every open decision requires an answer")
            blocking_task_ids = _decision_blocking_task_ids(items)
            linked_agent_ids = self._validate_decision_blockers(
                connection,
                team_run_id,
                row["cycle_id"],
                blocking_task_ids,
                allow_legacy_blocked=True,
            )
            if _decision_has_run_scope(items) and run["leader_agent_id"]:
                linked_agent_ids.add(run["leader_agent_id"])
            cursor = connection.execute(
                """
                update team_decision_requests
                set status = 'resolved', revision = revision + 1, answers_json = ?,
                    answered_at = ?, updated_at = ?
                    where id = ? and status = 'awaiting_user' and revision = ?
                """,
                (
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    request_id,
                    revision,
                ),
            )
            _require_one_updated(cursor, "Decision request changed before answer")
            if blocking_task_ids:
                placeholders = ", ".join("?" for _ in blocking_task_ids)
                connection.execute(
                    f"""
                    update team_tasks
                    set status = 'pending', result = null, error_message = null,
                        started_at = null, finished_at = null, updated_at = ?
                    where team_run_id = ?
                      and status in ('waiting_for_user', 'blocked')
                      and id in ({placeholders})
                    """,
                    (now, team_run_id, *sorted(blocking_task_ids)),
                )
            self._set_linked_agent_status(
                connection,
                team_run_id,
                linked_agent_ids,
                source="waiting",
                target="pending",
                now=now,
            )
            cursor = connection.execute(
                """
                update team_runs
                set status = 'running', summary = null, error_message = null,
                    finished_at = null, updated_at = ?
                    where id = ? and status = 'waiting_for_user'
                """,
                (now, team_run_id),
            )
            _require_one_updated(cursor, "Team run changed before decision answer")
            if cycle is not None:
                cursor = connection.execute(
                    """
                    update team_run_cycles
                    set status = 'running', error_message = null,
                        finished_at = null, updated_at = ?
                    where id = ? and status = 'waiting_for_user'
                    """,
                    (now, row["cycle_id"]),
                )
                _require_one_updated(cursor, "Team cycle changed before decision answer")
                self._set_decision_series_state(
                    connection,
                    cycle["request_id"],
                    status="running",
                    pause_reason=None,
                    paused_cycle_id=None,
                    now=now,
                    expected_status="paused_user",
                )
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                    kind, content, metadata_json, created_at
                ) values (?, ?, ?, null, null, 'user_decision_answer', ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    team_run_id,
                    row["cycle_id"],
                    f"User answered {len(normalized)} decision(s).",
                    json.dumps(
                        {"request_id": request_id, "question_count": len(normalized)},
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            for item in items:
                answer = normalized[str(item["id"])]
                for query_id in item.get("query_message_ids", []):
                    query = connection.execute(
                        """
                        select sender_agent_id from team_messages
                        where id = ? and team_run_id = ? and kind = 'query'
                        """,
                        (query_id, team_run_id),
                    ).fetchone()
                    if query is None:
                        continue
                    connection.execute(
                        """
                        insert into team_messages (
                            id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id,
                            kind, content, metadata_json, created_at
                        ) values (?, ?, ?, ?, ?, 'answer', ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            team_run_id,
                            row["cycle_id"],
                            run["leader_agent_id"],
                            query["sender_agent_id"],
                            answer,
                            json.dumps(
                                {
                                    "query_id": query_id,
                                    "request_id": request_id,
                                    "source": "user_decision",
                                },
                                sort_keys=True,
                            ),
                            now,
                        ),
                    )
        request = self._get_decision_request(request_id)
        self._project_decisions_safely(team_run_id)
        return self.get_team_run(team_run_id), request

    def decision_context_for_task(self, team_run_id: str, task_id: str) -> str:
        lines: list[str] = []
        for request in self.list_decision_requests(team_run_id):
            if request.status != "resolved":
                continue
            for item in request.items:
                if task_id not in item.get("blocking_task_ids", []):
                    continue
                answer = request.answers.get(str(item.get("id")))
                if answer:
                    lines.append(f"Q: {item.get('question', '')}\nA: {answer}")
        return "\n\n".join(lines)

    def decision_context_for_run(
        self,
        team_run_id: str,
        *,
        stage: Literal["planning", "synthesis"],
        cycle_id: str | None = None,
    ) -> str:
        lines: list[str] = []
        for request in self.list_decision_requests(team_run_id):
            if request.status != "resolved" or request.cycle_id != cycle_id:
                continue
            for item in request.items:
                if item.get("stage") != stage:
                    continue
                answer = request.answers.get(str(item.get("id")))
                if answer:
                    lines.append(f"Q: {item.get('question', '')}\nA: {answer}")
        return "\n\n".join(lines)

    def cancel_waiting_decision(self, team_run_id: str) -> TeamRun:
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            run = connection.execute(
                "select status, leader_agent_id from team_runs where id = ?", (team_run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Team run not found: {team_run_id}")
            if run["status"] != "waiting_for_user":
                raise ValueError("Team run is not waiting for user input")
            request = connection.execute(
                """
                select * from team_decision_requests
                where team_run_id = ? and status = 'awaiting_user'
                order by created_at desc limit 1
                """,
                (team_run_id,),
            ).fetchone()
            if request is None:
                raise ValueError("No decision request is awaiting user input")
            items = json.loads(request["items_json"])
            blocking_task_ids = _decision_blocking_task_ids(items)
            linked_agent_ids = self._validate_decision_blockers(
                connection,
                team_run_id,
                request["cycle_id"],
                blocking_task_ids,
                allow_legacy_blocked=True,
            )
            if _decision_has_run_scope(items) and run["leader_agent_id"]:
                linked_agent_ids.add(run["leader_agent_id"])
            cycle = None
            if request["cycle_id"] is not None:
                cycle = connection.execute(
                    "select status, request_id from team_run_cycles where id = ?",
                    (request["cycle_id"],),
                ).fetchone()
                if cycle is None or cycle["status"] != "waiting_for_user":
                    raise ValueError("Decision cycle is no longer awaiting user input")
                cursor = connection.execute(
                    """
                    update team_run_cycles
                    set status = 'canceled', finished_at = ?, updated_at = ?
                    where id = ? and status = 'waiting_for_user'
                    """,
                    (now, now, request["cycle_id"]),
                )
                _require_one_updated(cursor, "Team cycle changed before decision cancel")
                self._set_decision_series_state(
                    connection,
                    cycle["request_id"],
                    status="canceled",
                    pause_reason="canceled",
                    paused_cycle_id=request["cycle_id"],
                    now=now,
                    expected_status="paused_user",
                    completed_at=now,
                )
            cursor = connection.execute(
                """
                update team_decision_requests
                set status = 'canceled', revision = revision + 1, updated_at = ?
                where id = ? and status = 'awaiting_user'
                """,
                (now, request["id"]),
            )
            _require_one_updated(cursor, "Decision request changed before cancel")
            if blocking_task_ids:
                placeholders = ", ".join("?" for _ in blocking_task_ids)
                connection.execute(
                    f"""
                    update team_tasks
                    set status = 'canceled', finished_at = ?, updated_at = ?
                    where team_run_id = ?
                      and status in ('waiting_for_user', 'blocked')
                      and id in ({placeholders})
                    """,
                    (now, now, team_run_id, *sorted(blocking_task_ids)),
                )
            self._set_linked_agent_status(
                connection,
                team_run_id,
                linked_agent_ids,
                source="waiting",
                target="canceled",
                now=now,
                finished_at=now,
            )
            cursor = connection.execute(
                """
                update team_runs set status = 'canceled', finished_at = ?, updated_at = ?
                where id = ? and status = 'waiting_for_user'
                """,
                (now, now, team_run_id),
            )
            _require_one_updated(cursor, "Team run changed before decision cancel")
        self._project_decisions_safely(team_run_id)
        return self.get_team_run(team_run_id)

    def append_message(
        self,
        team_run_id: str,
        sender_agent_id: str | None,
        recipient_agent_id: str | None,
        kind: str,
        content: str,
        metadata: dict[str, object],
        cycle_id: str | None = None,
    ) -> TeamMessage:
        self.get_team_run(team_run_id)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
        message_id = uuid4().hex
        self._db.execute(
            """
            insert into team_messages (
                id, team_run_id, cycle_id, sender_agent_id, recipient_agent_id, kind,
                content, metadata_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                team_run_id,
                cycle_id,
                sender_agent_id,
                recipient_agent_id,
                kind,
                content,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                _now(),
            ),
        )
        return self._get_message(message_id)

    def create_plan_revision(
        self,
        team_run_id: str,
        cycle_id: str,
        task_ids: Sequence[str],
        required_approver_agent_ids: Sequence[str],
    ) -> TeamPlanRevision | None:
        """Open the next revision, or refuse when the budget is spent.

        The cap is enforced here and only here. Callers ask for a revision and
        handle None; they never compute the budget themselves, because the two
        loop defects already fixed in this repo were exactly that.

        ``cycle_id`` is required: negotiation never runs without one, and
        SQLite treats NULL as distinct from NULL in a unique index, so an
        optional ``cycle_id`` of None would let the (team_run_id, cycle_id,
        revision) index silently stop enforcing the cap.
        """
        self.get_team_run(team_run_id)
        self._require_cycle_for_run(team_run_id, cycle_id)
        if not required_approver_agent_ids:
            raise ValueError("a plan revision needs at least one approver")
        row = self._db.fetchone(
            "select coalesce(max(revision), 0) as current from team_plan_revisions"
            " where team_run_id = ? and cycle_id = ?",
            (team_run_id, cycle_id),
        )
        revision = next_revision(int(row["current"]))
        if revision is None:
            return None
        revision_id = uuid4().hex
        now = _now()
        try:
            self._db.execute(
                """
                insert into team_plan_revisions (
                    id, team_run_id, cycle_id, revision, status,
                    task_ids_json, required_approver_agent_ids_json, created_at
                ) values (?, ?, ?, ?, 'awaiting_approval', ?, ?, ?)
                """,
                (
                    revision_id,
                    team_run_id,
                    cycle_id,
                    revision,
                    json.dumps(list(task_ids)),
                    json.dumps(list(required_approver_agent_ids)),
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("plan revision already created") from exc
        return self._get_plan_revision(revision_id)

    def record_plan_review(
        self,
        plan_revision_id: str,
        agent_id: str,
        decision: str,
        objections: Sequence[dict[str, str]],
    ) -> None:
        """Record one agent's review, once.

        The unique index on (plan_revision_id, agent_id) is the source of
        truth for "once": we attempt the insert and translate the resulting
        sqlite3.IntegrityError rather than checking first, because a
        check-then-insert leaves a gap that resume can race through.
        """
        self._get_plan_revision(plan_revision_id)
        try:
            self._db.execute(
                """
                insert into team_plan_approvals (
                    id, plan_revision_id, agent_id, decision, objections_json, created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    plan_revision_id,
                    agent_id,
                    decision,
                    json.dumps(list(objections)),
                    _now(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("plan review already recorded") from exc

    def plan_reviews(self, plan_revision_id: str) -> dict[str, str]:
        return {
            row["agent_id"]: row["decision"]
            for row in self._db.fetchall(
                "select agent_id, decision from team_plan_approvals"
                " where plan_revision_id = ? order by created_at asc, id asc",
                (plan_revision_id,),
            )
        }

    def plan_review_objections(
        self, plan_revision_id: str
    ) -> dict[str, list[dict[str, str]]]:
        return {
            row["agent_id"]: json.loads(row["objections_json"])
            for row in self._db.fetchall(
                "select agent_id, objections_json from team_plan_approvals"
                " where plan_revision_id = ? order by created_at asc, id asc",
                (plan_revision_id,),
            )
        }

    def get_active_plan_revision(
        self, team_run_id: str, cycle_id: str
    ) -> TeamPlanRevision | None:
        self.get_team_run(team_run_id)
        self._require_cycle_for_run(team_run_id, cycle_id)
        row = self._db.fetchone(
            "select * from team_plan_revisions"
            " where team_run_id = ? and cycle_id = ? and status = 'awaiting_approval'"
            " order by revision desc limit 1",
            (team_run_id, cycle_id),
        )
        return _team_plan_revision_from_row(row) if row is not None else None

    def list_plan_revisions(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> list[TeamPlanRevision]:
        self.get_team_run(team_run_id)
        where = "team_run_id = ?"
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            where += " and cycle_id = ?"
            parameters += (cycle_id,)
        return [
            _team_plan_revision_from_row(row)
            for row in self._db.fetchall(
                f"select * from team_plan_revisions where {where} "
                "order by created_at asc, revision asc, id asc",
                parameters,
            )
        ]

    def set_plan_revision_status(
        self, plan_revision_id: str, status: str
    ) -> TeamPlanRevision:
        self._get_plan_revision(plan_revision_id)
        decided_at = None if status == "awaiting_approval" else _now()
        self._db.execute(
            """
            update team_plan_revisions
            set status = ?, decided_at = coalesce(decided_at, ?)
            where id = ?
            """,
            (status, decided_at, plan_revision_id),
        )
        return self._get_plan_revision(plan_revision_id)

    def _get_plan_revision(self, plan_revision_id: str) -> TeamPlanRevision:
        row = self._db.fetchone(
            "select * from team_plan_revisions where id = ?", (plan_revision_id,)
        )
        if row is None:
            raise KeyError(f"Plan revision not found: {plan_revision_id}")
        return _team_plan_revision_from_row(row)

    def record_operation_workspace_baseline(
        self,
        operation_id: str,
        *,
        team_run_id: str,
        cycle_id: str,
        task_id: str,
        agent_id: str,
        snapshot: Mapping[str, tuple[int, int]],
    ) -> None:
        normalized = {
            path: [int(value[0]), int(value[1])]
            for path, value in sorted(snapshot.items())
            if isinstance(path, str)
            and isinstance(value, tuple)
            and len(value) == 2
        }
        if len(normalized) != len(snapshot):
            raise ValueError("Workspace baseline snapshot is invalid")
        message_id = _operation_workspace_baseline_id(operation_id)
        metadata = {
            "operation_id": operation_id,
            "task_id": task_id,
            "snapshot": normalized,
        }
        now = _now()
        with self._db.connection() as connection:
            connection.execute("begin immediate")
            operation = connection.execute(
                """
                select team_run_id, cycle_id, task_id, agent_id, status
                from team_model_operations where id = ?
                """,
                (operation_id,),
            ).fetchone()
            if (
                operation is None
                or operation["team_run_id"] != team_run_id
                or operation["cycle_id"] != cycle_id
                or operation["task_id"] != task_id
                or operation["agent_id"] != agent_id
                or operation["status"] != "prepared"
            ):
                raise ValueError(
                    "Workspace baseline operation ownership is invalid"
                )
            existing = connection.execute(
                "select * from team_messages where id = ?",
                (message_id,),
            ).fetchone()
            if existing is not None:
                message = _team_message_from_row(existing)
                if (
                    message.team_run_id != team_run_id
                    or message.cycle_id != cycle_id
                    or message.sender_agent_id != agent_id
                    or message.recipient_agent_id is not None
                    or message.kind != "operation_workspace_baseline"
                    or message.metadata != metadata
                ):
                    raise ValueError(
                        "Workspace baseline receipt does not match"
                    )
                return
            connection.execute(
                """
                insert into team_messages (
                    id, team_run_id, cycle_id, sender_agent_id,
                    recipient_agent_id, kind, content, metadata_json, created_at
                ) values (?, ?, ?, ?, null, 'operation_workspace_baseline',
                          'Workspace baseline recorded.', ?, ?)
                """,
                (
                    message_id,
                    team_run_id,
                    cycle_id,
                    agent_id,
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )

    def get_operation_workspace_baseline(
        self,
        operation_id: str,
        *,
        team_run_id: str,
        cycle_id: str,
        task_id: str,
        agent_id: str,
    ) -> dict[str, tuple[int, int]]:
        message = self._get_message(
            _operation_workspace_baseline_id(operation_id)
        )
        metadata = message.metadata
        snapshot = metadata.get("snapshot")
        if (
            message.team_run_id != team_run_id
            or message.cycle_id != cycle_id
            or message.sender_agent_id != agent_id
            or message.recipient_agent_id is not None
            or message.kind != "operation_workspace_baseline"
            or metadata.get("operation_id") != operation_id
            or metadata.get("task_id") != task_id
            or not isinstance(snapshot, dict)
        ):
            raise ValueError("Workspace baseline receipt is invalid")
        normalized: dict[str, tuple[int, int]] = {}
        for path, value in snapshot.items():
            if (
                not isinstance(path, str)
                or not isinstance(value, list)
                or len(value) != 2
                or any(not isinstance(item, int) for item in value)
            ):
                raise ValueError("Workspace baseline snapshot is invalid")
            normalized[path] = (value[0], value[1])
        return normalized

    def _require_cycle_for_run(self, team_run_id: str, cycle_id: str) -> TeamRunCycle:
        cycle = self.get_cycle(cycle_id)
        if cycle.team_run_id != team_run_id:
            raise ValueError("Cycle belongs to a different team run")
        return cycle

    def list_messages(
        self, team_run_id: str, cycle_id: str | None = None
    ) -> list[TeamMessage]:
        self.get_team_run(team_run_id)
        where = "team_run_id = ?"
        parameters: tuple[object, ...] = (team_run_id,)
        if cycle_id is not None:
            self._require_cycle_for_run(team_run_id, cycle_id)
            where += " and cycle_id = ?"
            parameters += (cycle_id,)
        return [
            _team_message_from_row(row)
            for row in self._db.fetchall(
                f"select * from team_messages where {where} order by created_at asc, id asc",
                parameters,
            )
        ]

    def set_run_status(
        self,
        team_run_id: str,
        status: TeamRunStatus,
        summary: str | None = None,
        error_message: str | None = None,
    ) -> TeamRun:
        self.get_team_run(team_run_id)
        started_at = _now() if status in _ACTIVE_RUN_STATUSES else None
        # 종료는 걸려 있던 정지 요청을 소진시킨다. 끝난 런은 다시 배치 경계에
        # 닿지 않으므로 이행될 자리가 없고, 남겨두면 두 가지가 깨진다: 완료된
        # 런에 `정지 요청됨` 배너가 뜨고, 나중에 일감 추가로 그 런을 다시
        # 열었을 때 아무도 누르지 않은 정지가 첫 배치 경계에서 걸린다.
        #
        # finished_at 과 같은 사실에서 나오므로 같은 문장에서 함께 쓴다.
        # interrupted 는 TERMINAL_RUN_STATUSES 에 없다 -- 재시작을 건너 살아
        # 남는 요청(설계 「요청이 소진되는 조건」)은 여기에 걸리지 않는다.
        settled = status in TERMINAL_RUN_STATUSES
        finished_at = _now() if settled else None
        self._db.execute(
            """
            update team_runs
            set status = ?,
                summary = ?,
                error_message = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                pause_requested_at = case
                    when ? = 1 then null else pause_requested_at end,
                updated_at = ?
            where id = ?
            """,
            (
                status,
                summary,
                error_message,
                started_at,
                finished_at,
                1 if settled else 0,
                _now(),
                team_run_id,
            ),
        )
        return self.get_team_run(team_run_id)

    def request_pause(self, team_run_id: str) -> TeamRun:
        """사용자가 정지를 요청했음을 기록한다.

        런타임은 안전한 자리에 닿았을 때 이 칸을 보고 멈춘다. 요청과 정지를
        따로 두는 이유는 둘 사이에 지연이 있기 때문이다 -- 진행 중인 워커
        호출을 끊지 않고 끝나기를 기다린다.

        where 절의 is null 은 두 번 눌러도 첫 요청 시각이 유지되게 한다.
        """
        self.get_team_run(team_run_id)
        now = _now()
        self._db.execute(
            """
            update team_runs
            set pause_requested_at = ?, updated_at = ?
            where id = ? and pause_requested_at is null
            """,
            (now, now, team_run_id),
        )
        return self.get_team_run(team_run_id)

    def clear_pause_request(self, team_run_id: str) -> TeamRun:
        self.get_team_run(team_run_id)
        self._db.execute(
            "update team_runs set pause_requested_at = null, updated_at = ? where id = ?",
            (_now(), team_run_id),
        )
        return self.get_team_run(team_run_id)

    def backfill_agent_avatars(self) -> int:
        updated = 0
        for row in self._db.fetchall(
            "select id, persona_id, persona_snapshot_json from team_agents"
        ):
            snapshot = json.loads(row["persona_snapshot_json"])
            if "avatar" in snapshot:
                continue
            try:
                persona = self._personas.get_persona(row["persona_id"])
            except KeyError:
                continue
            snapshot["avatar"] = persona.avatar
            self._db.execute(
                "update team_agents set persona_snapshot_json = ?, updated_at = ? where id = ?",
                (json.dumps(snapshot, ensure_ascii=False, sort_keys=True), _now(), row["id"]),
            )
            updated += 1
        return updated

    def _insert_agent(
        self,
        connection: sqlite3.Connection,
        team_run_id: str,
        persona_id: str,
        role: str,
        now: str,
        workspace_path: str | None = None,
    ) -> TeamAgent:
        persona = self._personas.get_persona(persona_id)
        agent_id = uuid4().hex
        connection.execute(
            """
            insert into team_agents (
                id, team_run_id, name, role, persona_id, persona_snapshot_json,
                backend, model, status, workspace_path, current_task_id,
                reinvocations, upstream_session_id,
                started_at, finished_at, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id, team_run_id, persona.name, role, persona.id,
                json.dumps(_persona_snapshot(persona), ensure_ascii=False, sort_keys=True),
                persona.default_backend, persona.default_model, "pending", workspace_path, None,
                0, None,
                None, None, now, now,
            ),
        )
        row = connection.execute(
            "select * from team_agents where id = ?",
            (agent_id,),
        ).fetchone()
        return _team_agent_from_row(row)

    def _get_decision_request(self, request_id: str) -> TeamDecisionRequest:
        row = self._db.fetchone(
            "select * from team_decision_requests where id = ?", (request_id,)
        )
        if row is None:
            raise KeyError(f"Decision request not found: {request_id}")
        return _team_decision_request_from_row(row)

    def _project_decisions_safely(self, team_run_id: str) -> None:
        try:
            self._project_decisions(team_run_id)
        except OSError as exc:
            self.append_message(
                team_run_id,
                None,
                None,
                "document_projection_error",
                "Could not update USER_DECISIONS.md.",
                {"error_type": type(exc).__name__},
            )

    def _project_decisions(self, team_run_id: str) -> None:
        run = self.get_team_run(team_run_id)
        requests = self.list_decision_requests(team_run_id)
        if not requests:
            return
        current = next(
            (
                request
                for request in reversed(requests)
                if request.status in {"collecting", "awaiting_user"}
            ),
            requests[-1],
        )
        lines = [
            "---",
            "schema: gateway.team-decisions/v1",
            f"team_run_id: {team_run_id}",
            f"active_request_id: {current.id}",
            f"revision: {current.revision}",
            f"status: {current.status}",
            f"generated_at: {_now()}",
            "---",
            "",
            "# User decisions",
            "",
            "Team Run 화면의 INPUT NEEDED에서 답변하세요. 이 파일은 자동 생성됩니다.",
            "",
        ]
        for request in reversed(requests):
            heading = "Active request" if request.status in {"collecting", "awaiting_user"} else "History"
            lines.extend([f"## {heading} — {request.id}", ""])
            for item in request.items:
                item_id = str(item.get("id") or "")
                answer = request.answers.get(item_id)
                lines.extend(
                    [
                        f"### {item_id} — {item.get('topic') or 'Decision'}",
                        "",
                        f"- Status: {'answered' if answer else 'open'}",
                        f"- Stage: {item.get('stage') or 'task'}",
                        f"- Blocks: {', '.join(item.get('blocking_task_ids') or []) or '-'}",
                        f"- Why now: {item.get('why_needed') or '-'}",
                        f"- Question: {item.get('question') or '-'}",
                    ]
                )
                recommended = item.get("recommended_option_id")
                if recommended:
                    lines.append(f"- Recommended: {recommended}")
                options = item.get("options") or []
                if options:
                    lines.extend(["", "#### Options", ""])
                    for option in options:
                        lines.append(
                            f"- `{option.get('id', '')}` — {option.get('label', '')}: "
                            f"{option.get('impact', '')}"
                        )
                lines.extend(["", "#### Answer", "", answer or "Pending", ""])
        target = Path(run.workspace_root) / current.file_path
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        temporary.replace(target)

    def _get_agent(self, agent_id: str) -> TeamAgent:
        row = self._db.fetchone("select * from team_agents where id = ?", (agent_id,))
        if row is None:
            raise KeyError(f"Team agent not found: {agent_id}")
        return _team_agent_from_row(row)

    def _agent_from_connection(
        self,
        connection: sqlite3.Connection,
        agent_id: str,
    ) -> TeamAgent:
        row = connection.execute(
            "select * from team_agents where id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Team agent not found: {agent_id}")
        return _team_agent_from_row(row)

    def _get_task(self, task_id: str) -> TeamTask:
        row = self._db.fetchone("select * from team_tasks where id = ?", (task_id,))
        if row is None:
            raise KeyError(f"Team task not found: {task_id}")
        return _team_task_from_row(row)

    def _task_from_connection(
        self,
        connection: sqlite3.Connection,
        task_id: str,
    ) -> TeamTask:
        row = connection.execute(
            "select * from team_tasks where id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Team task not found: {task_id}")
        return _team_task_from_row(row)

    def _get_message(self, message_id: str) -> TeamMessage:
        row = self._db.fetchone("select * from team_messages where id = ?", (message_id,))
        if row is None:
            raise KeyError(f"Team message not found: {message_id}")
        return _team_message_from_row(row)

    def _message_from_connection(
        self,
        connection: sqlite3.Connection,
        message_id: str,
    ) -> TeamMessage:
        row = connection.execute(
            "select * from team_messages where id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Team message not found: {message_id}")
        return _team_message_from_row(row)

    def _decision_request_from_connection(
        self,
        connection: sqlite3.Connection,
        request_id: str,
    ) -> TeamDecisionRequest:
        row = connection.execute(
            "select * from team_decision_requests where id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Decision request not found: {request_id}")
        return _team_decision_request_from_row(row)


def _persona_snapshot(persona: Persona) -> dict[str, object]:
    return {
        "id": persona.id,
        "name": persona.name,
        "role": persona.role,
        "description": persona.description,
        "responsibilities": persona.responsibilities,
        "constraints": persona.constraints,
        "default_backend": persona.default_backend,
        "default_model": persona.default_model,
        "default_options": persona.default_options,
        "avatar": persona.avatar,
    }


def _retry_description(
    connection: sqlite3.Connection, task: sqlite3.Row
) -> str:
    """지난 시도에서 알아낸 것을 재시도 일감의 설명에 싣는다.

    설명을 그대로 복사하면 워커는 자기가 이미 실패했다는 것조차 모른 채 같은
    프롬프트를 다시 받는다. 실측에서 같은 일감이 세 사이클에 걸쳐 재시도됐고
    매번 같은 벽에 부딪혔다 -- 두 번째에 리드가 "이 워커는 다단계 하네스를
    못 만든다" 를 알아냈지만 그 진단이 다음 재시도로 가지 않았다.

    리드의 심사를 함께 싣는 이유: 워커가 스스로 쓴 요약은 무엇을 했는지를
    말하지만, 무엇이 막고 있는지는 그것을 밖에서 본 리드가 말한다. 재시도가
    필요한 이유는 대개 후자다.
    """
    description = str(task["description"] or "")
    lines: list[str] = []

    reason = str(task["error_message"] or "").strip()
    if reason:
        lines.append(f"- 판정: {reason}")
    if task["outcome_json"]:
        try:
            outcome = json.loads(task["outcome_json"])
        except (TypeError, ValueError):
            outcome = {}
        summary = str(outcome.get("summary") or "").strip()
        if summary:
            lines.append(f"- 지난 시도가 남긴 말: {summary}")

    review = connection.execute(
        """
        select content from team_messages
        where team_run_id = ? and kind = 'acceptance_review'
          and json_extract(metadata_json, '$.task_id') = ?
        order by created_at desc limit 1
        """,
        (task["team_run_id"], task["id"]),
    ).fetchone()
    if review is not None:
        content = str(review["content"] or "").strip()
        if content:
            lines.append(f"- 리드의 마지막 판단: {content}")

    if not lines:
        return description
    return (
        description
        + "\n\nPREVIOUS ATTEMPT\n"
        + "\n".join(lines)
        + "\n이번에는 위에서 막힌 지점을 먼저 닫아라. 같은 방법으로 다시 시도하지 마라."
    )


def _team_run_from_row(row: object) -> TeamRun:
    return TeamRun(
        id=row["id"],
        goal=row["goal"],
        status=row["status"],
        run_mode=row["run_mode"],
        lifecycle_mode=(
            row["lifecycle_mode"] if "lifecycle_mode" in row.keys() else "standard"
        ),
        leader_agent_id=row["leader_agent_id"],
        max_workers=row["max_workers"],
        rounds_budget=row["rounds_budget"],
        rounds_used=row["rounds_used"],
        workspace_root=row["workspace_root"],
        summary=row["summary"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
        team_id=row["team_id"] if "team_id" in row.keys() else None,
        rules_snapshot=(
            json.loads(row["rules_snapshot_json"])
            if "rules_snapshot_json" in row.keys() and row["rules_snapshot_json"]
            else None
        ),
        execution_policy=(
            row["execution_policy"] if "execution_policy" in row.keys() else None
        ),
        working_root=(row["working_root"] if "working_root" in row.keys() else None),
        artifact_root=(row["artifact_root"] if "artifact_root" in row.keys() else None),
        worktree_branch=(
            row["worktree_branch"] if "worktree_branch" in row.keys() else None
        ),
        space_policy=(
            json.loads(row["space_policy_snapshot_json"])
            if "space_policy_snapshot_json" in row.keys()
            and row["space_policy_snapshot_json"]
            else None
        ),
        parent_team_run_id=(
            row["parent_team_run_id"] if "parent_team_run_id" in row.keys() else None
        ),
        plan_negotiation_enabled=(
            bool(row["plan_negotiation_enabled"])
            if "plan_negotiation_enabled" in row.keys()
            else False
        ),
        pause_requested_at=(
            row["pause_requested_at"]
            if "pause_requested_at" in row.keys()
            else None
        ),
    )


def _team_run_cycle_from_row(row: object) -> TeamRunCycle:
    return TeamRunCycle(
        id=row["id"],
        team_run_id=row["team_run_id"],
        sequence=row["sequence"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        status=row["status"],
        rounds_budget=row["rounds_budget"],
        rounds_used=row["rounds_used"],
        summary=row["summary"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
        request_id=row["request_id"] if "request_id" in row.keys() else None,
        rules_snapshot=(
            json.loads(row["rules_snapshot_json"])
            if "rules_snapshot_json" in row.keys() and row["rules_snapshot_json"]
            else None
        ),
        execution_metadata=(
            json.loads(row["execution_metadata_json"])
            if "execution_metadata_json" in row.keys()
            and row["execution_metadata_json"]
            else None
        ),
        space_policy=(
            json.loads(row["space_policy_snapshot_json"])
            if "space_policy_snapshot_json" in row.keys()
            and row["space_policy_snapshot_json"]
            else None
        ),
    )


def _execution_metadata_object(value: str | None) -> dict[str, object]:
    if value is None:
        return {}
    metadata = json.loads(value)
    if not isinstance(metadata, dict):
        raise ValueError("Cycle execution metadata is invalid")
    return metadata


def _team_agent_from_row(row: object) -> TeamAgent:
    return TeamAgent(
        id=row["id"],
        team_run_id=row["team_run_id"],
        name=row["name"],
        role=row["role"],
        persona_id=row["persona_id"],
        persona_snapshot=json.loads(row["persona_snapshot_json"]),
        backend=row["backend"],
        model=row["model"],
        status=row["status"],
        workspace_path=row["workspace_path"],
        current_task_id=row["current_task_id"],
        reinvocations=row["reinvocations"],
        upstream_session_id=row["upstream_session_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _team_task_from_row(row: object) -> TeamTask:
    acceptance = (
        json.loads(row["acceptance_json"])
        if "acceptance_json" in row.keys() and row["acceptance_json"]
        else {}
    )
    return TeamTask(
        id=row["id"],
        team_run_id=row["team_run_id"],
        title=row["title"],
        description=row["description"],
        owner_agent_id=row["owner_agent_id"],
        status=row["status"],
        required=bool(row["required"]) if "required" in row.keys() else True,
        acceptance=TaskAcceptance(
            required_outputs=tuple(acceptance.get("required_outputs", ())),
            required_verifications=parse_required_verifications(
                acceptance.get("required_verifications", [])
            ),
        ),
        outcome=(
            json.loads(row["outcome_json"])
            if "outcome_json" in row.keys() and row["outcome_json"]
            else None
        ),
        acceptance_result=(
            json.loads(row["acceptance_result_json"])
            if "acceptance_result_json" in row.keys()
            and row["acceptance_result_json"]
            else None
        ),
        result=row["result"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        cycle_id=row["cycle_id"] if "cycle_id" in row.keys() else None,
        retry_of_task_id=(
            row["retry_of_task_id"] if "retry_of_task_id" in row.keys() else None
        ),
        acceptance_recovery_attempts=(
            int(row["acceptance_recovery_attempts"])
            if "acceptance_recovery_attempts" in row.keys()
            else 0
        ),
        plan_ordinal=(
            int(row["plan_ordinal"])
            if "plan_ordinal" in row.keys() and row["plan_ordinal"] is not None
            else 0
        ),
    )


def _task_acceptance_json(acceptance: TaskAcceptance) -> str:
    return json.dumps(
        {
            "required_outputs": list(acceptance.required_outputs),
            "required_verifications": [
                item.name
                if item.check is None
                else {"name": item.name, "check": verification_check_payload(item.check)}
                for item in acceptance.required_verifications
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def required_verifications_payload(
    required_verifications: tuple[RequiredVerification, ...],
) -> list[dict[str, object]]:
    """Explicit form for API/run-result consumers: always {"name", "check"}.

    This is distinct from `_task_acceptance_json`'s canonical form, which
    collapses a check-less verification to a bare string for DB/ledger digest
    stability. Consumers here (API responses, run-result packages) need a
    stable, uniform shape instead.
    """
    return [
        {
            "name": item.name,
            "check": None if item.check is None else verification_check_payload(item.check),
        }
        for item in required_verifications
    ]


def _acceptance_review_metadata(
    *,
    task_id: str,
    attempt: int,
    reason_code: str,
    action: str,
    reason: str,
    instruction: str | None,
    acceptance_before: dict[str, object],
    acceptance_after: dict[str, object] | None,
    rejected_deliverables: tuple[str, ...] | list[str],
    rejected_verifications: tuple[str, ...] | list[str],
    operation_id: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "task_id": task_id,
        "attempt": attempt,
        "reason_code": reason_code,
        "action": action,
        "reason": reason,
        "instruction": instruction,
        "acceptance_before": acceptance_before,
        "acceptance_after": acceptance_after,
        "rejected_deliverables": list(rejected_deliverables),
        "rejected_verifications": list(rejected_verifications),
    }
    if operation_id is not None:
        metadata["operation_id"] = operation_id
    return metadata


def _operation_workspace_baseline_id(operation_id: str) -> str:
    return f"operation-workspace-baseline:{operation_id}"


def parse_required_verifications(value: object) -> tuple[RequiredVerification, ...]:
    if not isinstance(value, list):
        raise ValueError(  # noqa: TRY004
            "Required verifications must be a list"
        )
    parsed: list[RequiredVerification] = []
    names: set[str] = set()
    for raw in value:
        if isinstance(raw, str):
            name, check = raw, None
        elif isinstance(raw, dict):
            if set(raw) - {"name", "check"}:
                raise ValueError("Required verification fields must be name and check")
            name = raw.get("name")
            raw_check = raw.get("check")
            check = None if raw_check is None else parse_verification_check(raw_check)
        else:
            raise ValueError(  # noqa: TRY004
                "Required verification must be a string or an object"
            )
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Required verification requires a name")
        normalized = name.strip()
        if normalized in names:
            raise ValueError("Acceptance has duplicate required verifications")
        names.add(normalized)
        parsed.append(RequiredVerification(normalized, check))
    return tuple(parsed)


def _validate_task_acceptance(acceptance: TaskAcceptance) -> None:
    outputs = acceptance.required_outputs
    verifications = acceptance.required_verifications
    if not outputs and not verifications:
        raise ValueError("Acceptance requires an output or verification")
    if len(set(outputs)) != len(outputs):
        raise ValueError("Acceptance has duplicate required outputs")
    names = [item.name for item in verifications]
    if len(set(names)) != len(names):
        raise ValueError("Acceptance has duplicate required verifications")
    if any(not item.strip() for item in (*outputs, *names)):
        raise ValueError("Acceptance items must not be blank")
    if any(not _safe_relative_task_output(path) for path in outputs):
        raise ValueError("Acceptance output path must be relative and bounded")


def _safe_relative_task_output(value: str) -> bool:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        value not in {"", "."}
        and not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and not windows.anchor
        and ".." not in posix.parts
        and ".." not in windows.parts
    )


def _team_message_from_row(row: object) -> TeamMessage:
    return TeamMessage(
        id=row["id"],
        team_run_id=row["team_run_id"],
        sender_agent_id=row["sender_agent_id"],
        recipient_agent_id=row["recipient_agent_id"],
        kind=row["kind"],
        content=row["content"],
        metadata=json.loads(row["metadata_json"]),
        created_at=row["created_at"],
        cycle_id=row["cycle_id"] if "cycle_id" in row.keys() else None,
    )


def _team_plan_revision_from_row(row: object) -> TeamPlanRevision:
    return TeamPlanRevision(
        id=row["id"],
        team_run_id=row["team_run_id"],
        cycle_id=row["cycle_id"],
        revision=row["revision"],
        status=row["status"],
        task_ids=tuple(json.loads(row["task_ids_json"])),
        required_approver_agent_ids=tuple(
            json.loads(row["required_approver_agent_ids_json"])
        ),
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )


def _team_decision_request_from_row(row: object) -> TeamDecisionRequest:
    return TeamDecisionRequest(
        id=row["id"],
        team_run_id=row["team_run_id"],
        status=row["status"],
        revision=row["revision"],
        items=json.loads(row["items_json"]),
        answers=json.loads(row["answers_json"]),
        file_path=row["file_path"],
        created_at=row["created_at"],
        published_at=row["published_at"],
        answered_at=row["answered_at"],
        updated_at=row["updated_at"],
        cycle_id=row["cycle_id"] if "cycle_id" in row.keys() else None,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Team recovery timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _require_one_updated(cursor: sqlite3.Cursor, message: str) -> None:
    if cursor.rowcount != 1:
        raise RuntimeError(message)


def _decision_blocking_task_ids(items: list[dict[str, object]]) -> set[str]:
    return {
        task_id
        for item in items
        for task_id in item.get("blocking_task_ids", [])
        if isinstance(task_id, str)
    }


def _decision_has_run_scope(items: list[dict[str, object]]) -> bool:
    return any(
        item.get("blocking_scope") == "run"
        or not item.get("blocking_task_ids")
        for item in items
    )


def _preplanning_source_is_pristine(
    run: sqlite3.Row,
    cycle: sqlite3.Row,
) -> bool:
    return (
        run["rounds_used"] == 0
        and run["summary"] is None
        and run["error_message"] is None
        and run["started_at"] is None
        and run["finished_at"] is None
        and cycle["rounds_used"] == 0
        and cycle["summary"] is None
        and cycle["error_message"] is None
        and cycle["started_at"] is None
        and cycle["finished_at"] is None
    )


def _preplanning_agents_are_pristine(
    agents: list[sqlite3.Row],
    leader_agent_id: str | None,
) -> bool:
    return (
        bool(agents)
        and leader_agent_id is not None
        and any(agent["id"] == leader_agent_id for agent in agents)
        and all(
            agent["status"] == "pending"
            and agent["current_task_id"] is None
            and agent["reinvocations"] == 0
            and agent["upstream_session_id"] is None
            and agent["started_at"] is None
            and agent["finished_at"] is None
            for agent in agents
        )
    )


def _validated_provider_recovery_metadata(
    stored_metadata: object,
) -> tuple[dict[str, object], str | None, str | None]:
    if not isinstance(stored_metadata, dict):
        raise ValueError("Invalid provider recovery metadata")
    recovery = stored_metadata.get("provider_recovery")
    required_fields = {
        "provider",
        "task_id",
        "agent_id",
        "reason_code",
        "attempts",
        "first_failed_at",
        "next_retry_at",
        "warning_visible_at",
    }
    if not isinstance(recovery, dict) or not required_fields.issubset(recovery):
        raise ValueError("Invalid provider recovery metadata")
    if (
        not isinstance(recovery["provider"], str)
        or not recovery["provider"].strip()
        or not isinstance(recovery["reason_code"], str)
        or not recovery["reason_code"].strip()
        or type(recovery["attempts"]) is not int
        or recovery["attempts"] < 1
    ):
        raise ValueError("Invalid provider recovery metadata")
    task_id = recovery["task_id"]
    agent_id = recovery["agent_id"]
    if (
        (task_id is not None and (not isinstance(task_id, str) or not task_id))
        or (agent_id is not None and (not isinstance(agent_id, str) or not agent_id))
        or (task_id is not None and agent_id is None)
    ):
        raise ValueError("Invalid provider recovery metadata")
    for field in ("first_failed_at", "next_retry_at", "warning_visible_at"):
        value = recovery[field]
        if not isinstance(value, str):
            raise ValueError("Invalid provider recovery metadata")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Invalid provider recovery metadata") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Invalid provider recovery metadata")
    return stored_metadata, task_id, agent_id


def _initials(name: str) -> str:
    parts = (name or "").strip().split()
    if not parts:
        return "?"
    letters = [word[0] for word in parts[:2]]
    return "".join(letters).upper()


def _current_objective(
    run: TeamRun,
    pending_request: sqlite3.Row | None,
    latest_cycle: sqlite3.Row | None,
) -> str:
    if pending_request is not None:
        instruction = str(pending_request["instruction"] or "").strip()
        if instruction:
            return instruction
    if latest_cycle is not None:
        instruction = str(latest_cycle["request_instruction"] or "").strip()
        if instruction:
            return instruction
    return run.goal.strip() or "Ready for trigger"


def _team_run_display_status(
    run: TeamRun,
    pending_request: sqlite3.Row | None,
    latest_cycle: sqlite3.Row | None,
    latest_series: sqlite3.Row | None,
) -> str:
    if run.status == "canceled":
        return "canceled"
    cycle_status = latest_cycle["status"] if latest_cycle is not None else None
    series_status = latest_series["status"] if latest_series is not None else None
    if (
        pending_request is not None
        or run.status in _ACTIVE_RUN_STATUSES
        or cycle_status in {"queued", "running"}
        or series_status == "running"
    ):
        return "active"
    if (
        run.status in {"waiting_for_user", "interrupted", "failed", "completed_with_failures"}
        or cycle_status in {"failed", "waiting_for_user", "interrupted"}
        or series_status in {"paused_failure", "paused_user", "paused_interrupted"}
    ):
        return "needs_attention"
    if series_status == "waiting_interval":
        return "auto_waiting"
    return "ready"


def _elapsed_seconds(started_at: str | None, finished_at: str | None) -> float:
    if not started_at:
        return 0.0
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
    return max(0.0, (end - start).total_seconds())
