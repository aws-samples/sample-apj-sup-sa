"""Tests for submitter.secrets.upsert_hf_token."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from llm_batch_deploy.submitter.secrets import upsert_hf_token


class TestUpsertHfTokenValidation:
    def test_rejects_empty_token(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            upsert_hf_token("arn:x", "", region="us-east-2", client=MagicMock())

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            upsert_hf_token("arn:x", "   ", region="us-east-2", client=MagicMock())

    def test_rejects_placeholder(self) -> None:
        with pytest.raises(ValueError, match="PLACEHOLDER"):
            upsert_hf_token(
                "arn:x", "PLACEHOLDER_something",
                region="us-east-2", client=MagicMock(),
            )

    def test_warns_on_non_hf_prefix(self, caplog) -> None:
        sm = MagicMock()
        sm.put_secret_value.return_value = {"ARN": "arn:x", "VersionId": "v1"}
        upsert_hf_token("arn:x", "not_an_hf_token_1234567890",
                        region="us-east-2", client=sm)
        assert any("not start with 'hf_'" in r.message for r in caplog.records)


class TestUpsertHfTokenHappyPath:
    def test_updates_existing_secret(self) -> None:
        sm = MagicMock()
        sm.put_secret_value.return_value = {
            "ARN": "arn:aws:secretsmanager:us-east-2:123:secret:foo",
            "VersionId": "v42",
        }
        arn = upsert_hf_token(
            "arn:aws:secretsmanager:us-east-2:123:secret:foo",
            "hf_realtokenvalue123",
            region="us-east-2", client=sm,
        )
        assert arn == "arn:aws:secretsmanager:us-east-2:123:secret:foo"
        sm.put_secret_value.assert_called_once_with(
            SecretId="arn:aws:secretsmanager:us-east-2:123:secret:foo",
            SecretString="hf_realtokenvalue123",
        )


class TestUpsertHfTokenEdgeCases:
    def test_restores_scheduled_for_deletion_secret(self) -> None:
        sm = MagicMock()
        # First PutSecretValue fails with scheduled-for-deletion error
        sm.put_secret_value.side_effect = [
            ClientError(
                {"Error": {
                    "Code": "InvalidRequestException",
                    "Message": "You can't perform this operation on the secret because it was marked for deletion.",
                }},
                "PutSecretValue",
            ),
            {"ARN": "arn:foo", "VersionId": "v1"},
        ]
        sm.restore_secret.return_value = {"ARN": "arn:foo"}

        arn = upsert_hf_token("arn:foo", "hf_realtoken", region="us-east-2", client=sm)
        assert arn == "arn:foo"
        sm.restore_secret.assert_called_once_with(SecretId="arn:foo")
        # PutSecretValue called twice: first failed, second after restore
        assert sm.put_secret_value.call_count == 2

    def test_creates_when_not_found(self) -> None:
        sm = MagicMock()
        sm.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "secret does not exist"}},
            "PutSecretValue",
        )
        sm.create_secret.return_value = {"ARN": "arn:new", "Name": "my-secret"}
        arn = upsert_hf_token("my-secret", "hf_realtoken", region="us-east-2", client=sm)
        assert arn == "arn:new"
        sm.create_secret.assert_called_once()

    def test_reraises_other_client_errors(self) -> None:
        sm = MagicMock()
        sm.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
            "PutSecretValue",
        )
        with pytest.raises(ClientError, match="AccessDenied"):
            upsert_hf_token("arn:x", "hf_realtoken", region="us-east-2", client=sm)
