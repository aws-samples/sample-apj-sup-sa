"""SG ingress self-heal against caller egress-IP rotation.

Corporate NAT egress IPs can rotate mid-run: the per-experiment SG holds the
caller /32 captured at creation time,
so a rotation silently blocks the vLLM ready-poll for the full timeout while
the endpoint is healthy. The fix: ``ResourceManager.refresh_caller_ingress``
is called from *inside* ``DeploymentRunner._wait_for_vllm_ready``'s loop,
detecting rotation and adding an additive ingress rule.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vllm_ec2_bench.deployer.resources import ResourceManager  # noqa: E402
from vllm_ec2_bench.deployer.runner import DeploymentRunner  # noqa: E402


def _make_mgr() -> ResourceManager:
    cfg = MagicMock()
    cfg.model_spec.project_tag_value = "qwen3-8b-benchmark"
    cfg.model_spec.resource_prefix = "qwen3-8b"
    cfg.deployment.experiment_id = "exp_1_g6_12xl"
    return ResourceManager(
        config=cfg, catalog=MagicMock(),
        ec2_client=MagicMock(), iam_client=MagicMock(),
    )


def test_refresh_noop_when_ip_unchanged():
    mgr = _make_mgr()
    mgr.security_group_id = "sg-123"
    mgr.caller_ip_cidr = "1.2.3.4/32"
    with patch.object(ResourceManager, "_discover_public_ip", return_value="1.2.3.4"):
        assert mgr.refresh_caller_ingress() is False
    mgr.ec2.authorize_security_group_ingress.assert_not_called()


def test_refresh_adds_rule_on_rotation():
    mgr = _make_mgr()
    mgr.security_group_id = "sg-123"
    mgr.caller_ip_cidr = "1.2.3.4/32"
    with patch.object(ResourceManager, "_discover_public_ip", return_value="5.6.7.8"):
        assert mgr.refresh_caller_ingress() is True
    call = mgr.ec2.authorize_security_group_ingress.call_args
    perms = call.kwargs["IpPermissions"][0]
    assert call.kwargs["GroupId"] == "sg-123"
    assert perms["FromPort"] == 8000 and perms["ToPort"] == 8001
    assert perms["IpRanges"][0]["CidrIp"] == "5.6.7.8/32"
    assert mgr.caller_ip_cidr == "5.6.7.8/32"


def test_refresh_tolerates_duplicate_rule():
    """Manual unblock may have already added the rule — treat as healed."""
    mgr = _make_mgr()
    mgr.security_group_id = "sg-123"
    mgr.caller_ip_cidr = "1.2.3.4/32"
    mgr.ec2.authorize_security_group_ingress.side_effect = ClientError(
        {"Error": {"Code": "InvalidPermission.Duplicate", "Message": "dup"}},
        "AuthorizeSecurityGroupIngress",
    )
    with patch.object(ResourceManager, "_discover_public_ip", return_value="5.6.7.8"):
        assert mgr.refresh_caller_ingress() is False
    assert mgr.caller_ip_cidr == "5.6.7.8/32"


def test_refresh_survives_ip_discovery_failure():
    mgr = _make_mgr()
    mgr.security_group_id = "sg-123"
    mgr.caller_ip_cidr = "1.2.3.4/32"
    with patch.object(ResourceManager, "_discover_public_ip",
                      side_effect=RuntimeError("checkip down")):
        assert mgr.refresh_caller_ingress() is False
    mgr.ec2.authorize_security_group_ingress.assert_not_called()


def test_ready_poll_invokes_self_heal():
    """The rotation fix MUST live inside the ready-poll loop — healing after
    launch() returns is exactly the bug this replaces."""
    src = inspect.getsource(DeploymentRunner._wait_for_vllm_ready)
    assert "refresh_caller_ingress" in src, (
        "_wait_for_vllm_ready must self-heal SG ingress inside its poll loop; "
        "a NAT egress rotation mid-launch otherwise wedges the poll for the "
        "full ready timeout."
    )
