"""LLM agent-selection + reply-composition via Ollama (langchain-ollama).

Two roles:
  1. Given the user message, pick which registered agent (if any) is best.
  2. Given an agent's outcome (ok / denied / error), compose a natural reply.

Uses a local Ollama server. Set OLLAMA_BASE_URL and OLLAMA_MODEL in .env.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from orchestrator.registry import AGENTS, AgentEntry, by_name


@dataclass
class Selection:
    agent: AgentEntry | None
    rationale: str


@lru_cache(maxsize=2)
def _llm(json_mode: bool) -> ChatOllama:
    kwargs = dict(
        model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0,
    )
    if json_mode:
        kwargs["format"] = "json"
    return ChatOllama(**kwargs)


def _registry_prompt() -> str:
    return "\n".join(f"- {a.name}: {a.description}" for a in AGENTS)


def _extract_json(text: str) -> dict:
    """Ollama in json mode is usually clean, but some models wrap or pad. Be tolerant."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return {}


async def pick_agent(user_message: str) -> Selection:
    """Ask the LLM which agent to route to. May return None for small-talk."""
    system = (
        "You route a user message to exactly one specialist agent, or to no agent "
        "when the message is a greeting or small-talk.\n"
        "Available agents:\n"
        f"{_registry_prompt()}\n\n"
        'Reply ONLY as strict JSON: {"agent": "<name or null>", "rationale": "<short>"}'
    )
    resp = await _llm(json_mode=True).ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user_message)]
    )
    data = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    name = data.get("agent")
    if isinstance(name, str) and name.lower() in ("null", "none", ""):
        name = None
    return Selection(
        agent=by_name(name) if name else None,
        rationale=str(data.get("rationale", "")),
    )


async def compose_reply(
    user_message: str,
    agent_name: str | None,
    status: str,
    agent_output: str | None,
    reason: str | None,
) -> str:
    """Turn the agent outcome into a friendly user-facing message."""
    if agent_name is None:
        # Small-talk path — let the LLM answer directly.
        resp = await _llm(json_mode=False).ainvoke(
            [
                SystemMessage(content="You are a concise helpful assistant."),
                HumanMessage(content=user_message),
            ]
        )
        return str(resp.content if hasattr(resp, "content") else resp).strip()

    context = json.dumps({
        "user_message": user_message,
        "agent_called": agent_name,
        "status": status,
        "agent_output": agent_output,
        "denial_reason": reason,
    })
    system = (
        "You are a helpful assistant composing a final reply. You just consulted a "
        "specialist agent. Use its output when status='ok'. If status='denied', "
        "apologise briefly, explain the user does not have access to that capability, "
        "and suggest what they CAN do based on the other agents' descriptions. "
        "If status='error', apologise briefly and suggest retrying. Be concise.\n\n"
        f"Agents in the system:\n{_registry_prompt()}"
    )
    resp = await _llm(json_mode=False).ainvoke(
        [SystemMessage(content=system), HumanMessage(content=context)]
    )
    return str(resp.content if hasattr(resp, "content") else resp).strip()
