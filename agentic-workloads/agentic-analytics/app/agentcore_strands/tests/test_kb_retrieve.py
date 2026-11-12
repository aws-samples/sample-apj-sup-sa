"""Knowledge base Retrieve calls must work against a managed knowledge base.

A managed knowledge base rejects vectorSearchConfiguration ("Incompatible configuration"),
so neither toolset Lambda may send it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import custom_sql_toolset_lambda as custom  # noqa: E402
import prebaked_sql_toolset_lambda as prebaked  # noqa: E402


class _FakeRuntime:
    def __init__(self, n):
        self.calls = []
        self.n = n

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {"retrievalResults": [{"content": {"text": f"chunk {i}"}, "score": 0.9} for i in range(self.n)]}


def test_custom_sql_rag_retrieve_sends_no_vector_search_config(monkeypatch):
    fake = _FakeRuntime(7)
    monkeypatch.setattr(custom, "KB_ID", "KB123")
    monkeypatch.setattr(custom, "bedrock_agent_runtime", fake)
    results = custom.rag_retrieve("revenue by unicorn color")
    assert "retrievalConfiguration" not in fake.calls[0]
    assert len(results) == 5
    assert results[0]["content"] == "chunk 0"


def test_semantic_search_sends_no_vector_search_config_and_caps_top_k(monkeypatch):
    fake = _FakeRuntime(5)
    monkeypatch.setattr(prebaked, "KNOWLEDGE_BASE_ID", "KB123")
    monkeypatch.setattr(prebaked, "bedrock_agent_runtime", fake)
    body = json.loads(prebaked.semantic_search({"query": "booking duration", "top_k": 3}, {})["body"])
    assert "retrievalConfiguration" not in fake.calls[0]
    assert body["success"] is True
    assert len(body["results"]) == 3
