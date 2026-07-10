"""Helper for writing the HF token value into the stack's SecretsManager secret.

Typical usage from the notebook::

    stack = deploy(plan)
    upsert_hf_token(stack.hf_token_secret_arn, HF_TOKEN, region=REGION)
    # From here on, HF_TOKEN is no longer needed in-process — the Batch
    # container fetches it from Secrets Manager at task-start.

The helper handles three states:

* Secret exists and is fine → calls ``PutSecretValue`` (updates in place,
  rotates the version).
* Secret is scheduled for deletion (e.g. stack was torn down and re-created
  within the 30-day recovery window) → calls ``RestoreSecret`` first, then
  ``PutSecretValue``.
* Secret does not exist → creates it. This is an escape hatch for
  environments where the stack hasn't been CFN-managed yet; the normal
  path is for CFN to have created the secret.
"""
from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)


def upsert_hf_token(
    secret_arn_or_name: str,
    token_value: str,
    *,
    region: str,
    client: Any | None = None,
) -> str:
    """Write ``token_value`` to the Secrets Manager secret.

    Parameters
    ----------
    secret_arn_or_name
        Either the full ARN (recommended; comes from ``StackOutputs``) or
        the secret name (e.g. ``"medgemma-27b-batch/hf-token"``).
    token_value
        The HuggingFace token to store.
    region
        AWS region where the secret lives.

    Returns
    -------
    The secret ARN.
    """
    if not token_value or token_value.strip() == "":
        raise ValueError("token_value is empty — refusing to store.")
    if "PLACEHOLDER" in token_value:
        raise ValueError(
            "token_value still contains 'PLACEHOLDER'. Paste your real "
            "HF token into the notebook cell before running this."
        )
    if not token_value.startswith("hf_"):
        LOG.warning(
            "token_value does not start with 'hf_' — are you sure this is "
            "a HuggingFace token?"
        )

    sm = client or boto3.client("secretsmanager", region_name=region)

    try:
        # Fast path: secret exists, just update the value.
        resp = sm.put_secret_value(
            SecretId=secret_arn_or_name,
            SecretString=token_value,
        )
        # All log calls below emit only ARN / VersionId / secret-name —
        # never the secret value. Semgrep flags any log line containing
        # "secret" + a format-arg as a potential disclosure heuristic.
        # nosemgrep: python.lang.security.audit.logging.python-logger-credential-disclosure
        LOG.info("Updated %s (version %s)", resp["ARN"], resp.get("VersionId", "?"))
        return resp["ARN"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "InvalidRequestException" and "deletion" in str(exc).lower():
            # nosemgrep: python.lang.security.audit.logging.python-logger-credential-disclosure
            LOG.info("Restoring %s (was scheduled for deletion).",
                     secret_arn_or_name)
            sm.restore_secret(SecretId=secret_arn_or_name)
            resp = sm.put_secret_value(
                SecretId=secret_arn_or_name,
                SecretString=token_value,
            )
            return resp["ARN"]
        if code == "ResourceNotFoundException":
            # nosemgrep: python.lang.security.audit.logging.python-logger-credential-disclosure
            LOG.info(
                "Creating %s (not found — normally CFN creates it).",
                secret_arn_or_name,
            )
            resp = sm.create_secret(
                Name=secret_arn_or_name,
                SecretString=token_value,
                Description="HuggingFace token (created out-of-band by upsert_hf_token).",
            )
            return resp["ARN"]
        raise
