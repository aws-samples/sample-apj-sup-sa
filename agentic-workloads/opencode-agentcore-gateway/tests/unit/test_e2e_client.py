# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""tests/e2e/run.py http_call: every header pair goes on the wire as given, so F1.token.duplicate_auth_header really
sends two Authorization headers (urllib.request would fold them into one)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
import run  # noqa: E402


class _Conn:
    sent = []

    def __init__(self, host, timeout):
        self.host = host

    def putrequest(self, method, path):
        _Conn.sent[:] = [("REQ", method, self.host, path)]

    def putheader(self, k, v):
        _Conn.sent.append((k, v))

    def endheaders(self, data=None):
        _Conn.sent.append(("BODY", data))

    def getresponse(self):
        class R:
            status = 403

            def read(self):
                return b"{}"
        return R()

    def close(self):
        pass


def test_duplicate_authorization_headers_are_both_sent(monkeypatch):
    monkeypatch.setattr(run.http.client, "HTTPSConnection", _Conn)
    hdrs = run.bad_token("duplicate_auth_header", "a.e30.c")
    assert run.http_call("https://gw.example", "/inference/v1/models", "GET", hdrs, None) == (403, b"{}")
    auth = [x[1] for x in _Conn.sent if len(x) == 2 and x[0].lower() == "authorization"]
    assert len(auth) == 2 and _Conn.sent[0] == ("REQ", "GET", "gw.example", "/inference/v1/models")


def test_body_gets_a_content_length_and_http_is_refused(monkeypatch):
    monkeypatch.setattr(run.http.client, "HTTPSConnection", _Conn)
    run.http_call("https://gw.example/base", "/x?y=1", "POST", {"Authorization": "Bearer t"}, b"{}")
    assert ("Content-Length", "2") in _Conn.sent and _Conn.sent[0][3] == "/base/x?y=1" and _Conn.sent[-1] == ("BODY", b"{}")
    with pytest.raises(ValueError):
        run.http_call("http://gw.example", "/", "GET", {}, None)


@pytest.fixture()
def ledger(monkeypatch):
    import boto3  # noqa: PLC0415
    from moto import mock_aws  # noqa: PLC0415
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1")
        c.create_table(TableName="t", BillingMode="PAY_PER_REQUEST",
                       KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                       AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}])
        yield c, type("Ctx", (), {"region": "us-east-1", "table": "t"})()


def _item(c, sk):
    return c.get_item(TableName="t", Key={"pk": {"S": "USER#carol"}, "sk": {"S": sk}}).get("Item")


def test_ledger_patch_restores_only_what_it_changed(ledger):
    c, ctx = ledger
    real = {"pk": {"S": "USER#carol"}, "sk": {"S": "BUDGET"}, "group": {"S": "ow"}, "token_groups": {"L": [{"S": "ow"}]},
            "budget_usd": {"N": "5"}, "budget_overrides_usd": {"N": "1"}}
    c.put_item(TableName="t", Item=real)
    with run.ledger_patch(ctx, "carol", "BUDGET", budget_usd=2, rpm=3):
        mid = _item(c, "BUDGET")
        assert mid["budget_usd"] == {"N": "2"} and mid["rpm"] == {"N": "3"} and mid["token_groups"] == real["token_groups"]
    assert _item(c, "BUDGET") == real


def test_ledger_patch_restores_even_when_the_call_fails(ledger):
    c, ctx = ledger
    with pytest.raises(RuntimeError), run.ledger_patch(ctx, "carol", "BUDGET", daily_usd=1):
        raise RuntimeError("boom")
    assert _item(c, "BUDGET") is None  # the run created the item, so it is gone again


