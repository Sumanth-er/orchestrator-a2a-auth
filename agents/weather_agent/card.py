from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

PORT = 9101
BASE_URL = f"http://localhost:{PORT}"

SKILL = AgentSkill(
    id="weather_lookup",
    name="Weather lookup",
    description="Returns current weather or a short forecast for a city.",
    tags=["weather", "forecast"],
    examples=["weather in Bangalore", "will it rain tomorrow in Tokyo"],
)

CARD = AgentCard(
    name="Weather Agent",
    description="Current weather and short forecasts for any city.",
    icon_url=f"{BASE_URL}/",
    version="1.0.0",
    default_input_modes=["text"],
    default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False, extended_agent_card=False),
    supported_interfaces=[
        AgentInterface(protocol_binding="JSONRPC", url=BASE_URL),
    ],
    skills=[SKILL],
)
