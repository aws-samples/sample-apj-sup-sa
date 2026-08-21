"""Biodiversity Anomaly Detection Agent.

A single agent definition that runs three ways:

1. Local, local tools        - reads JSON files on disk. Zero AWS tool backend.
                               `python agent.py tapir`
2. Local, remote tools       - calls the AgentCore Gateway (remote MCP tools).
                               `TOOL_MODE=gateway python agent.py tapir`
3. Deployed on AgentCore     - the same file is the AgentCore Runtime entrypoint.
   Runtime                     `agentcore deploy` (see skills/deploy-to-agentcore).

The model, system prompt, and tool contract are identical across all three. That
is the design point of the workshop: the GenAI Product Canvas decisions map to one
agent that you develop locally and promote to a managed runtime unchanged.
"""

import json
import os
import sys
from pathlib import Path

from strands import Agent
from strands.models import BedrockModel

MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)
REGION = os.environ.get("AWS_REGION", "us-east-1")
TOOL_MODE = os.environ.get("TOOL_MODE", "local")  # "local" | "gateway"

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.txt").read_text()

TEST_CASES = {
    "tapir": (
        "Malayan Tapir detections at Sungai Lebam Corridor (STN-03) dropped to 0 "
        "in June 2026. Baseline mean ~2.5/month. Investigate the probable cause."
    ),
    "elephant": (
        "Asian Elephant has not been detected at Tanjung Balau Forest (STN-01) "
        "since March 2026. Investigate the probable cause."
    ),
    "pangolin": (
        "Sunda Pangolin shows a consistent declining trend across all 6 monitoring "
        "stations over Jan-Jul 2026, now near zero. Investigate the probable cause."
    ),
}


def build_model() -> BedrockModel:
    return BedrockModel(model_id=MODEL_ID, region_name=REGION, max_tokens=4096)


def run_with_local_tools(prompt: str) -> str:
    """Mode 1: fully local. Tools read JSON from ./data."""
    from tools_local import LOCAL_TOOLS

    agent = Agent(model=build_model(), system_prompt=SYSTEM_PROMPT, tools=LOCAL_TOOLS)
    return str(agent(prompt))


def run_with_gateway_tools(prompt: str) -> str:
    """Mode 2: local agent, remote tools via AgentCore Gateway (MCP)."""
    from gateway_client import build_gateway_mcp_client

    gateway = build_gateway_mcp_client()
    with gateway:
        tools = gateway.list_tools_sync()
        agent = Agent(model=build_model(), system_prompt=SYSTEM_PROMPT, tools=tools)
        return str(agent(prompt))


def investigate(prompt: str) -> str:
    if TOOL_MODE == "gateway":
        return run_with_gateway_tools(prompt)
    return run_with_local_tools(prompt)


# --- AgentCore Runtime entrypoint (Mode 3) ---------------------------------
# Guarded so `python agent.py` locally does not require the runtime package.
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def handler(payload: dict) -> dict:
        """AgentCore Runtime invokes this with the request JSON payload."""
        prompt = payload.get("prompt") or payload.get("inputText") or ""
        result = investigate(prompt)
        return {"result": result}

except ImportError:
    app = None


# --- Entrypoint ------------------------------------------------------------
# On AgentCore Runtime the file is executed with no CLI args: start the server.
# Locally, pass a scenario (e.g. `python agent.py tapir`) to run one investigation.
if __name__ == "__main__":
    if len(sys.argv) > 1:
        scenario = sys.argv[1]
        prompt = TEST_CASES.get(scenario, scenario)  # allow a raw prompt too
        print(f"\n{'=' * 68}")
        print(f"SCENARIO : {scenario}")
        print(f"TOOL_MODE: {TOOL_MODE}   MODEL: {MODEL_ID}")
        print(f"{'=' * 68}")
        print(f"INPUT: {prompt}\n{'-' * 68}")
        print(investigate(prompt))
    elif app is not None:
        # AgentCore Runtime (or `python agent.py` with no args) -> serve.
        app.run()
    else:
        print("No scenario given and AgentCore runtime SDK not installed. "
              "Try: python agent.py tapir")