@pytest.mark.parametrize("model,web,target", [(0.0, 0.0, 6.0), (1.25, 0.5, 2.0), (3.0, 0.0, 1.0)])
def test_seeded_spend_hits_the_target_and_survives_a_sync(ledger, model, web, target):
    c, ctx = ledger
    sk = "MONTH#2026-11"
    c.put_item(TableName="t", Item={"pk": {"S": "USER#carol"}, "sk": {"S": sk}, "model_spend_usd": {"N": str(model)},
                                    "spend_usd": {"N": str(web)}, "requests": {"N": "7"}})
    with run.seeded_spend(ctx, "carol", sk, target):
        it = _item(c, sk)
        assert float(it["model_spend_usd"]["N"]) + float(it["spend_usd"]["N"]) == pytest.approx(target)
        c.update_item(TableName="t", Key={"pk": {"S": "USER#carol"}, "sk": {"S": sk}}, UpdateExpression="SET model_spend_usd = :m",
                      ExpressionAttributeValues={":m": {"N": str(model)}})  # usage_sync re-SETs the model spend
        it = _item(c, sk)
        assert float(it["model_spend_usd"]["N"]) + float(it["spend_usd"]["N"]) == pytest.approx(target)
    it = _item(c, sk)
    assert float(it["spend_usd"]["N"]) == pytest.approx(web) and it["requests"] == {"N": "7"}


def test_items_the_run_created_are_removed_again(ledger):
    c, ctx = ledger
    with run.ledger_patch(ctx, "carol", "BUDGET", rpm=3):
        assert _item(c, "BUDGET")["rpm"] == {"N": "3"}
    assert _item(c, "BUDGET") is None
    with run.seeded_spend(ctx, "carol", "HOUR#2026-11-12T06", 2.0):
        assert float(_item(c, "HOUR#2026-11-12T06")["spend_usd"]["N"]) == pytest.approx(2.0)
    assert _item(c, "HOUR#2026-11-12T06") is None


def test_a_created_item_the_gateway_wrote_to_is_kept(ledger):
    c, ctx = ledger
    sk = "HOUR#2026-11-12T07"
    with run.seeded_spend(ctx, "carol", sk, 2.0):
        c.update_item(TableName="t", Key={"pk": {"S": "USER#carol"}, "sk": {"S": sk}}, UpdateExpression="ADD requests :one",
                      ExpressionAttributeValues={":one": {"N": "1"}})  # the call was counted
    it = _item(c, sk)
    assert it["requests"] == {"N": "1"} and float(it["spend_usd"]["N"]) == pytest.approx(0)


# r136 /code-review: reply_of decides every F3 verdict and had no test.
@pytest.mark.parametrize("raw,want", [
    ('{"choices":[{"message":{"content":"PONG"}}]}', ("PONG", False)),
    ('{"choices":[{"message":{"content":[{"type":"text","text":"PO"},{"text":"NG"}]}}]}', ("PONG", False)),
    ('{"choices":[{"message":{"content":null,"tool_calls":[{"id":"1"}]}}]}', ("", True)),
    ('{"choices":[{"message":{"content":null,"tool_calls":[]}}]}', ("", False)),
    ('{"parallel_tool_calls":true,"output":[{"type":"reasoning","content":[{"type":"reasoning_text","text":"PONG"}]}]}', ("", False)),
    ('{"output":[{"type":"message","content":[{"type":"output_text","text":"PONG"}]},{"type":"function_call"}]}', ("PONG", True)),
    (': ping\nid: 1\ndata: {"choices":[{"delta":{"content":"PO"}}]}\n\ndata: {"choices":[{"delta":{"content":"NG"}}]}\ndata: [DONE]\n', ("PONG", False)),
    ('data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n', ("", True)),
    ('event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"PO"}\n'
     'data: {"type":"response.output_text.delta","delta":"NG"}\n', ("PONG", False)),
    ('data: {"type":"response.output_item.added","item":{"type":"function_call"}}\n', ("", True)),
    ('data: "keepalive"\ndata: [1]\ndata: {"choices":[5, {"delta":null}]}\n', ("", False)),
    ("[" * 5000 + "]" * 5000, ("", False)),
    ("data: " + "[" * 5000 + "]" * 5000, ("", False)),
    ("not json at all", ("", False)),
    ('{"choices":[{"message":{"content":[{"type":"reasoning","text":"PONG"}]}}]}', ("", False)),
    ('{"output":[{"type":"message","content":5}]}', ("", False)),
])
def test_reply_of_reads_a_reply_by_its_structure(raw, want):
    assert run.reply_of(raw) == want
