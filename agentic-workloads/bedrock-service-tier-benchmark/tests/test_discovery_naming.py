"""Unit tests for the discovery canonicalisation + display-name helpers."""

from __future__ import annotations

import pytest

from bedrock_bench.discovery import _canonical_key, _display_name, _family


@pytest.mark.parametrize(
    "raw,expected",
    [
        # InvokeModel and Mantle ids for the same model collapse to one key.
        ("openai.gpt-oss-120b-1:0", "openai.gpt-oss-120b"),
        ("openai.gpt-oss-120b", "openai.gpt-oss-120b"),
        ("moonshot.kimi-k2-thinking", "moonshotai.kimi-k2-thinking"),
        ("moonshotai.kimi-k2-thinking", "moonshotai.kimi-k2-thinking"),
        ("qwen.qwen3-235b-a22b-2507-v1:0", "qwen.qwen3-235b-a22b-2507"),
        ("qwen.qwen3-235b-a22b-2507", "qwen.qwen3-235b-a22b-2507"),
        ("qwen.qwen3-next-80b-a3b-instruct", "qwen.qwen3-next-80b-a3b"),
        ("qwen.qwen3-next-80b-a3b", "qwen.qwen3-next-80b-a3b"),
        ("google.gemma-3-27b-it", "google.gemma-3-27b"),
        ("deepseek.v3.2", "deepseek.v3.2"),  # version dots preserved
    ],
)
def test_canonical_key_unifies_transports(raw, expected):
    assert _canonical_key(raw) == expected


def test_canonical_key_pairs_match():
    # The two transport ids for gpt-oss-120b must produce the SAME key,
    # otherwise the model would be double-counted in reports.
    assert _canonical_key("openai.gpt-oss-120b-1:0") == _canonical_key("openai.gpt-oss-120b")


@pytest.mark.parametrize(
    "key,expected",
    [
        ("deepseek.v3.2", "DeepSeek V3.2"),  # version dot preserved, provider kept
        ("qwen.qwen3-235b-a22b-2507", "Qwen3 235B A22B 2507"),  # brand de-duplicated
        ("minimax.minimax-m2.5", "MiniMax M2.5"),
        ("moonshotai.kimi-k2.5", "Kimi K2.5"),
        ("zai.glm-5", "Z.AI GLM 5"),
        ("openai.gpt-oss-120b", "OpenAI GPT OSS 120B"),
    ],
)
def test_display_name(key, expected):
    assert _display_name(key) == expected


def test_family_mapping():
    assert _family("zai.glm-5") == "GLM (Z.AI)"
    assert _family("openai.gpt-oss-20b") == "OpenAI GPT-OSS"
    assert _family("unknownvendor.model") == "Unknownvendor"
