import json
import re
import subprocess
from pathlib import Path
from typing import Literal
from uuid import uuid4

from personal_agent_gateway.teams import TeamRun


_MUTATION_BLOCKED_STATUSES = {"planning", "running", "summarizing", "waiting_for_user"}
_SESSION_FILE = ".delivery-session.json"
_APPLIED_FILE = ".delivery-applied.json"
_INTEGRATION_PREFIX = ".delivery-integration-"
_MAX_MANUAL_BYTES = 1_000_000
_REPORT_TIMESTAMP = re.compile(r"—\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")


class TeamRunDeliveryError(ValueError):
    pass


class TeamRunDeliveryService:
    def preview(self, run: TeamRun) -> dict[str, object]:
        try:
            context = _delivery_context(run)
            session = _load_session(run)
        except TeamRunDeliveryError as exc:
            return {
                "available": False,
                "reason": str(exc),
                "can_commit": False,
                "can_apply": False,
            }

        source_files = _status(context["source"])
        target_files = _status(context["target"])
        applied_source_commits = _applied_source_commits(
            run,
            context["target"],
            context["target_head"],
        )
        pending_commits = _pending_commits(
            context["source"],
            context["target_head"],
            context["source_head"],
            applied_source_commits,
        )
        blocked_reasons: list[str] = []
        if run.status in _MUTATION_BLOCKED_STATUSES:
            blocked_reasons.append("Team Run is still active.")
        if source_files:
            blocked_reasons.append("Commit Team Run changes before applying.")
        if target_files:
            blocked_reasons.append("Target repository has uncommitted changes.")
        if session is not None:
            blocked_reasons.append("Resolve repository conflicts before applying.")

        conflict_session = (
            _session_payload(session, context["target_head"], target_files)
            if session is not None
            else None
        )
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
            "conflict_session": conflict_session,
            "can_commit": bool(source_files)
            and session is None
            and run.status not in _MUTATION_BLOCKED_STATUSES,
            "can_apply": bool(pending_commits)
            and session is None
            and not blocked_reasons,
            "up_to_date": session is None and not source_files and not pending_commits,
        }

    def commit(self, run: TeamRun, message: str) -> dict[str, object]:
        _ensure_mutation_allowed(run)
        if _load_session(run) is not None:
            raise TeamRunDeliveryError("Resolve or cancel repository conflicts first")
        context = _delivery_context(run)
        if not _status(context["source"]):
            raise TeamRunDeliveryError("Team Run has no uncommitted changes")
        _git(context["source"], "add", "--all")
        _git(context["source"], "commit", "-m", message)
        return self.preview(run)

    def apply(self, run: TeamRun) -> dict[str, object]:
        _ensure_mutation_allowed(run)
        if _load_session(run) is not None:
            raise TeamRunDeliveryError("Resolve or cancel repository conflicts first")
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
        session = _create_session(run, context, commits)
        integration = Path(str(session["integration_path"]))
        try:
            _git(integration, "cherry-pick", *commits)
        except TeamRunDeliveryError as exc:
            conflicts = _collect_conflicts(integration)
            if conflicts:
                session["status"] = "conflicted"
                session["files"] = conflicts
                _auto_resolve_conflicts(run, session)
                _write_session(run, session)
                if all(item.get("resolution") for item in conflicts):
                    return self._continue_session(run, session)
                return self.preview(run)
            _cleanup_session(run, session)
            raise TeamRunDeliveryError(f"Delivery integration failed: {exc}") from exc
        return _finalize_session(run, session)

    def resolve(
        self,
        run: TeamRun,
        conflict_id: str,
        mode: Literal["target", "team", "manual"],
        content: str | None = None,
    ) -> dict[str, object]:
        _ensure_mutation_allowed(run)
        session = _require_session(run)
        conflict = next(
            (
                item
                for item in session.get("files", [])
                if item.get("id") == conflict_id
            ),
            None,
        )
        if conflict is None:
            raise TeamRunDeliveryError("Delivery conflict not found")

        integration = _integration_path(run, session)
        _resolve_conflict(integration, conflict, mode, content)
        _write_session(run, session)
        return self.preview(run)

    def continue_apply(self, run: TeamRun) -> dict[str, object]:
        _ensure_mutation_allowed(run)
        session = _require_session(run)
        files = session.get("files", [])
        if not files or any(not item.get("resolution") for item in files):
            raise TeamRunDeliveryError("Resolve every repository conflict first")

        return self._continue_session(run, session)

    def cancel(self, run: TeamRun) -> dict[str, object]:
        session = _require_session(run)
        integration = _integration_path(run, session)
        _git(integration, "cherry-pick", "--abort", check=False)
        _cleanup_session(run, session)
        return self.preview(run)

    def _continue_session(
        self,
        run: TeamRun,
        session: dict[str, object],
    ) -> dict[str, object]:
        integration = _integration_path(run, session)
        while True:
            try:
                _git(
                    integration,
                    "-c",
                    "core.editor=true",
                    "cherry-pick",
                    "--continue",
                )
            except TeamRunDeliveryError as exc:
                conflicts = _collect_conflicts(integration)
                if not conflicts:
                    raise TeamRunDeliveryError(
                        f"Delivery could not continue: {exc}"
                    ) from exc
                session["files"] = conflicts
                _auto_resolve_conflicts(run, session)
                _write_session(run, session)
                if all(item.get("resolution") for item in conflicts):
                    continue
                return self.preview(run)
            return _finalize_session(run, session)


