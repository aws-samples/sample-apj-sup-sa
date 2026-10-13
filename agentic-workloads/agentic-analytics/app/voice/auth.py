import os

import boto3


def get_gateway_token() -> str:
    """Local-dev ONLY fallback: mint a Cognito access token via ROPC.

    Hosted deploys (fargate/pipecat-cloud) do NOT use this — the signed-in user's
    own access token is forwarded from the browser and used as the gateway_token,
    so AgentCore applies that real user's RBAC/RLS. There is deliberately NO demo
    identity in hosted mode: if a user isn't authenticated, there is no fallback.

    This function only runs when ALLOW_DEMO_FALLBACK=true (a developer convenience
    for laptop mode, where the JWT-gated proxy isn't in front). It requires
    DEMO_USERNAME, DEMO_PASSWORD, COGNITO_CLIENT_ID, AWS_REGION.
    """
    if os.getenv("ALLOW_DEMO_FALLBACK", "false").lower() not in ("true", "1", "yes"):
        raise RuntimeError(
            "No per-user gateway_token was provided and ALLOW_DEMO_FALLBACK is off. "
            "Hosted voice requires the signed-in user's token (forwarded by the start "
            "proxy); there is no demo fallback."
        )
    client = boto3.client("cognito-idp", region_name=os.environ["AWS_REGION"])
    resp = client.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": os.environ["DEMO_USERNAME"],
            "PASSWORD": os.environ["DEMO_PASSWORD"],
        },
        ClientId=os.environ["COGNITO_CLIENT_ID"],
    )
    # The MCP Gateway validates the OAuth AccessToken for scope (IdToken is rejected
    # with insufficient_scope).
    return resp["AuthenticationResult"]["AccessToken"]
