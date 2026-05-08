"""SkillMiddleware — injects skill descriptions into the system prompt and
exposes a `load_skill` tool the model can call to fetch the full prompt for
a chosen skill.

Auth-blind by construction:
  • Reads only request.system_message (no JWT, no tools, no dispatcher).
  • Writes only request.system_message via request.override(...).
  • The tool it owns (`load_skill`) is a pure local lookup — no I/O.

You can edit / replace SKILLS without touching the rest of the orchestrator.
"""
from __future__ import annotations

from typing import Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool


# ----------------------------------------------------------------------------
# Skill catalog — short metadata visible in every model call;
# full prompts loaded on demand via the load_skill tool.
# ----------------------------------------------------------------------------
SKILLS = [
    {
        "name": "plan",
        "description": (
            "Decompose a complex multi-step request into atomic sub-tasks "
            "(use before calling agents on hard prompts)."
        ),
        "prompt": (
            "You are a planner. Break the user's request into atomic "
            "sub-tasks, each one assignable to a specialist tool.\n"
            'Output JSON: {"steps":[{"tool":"<name>","query":"<self-contained sub-question>"}]}\n'
            "Do not call any tool yet — just plan."
        ),
    },
    {
        "name": "execute",
        "description": (
            "Call specialist agents (in parallel when independent) "
            "via the call_agent tool to gather data."
        ),
        "prompt": (
            "Use the call_agent tool. Pass a precise, self-contained query "
            "to each agent — NOT the raw user message. For independent "
            "sub-tasks, emit MULTIPLE tool_calls in the same turn. "
            "If a tool returns '[denied]', do not retry; explain to the user."
        ),
    },
    {
        "name": "summarize",
        "description": (
            "Compose ONE concise reply that fuses every tool result."
        ),
        "prompt": (
            "Compose a single concise reply for the user that merges the "
            "outputs from all tool calls. Be explicit about anything that "
            "was denied vs. answered. Do not call tools."
        ),
    },
    {
        "name": "smalltalk",
        "description": (
            "Reply directly without calling any tool for greetings/chitchat."
        ),
        "prompt": (
            "Reply briefly and helpfully without calling any tool. "
            "This is a greeting, small-talk, or out-of-scope message."
        ),
    },
]


# ----------------------------------------------------------------------------
# Tool the LLM uses to fetch the FULL prompt for a chosen skill.
# Registered as a class-variable on SkillMiddleware so create_agent picks it
# up alongside the user-supplied tools.
# ----------------------------------------------------------------------------
@tool
def load_skill(skill_name: str) -> str:
    """Load the full system prompt for a named skill.

    Args:
        skill_name: One of the names listed in '## Available Skills'.

    Returns:
        The full prompt text for that skill, or an error string.
    """
    for s in SKILLS:
        if s["name"] == skill_name:
            return s["prompt"]
    valid = [s["name"] for s in SKILLS]
    return f"Unknown skill: {skill_name}. Available: {valid}"


# ----------------------------------------------------------------------------
# The middleware
# ----------------------------------------------------------------------------
class SkillMiddleware(AgentMiddleware):
    """Middleware that injects skill descriptions into the system prompt."""

    # Register the load_skill tool as a class variable
    tools = [load_skill]

    def __init__(self):
        """Initialize and generate the skills prompt from SKILLS."""
        # Build skills prompt from the SKILLS list
        skills_list = []
        for skill in SKILLS:
            skills_list.append(
                f"- {skill['name']}: {skill['description']}"
            )
        self.skills_prompt = "\n".join(skills_list)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Sync: Inject skill descriptions into system prompt."""
        # Build the skills addendum
        skills_addendum = (
            f"\n\n## Available Skills\n\n{self.skills_prompt}\n\n"
            "Use the load_skill tool when you need detailed information "
            "about handling a specific type of request."
        )

        # Append to system message content blocks
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum}
        ]
        new_system_message = SystemMessage(content=new_content)
        modified_request = request.override(system_message=new_system_message)
        return handler(modified_request)
