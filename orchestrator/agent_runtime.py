"""Per-request create_agent runtime + event-stream driver.

Wires:
  • ChatOllama (cached LLM client)
  • One call_agent tool (closes over user_token + dispatcher + event_sink)
  • SkillMiddleware (registers its own load_skill tool, swaps system prompt)

Constructed inside /chat and discarded when the request ends — no shared
state across users, no JWT lifetime beyond the request scope.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Awaitable, Callable

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_ollama import ChatOllama

from orchestrator.a2a_dispatcher import Dispatcher
from orchestrator.agent_tools import make_tool
from orchestrator.skills_middleware import SkillMiddleware


log = logging.getLogger(__name__)


BASE_SYSTEM = (
    "You are a coordinator agent. You help the user by either answering "
    "directly (greetings / small-talk) or by delegating to specialist "
    "agents via the call_agent tool. Never call agents the user did not "
    "ask about. When multiple specialists are needed for one request, "
    "call them in parallel."
)


@lru_cache(maxsize=1)
def _llm() -> ChatOllama:
    return ChatOllama(
        model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0,
    )


def build_runtime(
    *,
    dispatcher: Dispatcher,
    user_token: str,
    event_sink: Callable[[str, dict], Awaitable[None]],
):
    """Construct a fresh create_agent runtime for one /chat request."""
    tool = make_tool(
        dispatcher=dispatcher,
        user_token=user_token,
        event_sink=event_sink,
    )
    return create_agent(
        model=_llm(),
        tools=[tool],
        middleware=[SkillMiddleware()],
        system_prompt=BASE_SYSTEM,
    )


async def run(runtime, user_message: str) -> str:
    """Drive the agent end-to-end and return the final assistant reply.

    The tools themselves emit SSE events as they execute (agent_selected /
    agent_result), so the caller does NOT need to inspect intermediate
    chunks for those. We only watch for the terminal AIMessage.
    """
    final = ""
    try:
        async for chunk in runtime.astream(
            {"messages": [HumanMessage(content=user_message)]},
            stream_mode="updates",
        ):
            for _node, payload in chunk.items():
                if not isinstance(payload, dict):
                    continue
                msgs = payload.get("messages") or []
                if not msgs:
                    continue
                last = msgs[-1]
                # The last AIMessage with NO pending tool_calls is the final reply.
                if isinstance(last, AIMessage) and not getattr(last, "tool_calls", None):
                    final = _flatten(last.content)
    except Exception:
        log.exception("agent_runtime: run() failed")
        raise
    return final or "(no response)"


def _flatten(content) -> str:
    """Flatten string- or content-blocks-style AIMessage content into text."""
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts).strip()
