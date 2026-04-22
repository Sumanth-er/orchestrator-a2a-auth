"""Adding a new agent = append one entry here.

The orchestrator stores NO authorization rules — it doesn't know who can use
what. Each agent enforces its own `required_roles` + `expected_audience` via
KeycloakAuthMiddleware. The orchestrator only needs routing info.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentEntry:
    name: str           # short label used in UI chips + LLM prompt
    url: str            # base URL of the A2A server
    audience: str       # Keycloak client_id to request in token-exchange
    description: str    # surfaced to LLM router


AGENTS: list[AgentEntry] = [
    AgentEntry(
        name="weather",
        url="http://localhost:9101",
        audience="weather-agent",
        description="Current weather, forecasts, and general meteorological questions for a city.",
    ),
    AgentEntry(
        name="billing",
        url="http://localhost:9102",
        audience="billing-agent",
        description="Invoices, payments, subscription plans, billing history, and account charges.",
    ),
]


def by_name(name: str) -> AgentEntry | None:
    for a in AGENTS:
        if a.name == name:
            return a
    return None
