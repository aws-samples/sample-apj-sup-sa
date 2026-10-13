"""Voice-token secret hardening (code-review #3): a DEPLOYED instance (Cognito configured) must NOT
fall back to the public dev secret; local/dev (no Cognito) still works for tests."""

from __future__ import annotations

import dataclasses

import pytest


def _patch(monkeypatch, **overrides):
    from src import voice_token
    patched = dataclasses.replace(voice_token.settings, **overrides)
    monkeypatch.setattr(voice_token, "settings", patched)


def test_real_secret_used_when_set(monkeypatch):
    from src import voice_token
    _patch(monkeypatch, voice_token_secret="a-real-secret-value-32-bytes-long!!", cognito_user_pool_id="us-west-2_pool")
    tok = voice_token.mint_voice_token("sess-1", "user-1")
    assert tok and tok.count(".") == 2  # a signed JWT


def test_deployed_without_secret_fails_fast(monkeypatch):
    from src import voice_token
    _patch(monkeypatch, voice_token_secret=None, cognito_user_pool_id="us-west-2_pool")
    with pytest.raises(RuntimeError):
        voice_token.mint_voice_token("sess-1", "user-1")


def test_local_dev_without_cognito_still_works(monkeypatch):
    from src import voice_token
    _patch(monkeypatch, voice_token_secret=None, cognito_user_pool_id=None)
    tok = voice_token.mint_voice_token("sess-1", "user-1")  # dev fallback OK locally
    assert tok and tok.count(".") == 2
