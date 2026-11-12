"""The agent derives the memory actor id only from a verified Cognito access token."""
import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from test_chart_stream_rewrite import _load_agent_helpers

POOL, CLIENT = "us-east-1_TestPool", "client-123"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"


@pytest.fixture(scope="module")
def agent():
    os.environ.update(COGNITO_USER_POOL_ID=POOL, COGNITO_USER_LOGIN_CLIENT_ID=CLIENT, AWS_REGION="us-east-1")
    return _load_agent_helpers()


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(key, **over):
    claims = {"sub": "user-1", "iss": ISSUER, "token_use": "access", "client_id": CLIENT,
              "exp": int(time.time()) + 600}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture(autouse=True)
def jwks(agent, key, monkeypatch):
    class _Client:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()
    monkeypatch.setattr(agent, "_jwks", _Client())


def test_valid_token(agent, key):
    assert agent.verify_user_token(_token(key))["sub"] == "user-1"


@pytest.mark.parametrize("over", [{"token_use": "id"}, {"client_id": "other"},
                                  {"iss": "https://example.com"}, {"exp": int(time.time()) - 10}])
def test_rejects_bad_claims(agent, key, over):
    assert agent.verify_user_token(_token(key, **over)) is None


def test_rejects_forged_signature(agent, key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert agent.verify_user_token(_token(other)) is None


def test_rejects_unsigned(agent):
    # Negative test: an unsigned (alg=none) token must be rejected by verify_user_token.
    # nosemgrep: python.jwt.security.jwt-python-none-alg
    forged = jwt.encode({"sub": "victim", "iss": ISSUER, "token_use": "access", "client_id": CLIENT,
                         "exp": int(time.time()) + 600}, key=None, algorithm="none")
    assert agent.verify_user_token(forged) is None
