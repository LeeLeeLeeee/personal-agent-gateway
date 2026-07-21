import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from personal_agent_gateway.db import Database


SpaceScope = Literal["global", "persona", "team"]
ReadMode = Literal["home", "selected", "all"]
WriteMode = Literal["isolated", "worktree", "full_access"]


@dataclass(frozen=True)
class SpacePolicy:
    scope: SpaceScope
    scope_id: str
    read_mode: ReadMode
    read_path: str | None
    write_mode: WriteMode
    workspace_path: str | None
    created_at: str
    updated_at: str

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EffectiveSpacePolicy:
    source: SpaceScope
    policy: SpacePolicy


@dataclass(frozen=True)
class PreparedSpace:
    working_root: Path
    artifact_root: Path
    worktree_branch: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SpacePolicyService:
    def __init__(
        self,
        db: Database,
        default_home: Path | None = None,
    ) -> None:
        self._db = db
        self._default_home = (default_home or Path.home()).expanduser().resolve()

    def seed_defaults(self) -> None:
        if self._get_optional("global", "") is None:
            self.upsert(
                "global",
                "",
                read_mode="home",
                read_path=str(self._default_home),
                write_mode="isolated",
                workspace_path=None,
            )
        for row in self._db.fetchall("select id from teams"):
            self.ensure_team(row["id"])

    def global_policy(self) -> SpacePolicy:
        policy = self._get_optional("global", "")
        if policy is None:
            self.seed_defaults()
            policy = self._get_optional("global", "")
        if policy is None:
            raise RuntimeError("Global SPACE policy is not configured")
        return policy

    def ensure_team(self, team_id: str) -> SpacePolicy:
        current = self._get_optional("team", team_id)
        if current is not None:
            return current
        global_policy = self.global_policy()
        return self.upsert(
            "team",
            team_id,
            read_mode=global_policy.read_mode,
            read_path=global_policy.read_path,
            write_mode=global_policy.write_mode,
            workspace_path=global_policy.workspace_path,
        )

    def list_persona_overrides(self) -> list[SpacePolicy]:
        return self._list_scope("persona")

    def list_team_policies(self) -> list[SpacePolicy]:
        return self._list_scope("team")

    def resolve(
        self,
        *,
        team_id: str | None = None,
        persona_id: str | None = None,
    ) -> EffectiveSpacePolicy:
        if team_id:
            return EffectiveSpacePolicy("team", self.ensure_team(team_id))
        if persona_id:
            persona = self._get_optional("persona", persona_id)
            if persona is not None:
                return EffectiveSpacePolicy("persona", persona)
        return EffectiveSpacePolicy("global", self.global_policy())

    def upsert(
        self,
        scope: SpaceScope,
        scope_id: str,
        *,
        read_mode: ReadMode,
        read_path: str | None,
        write_mode: WriteMode,
        workspace_path: str | None,
    ) -> SpacePolicy:
        normalized_scope_id = self._validate_scope_target(scope, scope_id)
        normalized_read_path = self._normalize_read_path(read_mode, read_path)
        normalized_workspace = self._normalize_workspace_path(
            scope,
            write_mode,
            workspace_path,
        )
        now = _now()
        self._db.execute(
            """
            insert into space_policies (
                scope, scope_id, read_mode, read_path, write_mode,
                workspace_path, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(scope, scope_id) do update set
                read_mode = excluded.read_mode,
                read_path = excluded.read_path,
                write_mode = excluded.write_mode,
                workspace_path = excluded.workspace_path,
                updated_at = excluded.updated_at
            """,
            (
                scope,
                normalized_scope_id,
                read_mode,
                normalized_read_path,
                write_mode,
                normalized_workspace,
                now,
                now,
            ),
        )
        policy = self._get_optional(scope, normalized_scope_id)
        if policy is None:
            raise RuntimeError("SPACE policy could not be saved")
        return policy

    def delete_persona_override(self, persona_id: str) -> None:
        self._db.execute(
            "delete from space_policies where scope = 'persona' and scope_id = ?",
            (persona_id,),
        )

    def delete_team_policy(self, team_id: str) -> None:
        self._db.execute(
            "delete from space_policies where scope = 'team' and scope_id = ?",
            (team_id,),
        )

    def _validate_scope_target(self, scope: SpaceScope, scope_id: str) -> str:
        if scope == "global":
            if scope_id:
                raise ValueError("Global SPACE policy cannot have a scope id")
            return ""
        if not scope_id:
            raise ValueError(f"{scope.title()} SPACE policy requires a scope id")
        table = "personas" if scope == "persona" else "teams"
        if self._db.fetchone(f"select id from {table} where id = ?", (scope_id,)) is None:
            raise ValueError(f"Unknown {scope}: {scope_id}")
        return scope_id

    def _normalize_read_path(self, mode: ReadMode, value: str | None) -> str | None:
        if mode == "all":
            return None
        candidate = self._default_home if mode == "home" else _required_path(value, "Read path")
        return str(_existing_directory(candidate, "Read path"))

    def _normalize_workspace_path(
        self,
        scope: SpaceScope,
        mode: WriteMode,
        value: str | None,
    ) -> str | None:
        if mode == "isolated":
            return None
        if mode == "worktree" and scope != "team":
            raise ValueError("Worktree SPACE mode is available only for Teams")
        candidate = _required_path(value, "Workspace path")
        return str(_existing_directory(candidate, "Workspace path"))

    def _list_scope(self, scope: SpaceScope) -> list[SpacePolicy]:
        return [
            _policy_from_row(row)
            for row in self._db.fetchall(
                "select * from space_policies where scope = ? order by created_at, scope_id",
                (scope,),
            )
        ]

    def _get_optional(self, scope: SpaceScope, scope_id: str) -> SpacePolicy | None:
        row = self._db.fetchone(
            "select * from space_policies where scope = ? and scope_id = ?",
            (scope, scope_id),
        )
        return _policy_from_row(row) if row is not None else None


