from __future__ import annotations

import logging
import random
from uuid import uuid4

from a2a.helpers.proto_helpers import (
    new_task,
    new_text_artifact,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

log = logging.getLogger(__name__)


class WeatherAgentExecutor(AgentExecutor):
    """Toy weather agent — LLM would go here in a real impl."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task(
            task_id=context.task_id or uuid4().hex,
            context_id=context.context_id or uuid4().hex,
            state=TaskState.TASK_STATE_SUBMITTED,
        )
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )

        user_text = context.get_user_input()
        log.info("weather: handling %r", user_text)
        reply = _fake_weather(user_text)

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                artifact=new_text_artifact(name="weather", text=reply),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise RuntimeError("cancel not supported")


def _fake_weather(query: str) -> str:
    conditions = ["sunny", "partly cloudy", "light rain", "thunderstorms", "clear skies"]
    temp = random.randint(18, 36)
    city = (query or "").strip() or "your area"
    return f"Weather for {city}: {random.choice(conditions)}, around {temp}°C."
