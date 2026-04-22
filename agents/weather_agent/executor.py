from __future__ import annotations

import logging
import random

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils import new_task, new_text_artifact
from a2a.types import TaskArtifactUpdateEvent

log = logging.getLogger(__name__)


class WeatherAgentExecutor(AgentExecutor):
    """Toy weather agent — LLM would go here in a real impl."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )

        user_text = _extract_text(context)
        log.info("weather: handling %r", user_text)
        reply = _fake_weather(user_text)

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


def _extract_text(context: RequestContext) -> str:
    msg = context.message
    if msg is None:
        return ""
    for p in msg.parts:
        t = getattr(p, "text", None)
        if t:
            return t
    return ""


def _fake_weather(query: str) -> str:
    conditions = ["sunny", "partly cloudy", "light rain", "thunderstorms", "clear skies"]
    temp = random.randint(18, 36)
    city = query.strip() or "your area"
    return f"Weather for {city}: {random.choice(conditions)}, around {temp}°C."
