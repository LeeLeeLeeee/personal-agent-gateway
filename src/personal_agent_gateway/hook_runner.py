import asyncio

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hooks import HookService, render_prompt
from personal_agent_gateway.mail_knowledge import (
    MailKnowledgeService,
    MailMessage,
    MailWorkspaceProjector,
    build_mail_team_instruction,
)
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator
from personal_agent_gateway.teams import TeamRun, TeamRunService


class HookRunner:
    def __init__(
        self,
        hooks: HookService,
        hook_runs: HookRunService,
        runtime_factory: AgentRuntimeFactory,
        event_bus: EventBus,
    ) -> None:
        self._hooks = hooks
        self._hook_runs = hook_runs
        self._runtime_factory = runtime_factory
        self._event_bus = event_bus
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None
        self._teams: TeamRunService | None = None
        self._team_orchestrator: TeamRunOrchestrator | None = None
        self._mail_knowledge: MailKnowledgeService | None = None
        self._mail_projector: MailWorkspaceProjector | None = None

    def attach_mail_knowledge(
        self,
        knowledge: MailKnowledgeService,
        projector: MailWorkspaceProjector,
    ) -> None:
        self._mail_knowledge = knowledge
        self._mail_projector = projector

    def attach_team_runtime(
        self,
        teams: TeamRunService,
        orchestrator: TeamRunOrchestrator,
    ) -> None:
        self._teams = teams
        self._team_orchestrator = orchestrator
        orchestrator.add_observer(self.on_team_run_settled)

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self.alive:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def enqueue(self, run_id: str) -> None:
        await self._queue.put(run_id)

    async def run_one(self, run_id: str) -> None:
        run = self._hook_runs.get_run(run_id)
        if run.status not in {"queued", "running"}:
            return
        hook = self._hooks.get_hook(run.hook_id)
        if hook.target_kind == "team_run":
            await self._run_team_cycle(run_id)
            return
        self._hook_runs.mark_running(run_id)
        prompt = render_prompt(hook.prompt_template, run.trigger_payload)
        runtime = self._runtime_factory.create_headless_runtime(
            hook.target_backend, hook.target_model, hook.target_options
        )
        result = await runtime.handle_user_message(prompt)
        if result.pending_approval is not None:
            self._hook_runs.mark_failed(
                run_id,
                "Agent turn paused awaiting tool approval; hook runs cannot approve tool calls.",
            )
            status = "failed"
        else:
            response_text = "\n".join(
                str(message["content"])
                for message in result.messages
                if message.get("content")
            )
            self._hook_runs.mark_succeeded(run_id, response_text)
            status = "succeeded"
        await self._publish(run.hook_id, run_id, status)

    async def _run_team_cycle(self, run_id: str) -> None:
        if self._teams is None or self._team_orchestrator is None:
            raise RuntimeError("Team Hook runtime is not attached")
        run = self._hook_runs.get_run(run_id)
        hook = self._hooks.get_hook(run.hook_id)
        if hook.target_team_run_id is None:
            raise ValueError("Team Hook target is missing")
        if run.team_run_cycle_id is None:
            cycle = self._teams.create_cycle(
                hook.target_team_run_id, "hook", run.id
            )
            run = self._hook_runs.link_cycle(run.id, cycle.id)
        cycle = self._teams.get_cycle(run.team_run_cycle_id)
        mail_message: MailMessage | None = None
        if hook.source_type == "email":
            if self._mail_knowledge is None or self._mail_projector is None:
                raise RuntimeError("Email Team Hook mail knowledge is not attached")
            mail_message = self._mail_knowledge.ingest_hook_run(
                hook,
                run,
                self._teams.get_team_run(cycle.team_run_id),
                cycle.id,
            )
            mail_message = self._mail_projector.project_safely(mail_message)
            if mail_message.projection_status != "projected":
                raise RuntimeError("Email Team Hook context projection failed")
        blockers = [
            candidate
            for candidate in self._teams.list_cycles(cycle.team_run_id)
            if candidate.sequence < cycle.sequence
            and candidate.status
            not in {"completed", "completed_with_failures", "failed", "canceled"}
        ]
        if blockers or self._team_orchestrator.is_running(cycle.team_run_id):
            await self._publish(run.hook_id, run.id, "queued")
            return
        if cycle.status == "waiting_for_user":
            self._hook_runs.mark_waiting_for_user(run.id)
            await self._publish(run.hook_id, run.id, "waiting_for_user")
            return
        if cycle.status == "interrupted":
            self._hook_runs.mark_interrupted(
                run.id, "Team Run Cycle requires explicit resume"
            )
            await self._publish(run.hook_id, run.id, "interrupted")
            return

        self._hook_runs.mark_running(run.id)
        instruction = (
            build_mail_team_instruction(mail_message, hook.prompt_template)
            if mail_message is not None
            else render_prompt(hook.prompt_template, run.trigger_payload)
        )
        await self._team_orchestrator.run_cycle(
            cycle.team_run_id, cycle.id, instruction
        )

    async def on_team_run_settled(
        self, _team_run: TeamRun, cycle_id: str | None
    ) -> None:
        if cycle_id is None or self._teams is None:
            return
        run = self._hook_runs.get_run_for_cycle(cycle_id)
        if run is None:
            return
        cycle = self._teams.get_cycle(cycle_id)
        if (
            self._mail_knowledge is not None
            and self._mail_projector is not None
            and cycle.status
            in {"completed", "completed_with_failures", "failed", "canceled"}
        ):
            message = self._mail_knowledge.complete_cycle(
                cycle.id, cycle.summary or cycle.error_message or ""
            )
            if message is not None:
                self._mail_projector.project_safely(message)
        if cycle.status in {"completed", "completed_with_failures"}:
            self._hook_runs.mark_succeeded(run.id, cycle.summary or "")
            status = "succeeded"
        elif cycle.status == "waiting_for_user":
            self._hook_runs.mark_waiting_for_user(run.id)
            status = "waiting_for_user"
        elif cycle.status == "interrupted":
            self._hook_runs.mark_interrupted(
                run.id, cycle.error_message or "Team Run Cycle interrupted"
            )
            status = "interrupted"
        elif cycle.status in {"failed", "canceled"}:
            self._hook_runs.mark_failed(
                run.id, cycle.error_message or f"Team Run Cycle {cycle.status}"
            )
            status = "failed"
        else:
            return
        await self._publish(run.hook_id, run.id, status)
        if cycle.status in {
            "completed",
            "completed_with_failures",
            "failed",
            "canceled",
        }:
            queued = self._hook_runs.next_queued_for_team_run(cycle.team_run_id)
            if queued is not None:
                await self.enqueue(queued.id)

    async def _run_loop(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                await self.run_one(run_id)
            except asyncio.CancelledError:
                self._fail_if_active(run_id, "Gateway shutdown interrupted hook run")
                raise
            except Exception as exc:
                message = str(exc)[:2000] or type(exc).__name__
                self._last_error = message
                self._fail_if_active(run_id, message)
                await self._publish_safe(run_id)
            finally:
                self._queue.task_done()

    def _fail_if_active(self, run_id: str, message: str) -> None:
        try:
            run = self._hook_runs.get_run(run_id)
            if run.status in {"queued", "running"}:
                if run.team_run_cycle_id is not None:
                    self._hook_runs.mark_interrupted(run_id, message)
                else:
                    self._hook_runs.mark_failed(run_id, message)
        except KeyError:
            return

    async def _publish(self, hook_id: str, run_id: str, status: str) -> None:
        await self._event_bus.publish(
            {"type": "hook.run.updated", "hook_id": hook_id, "run_id": run_id, "status": status}
        )

    async def _publish_safe(self, run_id: str) -> None:
        try:
            run = self._hook_runs.get_run(run_id)
        except KeyError:
            return
        await self._publish(run.hook_id, run_id, run.status)
