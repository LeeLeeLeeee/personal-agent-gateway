"""Drive one Team Run through the product's real execution path.

Different from evaluation/agent_radio/runner.py in the one way that matters
here. That harness takes the wired services off create_app and calls
TeamRuntime directly, and its docstring is explicit that the lifespan is
never entered -- so the cycle dispatcher, the cycle loop, the job worker and
the scheduler have never run while these features were exercised. This enters
the lifespan, so the queued path does the work: enqueue a run, a dispatcher
worker claims the request, creates and freezes the cycle, and the
orchestrator drives it.

Not HTTP. /api is OTP-gated and automating a login would mean handling the
operator's second factor; everything under test lives below that layer, so
this enters at the dispatcher instead and leaves the API for a human.

Storage and workspace are isolated from the operator's real data on purpose:
this creates personas, teams and runs, and they must not land next to real
work. Reads come from the pinned source export the evaluation already
stages, never from the live repository.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

STAGE_ROOT = Path.home() / "pag-eval" / "stage1"
SOURCE_EXPORT = Path.home() / "pag-eval" / "source" / "pag-b125268"

TERMINAL = {
    "completed",
    "completed_with_failures",
    "failed",
    "blocked",
    "canceled",
}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", required=True)
    parser.add_argument(
        "--read-path",
        default=str(SOURCE_EXPORT),
        help="what the run may read. A repository working tree is fine here "
        "and a pinned export is not required: this drives real work, not a "
        "measurement that has to be reproducible later.",
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--negotiation", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args(argv)

    read_path = Path(args.read_path).resolve()
    if not read_path.is_dir():
        print(f"error: read path is not a directory: {read_path}", file=sys.stderr)
        return 1

    # Set before load_config: every one of these is read from the environment.
    os.environ["AGENT_SESSION_DIR"] = str(STAGE_ROOT / "sessions")
    os.environ["AGENT_WORKSPACE_ROOT"] = str(STAGE_ROOT / "workspace")
    os.environ["AGENT_TEAM_CONCURRENT_WORKERS"] = "true"
    os.environ["AGENT_TEAM_PEER_MESSAGES"] = "true"
    (STAGE_ROOT / "sessions").mkdir(parents=True, exist_ok=True)
    (STAGE_ROOT / "workspace").mkdir(parents=True, exist_ok=True)

    from personal_agent_gateway.app import create_app
    from personal_agent_gateway.config import load_config

    config = load_config()
    app = create_app(config)
    state = app.state
    print(f"database:  {config.app_db_path}")
    print(f"workspace: {config.workspace_root}")
    print(f"reads:     {read_path}")
    print(
        "flags:     "
        f"peer_messages={config.team_peer_messages_enabled} "
        f"concurrent_workers={config.team_concurrent_workers_enabled} "
        f"negotiation={args.negotiation}"
    )

    async with app.router.lifespan_context(app):
        for name in (
            "job_worker",
            "scheduler_loop",
            "team_cycle_dispatcher",
            "team_cycle_loop",
        ):
            component = getattr(state, name)
            print(f"  {name}: alive={component.alive}")
            if not component.alive:
                print(f"error: {name} did not start", file=sys.stderr)
                return 1

        leader = state.persona_service.create_persona(
            "Stage1 Lead",
            "lead",
            "Plans the work and reports the answer.",
            [],
            [],
            default_backend="codex",
            default_model="gpt-5.6-luna",
            default_options={"effort": "high"},
        )
        members = [
            state.persona_service.create_persona(
                f"Stage1 Worker {index + 1}",
                "worker",
                "Carries out one assignment.",
                [],
                [],
                default_backend="codex",
                default_model="gpt-5.6-luna",
                default_options={"effort": "high"},
            )
            for index in range(args.workers)
        ]
        team = state.team_directory_service.create_team(
            "Stage1 Team",
            "All features on, driven through the dispatcher.",
            leader.id,
            [member.id for member in members],
        )
        # Snapshotted into the run at creation, so a later edit cannot change
        # what this run was allowed to do.
        state.space_policy_service.upsert(
            "team",
            team.id,
            read_mode="selected",
            read_path=str(read_path),
            write_mode="isolated",
            workspace_path=None,
        )
        run = state.team_run_service.create_team_run_from_team(
            state.team_directory_service,
            state.rule_set_service,
            team_id=team.id,
            goal=args.goal,
            run_mode="plan_and_execute",
            max_workers=args.workers,
            lifecycle_mode="continuous",
            execution_policy="triggered",
            plan_negotiation=args.negotiation,
        )
        print(f"run: {run.id}")

        # The one product entry point below HTTP: the API calls exactly this.
        state.team_cycle_service.enqueue_request(
            run.id,
            "manual",
            "stage1-1",
            args.goal,
            previous_cycle_id=None,
        )
        await state.team_cycle_dispatcher.enqueue_run(run.id)

        deadline = asyncio.get_running_loop().time() + args.timeout_seconds
        status = run.status
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(5)
            current = state.team_run_service.get_team_run(run.id)
            if current.status != status:
                status = current.status
                print(f"  status -> {status}")
            if status in TERMINAL:
                break
        else:
            print("error: run did not settle before the bound", file=sys.stderr)

        final = state.team_run_service.get_team_run(run.id)
        print(f"final: {final.status} | error: {final.error_message}")
        print(f"summary: {final.summary}")
        for task in state.team_run_service.list_tasks(final.id):
            print(f"  [{task.status}] {task.title}")
        return 0 if final.status in TERMINAL else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
