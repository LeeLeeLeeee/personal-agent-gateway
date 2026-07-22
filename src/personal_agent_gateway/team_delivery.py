import subprocess
from pathlib import Path
from uuid import uuid4

from personal_agent_gateway.teams import TeamRun


_MUTATION_BLOCKED_STATUSES = {"planning", "running", "summarizing", "waiting_for_user"}


class TeamRunDeliveryError(ValueError):
    pass


class TeamRunDeliveryService:
    def preview(self, run: TeamRun) -> dict[str, object]:
        try:
            context = _delivery_context(run)
        except TeamRunDeliveryError as exc:
            return {
                "available": False,
                "reason": str(exc),
                "can_commit": False,
                "can_apply": False,
            }

        source_files = _status(context["source"])
        target_files = _status(context["target"])
        pending_commits = _pending_commits(
            context["source"],
            context["target_head"],
            context["source_head"],
        )
        blocked_reasons: list[str] = []
        if run.status in _MUTATION_BLOCKED_STATUSES:
            blocked_reasons.append("Team Run is still active.")
        if source_files:
            blocked_reasons.append("Commit Team Run changes before applying.")
        if target_files:
            blocked_reasons.append("Target repository has uncommitted changes.")

        return {
            "available": True,
            "source": {
                "path": str(context["source"]),
                "branch": context["source_branch"],
                "head": context["source_head"],
            },
            "target": {
                "path": str(context["target"]),
                "branch": context["target_branch"],
                "head": context["target_head"],
                "dirty_files": target_files,
            },
            "uncommitted_files": source_files,
            "pending_commits": pending_commits,
            "blocked_reasons": blocked_reasons,
            "can_commit": bool(source_files)
            and run.status not in _MUTATION_BLOCKED_STATUSES,
            "can_apply": bool(pending_commits) and not blocked_reasons,
            "up_to_date": not source_files and not pending_commits,
        }

    def commit(self, run: TeamRun, message: str) -> dict[str, object]:
        if run.status in _MUTATION_BLOCKED_STATUSES:
            raise TeamRunDeliveryError("Team Run is still active")
        context = _delivery_context(run)
        if not _status(context["source"]):
            raise TeamRunDeliveryError("Team Run has no uncommitted changes")
        _git(context["source"], "add", "--all")
        _git(context["source"], "commit", "-m", message)
        return self.preview(run)

    def apply(self, run: TeamRun) -> dict[str, object]:
        preview = self.preview(run)
        if not preview.get("available"):
            raise TeamRunDeliveryError(str(preview.get("reason") or "Delivery unavailable"))
        if not preview.get("can_apply"):
            reasons = preview.get("blocked_reasons") or []
            if reasons:
                raise TeamRunDeliveryError(" ".join(str(reason) for reason in reasons))
            raise TeamRunDeliveryError("Team Run has no commits to apply")

        context = _delivery_context(run)
        commits = [str(item["sha"]) for item in preview["pending_commits"]]
        _preflight(run, context["target"], context["target_head"], commits)
        try:
            _git(context["target"], "cherry-pick", *commits)
        except TeamRunDeliveryError:
            _git(context["target"], "cherry-pick", "--abort", check=False)
            raise
        result = self.preview(run)
        result["applied_commits"] = commits
        result["result_head"] = _git(context["target"], "rev-parse", "HEAD")
        return result


def _delivery_context(run: TeamRun) -> dict[str, object]:
    policy = run.space_policy or {}
    if policy.get("write_mode") != "worktree":
        raise TeamRunDeliveryError("Delivery is available only for worktree Team Runs")
    configured_target = str(policy.get("workspace_path") or "").strip()
    if not configured_target:
        raise TeamRunDeliveryError("Worktree SPACE has no repository path")
    if not run.working_root:
        raise TeamRunDeliveryError("Team Run has no working root")

    source = _repository_root(Path(run.working_root).resolve())
    target = _repository_root(Path(configured_target).resolve())
    if source == target:
        raise TeamRunDeliveryError("Source and target worktrees must be different")
    if _common_git_dir(source) != _common_git_dir(target):
        raise TeamRunDeliveryError("Source and target do not belong to the same Git repository")

    source_branch = _branch(source, "source")
    target_branch = _branch(target, "target")
    if run.worktree_branch and source_branch != run.worktree_branch:
        raise TeamRunDeliveryError("Team Run worktree branch does not match its snapshot")
    return {
        "source": source,
        "target": target,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "source_head": _git(source, "rev-parse", "HEAD"),
        "target_head": _git(target, "rev-parse", "HEAD"),
    }


def _repository_root(path: Path) -> Path:
    if not path.is_dir():
        raise TeamRunDeliveryError(f"Git path is unavailable: {path}")
    return Path(_git(path, "rev-parse", "--show-toplevel")).resolve()


def _common_git_dir(path: Path) -> Path:
    value = Path(_git(path, "rev-parse", "--git-common-dir"))
    if not value.is_absolute():
        value = path / value
    return value.resolve()


def _branch(path: Path, label: str) -> str:
    branch = _git(path, "branch", "--show-current")
    if not branch:
        raise TeamRunDeliveryError(f"The {label} worktree is detached")
    return branch


def _status(path: Path) -> list[dict[str, str]]:
    lines = _git(path, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    return [
        {"status": line[:2].strip() or line[:2], "path": line[3:]}
        for line in lines
        if len(line) >= 4
    ]


def _pending_commits(source: Path, target_head: str, source_head: str) -> list[dict[str, str]]:
    unique = {
        line[2:].strip()
        for line in _git(source, "cherry", target_head, source_head).splitlines()
        if line.startswith("+")
    }
    ordered = _git(source, "rev-list", "--reverse", f"{target_head}..{source_head}").splitlines()
    return [
        {
            "sha": commit,
            "short_sha": commit[:8],
            "subject": _git(source, "show", "-s", "--format=%s", commit),
        }
        for commit in ordered
        if commit in unique
    ]


def _preflight(run: TeamRun, target: Path, target_head: str, commits: list[str]) -> None:
    run_root = Path(run.workspace_root).resolve()
    preview_root = run_root / f".delivery-preflight-{uuid4().hex}"
    if preview_root.parent != run_root:
        raise TeamRunDeliveryError("Invalid delivery preflight path")
    added = False
    try:
        _git(target, "worktree", "add", "--detach", str(preview_root), target_head)
        added = True
        _git(preview_root, "cherry-pick", *commits)
    except TeamRunDeliveryError as exc:
        if added:
            _git(preview_root, "cherry-pick", "--abort", check=False)
        raise TeamRunDeliveryError(f"Delivery conflicts with the target: {exc}") from exc
    finally:
        if added:
            _git(target, "worktree", "remove", "--force", str(preview_root), check=False)


def _git(path: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise TeamRunDeliveryError(detail or f"Git exited with status {result.returncode}")
    return result.stdout.strip()
