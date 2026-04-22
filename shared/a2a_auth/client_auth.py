from __future__ import annotations

import httpx


def bearer_httpx_client(token: str, *, timeout: float = 30.0) -> httpx.AsyncClient:
    """httpx.AsyncClient that injects `Authorization: Bearer <token>` on every request.

    Pass into `a2a.client.client.ClientConfig(httpx_client=...)` and also into
    `A2ACardResolver(httpx_client=...)` so card fetches are authenticated too
    (if the agent requires it — in this demo cards are public).
    """
    async def _inject(request: httpx.Request) -> None:
        request.headers["Authorization"] = f"Bearer {token}"

    return httpx.AsyncClient(
        timeout=timeout,
        event_hooks={"request": [_inject]},
    )
