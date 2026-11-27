import importlib
import json
import pathlib
import sys
from decimal import Decimal
from unittest import mock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "config" / "models.json").read_text())
MODELS = CONFIG["models"]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TABLE_NAME", "t")
    monkeypatch.setenv("BUCKET_NAME", "b")
    monkeypatch.setenv("WORKER_FUNCTION_NAME", "w")
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/x")
    monkeypatch.setenv("MODELS_JSON", json.dumps(MODELS))
    monkeypatch.setenv("RETIRED_MODELS_JSON", json.dumps(CONFIG["retired"]))
    monkeypatch.setenv("DAILY_GENERATION_LIMIT", "100")
    monkeypatch.setenv("DAILY_UPLOAD_LIMIT", "100")
    monkeypatch.setenv("UPLOADS_BUCKET_NAME", "uploads-b")


def load(name):
    sys.path.insert(0, str(ROOT / "backend" / name))
    sys.modules.pop("handler", None)
    with mock.patch("boto3.resource"), mock.patch("boto3.client"):
        mod = importlib.import_module("handler")
    sys.path.pop(0)
    if name == "worker":
        mod._agentcore.exceptions.RuntimeClientError = RuntimeClientError
    return mod


class RuntimeClientError(Exception):
    """Stands in for the botocore modelled exception in worker tests."""


class FakeContext:
    def __init__(self, remaining_s):
        self.remaining_ms = remaining_s * 1000

    def get_remaining_time_in_millis(self):
        return self.remaining_ms


def agent_ok():
    import base64
    body = {"status": "SUCCEEDED", "glbBase64": base64.b64encode(b"glTF").decode(), "glbBytes": 4, "script": "import bpy",
            "attempts": [{"attempt": 1, "ok": True}], "usage": {"inputTokens": 10, "outputTokens": 10, "totalTokens": 20}}
    return {"response": mock.Mock(read=lambda: json.dumps(body).encode())}


def event(method, resource, body=None, job_id=None, content_type="application/json"):
    return {
        "httpMethod": method,
        "resource": resource,
        "headers": {"Content-Type": content_type},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {"jobId": job_id} if job_id else None,
        "requestContext": {},
    }


def test_create_runs_the_chosen_claude_model_in_its_slot():
    api = load("api")
    res = api.handler(event("POST", "/api/generations", {"prompt": "a red mug", "models": {"claude-opus-5-5": "claude-haiku-5-5"}}), None)
    assert res["statusCode"] == 202
    keys = [json.loads(c.kwargs["Payload"])["modelKey"] for c in api._lambda.invoke.call_args_list]
    assert "claude-haiku-5-5" in keys and "claude-opus-5-5" not in keys and len(keys) == len(MODELS)
    order = api.TABLE.put_item.call_args.kwargs["Item"]["modelOrder"]
    assert order.index("claude-haiku-5-5") == [m["key"] for m in MODELS].index("claude-opus-5-5")
    body = json.loads(res["body"])
    assert [r["model"]["name"] for r in body["results"]][order.index("claude-haiku-5-5")] == "Claude Haiku 5.5"


@pytest.mark.parametrize("choices", [
    {"claude-opus-5-5": "gpt-6-2-astra"},    # a model that isn't an option for this slot
    {"claude-opus-5-5": "claude-nope"},
    {"gpt-6-2-astra": "claude-haiku-5-5"},   # options belong to the Claude slot only
    {"not-a-slot": "x"},
    ["claude-haiku-5-5"],
    {"claude-opus-5-5": ["claude-haiku-5-5"]},  # a real slot mapped to an array or object (was a 500)
    {"claude-opus-5-5": {"key": "claude-haiku-5-5"}},
    {"claude-opus-5-5": 5},
    {"claude-opus-5-5": None},
])
def test_create_rejects_invalid_model_choices(choices):
    api = load("api")
    res = api.handler(event("POST", "/api/generations", {"prompt": "a red mug", "models": choices}), None)
    assert res["statusCode"] == 400
    api.TABLE.update_item.assert_not_called()  # no quota used
    api._lambda.invoke.assert_not_called()


def test_models_endpoint_lists_the_claude_dropdown_options():
    api = load("api")
    slots = json.loads(api.handler(event("GET", "/api/models"), None)["body"])["models"]
    claude = next(s for s in slots if s["key"] == "claude-opus-5-5")
    assert [o["name"] for o in claude["options"]] == ["Claude Opus 5.5", "Claude Sonnet 5.5", "Claude Haiku 5.5"]
    assert all("options" not in s for s in slots if s["key"] != "claude-opus-5-5")


def test_worker_can_run_a_dropdown_option():
    worker = load("worker")
    assert worker.MODELS["claude-sonnet-5-5"]["modelId"] == "us.anthropic.claude-sonnet-5-5"
    assert worker.MODELS["claude-haiku-5-5"]["pricing"]["outputPerMTok"] == 0.55


