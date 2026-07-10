import os
import pytest
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

REQUIRED_VARS = [
    "AWS_REGION",
    "COGNITO_CLIENT_ID",
    "DEMO_USERNAME",
    "DEMO_PASSWORD",
    "AWS_AGENT_ARN",
]


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: requires live AWS credentials and deployed AgentCore stack"
    )


@pytest.fixture(scope="session", autouse=True)
def require_env():
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        pytest.skip(f"Integration test skipped — missing env vars: {', '.join(missing)}")
