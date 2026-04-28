"""Reusable builder for auth-protected A2A agents.

Every agent in this demo is three files:

    agents/<name>/card.py       - AgentCard definition
    agents/<name>/executor.py   - AgentExecutor implementing the skill
    agents/<name>/main.py       - calls make_agent_app(...) and uvicorn.run

`make_agent_app` stitches the a2a-sdk 1.0.0 server bits
(`create_agent_card_routes` + `create_jsonrpc_routes` +
`DefaultRequestHandler` + `InMemoryTaskStore`) onto a Starlette app and
adds our `KeycloakAuthMiddleware`.

No agent needs to know anything about auth beyond declaring its own
audience + required_roles.
"""
from __future__ import annotations

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from starlette.applications import Starlette

from shared.a2a_auth import JwtValidator, KeycloakAuthMiddleware, KeycloakSettings


def make_agent_app(
    *,
    card: AgentCard,
    executor: AgentExecutor,
    expected_audience: str,
    required_roles: list[str],
    settings: KeycloakSettings | None = None,
) -> Starlette:
    """Build a fully-wired, auth-protected A2A Starlette app."""
    settings = settings or KeycloakSettings()
    validator = JwtValidator(issuer=settings.issuer, jwks_uri=settings.jwks_uri)

    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = [
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, rpc_url="/"),
    ]

    app = Starlette(routes=routes)
    app.add_middleware(
        KeycloakAuthMiddleware,
        validator=validator,
        expected_audience=expected_audience,
        required_roles=required_roles,
    )
    return app
