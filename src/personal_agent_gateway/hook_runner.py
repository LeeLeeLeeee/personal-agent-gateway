import asyncio

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hooks import HookService, render_prompt
from personal_agent_gateway.mail_knowledge import (
    MailKnowledgeService,
    MailWorkspaceProjector,
    build_mail_team_instruction,
)
from personal_agent_gateway.personas import persona_system_prompt
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory
from personal_agent_gateway.redaction import redact_text
from personal_agent_gateway.team_cycle_dispatcher import TeamCycleDispatcher
from personal_agent_gateway.team_cycles import TeamCycleRequest, TeamCycleService
from personal_agent_gateway.team_run_orchestrator import TeamRunOrchestrator
from personal_agent_gateway.teams import TeamRun, TeamRunCycle, TeamRunService


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
        self._interruption: str | None = None
        self._teams: TeamRunService | None = None
        self._team_cycles: TeamCycleService | None = None
        self._team_dispatcher: TeamCycleDispatcher | None = None
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
        orchestrator.add_observer(self.on_team_run_settled)

    def attach_team_cycle_queue(
        self,
        cycles: TeamCycleService,
        dispatcher: TeamCycleDispatcher,
    ) -> None:
        self._team_cycles = cycles
        self._team_dispatcher = dispatcher

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if not self.alive:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        await self._stop_loop("Gateway shutdown interrupted hook run")

    async def emergency_stop(self) -> list[str]:
        targets = [run.id for run in self._hook_runs.list_active_runs()]
        was_alive = self.alive
        message = "Emergency stop interrupted hook run"
        await self._stop_loop(message)
        self._drain_queue()
        for run_id in targets:
            self._interrupt_if_active(run_id, message)
        if was_alive:
            await self.start()
        return targets

    async def enqueue(self, run_id: str) -> None:
        await self._queue.put(run_id)

    async def run_one(self, run_id: str) -> None:
        run = self._hook_runs.get_run(run_id)
        if run.status not in {"queued", "running"}:
            return
        hook = self._hooks.get_hook(run.hook_id)
        if hook.target_kind == "team_run":
            try:
                await self._run_team_cycle(run_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = self._safe_error(run_id, exc)
                self._interrupt_linked_pair(
                    run_id,
                    message,
                )
                raise
            return
        self._hook_runs.mark_running(run_id)
        prompt = render_prompt(hook.prompt_template, run.trigger_payload)
        if hook.target_kind == "persona":
            runtime = self._runtime_factory.create_headless_runtime(
                hook.target_backend,
                hook.target_model,
                hook.target_options,
                hook_run_id=run_id,
                system_prompt=persona_system_prompt(hook.target_persona_snapshot),
            )
        else:
            runtime = self._runtime_factory.create_headless_runtime(
                hook.target_backend,
                hook.target_model,
                hook.target_options,
                hook_run_id=run_id,
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
        if (
            self._teams is None
            or self._team_cycles is None
            or self._team_dispatcher is None
        ):
            raise RuntimeError("Team Hook cycle queue is not attached")
        run = self._hook_runs.get_run(run_id)
        hook = self._hooks.get_hook(run.hook_id)
        if hook.target_team_run_id is None:
            raise ValueError("Team Hook target is missing")
        target = self._teams.get_team_run(hook.target_team_run_id)
        if (
            target.lifecycle_mode != "continuous"
            or target.run_mode != "plan_and_execute"
            or target.execution_policy != "triggered"
        ):
            raise ValueError(
                "Hook target must be a continuous plan_and_execute TRIGGERED Team Run"
            )
        previous = self._team_cycles.latest_settled_cycle(hook.target_team_run_id)
        request = self._team_cycles.enqueue_request(
            hook.target_team_run_id,
            "hook",
            run.id,
            render_prompt(hook.prompt_template, run.trigger_payload),
            previous_cycle_id=previous.id if previous is not None else None,
        )
        self._hook_runs.link_cycle_request(run.id, request.id)
        await self._event_bus.publish(
            {
                "type": "team.cycle_request.queued",
                "team_run_id": hook.target_team_run_id,
                "cycle_request_id": request.id,
                "source_type": "hook",
            }
        )
        await self._team_dispatcher.enqueue_run(hook.target_team_run_id)

    async def prepare_team_cycle(
        self,
        request: TeamCycleRequest,
        cycle: TeamRunCycle,
    ) -> str | None:
        if request.source_type != "hook":
            return None
        if self._teams is None:
            raise RuntimeError("Team Hook runtime is not attached")
        hook_run = self._hook_runs.get_run(request.source_id)
        hook_run = self._hook_runs.link_cycle(hook_run.id, cycle.id)
        hook = self._hooks.get_hook(hook_run.hook_id)
        if hook.source_type != "email":
            return request.instruction
        try:
            if self._mail_knowledge is None or self._mail_projector is None:
                raise RuntimeError("Email Team Hook mail knowledge is not attached")
            message = self._mail_knowledge.ingest_hook_run(
                hook,
                hook_run,
                self._teams.get_team_run(cycle.team_run_id),
                cycle.id,
            )
            projected = self._mail_projector.project_safely(message)
            if projected.projection_status != "projected":
                raise RuntimeError("Email Team Hook context projection failed")
            return build_mail_team_instruction(projected, hook.prompt_template)
        except Exception as exc:
            self._hook_runs.mark_failed(hook_run.id, str(exc))
            raise

    async def on_team_run_settled(
        self, _team_run: TeamRun, cycle_id: str | None
    ) -> None:
        if cycle_id is None or self._teams is None:
            return
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.request_id is None:
            return
        run = self._hook_runs.get_run_for_cycle_request(cycle.request_id)
        if run is None:
            return
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

    def reconcile_linked_runs(self) -> None:
        if self._teams is None:
            return
        for cycle in self._teams.list_source_cycles("hook"):
            try:
                run = self._hook_runs.get_run(cycle.source_id)
            except KeyError:
                continue
            if cycle.request_id is not None and run.team_cycle_request_id is None:
                run = self._hook_runs.link_cycle_request(run.id, cycle.request_id)
            if run.team_run_cycle_id is None:
                run = self._hook_runs.link_cycle(run.id, cycle.id)
            if run.team_run_cycle_id != cycle.id:
                continue
            self._reconcile_pair(run.id, cycle.id)

    async def _run_loop(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                await self.run_one(run_id)
            except asyncio.CancelledError:
                self._fail_if_active(
                    run_id,
                    self._interruption or "Gateway shutdown interrupted hook run",
                )
                raise
            except Exception as exc:
                message = self._safe_error(run_id, exc)
                self._last_error = message
                self._fail_if_active(run_id, message)
                await self._publish_safe(run_id)
            finally:
                self._queue.task_done()

    async def _stop_loop(self, message: str) -> None:
        if self._task is None:
            return
        task = self._task
        self._task = None
        self._interruption = message
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._interruption = None

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
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

    def _interrupt_if_active(self, run_id: str, message: str) -> None:
        try:
            run = self._hook_runs.get_run(run_id)
            if run.status in {"queued", "running"}:
                self._hook_runs.mark_interrupted(run_id, message)
        except KeyError:
            return

    def _interrupt_linked_pair(self, run_id: str, message: str) -> None:
        try:
            run = self._hook_runs.get_run(run_id)
        except KeyError:
            return
        cycle = None
        if self._teams is not None:
            if run.team_run_cycle_id is not None:
                try:
                    cycle = self._teams.get_cycle(run.team_run_cycle_id)
                except KeyError:
                    cycle = None
            if cycle is None:
                cycle = self._teams.get_cycle_for_source("hook", run.id)
                if cycle is not None and run.team_run_cycle_id is None:
                    try:
                        run = self._hook_runs.link_cycle(run.id, cycle.id)
                    except (KeyError, ValueError):
                        pass
            if cycle is not None and cycle.status in {"queued", "running"}:
                self._teams.set_cycle_status(
                    cycle.id,
                    "interrupted",
                    error_message=message,
                )
        if run.status in {"queued", "running"}:
            self._hook_runs.mark_interrupted(run.id, message)

    def _reconcile_pair(self, run_id: str, cycle_id: str) -> None:
        if self._teams is None:
            return
        run = self._hook_runs.get_run(run_id)
        cycle = self._teams.get_cycle(cycle_id)
        if cycle.status in {"completed", "completed_with_failures"}:
            if run.status != "succeeded":
                self._hook_runs.mark_succeeded(run.id, cycle.summary or "")
            return
        if cycle.status == "waiting_for_user":
            if run.status != "waiting_for_user":
                self._hook_runs.mark_waiting_for_user(run.id)
            return
        if cycle.status == "interrupted":
            if run.status != "interrupted":
                self._hook_runs.mark_interrupted(
                    run.id,
                    cycle.error_message or "Team Run Cycle interrupted",
                )
            return
        if cycle.status in {"failed", "canceled"}:
            if run.status != "failed":
                self._hook_runs.mark_failed(
                    run.id,
                    cycle.error_message or f"Team Run Cycle {cycle.status}",
                )
            return
        if run.status in {"failed", "interrupted", "succeeded"}:
            self._teams.set_cycle_status(
                cycle.id,
                "interrupted",
                error_message=run.error_message or "Hook Run and Team Run Cycle diverged",
            )
            if run.status != "interrupted":
                self._hook_runs.mark_interrupted(
                    run.id,
                    run.error_message or "Hook Run and Team Run Cycle diverged",
                )
            return
        if cycle.status == "running" and run.status == "queued":
            self._hook_runs.mark_running(run.id)

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

    def _safe_error(self, run_id: str, value: object) -> str:
        try:
            run = self._hook_runs.get_run(run_id)
            return self._hooks.redact_error(run.hook_id, value) or type(value).__name__
        except KeyError:
            return redact_text(value) or type(value).__name__
