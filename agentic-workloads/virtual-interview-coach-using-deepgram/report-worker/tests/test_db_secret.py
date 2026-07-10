"""Tests for the rotation-proof DB password provider.

The provider is what makes RDS password rotation transparent: asyncpg calls it on every connection
attempt, and it returns the LIVE password from Secrets Manager (TTL-cached so a pool-fill burst is
one fetch, but never cached long enough to outlast a rotation that matters). boto3 is stubbed so the
test is offline.
"""

from __future__ import annotations

import sys
import types

import pytest

from src import db_secret

pytestmark = pytest.mark.asyncio


def _stub_boto3(monkeypatch, secrets: dict, calls: list):
    """Install a fake boto3 whose secretsmanager client returns secrets[SecretId] and counts calls."""

    class _Client:
        def get_secret_value(self, SecretId):
            calls.append(SecretId)
            return {"SecretString": secrets[SecretId]}

    fake = types.SimpleNamespace(client=lambda svc, region_name=None: _Client())
    monkeypatch.setitem(sys.modules, "boto3", fake)


async def test_provider_returns_password_field(monkeypatch):
    calls: list = []
    _stub_boto3(monkeypatch, {"arn:secret": '{"username":"icadmin","password":"hunter2"}'}, calls)
    provider = db_secret.make_password_provider("arn:secret", "us-west-2")
    assert await provider() == "hunter2"
    assert calls == ["arn:secret"]


async def test_provider_caches_within_ttl(monkeypatch):
    # A pool opening N connections in a burst must not make N Secrets Manager calls.
    calls: list = []
    _stub_boto3(monkeypatch, {"arn:secret": '{"password":"pw1"}'}, calls)
    provider = db_secret.make_password_provider("arn:secret", "us-west-2", ttl_s=60.0)
    for _ in range(5):
        assert await provider() == "pw1"
    assert len(calls) == 1  # one fetch served all five


async def test_provider_refetches_after_ttl(monkeypatch):
    # After the TTL lapses, the next connect picks up a rotated password (the whole point).
    calls: list = []
    secrets = {"arn:secret": '{"password":"old"}'}
    _stub_boto3(monkeypatch, secrets, calls)
    provider = db_secret.make_password_provider("arn:secret", "us-west-2", ttl_s=0.0)
    assert await provider() == "old"
    secrets["arn:secret"] = '{"password":"rotated"}'  # RDS rotated the master password
    assert await provider() == "rotated"
    assert len(calls) == 2