def test_create_fans_out_one_worker_per_model():
    api = load("api")
    res = api.handler(event("POST", "/api/generations", {"prompt": "a red mug"}), None)
    assert res["statusCode"] == 202
    assert api._lambda.invoke.call_count == len(MODELS)
    payloads = [json.loads(c.kwargs["Payload"]) for c in api._lambda.invoke.call_args_list]
    assert {p["modelKey"] for p in payloads} == {m["key"] for m in MODELS}
    assert all(c.kwargs["InvocationType"] == "Event" for c in api._lambda.invoke.call_args_list)
    item = api.TABLE.put_item.call_args.kwargs["Item"]
    assert item["owner"] == "public"
    quota = api.TABLE.update_item.call_args.kwargs
    assert quota["Key"]["jobId"].startswith("quota#")
    assert quota["ExpressionAttributeValues"][":limit"] == 100


def test_create_refuses_past_the_daily_limit():
    api = load("api")

    class Exhausted(Exception):
        pass

    api.TABLE.meta.client.exceptions.ConditionalCheckFailedException = Exhausted
    api.TABLE.update_item.side_effect = Exhausted()
    res = api.handler(event("POST", "/api/generations", {"prompt": "a red mug"}), None)
    assert res["statusCode"] == 429
    api._lambda.invoke.assert_not_called()
    api.TABLE.put_item.assert_not_called()


@pytest.mark.parametrize("prompt", ["", "ab", "x" * 1001, 42])
def test_create_rejects_bad_prompts(prompt):
    api = load("api")
    assert api.handler(event("POST", "/api/generations", {"prompt": prompt}), None)["statusCode"] == 400


def test_create_requires_json_content_type():
    api = load("api")
    res = api.handler(event("POST", "/api/generations", {"prompt": "a mug"}, content_type="text/plain"), None)
    assert res["statusCode"] == 415


def test_models_need_no_sign_in():
    api = load("api")
    assert api.handler(event("GET", "/api/models"), None)["statusCode"] == 200


def test_get_rejects_non_uuid_ids_such_as_quota_counters():
    api = load("api")
    assert api.handler(event("GET", "/api/generations/{jobId}", job_id="quota#2026-11-18"), None)["statusCode"] == 404
    api.TABLE.get_item.assert_not_called()


def test_get_returns_cloudfront_paths_for_successful_results():
    api = load("api")
    job = "0b3f5a8e-6d1c-4c3e-9a51-1b2a3c4d5e6f"
    api.TABLE.get_item.return_value = {"Item": {
        "jobId": job, "owner": "public", "prompt": "p", "createdAt": "2026-11-11T00:00:00+00:00",
        "modelOrder": ["glm-5"],
        "models": {"glm-5": {"status": "SUCCEEDED", "glbKey": f"generations/{job}/glm-5/model.glb",
                              "scriptKey": f"generations/{job}/glm-5/script.py", "costUsd": Decimal("0.0123")}},
    }}
    body = json.loads(api.handler(event("GET", "/api/generations/{jobId}", job_id=job), None)["body"])
    r = body["results"][0]
    assert r["glbUrl"] == f"/generations/{job}/glm-5/model.glb" and "glbKey" not in r and r["costUsd"] == 0.0123


def test_worker_records_cost_and_files():
    worker = load("worker")
    glb = b"glTF-fake"
    import base64
    agent_result = {
        "status": "SUCCEEDED", "glbBase64": base64.b64encode(glb).decode(), "glbBytes": len(glb), "script": "import bpy",
        "attempts": [{"attempt": 1, "ok": True}],
        "usage": {"inputTokens": 1_000_000, "outputTokens": 100_000, "totalTokens": 1_100_000, "llmCalls": 1},
        "timings": {"llmSeconds": 12.3, "blenderSeconds": 1.1},
    }
    worker._agentcore.invoke_agent_runtime.return_value = {"response": mock.Mock(read=lambda: json.dumps(agent_result).encode())}
    worker.handler({"jobId": "0b3f5a8e-6d1c-4c3e-9a51-1b2a3c4d5e6f", "modelKey": "gpt-6-1-sol", "prompt": "mug"}, None)
    final = worker.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"]
    assert final["status"] == "SUCCEEDED"
    assert final["costUsd"] == Decimal("3.3")  # 1M in x $2.2 + 0.1M out x $11
    assert final["glbKey"] == "generations/0b3f5a8e-6d1c-4c3e-9a51-1b2a3c4d5e6f/gpt-6-1-sol/model.glb"
    assert worker._s3.put_object.call_count == 2
    kwargs = worker._agentcore.invoke_agent_runtime.call_args.kwargs
    assert len(kwargs["runtimeSessionId"]) >= 33


