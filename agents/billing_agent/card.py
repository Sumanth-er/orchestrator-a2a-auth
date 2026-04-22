from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

PORT = 9102
BASE_URL = f"http://localhost:{PORT}"

SKILLS = [
    AgentSkill(
        id="invoice_lookup",
        name="Invoice lookup",
        description="Look up a user's latest invoices and amounts due.",
        tags=["billing", "invoice"],
        examples=["show my last invoice", "what do I owe"],
    ),
]

CARD = AgentCard(
    name="Billing Agent",
    description="Invoices, payments, and subscription details.",
    icon_url=f"{BASE_URL}/",
    version="1.0.0",
    default_input_modes=["text"],
    default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=False, extended_agent_card=False),
    supported_interfaces=[
        AgentInterface(protocol_binding="JSONRPC", url=BASE_URL),
    ],
    skills=SKILLS,
)
