"""Capacity strategy tests.

We mock the boto3 EC2 client directly — cleaner than ``moto`` for EC2 Fleet
and Capacity Block which moto covers incompletely. Each test asserts:

* Happy path: the strategy returns a sensible :class:`LaunchResult`.
* Failure path: on ICE / no offerings, the strategy raises
  :class:`CapacityExhausted` so the runner can try the next mode.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from vllm_ec2_bench import (
    DeploymentPlan,
    ExperimentConfig,
    ModelSpec,
)
from vllm_ec2_bench.deployer.capacity import (
    CapacityExhausted,
    LaunchContext,
    ODCRStrategy,
    OnDemandStrategy,
    SpotFleetStrategy,
    StrategyMisconfigured,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _model_spec() -> ModelSpec:
    return ModelSpec(
        resource_prefix="test-model",
        display_name="Test Model",
        hf_model_id="org/test-model",
        served_model_name="test-model",
        weight_size_gib=55.0,
    )


def _config(instance_type: str, capacity_preference: list) -> ExperimentConfig:
    # Use p4d.24xl (TP=2, DP=4, 8 GPUs) for regular tests.
    # Use p5.48xl for CB tests.
    plans = {
        "p4d.24xlarge": (2, 4),
        "p5.48xlarge": (1, 8),
        "g5.12xlarge": (4, 1),
    }
    tp, dp = plans[instance_type]
    return ExperimentConfig(
        model_spec=_model_spec(),
        deployment=DeploymentPlan(
            experiment_id="exp_test",
            instance_type=instance_type,
            tensor_parallel=tp,
            data_parallel=dp,
            pipeline_parallel=1,
            region="us-east-2",
            capacity_preference=capacity_preference,
        ),
    )


def _make_ctx(
    config: ExperimentConfig,
    *,
    ec2: MagicMock | None = None,
    subnets: dict[str, str] | None = None,
) -> LaunchContext:
    # Distinguish "not passed" (use default) from "explicitly empty".
    if subnets is None:
        subnets = {"us-east-2a": "subnet-aaa", "us-east-2b": "subnet-bbb"}
    return LaunchContext(
        config=config,
        security_group_id="sg-123",
        ami_id="ami-abc",
        user_data_b64="dXNlci1kYXRh",  # "user-data"
        iam_instance_profile_name="test-profile",
        tags=[{"Key": "Project", "Value": "test-benchmark"}],
        ec2=ec2 or MagicMock(),
        iam=MagicMock(),
        get_subnets_for_preferred_azs=lambda: subnets,
        cleanup_partial_launch=lambda *_args, **_kw: None,
    )


# ---------------------------------------------------------------------------
# SpotFleetStrategy
# ---------------------------------------------------------------------------
class TestSpotFleetStrategy:
    def test_happy_path(self) -> None:
        ec2 = MagicMock()
        ec2.create_launch_template.return_value = {
            "LaunchTemplate": {"LaunchTemplateId": "lt-999"}
        }
        ec2.create_fleet.return_value = {
            "FleetId": "fleet-xxx",
            "Instances": [{
                "InstanceIds": ["i-abc"],
                "LaunchTemplateAndOverrides": {
                    "Overrides": {
                        "AvailabilityZone": "us-east-2a",
                        "SubnetId": "subnet-aaa",
                    },
                },
            }],
            "Errors": [],
        }
        ctx = _make_ctx(_config("p4d.24xlarge", ["spot"]), ec2=ec2)
        result = SpotFleetStrategy().launch(ctx)

        assert result.instance_id == "i-abc"
        assert result.availability_zone == "us-east-2a"
        assert result.capacity_mode == "spot"
        assert result.spot_fleet_id == "fleet-xxx"
        assert result.launch_template_id == "lt-999"
        ec2.create_launch_template.assert_called_once()
        ec2.create_fleet.assert_called_once()

    def test_unfulfillable_capacity_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Skip the retry backoff sleeps so tests stay fast.
        monkeypatch.setattr("vllm_ec2_bench.deployer.capacity.spot.time.sleep", lambda *_: None)

        ec2 = MagicMock()
        ec2.create_launch_template.return_value = {
            "LaunchTemplate": {"LaunchTemplateId": "lt-999"}
        }
        # Fleet creates OK but returns zero instances every time.
        ec2.create_fleet.return_value = {
            "FleetId": "fleet-empty",
            "Instances": [],
            "Errors": [
                {
                    "ErrorCode": "UnfulfillableCapacity",
                    "ErrorMessage": "No capacity available for p4d.24xlarge",
                }
            ],
        }
        ctx = _make_ctx(_config("p4d.24xlarge", ["spot"]), ec2=ec2)
        with pytest.raises(CapacityExhausted, match="Spot Fleet exhausted"):
            SpotFleetStrategy().launch(ctx)
        # Delete_fleets called to clean up each empty fleet.
        assert ec2.delete_fleets.call_count >= 1

    def test_no_subnets_raises(self) -> None:
        ctx = _make_ctx(_config("p4d.24xlarge", ["spot"]), subnets={})
        with pytest.raises(CapacityExhausted, match="No usable subnets"):
            SpotFleetStrategy().launch(ctx)


# ---------------------------------------------------------------------------
# OnDemandStrategy
# ---------------------------------------------------------------------------
class TestOnDemandStrategy:
    def test_happy_path(self) -> None:
        ec2 = MagicMock()
        ec2.run_instances.return_value = {
            "Instances": [{
                "InstanceId": "i-happy",
                "Placement": {"AvailabilityZone": "us-east-2a"},
            }]
        }
        ctx = _make_ctx(_config("g5.12xlarge", ["on-demand"]), ec2=ec2)
        result = OnDemandStrategy().launch(ctx)

        assert result.instance_id == "i-happy"
        assert result.availability_zone == "us-east-2a"
        assert result.capacity_mode == "on-demand"
        ec2.run_instances.assert_called_once()

    def test_ice_retries_next_az(self) -> None:
        ice = ClientError(
            {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "ICE"}},
            "RunInstances",
        )
        happy_response = {
            "Instances": [{
                "InstanceId": "i-retry",
                "Placement": {"AvailabilityZone": "us-east-2b"},
            }]
        }
        ec2 = MagicMock()
        ec2.run_instances.side_effect = [ice, happy_response]

        ctx = _make_ctx(_config("g5.12xlarge", ["on-demand"]), ec2=ec2)
        result = OnDemandStrategy().launch(ctx)

        assert result.instance_id == "i-retry"
        assert ec2.run_instances.call_count == 2

    def test_ice_everywhere_raises(self) -> None:
        ice = ClientError(
            {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "ICE"}},
            "RunInstances",
        )
        ec2 = MagicMock()
        ec2.run_instances.side_effect = ice
        ctx = _make_ctx(_config("g5.12xlarge", ["on-demand"]), ec2=ec2)
        with pytest.raises(CapacityExhausted, match="On-demand failed in all AZs"):
            OnDemandStrategy().launch(ctx)

    def test_non_ice_error_bubbles(self) -> None:
        other = ClientError(
            {"Error": {"Code": "InvalidParameterValue", "Message": "bad AMI"}},
            "RunInstances",
        )
        ec2 = MagicMock()
        ec2.run_instances.side_effect = other
        ctx = _make_ctx(_config("g5.12xlarge", ["on-demand"]), ec2=ec2)
        with pytest.raises(ClientError, match="InvalidParameterValue"):
            OnDemandStrategy().launch(ctx)

    def test_no_subnets_raises(self) -> None:
        """Regression: when the instance type isn't offered in any AZ of the
        region (e.g. p4de in us-east-2), the subnet map is empty. The
        strategy must raise CapacityExhausted so the runner tries the next
        mode, rather than calling RunInstances with no SubnetId and getting
        a generic 'Unsupported' error."""
        ec2 = MagicMock()
        ctx = _make_ctx(_config("p4d.24xlarge", ["on-demand"]), ec2=ec2, subnets={})
        with pytest.raises(CapacityExhausted, match="No usable subnets"):
            OnDemandStrategy().launch(ctx)
        ec2.run_instances.assert_not_called()


# ---------------------------------------------------------------------------
# ODCRStrategy
# ---------------------------------------------------------------------------
class TestODCRStrategy:
    def test_happy_path(self) -> None:
        ec2 = MagicMock()
        ec2.create_capacity_reservation.return_value = {
            "CapacityReservation": {
                "CapacityReservationId": "cr-happy",
                "AvailabilityZone": "us-east-2a",
            }
        }
        ec2.run_instances.return_value = {
            "Instances": [{"InstanceId": "i-odcr", "Placement": {"AvailabilityZone": "us-east-2a"}}]
        }
        ctx = _make_ctx(_config("p4d.24xlarge", ["odcr"]), ec2=ec2)
        result = ODCRStrategy().launch(ctx)

        assert result.instance_id == "i-odcr"
        assert result.auto_created_odcr_id == "cr-happy"
        assert result.capacity_mode == "odcr"
        assert result.availability_zone == "us-east-2a"

    def test_ice_tries_next_az(self) -> None:
        ice = ClientError(
            {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "ICE"}},
            "CreateCapacityReservation",
        )
        happy = {
            "CapacityReservation": {
                "CapacityReservationId": "cr-2b",
                "AvailabilityZone": "us-east-2b",
            }
        }
        ec2 = MagicMock()
        ec2.create_capacity_reservation.side_effect = [ice, happy]
        ec2.run_instances.return_value = {
            "Instances": [{"InstanceId": "i-after", "Placement": {"AvailabilityZone": "us-east-2b"}}]
        }
        ctx = _make_ctx(_config("p4d.24xlarge", ["odcr"]), ec2=ec2)
        result = ODCRStrategy().launch(ctx)

        assert result.auto_created_odcr_id == "cr-2b"
        assert ec2.create_capacity_reservation.call_count == 2

    def test_ice_all_azs_raises(self) -> None:
        ice = ClientError(
            {"Error": {"Code": "InsufficientInstanceCapacity", "Message": "ICE"}},
            "CreateCapacityReservation",
        )
        ec2 = MagicMock()
        ec2.create_capacity_reservation.side_effect = ice
        ctx = _make_ctx(_config("p4d.24xlarge", ["odcr"]), ec2=ec2)
        with pytest.raises(CapacityExhausted, match="Auto-ODCR failed in all AZs"):
            ODCRStrategy().launch(ctx)


# ---------------------------------------------------------------------------

