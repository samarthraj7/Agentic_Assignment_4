"""The node's A2A server: receives a JobManifest as a data part, enforces the grant, runs the sandbox,
streams METRIC events as `working` status updates, and returns the RunResult as an artifact."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date

from a2a.helpers.proto_helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCard, TaskState
from pydantic import ValidationError
from starlette.applications import Starlette

from node.executor import sandbox
from node.policy.grants import check_grant
from shared.schemas import Grant, JobManifest, RunResult

RPC_PATH = "/a2a"
ReportFn = Callable[[RunResult], Awaitable[None]]


class MeshNodeExecutor(AgentExecutor):
    def __init__(self, node_id: str, grant: Grant, report: ReportFn | None = None):
        self.node_id, self.grant, self.report = node_id, grant, report
        self._day, self._jobs_today = date.today(), 0

    def _count_job(self) -> int:
        if date.today() != self._day:
            self._day, self._jobs_today = date.today(), 0
        return self._jobs_today

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        async def say(state: TaskState, text: str, data: dict | None = None) -> None:
            parts = [new_text_part(text)] + ([new_data_part(data)] if data else [])
            await updater.update_status(state, updater.new_agent_message(parts))

        payloads = get_data_parts(context.message.parts) if context.message else []
        try:
            job = JobManifest.model_validate(payloads[0])
        except (IndexError, ValidationError) as e:
            await say(TaskState.TASK_STATE_REJECTED, f"invalid JobManifest: {e}")
            return

        allowed, reason = check_grant(job, self.grant, self._count_job())
        if not allowed:
            result = RunResult(job_id=job.job_id, node_id=self.node_id, status="rejected", reason=reason)
            await self._report(result)
            await say(TaskState.TASK_STATE_REJECTED, f"rejected by owner grant: {reason}", result.model_dump())
            return
        self._jobs_today += 1

        await say(TaskState.TASK_STATE_WORKING, f"job {job.job_id} accepted on node {self.node_id}")
        loop = asyncio.get_running_loop()
        pending = []

        def on_metric(m):  # called from the sandbox thread
            pending.append(asyncio.run_coroutine_threadsafe(
                say(TaskState.TASK_STATE_WORKING, "metric", {"metric": m.model_dump()}), loop))

        result = await asyncio.to_thread(sandbox.run_job, job, self.node_id, on_metric)
        await asyncio.gather(*(asyncio.wrap_future(f) for f in pending))  # metrics before terminal event
        await self._report(result)
        await updater.add_artifact([new_data_part(result.model_dump())], name="run_result")
        if result.status == "completed":
            await updater.complete()
        elif result.status == "rejected":
            await say(TaskState.TASK_STATE_REJECTED, result.reason)
        else:
            await say(TaskState.TASK_STATE_FAILED, f"job {result.status} (exit {result.exit_code})")

    async def _report(self, result: RunResult) -> None:
        if self.report:
            await self.report(result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancellation lands with the kill switch (week 4)")


def build_app(card: AgentCard, executor: MeshNodeExecutor) -> Starlette:
    handler = DefaultRequestHandlerV2(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, RPC_PATH))
