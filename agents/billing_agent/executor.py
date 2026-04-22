from __future__ import annotations

import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import new_task, new_text_artifact

log = logging.getLogger(__name__)


class BillingAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )

        reply = (
            "Latest invoice:\n"
            "  INV-4217  |  $148.00  |  Due 2026-05-01\n"
            "  Previous: INV-4201  |  $148.00  |  Paid"
        )

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task.id,
                artifact=new_text_artifact(text=reply),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise RuntimeError("cancel not supported")
