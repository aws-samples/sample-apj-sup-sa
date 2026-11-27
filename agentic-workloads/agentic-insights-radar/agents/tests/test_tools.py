# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from botocore.credentials import Credentials
from insights_radar.api.agent_tools import AgentTools
from insights_radar.modules.model import GRANT_TOOLS

from insights_radar_agents.remote_tools import WRITE_TOOLS, RemoteToolFactory, WriteLedger
from insights_radar_agents.tool_api import RUN_HEADER, SigV4HttpxAuth, ToolApi, ToolCallError
from insights_radar_agents.tool_specs import SPECS
from tests.fakes import FakeBackend

BACKEND_TOOLS = set(AgentTools(None).names)
LOCAL_TOOLS = {"http_fetch"}  # served by the runtime itself, not the backend
# The runtime reads these; the model must never get them (MCP URLs, source credentials).
RUNTIME_ONLY = {"run_definition", "source_address", "source_mark_read", "source_inbox_ack"}


def test_every_backend_tool_has_a_spec_and_no_extras() -> None:
    assert set(SPECS) == BACKEND_TOOLS - RUNTIME_ONLY
    assert RUNTIME_ONLY <= BACKEND_TOOLS and not RUNTIME_ONLY & set(SPECS)


def test_every_granted_tool_is_served() -> None:
    granted = set().union(*GRANT_TOOLS.values())
    assert granted <= BACKEND_TOOLS | LOCAL_TOOLS


@pytest.mark.parametrize("name", sorted(SPECS))
def test_spec_shape(name: str) -> None:
    spec = SPECS[name].to_strands()
    schema = spec["inputSchema"]["json"]
    assert spec["name"] == name and 20 <= len(spec["description"]) <= 400
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])
    json.dumps(spec)


def test_write_tools_are_backend_tools() -> None:
    assert WRITE_TOOLS <= BACKEND_TOOLS


def test_sigv4_auth_signs_body_and_host() -> None:
    creds = Credentials("AKIAEXAMPLE", "secret", "session-token")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"result": 1})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), auth=SigV4HttpxAuth(creds, "us-east-1", "execute-api")
    )
    api = ToolApi("https://abc.execute-api.us-east-1.amazonaws.com/", "tok", httpx.Auth(), client)
    assert api.call("kg_schema", {}) == 1
    assert seen["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/")
    assert "execute-api/aws4_request" in seen["authorization"]
    assert seen["x-amz-security-token"] == "session-token" and seen[RUN_HEADER] == "tok"
    assert api.calls == {"kg_schema": 1}
    api.close()


@pytest.mark.parametrize(
    ("response", "code", "fragment"),
    [
        (httpx.Response(403, json={"code": "forbidden", "message": "no"}), "forbidden", "no"),
        (httpx.Response(502, text="bad gateway"), "error", "bad gateway"),
        (httpx.Response(400, json=["x"]), "error", ""),
        (
            httpx.Response(400, json={"code": "validation_failed", "message": "m", "details": {"f": 1}}),
            "validation_failed",
            '"f": 1',
        ),
    ],
)
def test_tool_api_errors(response: httpx.Response, code: str, fragment: str) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(ToolCallError) as err:
        ToolApi("https://x", "t", httpx.Auth(), client).call("kg_get", {"id": "a/b"})
    assert err.value.code == code and fragment in err.value.message


def test_tool_api_non_object_result() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[1])))
    assert ToolApi("https://x", "t", httpx.Auth(), client).call("kg_get", {}) is None


def test_factory_builds_only_known_granted_tools_and_counts_writes() -> None:
    backend = FakeBackend(tools=["kg_search", "kg_link", "kg_save_output", "http_fetch"])
    api = ToolApi("https://x", "t", httpx.Auth(), backend.client())
    factory = RemoteToolFactory(api)
    tools = {t.tool_name: t for t in factory.build(["kg_search", "kg_link", "kg_save_output", "http_fetch", "zzz"])}
    assert set(tools) == {"kg_search", "kg_link", "kg_save_output"}

    def invoke(name: str, args: Any) -> Any:
        return tools[name]._tool_func({"toolUseId": "1", "name": name, "input": args})

    assert invoke("kg_search", {"query": "x"})["status"] == "success"
    assert invoke("kg_link", {"from": "a/1", "type": "about", "to": "b/2"})["status"] == "success"
    assert invoke("kg_save_output", "not a dict")["status"] == "success"
    assert factory.ledger.counts == {"kg_link": 1, "kg_save_output": 1} and factory.ledger.nodes_written == 1
    assert backend.calls_to("kg_save_output") == [{}]