def test_worker_marks_failure_when_agent_call_raises():
    worker = load("worker")
    worker._agentcore.invoke_agent_runtime.side_effect = RuntimeError("boom")
    worker.handler({"jobId": "j", "modelKey": "gpt-6-luna", "prompt": "mug"}, None)
    final = worker.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"]
    assert final["status"] == "FAILED" and "RuntimeError" in final["error"]


def test_worker_sends_luna_a_plain_converse_payload():
    worker = load("worker")
    worker._agentcore.invoke_agent_runtime.return_value = agent_ok()
    worker.handler({"jobId": DONE_JOB_ID, "modelKey": "gpt-6-luna", "prompt": "mug"}, FakeContext(900))
    payload = json.loads(worker._agentcore.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload == {"prompt": "mug", "api": "converse", "modelId": "us.openai.gpt-6-luna"}


def test_worker_passes_a_mantle_path_through():
    worker = load("worker")
    worker.MODELS["m"] = {"key": "m", "api": "mantle", "modelId": "google.gemma-4-31b", "mantlePath": "/openai/v1",
                          "pricing": {"inputPerMTok": 1, "outputPerMTok": 1}}
    worker._agentcore.invoke_agent_runtime.return_value = agent_ok()
    worker.handler({"jobId": DONE_JOB_ID, "modelKey": "m", "prompt": "mug"}, FakeContext(900))
    assert json.loads(worker._agentcore.invoke_agent_runtime.call_args.kwargs["payload"])["mantlePath"] == "/openai/v1"


def test_worker_sends_a_model_its_own_output_limit():
    worker = load("worker")
    worker.MODELS["capped"] = {"key": "capped", "api": "converse", "modelId": "x.y-v1:0", "pricing": {"inputPerMTok": 1, "outputPerMTok": 1},
                               "maxOutputTokens": 8192}
    worker._agentcore.invoke_agent_runtime.return_value = agent_ok()
    worker.handler({"jobId": DONE_JOB_ID, "modelKey": "capped", "prompt": "mug"}, FakeContext(900))
    assert json.loads(worker._agentcore.invoke_agent_runtime.call_args.kwargs["payload"])["maxOutputTokens"] == 8192


def test_worker_sends_model_request_fields_to_the_agent():
    worker = load("worker")
    worker.MODELS["reasoner"] = {"key": "reasoner", "api": "converse", "modelId": "us.x.y", "pricing": {"inputPerMTok": 1, "outputPerMTok": 1},
                                 "requestFields": {"reasoning": {"effort": "low"}}}
    worker._agentcore.invoke_agent_runtime.return_value = agent_ok()
    worker.handler({"jobId": DONE_JOB_ID, "modelKey": "reasoner", "prompt": "mug"}, FakeContext(900))
    payload = json.loads(worker._agentcore.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["requestFields"] == {"reasoning": {"effort": "low"}}


def test_worker_retries_once_after_a_cold_start_timeout():
    worker = load("worker")
    cold = RuntimeClientError("Runtime initialization time exceeded. Please make sure that initialization completes in 120s.")
    worker._agentcore.invoke_agent_runtime.side_effect = [cold, agent_ok()]
    worker.handler({"jobId": DONE_JOB_ID, "modelKey": "gpt-6-1-sol", "prompt": "mug"}, FakeContext(700))
    calls = worker._agentcore.invoke_agent_runtime.call_args_list
    assert len(calls) == 2
    assert calls[1].kwargs["runtimeSessionId"].endswith("-r2")
    assert json.loads(calls[1].kwargs["payload"])["deadlineSeconds"] == 640
    final = worker.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"]
    assert final["status"] == "SUCCEEDED"


def test_worker_does_not_retry_cold_start_without_enough_time():
    worker = load("worker")
    worker._agentcore.invoke_agent_runtime.side_effect = RuntimeClientError("Runtime initialization time exceeded.")
    worker.handler({"jobId": DONE_JOB_ID, "modelKey": "gpt-6-1-sol", "prompt": "mug"}, FakeContext(200))
    assert worker._agentcore.invoke_agent_runtime.call_count == 1
    final = worker.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"]
    assert final["status"] == "FAILED" and "RuntimeClientError" in final["error"]


DONE_JOB_ID = "0b3f5a8e-6d1c-4c3e-9a51-1b2a3c4d5e6f"


def test_gallery_prefers_astra_and_falls_back_to_another_model():
    api = load("api")
    job_a, job_b, job_c, job_d = (f"0b3f5a8e-6d1c-4c3e-9a51-1b2a3c4d5e6{i}" for i in "abcd")
    order = [m["key"] for m in MODELS]
    old_order = ["gpt-6-astra", "gpt-6-1-sol", "claude-opus-5-5", "gpt-6-luna"]  # before GPT-6.2 Astra
    ok = lambda job, k: {"status": "SUCCEEDED", "glbKey": f"generations/{job}/{k}/model.glb"}
    api.TABLE.query.return_value = {
        "Items": [
            {"jobId": job_a, "prompt": "mug", "createdAt": "2026-11-18T03:00:00+00:00", "modelOrder": order,
             "models": {"gpt-6-2-astra": ok(job_a, "gpt-6-2-astra"), "gpt-6-1-sol": ok(job_a, "gpt-6-1-sol")}},
            {"jobId": job_b, "prompt": "tree", "createdAt": "2026-11-18T02:00:00+00:00", "modelOrder": order,
             "models": {"gpt-6-2-astra": {"status": "FAILED"}, "claude-opus-5-5": ok(job_b, "claude-opus-5-5")}},
            {"jobId": job_d, "prompt": "lamp", "createdAt": "2026-11-18T01:00:00+00:00", "modelOrder": old_order,
             "models": {"gpt-6-1-sol": ok(job_d, "gpt-6-1-sol"), "gpt-6-astra": ok(job_d, "gpt-6-astra")}},
            {"jobId": job_c, "prompt": "chair", "createdAt": "2026-11-18T00:00:00+00:00", "modelOrder": order,
             "models": {k: {"status": "FAILED"} for k in order}},
        ],
        "LastEvaluatedKey": {"jobId": job_c, "owner": "public", "createdAt": "2026-11-18T00:00:00+00:00"},
    }
    body = json.loads(api.handler(event("GET", "/api/gallery"), None)["body"])
    a, b, d, c = body["items"]
    assert a["preview"]["modelKey"] == "gpt-6-2-astra" and a["preview"]["glbUrl"] == f"/generations/{job_a}/gpt-6-2-astra/model.glb"
    assert b["preview"]["modelKey"] == "claude-opus-5-5"
    assert d["preview"]["modelKey"] == "gpt-6-astra" and d["preview"]["modelName"] == "GPT-6 Astra"  # older generation
    assert c["preview"] is None and c["finished"] == len(order) and c["succeeded"] == 0
    # The token round-trips into ExclusiveStartKey on the next page.
    page2 = event("GET", "/api/gallery")
    page2["queryStringParameters"] = {"nextToken": body["nextToken"]}
    api.handler(page2, None)
    assert api.TABLE.query.call_args.kwargs["ExclusiveStartKey"]["jobId"] == job_c


@pytest.mark.parametrize("token", ["not-base64!", "eyJ4IjoxfQ==", "eyJqb2JJZCI6IngiLCJvd25lciI6InNvbWVvbmUiLCJjcmVhdGVkQXQiOiJ5In0="])
def test_gallery_rejects_forged_tokens(token):
    api = load("api")
    e = event("GET", "/api/gallery")
    e["queryStringParameters"] = {"nextToken": token}
    assert api.handler(e, None)["statusCode"] == 400
    api.TABLE.query.assert_not_called()


DONE_JOB = "0b3f5a8e-6d1c-4c3e-9a51-1b2a3c4d5e6f"


def _job(statuses):
    return {"Item": {"jobId": DONE_JOB, "owner": "public", "prompt": "p", "createdAt": "2026-11-18T00:00:00+00:00",
                     "modelOrder": list(statuses), "models": {k: {"status": v, "startedAt": "2026-11-18T00:00:00+00:00"} for k, v in statuses.items()}}}


def test_delete_removes_files_and_the_job():
    api = load("api")
    api.TABLE.get_item.return_value = _job({"gpt-6-astra": "SUCCEEDED", "glm-5": "FAILED"})
    api._s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": f"generations/{DONE_JOB}/gpt-6-astra/model.glb"}, {"Key": f"generations/{DONE_JOB}/gpt-6-astra/script.py"}]},
    ]
    res = api.handler(event("DELETE", "/api/generations/{jobId}", job_id=DONE_JOB), None)
    assert res["statusCode"] == 200 and json.loads(res["body"])["deletedFiles"] == 2
    assert api._s3.get_paginator.return_value.paginate.call_args.kwargs["Prefix"] == f"generations/{DONE_JOB}/"
    api.TABLE.delete_item.assert_called_once_with(Key={"jobId": DONE_JOB})


def test_delete_refuses_while_a_model_is_running():
    api = load("api")
    job = _job({"gpt-6-astra": "SUCCEEDED", "glm-5": "RUNNING"})
    job["Item"]["models"]["glm-5"]["startedAt"] = api.datetime.now(api.timezone.utc).isoformat()
    api.TABLE.get_item.return_value = job
    assert api.handler(event("DELETE", "/api/generations/{jobId}", job_id=DONE_JOB), None)["statusCode"] == 409
    api.TABLE.delete_item.assert_not_called()
    api._s3.delete_objects.assert_not_called()


@pytest.mark.parametrize("job_id", ["quota#2026-11-18", "../x", DONE_JOB])
def test_delete_unknown_or_invalid_ids_is_404(job_id):
    api = load("api")
    api.TABLE.get_item.return_value = {}
    assert api.handler(event("DELETE", "/api/generations/{jobId}", job_id=job_id), None)["statusCode"] == 404
    api.TABLE.delete_item.assert_not_called()


def test_worker_stores_mesh_metrics_and_drops_unknown_fields():
    import base64
    worker = load("worker")
    mesh = {"objects": 2, "pieces": 3, "vertices": 100, "faces": 90, "triangles": 180, "holes": 1, "boundaryEdges": 4,
            "nonManifoldEdges": 0, "flippedFaces": 0, "looseVertices": 2, "looseEdges": 0, "zeroAreaFaces": 0,
            "duplicateVertices": 5, "watertight": False, "evil": "<script>", "faces_str": "9"}
    body = {"status": "SUCCEEDED", "glbBase64": base64.b64encode(b"glTF").decode(), "glbBytes": 4, "script": "import bpy",
            "attempts": [{"attempt": 1, "ok": True}], "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "meshMetrics": mesh}
    worker._agentcore.invoke_agent_runtime.return_value = {"response": mock.Mock(read=lambda: json.dumps(body).encode())}
    worker.handler({"jobId": DONE_JOB_ID, "modelKey": "gpt-6-luna", "prompt": "mug"}, FakeContext(900))
    stored = worker.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"]["mesh"]
    assert stored["triangles"] == 180 and stored["holes"] == 1 and stored["watertight"] is False
    assert "evil" not in stored and "faces_str" not in stored


def test_worker_skips_missing_or_bad_mesh_metrics():
    worker = load("worker")
    assert worker._mesh_metrics(None) is None
    assert worker._mesh_metrics("nope") is None
    assert worker._mesh_metrics({"watertight": True}) is None  # no counts at all


# ---------- Image to 3D ----------
import base64 as _b64
import struct as _struct

IMAGE_JOB = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


def png_header(width, height):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + _struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\x00" * 32


def jpeg_header(width, height):
    app0 = b"\xff\xe0" + _struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof2 = b"\xff\xc2" + _struct.pack(">HBHH", 17, 8, height, width) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + sof2 + b"\x00" * 16


def image_event(data, **extra):
    return event("POST", "/api/image-generations", {"image": _b64.b64encode(data).decode(), **extra})


@pytest.mark.parametrize("data,fmt,ext", [(png_header(800, 600), "png", "png"), (jpeg_header(1024, 1024), "jpeg", "jpg")])
def test_image_upload_goes_to_the_uploads_bucket_for_scanning(data, fmt, ext):
    api = load("api")
    res = api.handler(image_event(data, description="a robot dog"), None)
    assert res["statusCode"] == 202
    body = json.loads(res["body"])
    put = api._s3.put_object.call_args.kwargs
    assert put["Bucket"] == "uploads-b" and put["Key"] == f"uploads/{body['jobId']}/source.{ext}" and put["ContentType"] == f"image/{fmt}"
    assert api.TABLE.update_item.call_args.kwargs["Key"]["jobId"].startswith("uploads#")  # the upload cap, not the prompt cap
    api._lambda.invoke.assert_not_called()  # models start only after a clean scan
    item = api.TABLE.put_item.call_args.kwargs["Item"]
    assert item["kind"] == "image" and item["input"]["status"] == "SCANNING" and item["description"] == "a robot dog"
    assert body["input"]["status"] == "SCANNING" and all(r["status"] == "PENDING" for r in body["results"])


@pytest.mark.parametrize("data", [
    b"GIF89a" + b"\x00" * 40,       # not JPEG or PNG
    png_header(40, 40),               # too small
    png_header(1000, 300),            # too wide
    png_header(4000, 4000),           # too many pixels
])
def test_image_upload_rejects_bad_images(data):
    api = load("api")
    assert api.handler(image_event(data), None)["statusCode"] == 400
    api._s3.put_object.assert_not_called()
    api.TABLE.update_item.assert_not_called()


def test_image_upload_rejects_bad_base64_and_long_descriptions():
    api = load("api")
    bad = event("POST", "/api/image-generations", {"image": "not base64!"})
    assert api.handler(bad, None)["statusCode"] == 400
    assert api.handler(image_event(png_header(500, 500), description="x" * 301), None)["statusCode"] == 400
    api._s3.put_object.assert_not_called()


def test_image_upload_refuses_past_the_daily_upload_limit():
    api = load("api")

    class Exhausted(Exception):
        pass

    api.TABLE.meta.client.exceptions.ConditionalCheckFailedException = Exhausted
    api.TABLE.update_item.side_effect = Exhausted()
    res = api.handler(image_event(png_header(500, 500)), None)
    assert res["statusCode"] == 429 and "image uploads" in json.loads(res["body"])["message"]
    api._s3.put_object.assert_not_called()


def test_image_job_that_never_finished_scanning_shows_as_failed():
    api = load("api")
    api.TABLE.get_item.return_value = {"Item": {
        "jobId": IMAGE_JOB, "owner": "public", "kind": "image", "prompt": "Image to 3D", "createdAt": "2026-11-18T00:00:00+00:00",
        "modelOrder": ["gpt-6-luna"], "models": {"gpt-6-luna": {"status": "PENDING"}},
        "input": {"status": "SCANNING", "updatedAt": "2026-11-18T00:00:00+00:00"},
    }}
    body = json.loads(api.handler(event("GET", "/api/generations/{jobId}", job_id=IMAGE_JOB), None)["body"])
    assert body["input"]["status"] == "FAILED" and body["results"][0]["status"] == "FAILED"


def scan_event(result="NO_THREATS_FOUND", key=f"uploads/{IMAGE_JOB}/source.jpg", bucket="uploads-b", status="COMPLETED"):
    return {"detail": {"scanStatus": status, "resourceType": "S3_OBJECT",
                       "s3ObjectDetails": {"bucketName": bucket, "objectKey": key},
                       "scanResultDetails": {"scanResultStatus": result, "threats": None}}}


def scanning_job():
    return {"Item": {"jobId": IMAGE_JOB, "kind": "image", "description": "a robot dog", "modelOrder": ["gpt-6-2-astra", "gpt-6-luna"],
                     "models": {}, "input": {"status": "SCANNING"}}}


CUTOUT = b"\x89PNG\r\n\x1a\n" + b"cutout"


def test_prepare_removes_the_background_and_starts_every_model():
    prep = load("prepare")
    prep.TABLE.get_item.return_value = scanning_job()
    prep._s3.get_object.return_value = {"Body": mock.Mock(read=lambda: b"\xff\xd8jpeg")}
    prep._bedrock.invoke_model.return_value = {"body": mock.Mock(read=lambda: json.dumps(
        {"images": [_b64.b64encode(CUTOUT).decode()], "finish_reasons": [None]}).encode())}
    prep.handler(scan_event(), None)
    put = prep._s3.put_object.call_args.kwargs
    assert put["Bucket"] == "b" and put["Key"] == f"generations/{IMAGE_JOB}/input.png" and put["Body"] == CUTOUT
    prep._s3.delete_object.assert_called_once_with(Bucket="uploads-b", Key=f"uploads/{IMAGE_JOB}/source.jpg")
    payloads = [json.loads(c.kwargs["Payload"]) for c in prep._lambda.invoke.call_args_list]
    assert [p["modelKey"] for p in payloads] == ["gpt-6-2-astra", "gpt-6-luna"]
    assert all(p["imageKey"] == f"generations/{IMAGE_JOB}/input.png" and p["prompt"] == "a robot dog" for p in payloads)
    assert prep.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":i"]["status"] == "READY"


@pytest.mark.parametrize("result,status", [("THREATS_FOUND", "COMPLETED"), ("UNSUPPORTED", "SKIPPED")])
def test_prepare_rejects_uploads_that_are_not_clean(result, status):
    prep = load("prepare")
    prep.TABLE.get_item.return_value = scanning_job()
    prep.handler(scan_event(result=result, status=status), None)
    prep._bedrock.invoke_model.assert_not_called()
    prep._lambda.invoke.assert_not_called()
    prep._s3.put_object.assert_not_called()
    prep._s3.delete_object.assert_called_once()
    values = prep.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert values[":i"]["status"] == "REJECTED" and values[":m0"]["status"] == "FAILED"


@pytest.mark.parametrize("evt", [
    scan_event(bucket="someone-else"),
    scan_event(key="uploads/../x/source.jpg"),
    scan_event(key=f"generations/{IMAGE_JOB}/input.png"),
])
def test_prepare_ignores_other_buckets_and_keys(evt):
    prep = load("prepare")
    prep.handler(evt, None)
    prep.TABLE.get_item.assert_not_called()
    prep._s3.get_object.assert_not_called()


def test_prepare_ignores_a_repeated_scan_event():
    prep = load("prepare")
    job = scanning_job()
    job["Item"]["input"]["status"] = "READY"
    prep.TABLE.get_item.return_value = job
    prep.handler(scan_event(), None)
    prep._s3.get_object.assert_not_called()
    prep._lambda.invoke.assert_not_called()


def test_worker_sends_the_prepared_image_to_the_agent():
    worker = load("worker")
    worker._s3.get_object.return_value = {"Body": mock.Mock(read=lambda: CUTOUT)}
    worker._agentcore.invoke_agent_runtime.return_value = agent_ok()
    worker.handler({"jobId": IMAGE_JOB, "modelKey": "gpt-6-luna", "prompt": "", "imageKey": f"generations/{IMAGE_JOB}/input.png"},
                   FakeContext(900))
    payload = json.loads(worker._agentcore.invoke_agent_runtime.call_args.kwargs["payload"])
    assert _b64.b64decode(payload["imageBase64"]) == CUTOUT


def test_worker_refuses_an_image_from_another_job():
    worker = load("worker")
    worker.handler({"jobId": IMAGE_JOB, "modelKey": "gpt-6-luna", "prompt": "", "imageKey": f"generations/{DONE_JOB}/input.png"},
                   FakeContext(900))
    worker._s3.get_object.assert_not_called()
    worker._agentcore.invoke_agent_runtime.assert_not_called()
    final = worker.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"]
    assert final["status"] == "FAILED"


# ---------- Models whose worker never starts ----------

def _ago(api, minutes):
    return (api.datetime.now(api.timezone.utc) - api.timedelta(minutes=minutes)).isoformat()


def _pending_job(api, minutes):
    return {"Item": {"jobId": DONE_JOB, "owner": "public", "prompt": "p", "createdAt": _ago(api, minutes),
                     "modelOrder": ["gpt-6-luna"], "models": {"gpt-6-luna": {"status": "PENDING"}}}}


@pytest.mark.parametrize("minutes,status", [(9, "PENDING"), (11, "FAILED")])
def test_a_model_that_never_started_fails_after_the_worker_event_expires(minutes, status):
    api = load("api")
    api.TABLE.get_item.return_value = _pending_job(api, minutes)
    body = json.loads(api.handler(event("GET", "/api/generations/{jobId}", job_id=DONE_JOB), None)["body"])
    assert body["results"][0]["status"] == status
    if status == "FAILED":
        assert body["results"][0]["error"] == "The model never started"


@pytest.mark.parametrize("input_status,minutes,status", [
    ("READY", 11, "FAILED"),       # workers started when the image was ready, over 10 minutes ago
    ("READY", 9, "PENDING"),
])
def test_an_image_job_counts_from_when_the_image_was_ready(input_status, minutes, status):
    api = load("api")
    api.TABLE.get_item.return_value = {"Item": {
        "jobId": IMAGE_JOB, "owner": "public", "kind": "image", "prompt": "Image to 3D", "createdAt": _ago(api, 120),
        "modelOrder": ["gpt-6-luna"], "models": {"gpt-6-luna": {"status": "PENDING"}},
        "input": {"status": input_status, "updatedAt": _ago(api, minutes)},
    }}
    body = json.loads(api.handler(event("GET", "/api/generations/{jobId}", job_id=IMAGE_JOB), None)["body"])
    assert body["results"][0]["status"] == status


@pytest.mark.parametrize("minutes,code", [(9, 409), (11, 200)])
def test_delete_waits_while_a_pending_worker_could_still_start(minutes, code):
    api = load("api")
    api.TABLE.get_item.return_value = _pending_job(api, minutes)
    api._s3.get_paginator.return_value.paginate.return_value = []
    assert api.handler(event("DELETE", "/api/generations/{jobId}", job_id=DONE_JOB), None)["statusCode"] == code


def test_create_records_a_model_whose_worker_could_not_be_started():
    api = load("api")
    calls = []

    def invoke(**kw):
        calls.append(json.loads(kw["Payload"])["modelKey"])
        if len(calls) == 2:
            raise RuntimeError("throttled")

    api._lambda.invoke.side_effect = invoke
    res = api.handler(event("POST", "/api/generations", {"prompt": "a red mug"}), None)
    assert res["statusCode"] == 202 and len(calls) == len(MODELS)  # the others still started
    failed = calls[1]
    update = api.TABLE.update_item.call_args.kwargs
    assert update["ExpressionAttributeNames"]["#k"] == failed and update["ExpressionAttributeValues"][":v"]["status"] == "FAILED"
    statuses = {r["model"]["key"]: r["status"] for r in json.loads(res["body"])["results"]}
    assert statuses[failed] == "FAILED" and all(s == "PENDING" for k, s in statuses.items() if k != failed)


def test_prepare_records_a_model_whose_worker_could_not_be_started():
    prep = load("prepare")
    prep.TABLE.get_item.return_value = scanning_job()
    prep._s3.get_object.return_value = {"Body": mock.Mock(read=lambda: b"\xff\xd8jpeg")}
    prep._bedrock.invoke_model.return_value = {"body": mock.Mock(read=lambda: json.dumps(
        {"images": [_b64.b64encode(CUTOUT).decode()], "finish_reasons": [None]}).encode())}
    prep._lambda.invoke.side_effect = [RuntimeError("throttled"), None]
    prep.handler(scan_event(), None)
    assert prep._lambda.invoke.call_count == 2  # the second model still started
    update = prep.TABLE.update_item.call_args.kwargs
    assert update["ExpressionAttributeNames"]["#k"] == "gpt-6-2-astra"
    assert update["ExpressionAttributeValues"][":v"]["status"] == "FAILED"


def failure_event(condition, model_key="gpt-6-luna"):
    return {"requestContext": {"condition": condition, "approximateInvokeCount": 1},
            "requestPayload": {"jobId": DONE_JOB, "modelKey": model_key, "prompt": "p"}}


@pytest.mark.parametrize("condition,message", [
    ("EventAgeExceeded", "never started"),
    ("RetriesExhausted", "stopped unexpectedly"),
])
def test_worker_failure_destination_marks_the_model_failed(condition, message):
    worker = load("worker")
    worker.on_failure(failure_event(condition), None)
    kw = worker.TABLE.update_item.call_args.kwargs
    assert kw["Key"] == {"jobId": DONE_JOB} and kw["ExpressionAttributeNames"]["#k"] == "gpt-6-luna"
    assert kw["ExpressionAttributeValues"][":failed"] == "FAILED" and message in kw["ExpressionAttributeValues"][":error"]
    # Never overwrites a model that already finished.
    assert "NOT #m.#k.#s IN (:failed, :ok)" in kw["ConditionExpression"]


def test_worker_failure_destination_ignores_finished_models_and_bad_events():
    worker = load("worker")

    class ConditionalCheckFailed(Exception):
        pass

    worker.TABLE.meta.client.exceptions.ConditionalCheckFailedException = ConditionalCheckFailed
    worker.TABLE.update_item.side_effect = ConditionalCheckFailed()
    worker.on_failure(failure_event("RetriesExhausted"), None)  # already SUCCEEDED: no error raised
    worker.TABLE.update_item.reset_mock()
    worker.on_failure(failure_event("RetriesExhausted", model_key="not-a-model"), None)
    worker.on_failure({"requestPayload": None}, None)
    worker.TABLE.update_item.assert_not_called()


def test_pending_cutoff_is_after_lambda_drops_the_worker_event():
    # Otherwise a model shown as failed could still start, bill, and write files after a delete.
    api = load("api")
    assert api.NEVER_STARTED_SECONDS == 10 * 60
    assert api.WORKER_MAX_EVENT_AGE_SECONDS < api.NEVER_STARTED_SECONDS


# ---------- Saving a paid result ----------

def _paid_result(glb_b64=None):
    import base64
    return {
        "status": "SUCCEEDED", "glbBase64": glb_b64 if glb_b64 is not None else base64.b64encode(b"glTF").decode(),
        "glbBytes": 4, "script": "import bpy", "attempts": [{"attempt": 1, "ok": True}],
        "usage": {"inputTokens": 1_000_000, "outputTokens": 100_000, "totalTokens": 1_100_000, "llmCalls": 1},
        "meshMetrics": None,
    }


def _run_worker_with(agent_result, put_side_effect=None):
    worker = load("worker")
    worker._agentcore.invoke_agent_runtime.return_value = {"response": mock.Mock(read=lambda: json.dumps(agent_result).encode())}
    if put_side_effect is not None:
        worker._s3.put_object.side_effect = put_side_effect
    worker.handler({"jobId": DONE_JOB, "modelKey": "gpt-6-1-sol", "prompt": "mug"}, None)  # must not raise
    return worker, worker.TABLE.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"]


class S3Down(Exception):
    pass


def _fail_on(suffix):
    def put(**kw):
        if kw["Key"].endswith(suffix):
            raise S3Down("SlowDown")
    return put


@pytest.mark.parametrize("failing,script_saved", [("script.py", False), ("model.glb", True)])
def test_worker_still_records_the_cost_when_saving_a_file_fails(failing, script_saved):
    worker, final = _run_worker_with(_paid_result(), put_side_effect=_fail_on(failing))
    assert final["status"] == "FAILED" and final["error"] == "Saving the result failed: S3Down. Try again."
    assert final["costUsd"] == Decimal("3.3") and final["totalTokens"] == 1_100_000 and final["attempts"] == 1
    assert "glbKey" not in final and "glbBytes" not in final   # never point the page at a missing GLB
    assert ("scriptKey" in final) is script_saved              # only keys of files that were saved


def test_worker_records_the_cost_when_the_glb_is_not_valid_base64():
    worker, final = _run_worker_with(_paid_result(glb_b64="not base64!"))
    assert final["status"] == "FAILED" and final["error"].startswith("Saving the result failed")
    assert final["costUsd"] == Decimal("3.3") and "glbKey" not in final
    assert [c.kwargs["Key"].rsplit("/", 1)[-1] for c in worker._s3.put_object.call_args_list] == ["script.py"]


def test_worker_saves_with_extra_retries_and_short_timeouts():
    worker = load("worker")
    cfg = worker.SAVE_CONFIG
    assert cfg.retries == {"mode": "standard", "max_attempts": 6}
    assert cfg.connect_timeout == 5 and cfg.read_timeout == 20
