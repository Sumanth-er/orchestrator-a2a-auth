import shared.proto_compat  # noqa: F401  MUST be first — see module docstring

import logging
import os

from dotenv import load_dotenv
load_dotenv()

import uvicorn

from agents.base_agent import make_agent_app
from agents.billing_agent.card import CARD, PORT
from agents.billing_agent.executor import BillingAgentExecutor

logging.basicConfig(level=logging.INFO)

app = make_agent_app(
    card=CARD,
    executor=BillingAgentExecutor(),
    expected_audience=os.getenv("KC_BILLING_CLIENT_ID", "billing-agent"),
    required_roles=["billing.read"],
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("BILLING_AGENT_PORT", PORT)))