def test_ledger_defaults() -> None:
    assert WriteLedger().nodes_written == 0


def test_large_results_are_truncated() -> None:
    backend = FakeBackend(handlers={"kg_search": lambda b: (200, "x" * 200_000)})
    tool = RemoteToolFactory(ToolApi("https://x", "t", httpx.Auth(), backend.client())).build(["kg_search"])[0]
    text = tool._tool_func({"toolUseId": "1", "name": "kg_search", "input": {}})["content"][0]["text"]
    assert text.endswith("[truncated]") and len(text) < 100_100


def test_credentials_are_required_for_signing() -> None:
    from insights_radar_agents.tool_api import credentials_of

    class NoCreds:
        def get_credentials(self) -> None:
            return None

    with pytest.raises(RuntimeError, match="no AWS credentials"):
        credentials_of(NoCreds())


# ---- P3-088: a call that never ran is retried -------------------------------------------------------


def test_p3_088_a_throttled_or_unconnected_call_is_retried_and_nothing_else_is() -> None:
    attempts: list[str] = []
    answers = iter(["429", "connect", "200"])

    def handler(request: httpx.Request) -> httpx.Response:
        answer = next(answers)
        attempts.append(answer)
        if answer == "connect":
            raise httpx.ConnectError("refused", request=request)
        if answer == "429":
            return httpx.Response(429, json={"message": "Rate exceeded"})
        return httpx.Response(200, json={"result": {"ok": True}})

    sleeps: list[float] = []
    api = ToolApi("https://x", "t", httpx.Auth(), httpx.Client(transport=httpx.MockTransport(handler)), sleeps.append)
    assert api.call("run_context", {}) == {"ok": True}
    assert attempts == ["429", "connect", "200"] and len(sleeps) == 2 and api.calls == {"run_context": 1}

    always = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"message": "slow"})))
    with pytest.raises(ToolCallError) as err:
        ToolApi("https://x", "t", httpx.Auth(), always, sleeps.append).call("kg_get", {})
    assert err.value.status == 429

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(httpx.ConnectError):
        ToolApi("https://x", "t", httpx.Auth(), httpx.Client(transport=httpx.MockTransport(down)), sleeps.append).call(
            "kg_get", {}
        )

    served: list[int] = []

    def failing(request: httpx.Request) -> httpx.Response:
        served.append(1)
        return httpx.Response(503, json={"code": "genai_disabled", "message": "paused"})

    with pytest.raises(ToolCallError):
        ToolApi("https://x", "t", httpx.Auth(), httpx.Client(transport=httpx.MockTransport(failing))).call(
            "kg_save_output", {}
        )
    assert served == [1]  # a write that may have run is never sent twice


def test_p3_083_the_tool_library_does_not_log_full_addresses() -> None:
    from insights_radar_agents.settings import QUIET_LOGGERS

    assert "strands_tools" in QUIET_LOGGERS


# ---- A3-018 to A3-022, A3-024: the specs never invite a call the backend refuses ----------------------


def test_a3_024_spec_limits_match_the_backend() -> None:
    import inspect
    import re

    from insights_radar.api import agent_tools
    from insights_radar.radars import service

    from insights_radar_agents import tool_specs

    assert tool_specs.MAX_LINKS_PER_CALL == agent_tools.MAX_LINKS_PER_CALL
    assert tool_specs.DRAFT_MAX_CHARS == agent_tools.DRAFT_MAX_BYTES
    for name in ("kg_save_output", "kg_update_output", "kg_add_evidence", "radar_suggest"):
        for key, prop in SPECS[name].properties.items():
            if prop.get("type") == "array" and "maxItems" in prop and key != "types":
                assert prop["maxItems"] <= agent_tools.MAX_LINKS_PER_CALL, (name, key)
    neighbors = inspect.getsource(agent_tools.AgentTools.kg_neighbors)
    (high,) = re.findall(r'"max_nodes".*?high=(\d+)', neighbors)
    assert SPECS["kg_neighbors"].properties["max_nodes"]["maximum"] == int(high)
    assert "ids" in inspect.getsource(agent_tools.AgentTools.kg_get) and "ids" in SPECS["kg_get"].properties
    assert SPECS["kg_get"].properties["ids"]["maxItems"] == agent_tools.MAX_LINKS_PER_CALL
    for field in ("title", "sector", "ring", *service._ENTRY_OPTIONAL):
        assert field in SPECS["radar_suggest"].properties["proposal"]["description"], field
    assert SPECS["draft_save"].properties["markdown"]["maxLength"] == agent_tools.DRAFT_MAX_BYTES