class TeamSpaceManager:
    def prepare(self, run_id: str, run_root: Path, policy: SpacePolicy) -> PreparedSpace:
        artifact_root = run_root / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        if policy.write_mode == "isolated":
            working_root = run_root / "workspace"
            working_root.mkdir(parents=True, exist_ok=True)
            return PreparedSpace(working_root, artifact_root)
        if policy.write_mode == "full_access":
            if not policy.workspace_path:
                raise ValueError("Full access SPACE requires a workspace path")
            return PreparedSpace(Path(policy.workspace_path).resolve(), artifact_root)
        if not policy.workspace_path:
            raise ValueError("Worktree SPACE requires a repository path")
        repository = Path(policy.workspace_path).resolve()
        working_root = run_root / "project"
        branch = f"team-run/{run_id}"
        _run_git(repository, "worktree", "add", "-b", branch, str(working_root))
        return PreparedSpace(working_root, artifact_root, branch)

    def cleanup(
        self,
        run_root: Path,
        policy: SpacePolicy | None,
        working_root: Path | None,
        branch: str | None,
    ) -> None:
        if policy and policy.write_mode == "worktree" and policy.workspace_path and working_root:
            repository = Path(policy.workspace_path).resolve()
            if working_root.exists():
                _run_git(repository, "worktree", "remove", "--force", str(working_root))
            if branch:
                _run_git(repository, "branch", "-D", branch)
        if run_root.exists():
            _clear_readonly(run_root)
            shutil.rmtree(run_root)


def policy_from_snapshot(value: dict[str, object] | None) -> SpacePolicy | None:
    if not value:
        return None
    return SpacePolicy(
        scope=str(value.get("scope") or "global"),
        scope_id=str(value.get("scope_id") or ""),
        read_mode=str(value.get("read_mode") or "home"),
        read_path=str(value["read_path"]) if value.get("read_path") else None,
        write_mode=str(value.get("write_mode") or "isolated"),
        workspace_path=(
            str(value["workspace_path"]) if value.get("workspace_path") else None
        ),
        created_at=str(value.get("created_at") or ""),
        updated_at=str(value.get("updated_at") or ""),
    )


def policy_json(policy: SpacePolicy) -> str:
    return json.dumps(policy.snapshot(), ensure_ascii=False, sort_keys=True)


def _required_path(value: str | Path | None, label: str) -> Path:
    if value is None or not str(value).strip():
        raise ValueError(f"{label} is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    return path


def _existing_directory(value: Path, label: str) -> Path:
    path = value.resolve()
    if not path.is_dir():
        raise ValueError(f"{label} must be an existing directory")
    return path


def _policy_from_row(row) -> SpacePolicy:
    return SpacePolicy(
        scope=row["scope"],
        scope_id=row["scope_id"],
        read_mode=row["read_mode"],
        read_path=row["read_path"],
        write_mode=row["write_mode"],
        workspace_path=row["workspace_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_git(repository: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(f"Git worktree command failed: {detail}")


def _clear_readonly(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            try:
                path.chmod(path.stat().st_mode | 0o200)
            except OSError:
                continue
