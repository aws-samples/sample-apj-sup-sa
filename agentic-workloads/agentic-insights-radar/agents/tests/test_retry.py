# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from strands.types.exceptions import ModelThrottledException

from insights_radar_agents.retry import TransientRetry


class EventStreamError(Exception):
    pass


def test_transient_errors_are_retryable() -> None:
    r = TransientRetry()
    assert r.is_retryable(ModelThrottledException("slow down"))
    assert r.is_retryable(EventStreamError("An error occurred (internalServerException) when calling ConverseStream"))
    assert r.is_retryable(EventStreamError("modelStreamErrorException: stream broke"))
    assert not r.is_retryable(ValueError("ValidationException: bad input"))
    assert not r.is_retryable(EventStreamError("AccessDeniedException"))