ORG = "organization/0190a6c4-0b5e-7c3a-9f00-000000000001"


def test_ledger_keeps_the_ids_of_the_entities_the_run_created() -> None:
    """Daily check 2026-11-10: finalize removes the run's own unlinked entities, so the runtime reports them."""
    ledger = WriteLedger()
    ledger.record("kg_upsert_entity", {"id": ORG, "created": True})
    ledger.record("kg_upsert_entity", {"id": ORG, "created": True})  # the same id twice is reported once
    ledger.record("kg_upsert_entity", {"id": "topic/0190a6c4-0b5e-7c3a-9f00-000000000002", "created": False})
    ledger.record("kg_upsert_entity", {"id": "Organization/not-an-id", "created": True})
    ledger.record("kg_upsert_entity", {"id": 7, "created": True})
    ledger.record("kg_upsert_entity", {"id": ORG.replace("1", "3"), "created": "yes"})
    ledger.record("kg_upsert_entity", ["not", "a", "dict"])
    ledger.record("kg_add_evidence", {"id": "evidence/0190a6c4-0b5e-7c3a-9f00-000000000004", "created": True})
    ledger.record("kg_upsert_entity")
    assert ledger.created == [ORG] and ledger.counts == {"kg_upsert_entity": 8, "kg_add_evidence": 1}


def test_ledger_caps_the_created_ids() -> None:
    from insights_radar_agents.remote_tools import MAX_CREATED_ENTITIES

    ledger = WriteLedger()
    for n in range(MAX_CREATED_ENTITIES + 5):
        ledger.record("kg_upsert_entity", {"id": f"topic/0190a6c4-0b5e-7c3a-9f00-{n:012x}", "created": True})
    assert len(ledger.created) == MAX_CREATED_ENTITIES


def test_factory_records_a_created_entity_from_the_backend_answer() -> None:
    backend = FakeBackend(tools=["kg_upsert_entity"])
    backend.handlers["kg_upsert_entity"] = lambda body: (200, {"id": ORG, "created": True, "title": "FCDO"})
    factory = RemoteToolFactory(ToolApi("https://x", "t", httpx.Auth(), backend.client()))
    (tool,) = factory.build(["kg_upsert_entity"])
    out = tool._tool_func({"toolUseId": "1", "name": "kg_upsert_entity", "input": {"type": "organization"}})
    assert out["status"] == "success" and factory.ledger.created == [ORG]


def test_ledger_keeps_a_created_entity_a_later_write_named() -> None:
    # Code review r50: an entity named by a later write (a radar suggestion's evidence, an insight's supports)
    # is in use even when that use is not a graph edge, so finalize must not remove it.
    from insights_radar_agents.remote_tools import WriteLedger

    named = "topic/0190a6c4-0b5e-7c3a-9f00-00000000000a"
    alone = "topic/0190a6c4-0b5e-7c3a-9f00-00000000000b"
    ledger = WriteLedger()
    ledger.record("kg_add_evidence", {"id": "x"}, {"about": named})  # before any entity: nothing to match
    ledger.record("kg_upsert_entity", {"id": named, "created": True}, {"name": "A"})
    ledger.record("kg_upsert_entity", {"id": alone, "created": True}, {"name": "B"})
    ledger.record("radar_suggest", {"ok": True}, {"evidence": [named]})
    ledger.record("kg_link", {"ok": True})  # no arguments reported
    assert ledger.created == [named, alone] and ledger.unreferenced == [alone]