def _ensure_mutation_allowed(run: TeamRun) -> None:
    if run.status in _MUTATION_BLOCKED_STATUSES:
        raise TeamRunDeliveryError("Team Run is still active")


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


def _pending_commits(
    source: Path,
    target_head: str,
    source_head: str,
    applied_source_commits: set[str] | None = None,
) -> list[dict[str, str]]:
    applied_source_commits = applied_source_commits or set()
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
        if commit in unique and commit not in applied_source_commits
    ]


def _session_file(run: TeamRun) -> Path:
    run_root = Path(run.workspace_root).resolve()
    return run_root / _SESSION_FILE


def _applied_file(run: TeamRun) -> Path:
    return Path(run.workspace_root).resolve() / _APPLIED_FILE


def _applied_source_commits(
    run: TeamRun,
    target: Path,
    target_head: str,
) -> set[str]:
    path = _applied_file(run)
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TeamRunDeliveryError("Delivery applied history is unreadable") from exc
    batches = payload.get("batches", []) if isinstance(payload, dict) else []
    applied: set[str] = set()
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        result_head = str(batch.get("result_head") or "")
        if not result_head or not _git_success(
            target,
            "merge-base",
            "--is-ancestor",
            result_head,
            target_head,
        ):
            continue
        applied.update(str(commit) for commit in batch.get("source_commits", []))
    return applied


