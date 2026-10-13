"""Tests for the caller-egress-IP self-heal on the security group.

The security group only allows the caller /32 captured when it was created. A
NAT rotation therefore blocks a perfectly healthy endpoint, and from the
caller's side it is indistinguishable from "vLLM is still loading" — both are
connection timeouts. Observed three times; each cost a full readiness budget.
"""
from __future__ import annotations

from botocore.exceptions import ClientError

from vllm_ec2_bench import DeploymentPlan, ExperimentConfig, ModelSpec
from vllm_ec2_bench.deployer.resources import ResourceManager

SPEC = ModelSpec(
    resource_prefix="test-model",
    display_name="Test Model",
    hf_model_id="org/test-model",
    served_model_name="test-model",
    weight_size_gib=10.0,
)

PLAN = DeploymentPlan(
    experiment_id="exp_1",
    instance_type="g5.12xlarge",
    tensor_parallel=1,
    data_parallel=1,
    region="us-west-2",
    capacity_preference=["spot", "on-demand"],
)


class _FakeEc2:
    """Records authorize calls; can be told to raise a given error code."""

    def __init__(self, raise_code: str | None = None) -> None:
        self.calls: list[dict] = []
        self._raise_code = raise_code

    def authorize_security_group_ingress(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_code:
            raise ClientError(
                {"Error": {"Code": self._raise_code, "Message": "test"}},
                "AuthorizeSecurityGroupIngress",
            )
        return {}


def _make(ec2) -> ResourceManager:
    return ResourceManager(
        config=ExperimentConfig(model_spec=SPEC, deployment=PLAN),
        catalog=None,
        ec2_client=ec2,
        iam_client=None,
    )


def _manager(ec2, *, sg_id="sg-123", cidr="203.0.113.1/32") -> ResourceManager:
    mgr = _make(ec2)
    mgr.security_group_id = sg_id
    mgr.caller_ip_cidr = cidr
    return mgr


class TestRefreshCallerIngress:
    def test_no_rotation_is_a_no_op(self, monkeypatch) -> None:
        ec2 = _FakeEc2()
        mgr = _manager(ec2)
        monkeypatch.setattr(
            ResourceManager, "_discover_public_ip", staticmethod(lambda: "203.0.113.1")
        )
        assert mgr.refresh_caller_ingress() is False
        assert ec2.calls == [], "must not touch the SG when the IP is unchanged"

    def test_rotation_adds_a_rule_for_the_new_ip(self, monkeypatch) -> None:
        ec2 = _FakeEc2()
        mgr = _manager(ec2)
        monkeypatch.setattr(
            ResourceManager, "_discover_public_ip", staticmethod(lambda: "203.0.113.9")
        )
        assert mgr.refresh_caller_ingress() is True
        assert len(ec2.calls) == 1
        perm = ec2.calls[0]["IpPermissions"][0]
        assert ec2.calls[0]["GroupId"] == "sg-123"
        assert perm["FromPort"] == 8000
        assert perm["ToPort"] == 8001
        assert perm["IpRanges"][0]["CidrIp"] == "203.0.113.9/32"
        assert mgr.caller_ip_cidr == "203.0.113.9/32", "tracked CIDR must advance"

    def test_second_call_after_rotation_is_a_no_op(self, monkeypatch) -> None:
        ec2 = _FakeEc2()
        mgr = _manager(ec2)
        monkeypatch.setattr(
            ResourceManager, "_discover_public_ip", staticmethod(lambda: "203.0.113.9")
        )
        mgr.refresh_caller_ingress()
        assert mgr.refresh_caller_ingress() is False
        assert len(ec2.calls) == 1, "idempotent — one rule per rotation"

    def test_duplicate_rule_is_tolerated(self, monkeypatch) -> None:
        """The rule may already exist from a concurrent run; not an error."""
        ec2 = _FakeEc2(raise_code="InvalidPermission.Duplicate")
        mgr = _manager(ec2)
        monkeypatch.setattr(
            ResourceManager, "_discover_public_ip", staticmethod(lambda: "203.0.113.9")
        )
        assert mgr.refresh_caller_ingress() is False
        assert mgr.caller_ip_cidr == "203.0.113.9/32"

    def test_other_client_errors_do_not_propagate(self, monkeypatch) -> None:
        """Never kill a paid-for run over a self-heal failure."""
        ec2 = _FakeEc2(raise_code="RulesPerSecurityGroupLimitExceeded")
        mgr = _manager(ec2)
        monkeypatch.setattr(
            ResourceManager, "_discover_public_ip", staticmethod(lambda: "203.0.113.9")
        )
        assert mgr.refresh_caller_ingress() is False

    def test_ip_discovery_failure_is_non_fatal(self, monkeypatch) -> None:
        ec2 = _FakeEc2()
        mgr = _manager(ec2)

        def boom() -> str:
            raise RuntimeError("checkip unreachable")

        monkeypatch.setattr(
            ResourceManager, "_discover_public_ip", staticmethod(boom)
        )
        assert mgr.refresh_caller_ingress() is False
        assert ec2.calls == []

    def test_without_a_security_group_it_does_nothing(self) -> None:
        ec2 = _FakeEc2()
        mgr = _manager(ec2)
        mgr.security_group_id = None
        assert mgr.refresh_caller_ingress() is False
        assert ec2.calls == []


class TestEnsureAllTracksTheCidr:
    def test_explicit_cidr_is_recorded(self, monkeypatch) -> None:
        ec2 = _FakeEc2()
        mgr = _make(ec2)
        monkeypatch.setattr(ResourceManager, "_ensure_instance_profile", lambda self: None)
        monkeypatch.setattr(
            ResourceManager, "_create_security_group", lambda self, cidr: "sg-xyz"
        )
        monkeypatch.setattr(ResourceManager, "_pick_ami", lambda self: "ami-1")
        mgr.ensure_all(caller_ip_cidr="198.51.100.7/32")
        assert mgr.caller_ip_cidr == "198.51.100.7/32"
