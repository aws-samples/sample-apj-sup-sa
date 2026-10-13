"""AnyCompany Shop customer-support agent (Strands Agents on Amazon Bedrock).

There is no tracing code here on purpose. The ADOT distribution, started by
`opentelemetry-instrument` in run.sh, registers the global tracer provider, and
Strands' built-in OpenTelemetry tracing emits agent, model, and tool spans
through it.

Tools run in one of two modes:
  * mock (default): answer from the in-memory MOCK_ORDERS below
  * live: set ORDERS_API_URL (sample 03) and tools call the shop's orders service
    over HTTP, so the agent's trace continues into orders -> payments.
"""

import os

import httpx
from strands import Agent, tool
from strands.models import BedrockModel

ORDERS_API_URL = os.environ.get("ORDERS_API_URL")
TOOL_TIMEOUT_SECONDS = float(os.environ.get("TOOL_TIMEOUT_SECONDS", "3"))

# v1 has no scope guardrail, so it happily answers off-topic questions. That's
# the regression evaluators catch. v2 is the fix.
SYSTEM_PROMPTS = {
    "v1": (
        "You are a friendly customer support assistant for AnyCompany Shop. "
        "Help customers with their questions. Use the tools to look up orders."
    ),
    "v2": (
        "You are the customer support assistant for AnyCompany Shop. You ONLY help with "
        "AnyCompany orders, shipping, returns, refunds, and payments. Use the tools to look "
        "up orders; never guess order details. If the customer asks about anything else "
        "(recipes, coding, trivia, advice), politely decline in one sentence and offer to "
        "help with an order instead. If a tool fails, say so plainly and suggest retrying later."
    ),
}

MOCK_ORDERS = {
    "ORD-1001": {"status": "shipped", "carrier": "UPS", "eta": "2026-10-03", "total": 59.90, "payment": "captured"},
    "ORD-1002": {"status": "processing", "carrier": None, "eta": "2026-10-06", "total": 120.00, "payment": "authorized"},
    "ORD-1003": {"status": "delivered", "carrier": "USPS", "eta": None, "total": 18.25, "payment": "captured"},
}

REFUND_POLICY = (
    "Items can be returned within 30 days of delivery in original condition. Refunds go to the "
    "original payment method within 5-7 business days after the return is received. Shipping "
    "fees are non-refundable unless the item arrived damaged."
)


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an AnyCompany order by its ID (for example ORD-1001).

    Returns status, carrier, estimated delivery date, order total, and payment state.
    """
    order_id = order_id.strip().upper()
    if ORDERS_API_URL:
        # Auto-instrumented by opentelemetry-instrumentation-httpx: this call becomes
        # a CLIENT span and propagates traceparent to the orders service.
        response = httpx.get(f"{ORDERS_API_URL}/orders/{order_id}", timeout=TOOL_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    if order_id not in MOCK_ORDERS:
        raise ValueError(f"Order {order_id} not found")
    return {"order_id": order_id, **MOCK_ORDERS[order_id]}


@tool
def get_refund_policy() -> str:
    """Return AnyCompany's refund and return policy."""
    return REFUND_POLICY


@tool
def start_return(order_id: str, reason: str) -> dict:
    """Start a return for a delivered order. Requires the order ID and the customer's reason."""
    order_id = order_id.strip().upper()
    if ORDERS_API_URL:
        response = httpx.post(
            f"{ORDERS_API_URL}/orders/{order_id}/returns",
            json={"reason": reason},
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    order = MOCK_ORDERS.get(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    if order["status"] != "delivered":
        return {"order_id": order_id, "accepted": False, "detail": "Only delivered orders can be returned"}
    return {"order_id": order_id, "accepted": True, "return_label": f"RMA-{order_id[-4:]}"}


def build_agent(session_id: str, prompt_version: str | None = None) -> Agent:
    prompt_version = prompt_version or os.environ.get("PROMPT_VERSION", "v1")
    model = BedrockModel(
        model_id=os.environ["MODEL_ID"],
        region_name=os.environ["AWS_REGION"],
        temperature=0.3,
    )
    return Agent(
        name="support-agent",
        model=model,
        system_prompt=SYSTEM_PROMPTS[prompt_version],
        tools=[lookup_order, get_refund_policy, start_return],
        # session.id groups turns into a session for Omni and for online evaluation.
        # app.prompt.version lets you compare scores before and after the fix.
        # app.scenario.phase is set by sample 03 (baseline / incident / recovery).
        trace_attributes={
            "session.id": session_id,
            "app.prompt.version": prompt_version,
            "app.tools.mode": "live" if ORDERS_API_URL else "mock",
            "app.scenario.phase": os.environ.get("SCENARIO_PHASE", "none"),
        },
        callback_handler=None,
    )
