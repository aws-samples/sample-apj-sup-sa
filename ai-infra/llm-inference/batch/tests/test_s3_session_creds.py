"""Regression test for s3_session() credential resolution.

Background: aiobotocore (>=2.25) shipped in the vllm/vllm-openai:latest base
image had a regression where AioContainerProvider didn't reliably resolve
ECS task credentials in some Batch CE configurations. The fix
(`runtime/s3_io.py:s3_session`) calls sync boto3 credential discovery once
and hands the frozen credentials to the aioboto3 Session. This pins that
shape so a future refactor doesn't accidentally revert.
"""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

from llm_batch_deploy.runtime.s3_io import s3_session


def test_s3_session_passes_explicit_credentials_from_boto3() -> None:
    """If sync boto3 has creds, aioboto3.Session must receive them explicitly."""
    fake_creds = MagicMock()
    fake_frozen = MagicMock()
    # Synthetic test credentials, not real values.
    fake_frozen.access_key = "AKIATEST"  # nosec B105
    fake_frozen.secret_key = "SECRET"  # nosec B105
    fake_frozen.token = "TOKEN"  # nosec B105
    fake_creds.get_frozen_credentials.return_value = fake_frozen
    fake_sync_session = MagicMock()
    fake_sync_session.get_credentials.return_value = fake_creds

    fake_aio_session = MagicMock()
    with patch("boto3.Session", return_value=fake_sync_session):
        with patch("llm_batch_deploy.runtime.s3_io.aioboto3.Session",
                   return_value=fake_aio_session) as mock_aio:
            os.environ["AWS_REGION"] = "us-west-2"
            try:
                result = s3_session()
            finally:
                os.environ.pop("AWS_REGION", None)
            assert result is fake_aio_session
            mock_aio.assert_called_once_with(  # nosec B106
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="SECRET",
                aws_session_token="TOKEN",
                region_name="us-west-2",
            )


def test_s3_session_falls_back_when_boto3_has_no_creds() -> None:
    """If boto3 returns None (no creds), don't crash — return default Session.

    The error will surface on first S3 call instead, but the message will
    be the actual boto3 error rather than a stack trace at session creation.
    """
    fake_sync_session = MagicMock()
    fake_sync_session.get_credentials.return_value = None
    fake_aio_session = MagicMock()
    with patch("boto3.Session", return_value=fake_sync_session):
        with patch("llm_batch_deploy.runtime.s3_io.aioboto3.Session",
                   return_value=fake_aio_session) as mock_aio:
            result = s3_session()
            assert result is fake_aio_session
            # Default Session call: only region_name kwarg (or empty) — no
            # explicit creds when there are none.
            mock_aio.assert_called_once()
            kwargs = mock_aio.call_args.kwargs
            assert "aws_access_key_id" not in kwargs
            assert "aws_secret_access_key" not in kwargs


def test_s3_session_uses_aws_default_region_when_aws_region_unset() -> None:
    fake_creds = MagicMock()
    fake_frozen = MagicMock()
    # Synthetic test credentials.
    fake_frozen.access_key = "X"  # nosec B105
    fake_frozen.secret_key = "Y"  # nosec B105
    fake_frozen.token = None
    fake_creds.get_frozen_credentials.return_value = fake_frozen
    fake_sync = MagicMock()
    fake_sync.get_credentials.return_value = fake_creds
    fake_aio = MagicMock()
    with patch("boto3.Session", return_value=fake_sync):
        with patch("llm_batch_deploy.runtime.s3_io.aioboto3.Session",
                   return_value=fake_aio) as mock_aio:
            os.environ.pop("AWS_REGION", None)
            os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
            try:
                s3_session()
            finally:
                os.environ.pop("AWS_DEFAULT_REGION", None)
            assert mock_aio.call_args.kwargs["region_name"] == "us-east-1"
