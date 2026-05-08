"""Weather agent entry point.

Adding a new agent = copy this folder, change card.py / executor.py,
and point main.py at the right audience + role. That's it.
"""
import shared.proto_compat  # noqa: F401  MUST be the first import — see module docstring

import logging
import os

from dotenv import load_dotenv
load_dotenv()

import uvicorn

from agents.base_agent import make_agent_app
from agents.weather_agent.card import CARD, PORT
from agents.weather_agent.executor import WeatherAgentExecutor

logging.basicConfig(level=logging.INFO)

app = make_agent_app(
    card=CARD,
    executor=WeatherAgentExecutor(),
    expected_audience=os.getenv("KC_WEATHER_CLIENT_ID", "weather-agent"),
    required_roles=["weather.read"],
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("WEATHER_AGENT_PORT", PORT)))
