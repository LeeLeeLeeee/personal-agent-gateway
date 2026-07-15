import asyncio

from personal_agent_gateway.events import EventBus
from personal_agent_gateway.hook_runs import HookRunService
from personal_agent_gateway.hooks import HookService, render_prompt
from personal_agent_gateway.runtime_factory import AgentRuntimeFactory


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
        hook = self._hooks.get_hook(run.hook_id)
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
