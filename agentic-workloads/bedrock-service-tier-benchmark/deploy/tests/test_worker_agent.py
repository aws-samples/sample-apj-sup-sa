"""Discovery agent tools: feed parsing, host pinning, and the verified-only persist path."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
os.environ.setdefault("CLUSTER_ARN", "arn:aws:rds:us-east-1:111122223333:cluster:c")
os.environ.setdefault("WRITER_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111122223333:secret:w")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from bedrock_bench.registry import load_registry  # noqa: E402

from worker import agent  # noqa: E402

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
FEED = b"""<?xml version="1.0"?><rss><channel>
<item><title>Amazon Bedrock adds Flex tier for Kimi K3</title><pubDate>Mon, 06 Oct 2026 10:00:00 GMT</pubDate>
<link>https://aws.amazon.com/x</link></item>
<item><title>Amazon S3 feature</title><pubDate>Mon, 06 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Amazon Bedrock old news</title><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_feed_keeps_recent_bedrock_items():
    items = agent.parse_feed(FEED, days=14, now=NOW)
    assert [i["title"] for i in items] == ["Amazon Bedrock adds Flex tier for Kimi K3"]


def test_parse_feed_rejects_entity_expansion():
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><item><title>&a;</title></item></rss>'
    with pytest.raises(Exception):  # noqa: B017 - defusedxml raises its own EntitiesForbidden
        agent.parse_feed(bomb, days=14, now=NOW)


@pytest.mark.parametrize("url", ["http://aws.amazon.com/feed", "https://evil.example/feed"])
def test_feed_fetch_is_host_pinned(url):
    with pytest.raises(ValueError):
        agent._fetch_feed(url)


def test_probe_and_store_persists_only_verified(monkeypatch):
    specs = load_registry()[:2]
    documented = {s.display_name: s for s in specs}
    stored, events = [], []
    verified = replace(specs[0], verified_at="2026-10-08")
    monkeypatch.setattr(agent, "probe", lambda wanted, broker, regions: [verified])
    monkeypatch.setattr(agent.db, "upsert_spec", stored.append)
    monkeypatch.setattr(agent.db, "record_event", lambda *a: events.append(a))
    out = json.loads(
        agent.probe_and_store_names([specs[0].display_name, specs[1].display_name, "Made Up"], documented, None)
    )
    assert out == {"stored": [specs[0].key], "not_verified": [specs[1].key], "unknown_names": ["Made Up"]}
    assert stored == [verified]


def test_agent_builds_with_only_four_tools():
    a = agent.build_agent(broker=None, model_id="us.anthropic.claude-sonnet-5-5")
    assert sorted(a.tool_names) == ["documented_models", "probe_and_store", "recent_announcements", "registry_summary"]
