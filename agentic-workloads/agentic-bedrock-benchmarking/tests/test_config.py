"""Unit tests for config, quota logic, and rag chunking — no AWS credentials required."""
import os
import pytest


def test_model_allowed_empty_allowlist():
    os.environ["DEPLOY_MODE"] = "development"
    import importlib
    import config
    importlib.reload(config)
    assert config.model_allowed("any-model-id") is True


def test_model_allowed_with_pattern():
    os.environ["DEPLOY_MODE"] = "development"
    import importlib
    import config
    importlib.reload(config)
    # In dev mode allowlist is empty — everything passes
    assert config.model_allowed("claude-3-5-haiku-20241022") is True
    assert config.model_allowed("amazon.nova-lite-v1:0") is True


def test_chunk_text_basic():
    from rag import chunk_text
    text = "Hello world. " * 100
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 300  # allow some flex for overlap


def test_chunk_text_empty():
    from rag import chunk_text
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_quota_check_dev_mode():
    os.environ["DEPLOY_MODE"] = "development"
    import importlib
    import config
    importlib.reload(config)
    import quota
    importlib.reload(quota)
    result = quota.check_and_increment("user-123", 5)
    assert result.allowed is True


def test_quota_invalid_n():
    os.environ["DEPLOY_MODE"] = "production"
    os.environ["QUOTA_TABLE_NAME"] = "test-table"
    os.environ["DAILY_INVOCATION_LIMIT"] = "50"
    import importlib
    import config
    importlib.reload(config)
    import quota
    importlib.reload(quota)
    result = quota.check_and_increment("user-123", 0)
    assert result.allowed is False
    assert "invalid" in result.reason
