"""The Cognito pre-token Lambda must put the role and tenant into every access token,
including refreshed ones: the Cedar role forbids and the RLS session variables read them."""
import importlib.util
import os

import pytest
import yaml

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "infrastructure", "stacks", "cognito-stack.yaml")
TRIGGERS = ["TokenGeneration_HostedAuth", "TokenGeneration_Authentication", "TokenGeneration_RefreshTokens",
            "TokenGeneration_NewPasswordChallenge", "TokenGeneration_AuthenticateDevice"]


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


@pytest.fixture(scope="module")
def handler(tmp_path_factory):
    with open(TEMPLATE) as f:
        template = _Loader(f).get_single_data()
    # Write the template's inline Lambda to a file and import it like Lambda would.
    path = tmp_path_factory.mktemp("pretoken") / "index.py"
    path.write_text(template["Resources"]["PreTokenLambda"]["Properties"]["Code"]["ZipFile"])
    spec = importlib.util.spec_from_file_location("pretoken_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.lambda_handler


@pytest.mark.parametrize("trigger", TRIGGERS)
def test_access_token_gets_role_and_tenant(handler, trigger):
    event = {"triggerSource": trigger, "request": {"userAttributes": {"custom:role": "staff", "custom:account_id": "acct-1"}}}
    out = handler(event, None)["response"]["claimsAndScopeOverrideDetails"]
    assert out["accessTokenGeneration"]["claimsToAddOrOverride"] == {"custom:role": "staff", "custom:account_id": "acct-1"}
    assert out["idTokenGeneration"]["claimsToAddOrOverride"]["custom:role"] == "staff"


def test_user_without_role_gets_no_role_claim(handler):
    event = {"triggerSource": "TokenGeneration_Authentication", "request": {"userAttributes": {"email": "x@example.com"}}}
    out = handler(event, None)["response"]["claimsAndScopeOverrideDetails"]
    assert "custom:role" not in out["accessTokenGeneration"]["claimsToAddOrOverride"]
