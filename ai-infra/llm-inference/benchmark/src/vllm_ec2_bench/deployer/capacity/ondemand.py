"""On-demand strategy — plain RunInstances, per-AZ ICE retry.

This strategy does not create any reservations. It just walks ``preferred_azs``
(or all offered AZs, if none preferred) and retries ``RunInstances`` on
``InsufficientInstanceCapacity`` until it either succeeds or runs out of AZs.

For accounts where the regional on-demand pool is exhausted in every AZ,
the next strategy in the preference list (usually ``odcr``) can unblock by
creating a targeted capacity reservation first.
"""
from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from .base import CapacityExhausted, CapacityStrategy, LaunchContext, LaunchResult

LOG = logging.getLogger(__name__)


class OnDemandStrategy(CapacityStrategy):
    """Launch a single on-demand instance in the first AZ that accepts it."""

    mode = "on-demand"

    def __init__(self, *, capacity_reservation_id: str | None = None) -> None:
        """
        Parameters
        ----------
        capacity_reservation_id
            If provided, the launch targets this CR. Useful for running inside
            a Capacity Block or a pre-purchased ODCR. Normally you'd use the
            ``odcr`` or ``capacity-block`` strategy instead, but this flag is
            handy for tests and manual interventions.
        """
        self.capacity_reservation_id = capacity_reservation_id

    def launch(self, ctx: LaunchContext) -> LaunchResult:
        cfg = ctx.config.deployment
        run_params = _build_run_instances_params(ctx)
        if self.capacity_reservation_id:
            run_params["CapacityReservationSpecification"] = {
                "CapacityReservationTarget": {
                    "CapacityReservationId": self.capacity_reservation_id,
                },
            }

        subnets_by_az = ctx.get_subnets_for_preferred_azs()
        if not subnets_by_az:
            raise CapacityExhausted(
                f"No usable subnets for {cfg.instance_type} in "
                f"preferred AZs {list(cfg.preferred_azs) or '<any offered>'} "
                f"— instance type is likely not offered in this region."
            )
        candidate_azs = list(subnets_by_az.items())

        last_err: Exception | None = None
        for az, subnet_id in candidate_azs:
            attempt_params = dict(run_params)
            if subnet_id:
                attempt_params["SubnetId"] = subnet_id
            try:
                LOG.info("[%s] RunInstances on-demand az=%s", cfg.experiment_id, az or "<auto>")
                resp = ctx.ec2.run_instances(**attempt_params)
                instance = resp["Instances"][0]
                return LaunchResult(
                    instance_id=instance["InstanceId"],
                    availability_zone=instance.get("Placement", {}).get("AvailabilityZone", az or ""),
                    subnet_id=subnet_id or "",
                    capacity_mode=self.mode,
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code == "InsufficientInstanceCapacity":
                    last_err = exc
                    LOG.warning(
                        "[%s] OD ICE in %s, trying next AZ", cfg.experiment_id, az
                    )
                    continue
                raise  # non-capacity error — bubble up

        raise CapacityExhausted(f"On-demand failed in all AZs: {last_err}") from last_err


def _build_run_instances_params(ctx: LaunchContext) -> dict:
    """Shared RunInstances param builder used by OD + CB + ODCR strategies."""
    cfg = ctx.config
    return {
        "ImageId": ctx.ami_id,
        "InstanceType": cfg.deployment.instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "SecurityGroupIds": [ctx.security_group_id],
        "IamInstanceProfile": {"Name": ctx.iam_instance_profile_name},
        "UserData": ctx.user_data_b64,
        "MetadataOptions": {
            "HttpTokens": "required",
            "HttpPutResponseHopLimit": 2,
            "HttpEndpoint": "enabled",
        },
        "BlockDeviceMappings": [{
            "DeviceName": "/dev/sda1",
            "Ebs": {
                "VolumeSize": cfg.root_ebs_gib,
                "VolumeType": "gp3",
                "DeleteOnTermination": True,
                "Encrypted": True,
            },
        }],
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": ctx.tags + [
                    {
                        "Key": "Name",
                        "Value": f"{cfg.model_spec.resource_prefix}-{cfg.deployment.experiment_id}",
                    }
                ],
            },
            {"ResourceType": "volume", "Tags": ctx.tags},
        ],
    }


__all__ = ["OnDemandStrategy", "_build_run_instances_params"]
