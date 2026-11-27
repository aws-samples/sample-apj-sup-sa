# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Retry transient model errors, not only throttling.

Long module runs make hundreds of model calls; a single ``internalServerException`` or
``modelStreamErrorException`` from Bedrock used to fail the whole run (seen live after 200k
tokens). Strands' strategy retries throttling only; this widens it to the transient service
errors Bedrock documents as retryable.
"""

from __future__ import annotations

from strands.agent import ModelRetryStrategy

TRANSIENT = (
    "internalServerException",
    "InternalServerException",
    "serviceUnavailableException",
    "ServiceUnavailableException",
    "modelStreamErrorException",
    "ModelStreamErrorException",
    "ThrottlingException",
    "throttlingException",
    "ModelNotReadyException",
)


class TransientRetry(ModelRetryStrategy):
    def __init__(self) -> None:
        super().__init__(max_attempts=6, initial_delay=4, max_delay=60)

    def is_retryable(self, exception: Exception) -> bool:
        text = f"{type(exception).__name__} {exception}"
        return super().is_retryable(exception) or any(t in text for t in TRANSIENT)


class ChatRetry(TransientRetry):
    """Chat is interactive: two quick retries, not six long ones. A retry after text has streamed repeats
    that text, so keeping retries short keeps the repeat rare and the wait bounded."""

    def __init__(self) -> None:
        ModelRetryStrategy.__init__(self, max_attempts=3, initial_delay=1, max_delay=4)
