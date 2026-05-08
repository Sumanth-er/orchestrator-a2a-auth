"""Single A2A-call tool exposed to the orchestrator's create_agent runtime.

The LLM picks (agent, query) at each step; the tool body calls the existing
Dispatcher.call() — so token-exchange, audience enforcement, and per-agent
role checks remain identical to the pre-runtime path.

Auth surface (intentionally tight):
  • user_token is captured by closure when make_tool() is invoked from /chat.
  • It is never put into graph state, never reaches the LLM, never logged.
  • event_sink is also closed-over so the SSE pipeline keeps emitting the
    same event names the UI listens for (agent_selected / agent_result).
"""
from __future__ import annotations

from typing import Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, field_validator

from orchestrator.a2a_dispatcher import Dispatcher
from orchestrator.registry import AGENTS, by_name


# --- input schema -----------------------------------------------------------

def _agent_names() -> list[str]:
    return [a.name for a in AGENTS]


class CallAgentInput(BaseModel):
    """Args the LLM must produce to call a specialist agent."""

    agent: str = Field(
        ...,
        description=(
            "Specialist agent to call. Must be one of: "
            + ", ".join(a.name for a in AGENTS)
        ),
    )
    query: str = Field(
        ...,
        description=(
            "A clear, self-contained question for that agent. "
            "Rephrase the user's message — do NOT pass it verbatim. "
            "Include any context the agent needs (city name, account id, "
            "expression, etc.)."
        ),
    )

    @field_validator("agent")
    @classmethod
    def _known(cls, v: str) -> str:
        names = _agent_names()
        if v not in names:
            raise ValueError(f"agent must be one of {names}, got {v!r}")
        return v


# --- factory ----------------------------------------------------------------

EventSink = Callable[[str, dict], Awaitable[None]]


def make_tool(
    *,
    dispatcher: Dispatcher,
    user_token: str,
    event_sink: EventSink,
) -> StructuredTool:
    """Build a per-request 'call_agent' tool.

    The returned tool closes over the user JWT and the SSE event sink so
    neither leaks into LangGraph state. Re-build per /chat request.
    """

    description = (
        "Call exactly one specialist agent with a precise query.\n"
        "Available agents:\n"
        + "\n".join(f"  - {a.name}: {a.description}" for a in AGENTS)
        + "\n\nGuidance:\n"
        "  • Call this tool MULTIPLE TIMES IN PARALLEL for independent "
        "sub-tasks (e.g. weather AND billing in one user prompt → two calls).\n"
        "  • Choose the smallest set of agents that covers the request.\n"
        "  • If the user is just chatting, do NOT call this tool — answer "
        "directly."
    )

    async def _call_agent(agent: str, query: str) -> str:
        entry = by_name(agent)
        if entry is None:
            return f"[error] unknown agent {agent!r}. valid: {_agent_names()}"

        # Mirror the SSE events the legacy router emitted, so the UI's
        # chip pipeline (static/app.js · sendMessage) keeps working.
        await event_sink("agent_selected", {
            "agent": entry.name,
            "rationale": query,
        })
        result = await dispatcher.call(
            entry, user_token=user_token, user_text=query,
        )
        await event_sink("agent_result", result.to_event())

        if result.status == "ok":
            return result.output or ""
        # Surface denial/error to the LLM verbatim — the summarize-skill
        # phrases a polite reply for the user.
        return f"[{result.status}] {result.reason or 'no detail'}"

    return StructuredTool.from_function(
        coroutine=_call_agent,
        name="call_agent",
        description=description,
        args_schema=CallAgentInput,
    )
