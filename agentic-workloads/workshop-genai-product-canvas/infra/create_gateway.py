"""Create an Amazon Bedrock AgentCore Gateway for local / own-account use.

This is a thin CLI wrapper around the shared logic in
`infra/gateway_provisioner/index.py` - the exact same code the CloudFormation
`GatewaySetup` custom resource runs in the workshop. Keeping one implementation
means the own-account path and the Workshop Studio path cannot drift.

The only difference from the CFN path: this writes `workshop/gateway.env` to disk,
whereas the custom resource writes the same content to SSM (`/touch-grass/gateway-env`).

Run AFTER deploy_tools.sh:

    source workshop/tools.env
    python infra/create_gateway.py

Requires a boto3 with the `bedrock-agentcore-control` client (see README).
"""

import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).parent

# Load the shared gateway logic (infra/gateway_provisioner/index.py) by path, so
# it works regardless of the current working directory.
_spec = importlib.util.spec_from_file_location(
    "gateway_core", HERE / "gateway_provisioner" / "index.py"
)
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

PROJECT = os.environ.get("PROJECT_NAME", "biodiversity-anomaly")
TOOLS_FUNCTION_ARN = os.environ["TOOLS_FUNCTION_ARN"]
TOOL_DEF = json.loads((HERE / ".." / "agent" / "tool_definition.json").read_text())


def main():
    print("==> Setting up Cognito inbound authorizer...")
    cognito = gw.setup_cognito(PROJECT)
    print(f"    token_url: {cognito['token_url']}")

    print("==> Ensuring gateway IAM role...")
    role_arn, _role_name = gw.ensure_gateway_role(PROJECT, TOOLS_FUNCTION_ARN)

    print("==> Creating AgentCore Gateway + Lambda target...")
    _gateway_id, gateway_url, _target_id = gw.create_gateway(
        PROJECT, cognito, role_arn, TOOLS_FUNCTION_ARN, TOOL_DEF
    )
    print(f"    gateway_url: {gateway_url}")

    env_path = HERE / ".." / "gateway.env"
    env_path.write_text(gw._gateway_env(gateway_url, cognito))
    print(f"\n==> Wrote {env_path.resolve()}")
    print("    Next:  source workshop/gateway.env && python agent/agent.py tapir")


if __name__ == "__main__":
    main()