def _record_applied(
    run: TeamRun,
    source_commits: list[str],
    result_head: str,
) -> None:
    path = _applied_file(run)
    payload: dict[str, object] = {"batches": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TeamRunDeliveryError("Delivery applied history is unreadable") from exc
        if isinstance(loaded, dict) and isinstance(loaded.get("batches"), list):
            payload = loaded
    payload["batches"].append(
        {"source_commits": source_commits, "result_head": result_head}
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_session(run: TeamRun) -> dict[str, object] | None:
    path = _session_file(run)
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TeamRunDeliveryError("Delivery conflict session is unreadable") from exc
    if not isinstance(session, dict):
        raise TeamRunDeliveryError("Delivery conflict session is invalid")
    _integration_path(run, session)
    return session


def _require_session(run: TeamRun) -> dict[str, object]:
    session = _load_session(run)
    if session is None:
        raise TeamRunDeliveryError("Delivery has no active conflict session")
    return session


def _write_session(run: TeamRun, session: dict[str, object]) -> None:
    _session_file(run).write_text(
        json.dumps(session, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _integration_path(run: TeamRun, session: dict[str, object]) -> Path:
    run_root = Path(run.workspace_root).resolve()
    integration = Path(str(session.get("integration_path") or "")).resolve()
    if integration.parent != run_root or not integration.name.startswith(_INTEGRATION_PREFIX):
        raise TeamRunDeliveryError("Delivery integration path is invalid")
    return integration


def _create_session(
    run: TeamRun,
    context: dict[str, object],
    commits: list[str],
) -> dict[str, object]:
    run_root = Path(run.workspace_root).resolve()
    session_id = uuid4().hex
    integration = run_root / f"{_INTEGRATION_PREFIX}{session_id}"
    session: dict[str, object] = {
        "id": session_id,
        "status": "integrating",
        "integration_path": str(integration),
        "source_head": context["source_head"],
        "target_path": str(context["target"]),
        "target_branch": context["target_branch"],
        "target_head": context["target_head"],
        "commits": commits,
        "auto_resolved_files": [],
        "files": [],
    }
    _git(
        context["target"],
        "worktree",
        "add",
        "--detach",
        str(integration),
        str(context["target_head"]),
    )
    _write_session(run, session)
    return session


def _collect_conflicts(integration: Path) -> list[dict[str, object]]:
    paths = _git(
        integration,
        "diff",
        "--name-only",
        "--diff-filter=U",
    ).splitlines()
    conflicts: list[dict[str, object]] = []
    for path in paths:
        stages: dict[int, str] = {}
        for line in _git(integration, "ls-files", "-u", "--", path).splitlines():
            metadata, _name = line.split("\t", 1)
            _mode, blob, stage = metadata.split()
            stages[int(stage)] = blob
        conflicts.append(
            {
                "id": uuid4().hex,
                "path": path,
                "base_blob": stages.get(1),
                "target_blob": stages.get(2),
                "team_blob": stages.get(3),
                "resolution": None,
            }
        )
    return conflicts


def _auto_resolve_conflicts(
    run: TeamRun,
    session: dict[str, object],
) -> None:
    integration = _integration_path(run, session)
    resolved_files = session.setdefault("auto_resolved_files", [])
    candidates: dict[str, tuple[dict[str, object], str, str]] = {}
    for conflict in session.get("files", []):
        if not isinstance(conflict, dict) or conflict.get("resolution"):
            continue
        target_content, target_text = _blob_text(
            integration,
            conflict.get("target_blob"),
        )
        team_content, team_text = _blob_text(
            integration,
            conflict.get("team_blob"),
        )
        if not target_text or not team_text:
            continue
        path = str(conflict.get("path") or "").replace("\\", "/")
        if target_content is not None and team_content is not None:
            candidates[path] = (conflict, target_content, team_content)

    merged_contents: dict[str, str] = {}
    for path in (
        "docs/component-inspector/index.md",
        "docs/registry.json",
    ):
        candidate = candidates.get(path)
        if candidate is None:
            continue
        conflict, target_content, team_content = candidate
        merged = _merge_generated_content(
            path,
            target_content,
            team_content,
            merged_contents.get("docs/component-inspector/index.md"),
        )
        if merged is None:
            continue
        _resolve_conflict(integration, conflict, "auto", merged)
        merged_contents[path] = merged
        if conflict["path"] not in resolved_files:
            resolved_files.append(conflict["path"])


def _merge_generated_content(
    path: str,
    target_content: str | None,
    team_content: str | None,
    merged_component_index: str | None = None,
) -> str | None:
    normalized = path.replace("\\", "/")
    if target_content is None or team_content is None:
        return None
    if normalized == "docs/component-inspector/index.md":
        return _merge_component_report_index(target_content, team_content)
    if normalized == "docs/registry.json":
        return _merge_docs_registry(
            target_content,
            team_content,
            merged_component_index,
        )
    return None


def _merge_component_report_index(target_content: str, team_content: str) -> str:
    target_lines = target_content.splitlines()
    target_bullets = [line for line in target_lines if line.startswith("- [")]
    team_bullets = [line for line in team_content.splitlines() if line.startswith("- [")]
    bullets = list(dict.fromkeys([*target_bullets, *team_bullets]))
    bullets.sort(key=_report_line_sort_key, reverse=True)

    bullet_indexes = [
        index for index, line in enumerate(target_lines) if line.startswith("- [")
    ]
    if not bullet_indexes:
        return target_content
    first = bullet_indexes[0]
    last = bullet_indexes[-1]
    merged = [*target_lines[:first], *bullets, *target_lines[last + 1 :]]
    return "\n".join(merged) + "\n"


def _report_line_sort_key(line: str) -> tuple[str, str]:
    match = _REPORT_TIMESTAMP.search(line)
    return (match.group(1) if match else "", line)


def _merge_docs_registry(
    target_content: str,
    team_content: str,
    merged_component_index: str | None = None,
) -> str | None:
    try:
        target = json.loads(target_content)
        team = json.loads(team_content)
    except json.JSONDecodeError:
        return None
    if not isinstance(target, dict) or not isinstance(team, dict):
        return None
    target_documents = target.get("documents")
    team_documents = team.get("documents")
    if not isinstance(target_documents, list) or not isinstance(team_documents, list):
        return None

    documents_by_path: dict[str, dict[str, object]] = {}
    for document in [*team_documents, *target_documents]:
        if not isinstance(document, dict):
            return None
        document_path = document.get("path")
        if not isinstance(document_path, str) or not document_path:
            return None
        documents_by_path[document_path] = document
    documents = [documents_by_path[path] for path in sorted(documents_by_path)]
    if merged_component_index is not None:
        index_document = documents_by_path.get("docs/component-inspector/index.md")
        if index_document is not None:
            index_document = {
                **index_document,
                "excerpt": _markdown_excerpt(merged_component_index),
            }
            documents_by_path["docs/component-inspector/index.md"] = index_document
            documents = [documents_by_path[path] for path in sorted(documents_by_path)]
    merged = {**target, "document_count": len(documents), "documents": documents}
    return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"


def _markdown_excerpt(markdown: str) -> str:
    body = markdown
    if markdown.startswith("---"):
        marker = markdown.find("\n---", 3)
        if marker >= 0:
            body = markdown[marker + 4 :]
    for block in re.split(r"\r?\n\r?\n", body):
        paragraph = " ".join(block.splitlines()).strip()
        if paragraph and not paragraph.startswith(("#", "|")):
            return paragraph if len(paragraph) <= 220 else f"{paragraph[:217]}..."
    return ""


def _resolve_conflict(
    integration: Path,
    conflict: dict[str, object],
    mode: Literal["target", "team", "manual", "auto"],
    content: str | None,
) -> None:
    file_path = _conflict_path(integration, str(conflict["path"]))
    if mode in {"manual", "auto"}:
        if content is None:
            raise TeamRunDeliveryError("Text resolution requires content")
        if len(content.encode("utf-8")) > _MAX_MANUAL_BYTES:
            raise TeamRunDeliveryError("Text resolution is too large")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    else:
        blob = conflict.get(f"{mode}_blob")
        if blob:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(
                _git_bytes(integration, "cat-file", "blob", str(blob))
            )
        elif file_path.exists():
            file_path.unlink()

    if file_path.exists():
        _git(integration, "add", "--", str(conflict["path"]))
    else:
        _git(
            integration,
            "rm",
            "--ignore-unmatch",
            "--",
            str(conflict["path"]),
        )
    conflict["resolution"] = mode


def _session_payload(
    session: dict[str, object],
    current_target_head: str,
    target_files: list[dict[str, str]],
) -> dict[str, object]:
    integration = Path(str(session["integration_path"]))
    files = [
        _conflict_payload(integration, item)
        for item in session.get("files", [])
        if isinstance(item, dict)
    ]
    resolved_count = sum(bool(item.get("resolved")) for item in files)
    target_changed = current_target_head != session.get("target_head")
    return {
        "id": session["id"],
        "status": session["status"],
        "target_head": session["target_head"],
        "target_changed": target_changed,
        "files": files,
        "resolved_count": resolved_count,
        "total_count": len(files),
        "can_continue": bool(files)
        and resolved_count == len(files)
        and not target_changed
        and not target_files,
    }


def _conflict_payload(
    integration: Path,
    conflict: dict[str, object],
) -> dict[str, object]:
    target_content, target_text = _blob_text(integration, conflict.get("target_blob"))
    team_content, team_text = _blob_text(integration, conflict.get("team_blob"))
    file_path = _conflict_path(integration, str(conflict["path"]))
    working_content, working_text = _file_text(file_path)
    return {
        "id": conflict["id"],
        "path": conflict["path"],
        "resolved": bool(conflict.get("resolution")),
        "resolution": conflict.get("resolution"),
        "target_deleted": conflict.get("target_blob") is None,
        "team_deleted": conflict.get("team_blob") is None,
        "target_content": target_content,
        "team_content": team_content,
        "working_content": working_content,
        "manual_allowed": target_text and team_text and working_text,
    }


def _blob_text(integration: Path, blob: object) -> tuple[str | None, bool]:
    if blob is None:
        return None, True
    data = _git_bytes(integration, "cat-file", "blob", str(blob))
    return _decode_manual_text(data)


def _file_text(path: Path) -> tuple[str | None, bool]:
    if not path.exists():
        return None, True
    return _decode_manual_text(path.read_bytes())


def _decode_manual_text(data: bytes) -> tuple[str | None, bool]:
    if len(data) > _MAX_MANUAL_BYTES or b"\x00" in data:
        return None, False
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return None, False


def _conflict_path(integration: Path, git_path: str) -> Path:
    candidate = (integration / git_path).resolve()
    try:
        candidate.relative_to(integration.resolve())
    except ValueError as exc:
        raise TeamRunDeliveryError("Delivery conflict path is invalid") from exc
    return candidate


def _finalize_session(run: TeamRun, session: dict[str, object]) -> dict[str, object]:
    context = _delivery_context(run)
    if str(context["target"]) != str(session["target_path"]):
        raise TeamRunDeliveryError("Delivery target changed; cancel and apply again")
    if context["target_head"] != session["target_head"]:
        raise TeamRunDeliveryError("Delivery target HEAD changed; cancel and apply again")
    if _status(context["target"]):
        raise TeamRunDeliveryError("Target repository has uncommitted changes")

    integration = _integration_path(run, session)
    result_head = _git(integration, "rev-parse", "HEAD")
    _git(context["target"], "merge", "--ff-only", result_head)
    commits = [str(commit) for commit in session.get("commits", [])]
    _record_applied(run, commits, result_head)
    _cleanup_session(run, session)
    result = TeamRunDeliveryService().preview(run)
    result["applied_commits"] = commits
    result["auto_resolved_files"] = list(session.get("auto_resolved_files", []))
    result["result_head"] = _git(context["target"], "rev-parse", "HEAD")
    return result


def _cleanup_session(run: TeamRun, session: dict[str, object]) -> None:
    integration = _integration_path(run, session)
    target = Path(str(session["target_path"])).resolve()
    _git(target, "worktree", "remove", "--force", str(integration), check=False)
    _git(target, "worktree", "prune", check=False)
    _session_file(run).unlink(missing_ok=True)


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


def _git_bytes(path: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise TeamRunDeliveryError(detail or f"Git exited with status {result.returncode}")
    return result.stdout


def _git_success(path: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        timeout=60,
        check=False,
    ).returncode == 0
