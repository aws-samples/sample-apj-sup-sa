"""Helper for writing the HF token into AWS Secrets Manager.

Mirrors the shape of ../../batch/submitter/secrets.py so both subpackages
have the same notebook UX. Used from the benchmark notebook:

    upsert_hf_token(secret_name="medgemma-27b-benchmark/hf-token",
                    value=HF_TOKEN, region=REGION)

The EC2 instance's role (granted by ResourceManager) reads the value
at boot via cloud-init — the token never transits through launch-time
APIs or into user-data.
"""
from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)


def upsert_hf_token(
    secret_name: str,
    value: str,
    *,
    region: str,
    client: Any | None = None,
) -> str:
    """Put ``value`` into the Secrets Manager secret, creating if absent.

    Handles three states:
    * Secret exists → PutSecretValue (rotates).
    * Secret scheduled-for-deletion → RestoreSecret first, then Put.
    * Secret missing → CreateSecret.

    Rejects placeholder / empty values.

    Returns
    -------
    The secret ARN.
    """
    if not value or value.strip() == "":
        raise ValueError("HF token value is empty — refusing to store.")
    if "PLACEHOLDER" in value:
        raise ValueError(
            "Token still contains 'PLACEHOLDER'. Paste your real HF token "
            "into the previous cell before running this."
        )
    if not value.startswith("hf_"):
        LOG.warning("value does not have the expected 'hf_' prefix")

    sm = client or boto3.client("secretsmanager", region_name=region)

    try:
        resp = sm.put_secret_value(SecretId=secret_name, SecretString=value)
        # All log calls below emit only ARN / VersionId / secret-name —
        # never the secret value. Semgrep flags any log line containing
        # "secret" + a format-arg as a potential disclosure heuristic.
        # nosemgrep: python.lang.security.audit.logging.python-logger-credential-disclosure
        LOG.info("Updated %s (version %s)",
                 resp["ARN"], resp.get("VersionId", "?"))
        return resp["ARN"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "InvalidRequestException" and "deletion" in str(exc).lower():
            # nosemgrep: python.lang.security.audit.logging.python-logger-credential-disclosure
            LOG.info("Restoring %s (was scheduled for deletion).", secret_name)
            sm.restore_secret(SecretId=secret_name)
            resp = sm.put_secret_value(SecretId=secret_name, SecretString=value)
            return resp["ARN"]
        if code == "ResourceNotFoundException":
            # nosemgrep: python.lang.security.audit.logging.python-logger-credential-disclosure
            LOG.info("Creating %s (not found).", secret_name)
            resp = sm.create_secret(
                Name=secret_name,
                SecretString=value,
                Description=(
                    f"HuggingFace token for vLLM benchmark. "
                    "Read by EC2 instance role via GetSecretValue."
                ),
            )
            return resp["ARN"]
        raise


__all__ = ["upsert_hf_token"]
