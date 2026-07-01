from temporalio import activity

from a2a_client import invoke_agent as _invoke_agent


@activity.defn
async def invoke_agent(agent_name: str, input_data: dict) -> dict:
    """Invoke an AgentCore Runtime via the A2A protocol."""
    return await _invoke_agent(agent_name, input_data)
