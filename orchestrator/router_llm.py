"""Azure OpenAI agent-selection + reply-composition.

Two roles:
  1. Given the user message, pick which registered agent (if any) is best.
  2. Given an agent's outcome (ok / denied / error), compose a natural reply.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from openai import AsyncAzureOpenAI

from orchestrator.registry import AGENTS, AgentEntry, by_name


@dataclass
class Selection:
    agent: AgentEntry | None
    rationale: str


def _client() -> AsyncAzureOpenAI:
    return AsyncAzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )


_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")


def _registry_prompt() -> str:
    lines = [f"- {a.name}: {a.description}" for a in AGENTS]
    return "\n".join(lines)


async def pick_agent(user_message: str) -> Selection:
    """Ask the LLM which agent to route to. May return None for small-talk."""
    system = (
        "You route a user message to exactly one specialist agent, or to no agent "
        "when the message is a greeting/small-talk. "
        "Available agents:\n"
        f"{_registry_prompt()}\n\n"
        "Reply as strict JSON: {\"agent\": \"<name or null>\", \"rationale\": \"<short>\"}"
    )
    client = _client()
    resp = await client.chat.completions.create(
        model=_DEPLOYMENT,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    name = data.get("agent")
    return Selection(agent=by_name(name) if name else None, rationale=data.get("rationale", ""))


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
        client = _client()
        resp = await client.chat.completions.create(
            model=_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are a concise helpful assistant."},
                {"role": "user", "content": user_message},
            ],
            temperature=0.4,
        )
        return (resp.choices[0].message.content or "").strip()

    context = {
        "user_message": user_message,
        "agent_called": agent_name,
        "status": status,
        "agent_output": agent_output,
        "denial_reason": reason,
    }
    system = (
        "You are a helpful assistant composing a final reply. You just consulted a "
        "specialist agent. Use its output when status='ok'. If status='denied', "
        "apologise briefly, explain the user does not have access to that capability, "
        "and suggest what they CAN do based on the other agents' descriptions. "
        "If status='error', apologise briefly and suggest retrying. Be concise."
        f"\n\nAgents in the system:\n{_registry_prompt()}"
    )
    client = _client()
    resp = await client.chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context)},
        ],
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip()
