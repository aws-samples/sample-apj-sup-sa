"""The Amplify custom resource adds its URL to the Cognito client without making
custom:role or custom:account_id user-writable (UpdateUserPoolClient resets any
field it is not given, and no WriteAttributes means every attribute is writable)."""
import importlib.util
import pathlib
import sys
import types
from unittest import mock

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[3] / "infrastructure" / "custom-resource-lambdas" / "amplify_hosting" / "handler.py"


class FakeCognito:
    def __init__(self, client):
        self.client, self.updates = client, []

    def describe_user_pool_client(self, **_):
        return {"UserPoolClient": dict(self.client)}

    def update_user_pool_client(self, **kw):
        self.updates.append(kw)


@pytest.fixture(scope="module")
def handler():
    common = types.ModuleType("common")
    utils = types.ModuleType("common.amplify_utils")
    utils.__getattr__ = lambda name: (lambda *a, **k: None)
    stubs = {"common": common, "common.amplify_utils": utils}
    spec = importlib.util.spec_from_file_location("amplify_hosting_handler", _PATH)
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(mod)
    return mod


def _run(handler, client, write=("email", "name")):
    fake = FakeCognito(client)
    with mock.patch.object(handler.boto3, "client", return_value=fake):
        handler._add_url_to_cognito_client("pool", "cid", "https://main.d1.amplifyapp.com", list(write) if write else None)
    return fake.updates


BASE = {"ClientName": "aa-demo-user-login", "CallbackURLs": ["http://localhost:3000/"], "LogoutURLs": [],
        "ExplicitAuthFlows": ["ALLOW_USER_PASSWORD_AUTH"], "ReadAttributes": ["email", "custom:role"],
        "WriteAttributes": ["email", "name"], "AccessTokenValidity": 60, "TokenValidityUnits": {"AccessToken": "minutes"}}


def test_new_url_keeps_write_and_read_lists(handler):
    (upd,) = _run(handler, BASE)
    assert upd["WriteAttributes"] == ["email", "name"]
    assert upd["ReadAttributes"] == ["email", "custom:role"]
    assert upd["AccessTokenValidity"] == 60 and upd["TokenValidityUnits"] == {"AccessToken": "minutes"}
    assert "https://main.d1.amplifyapp.com/" in upd["CallbackURLs"]


def test_wiped_client_is_repaired_even_when_url_present(handler):
    wiped = {k: v for k, v in BASE.items() if k != "WriteAttributes"}
    wiped["CallbackURLs"] = wiped["LogoutURLs"] = ["https://main.d1.amplifyapp.com/"]
    (upd,) = _run(handler, wiped)
    assert upd["WriteAttributes"] == ["email", "name"]


def test_no_update_when_nothing_changed(handler):
    done = dict(BASE, CallbackURLs=["https://main.d1.amplifyapp.com/"], LogoutURLs=["https://main.d1.amplifyapp.com/"])
    assert _run(handler, done) == []
