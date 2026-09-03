import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "bedrock_metrics.json"

REFERENCED = {
    "Invocations", "InvocationLatency", "InvocationClientErrors",
    "InvocationServerErrors", "InvocationThrottles", "LegacyModelInvocations",
    "InputTokenCount", "OutputTokenCount",
    "CacheReadInputTokenCount", "CacheWriteInputTokenCount",
    "TimeToFirstToken", "EstimatedTPMQuotaUsage",
    "ModelInvocationLogsCloudWatchDeliverySuccess",
    "ModelInvocationLogsCloudWatchDeliveryFailure",
}


def test_referenced_metrics_appear_in_template():
    text = (ROOT / "cloudformation" / "dashboard.yaml").read_text()
    for m in REFERENCED:
        assert m in text, f"{m} not referenced in template"


@pytest.mark.sandbox
@pytest.mark.skipif(not FIXTURE.exists(), reason="run Stage 0 metric snapshot first")
def test_referenced_metrics_exist_live():
    live = set(json.loads(FIXTURE.read_text()))
    if not live:
        pytest.skip("no live metrics captured")
    core = {"Invocations", "InputTokenCount", "OutputTokenCount",
            "CacheReadInputTokenCount", "CacheWriteInputTokenCount"}
    missing = core - live
    assert not missing, f"template references metrics absent in live account: {missing}"
