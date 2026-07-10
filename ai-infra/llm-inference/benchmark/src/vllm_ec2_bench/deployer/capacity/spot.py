"""Spot Fleet strategy — CreateLaunchTemplate + CreateFleet.

Why Fleet (not plain spot RunInstances)? The ``capacity-optimized`` allocation
picks whichever AZ has the deepest spot pool at launch time — much more
robust than guessing an AZ upfront. We feed the Fleet ``Overrides`` for
every AZ where the instance type is offered.
"""
from __future__ import annotations

import logging
import time

from botocore.exceptions import ClientError

from .base import CapacityExhausted, CapacityStrategy, LaunchContext, LaunchResult

LOG = logging.getLogger(__name__)


# Attempt schedule for retrying CreateFleet when every AZ reports ICE.
# Capacity can shift minute-to-minute, so a short backoff sometimes helps.
_SPOT_FLEET_RETRY_BACKOFFS_S: tuple[int, ...] = (0, 5, 20, 50)


class SpotFleetStrategy(CapacityStrategy):
    """Provision one spot instance via EC2 Fleet in ``instant`` mode."""

    mode = "spot"

    def launch(self, ctx: LaunchContext) -> LaunchResult:
        cfg = ctx.config.deployment
        subnets_by_az = ctx.get_subnets_for_preferred_azs()
        if not subnets_by_az:
            raise CapacityExhausted(
                f"No usable subnets for {cfg.instance_type} in "
                f"preferred AZs {cfg.preferred_azs or 'default VPC'}"
            )

        lt_id = self._create_launch_template(ctx)
        overrides = [
            {
                "InstanceType": cfg.instance_type,
                "SubnetId": subnet_id,
                "AvailabilityZone": az,
            }
            for az, subnet_id in subnets_by_az.items()
        ]

        last_err: str | None = None
        for attempt, backoff_s in enumerate(_SPOT_FLEET_RETRY_BACKOFFS_S):
            if backoff_s > 0:
                LOG.info(
                    "[%s] Spot Fleet retry %d/%d after %ds backoff",
                    cfg.experiment_id, attempt,
                    len(_SPOT_FLEET_RETRY_BACKOFFS_S) - 1, backoff_s,
                )
                time.sleep(backoff_s)

            try:
                resp = ctx.ec2.create_fleet(
                    Type="instant",
                    TargetCapacitySpecification={
                        "TotalTargetCapacity": 1,
                        "DefaultTargetCapacityType": "spot",
                        "OnDemandTargetCapacity": 0,
                        "SpotTargetCapacity": 1,
                    },
                    SpotOptions={"AllocationStrategy": "capacity-optimized"},
                    LaunchTemplateConfigs=[{
                        "LaunchTemplateSpecification": {
                            "LaunchTemplateId": lt_id, "Version": "1",
                        },
                        "Overrides": overrides,
                    }],
                    TagSpecifications=[{
                        "ResourceType": "fleet",
                        "Tags": self._with_purpose(ctx.tags, "spot-fleet"),
                    }],
                )
            except ClientError as exc:
                last_err = f"CreateFleet API error: {exc}"
                continue

            fleet_id = resp.get("FleetId")
            errors = resp.get("Errors", [])
            instances = resp.get("Instances", [])
            instance_ids: list[str] = []
            for inst_group in instances:
                instance_ids.extend(inst_group.get("InstanceIds", []))

            if instance_ids:
                chosen = instances[0].get("LaunchTemplateAndOverrides", {}).get("Overrides", {})
                return LaunchResult(
                    instance_id=instance_ids[0],
                    availability_zone=chosen.get("AvailabilityZone", ""),
                    subnet_id=chosen.get("SubnetId", ""),
                    capacity_mode=self.mode,
                    spot_fleet_id=fleet_id,
                    launch_template_id=lt_id,
                    metadata={"attempts": attempt + 1},
                )

            # Fleet created but no instances — drop the empty fleet and retry
            if fleet_id:
                try:
                    ctx.ec2.delete_fleets(FleetIds=[fleet_id], TerminateInstances=False)
                except ClientError:
                    pass
            error_summary = "; ".join(
                f"{e.get('ErrorCode')}: {e.get('ErrorMessage')}" for e in errors
            ) or "<no instances launched, no errors reported>"
            last_err = f"Fleet {fleet_id} returned no instances: {error_summary}"
            LOG.warning("[%s] attempt %d: %s", cfg.experiment_id, attempt + 1, last_err)

        # All retries exhausted — clean up the launch template
        try:
            ctx.ec2.delete_launch_template(LaunchTemplateId=lt_id)
        except ClientError:
            pass

        raise CapacityExhausted(
            f"Spot Fleet exhausted {len(_SPOT_FLEET_RETRY_BACKOFFS_S)} attempts "
            f"across AZs {list(subnets_by_az)}: {last_err}"
        )

    # ------------------------------------------------------------------
    def _create_launch_template(self, ctx: LaunchContext) -> str:
        cfg = ctx.config
        lt_name = f"{cfg.model_spec.resource_prefix}-{cfg.deployment.experiment_id}-{int(time.time())}"
        lt_data = {
            "ImageId": ctx.ami_id,
            "InstanceType": cfg.deployment.instance_type,
            "IamInstanceProfile": {"Name": ctx.iam_instance_profile_name},
            "UserData": ctx.user_data_b64,
            "SecurityGroupIds": [ctx.security_group_id],
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
                    "Tags": self._with_extra(
                        ctx.tags,
                        {"Name": f"{cfg.model_spec.resource_prefix}-{cfg.deployment.experiment_id}"},
                    ),
                },
                {"ResourceType": "volume", "Tags": ctx.tags},
            ],
        }
        resp = ctx.ec2.create_launch_template(
            LaunchTemplateName=lt_name,
            LaunchTemplateData=lt_data,
            TagSpecifications=[{"ResourceType": "launch-template", "Tags": ctx.tags}],
        )
        return resp["LaunchTemplate"]["LaunchTemplateId"]

    @staticmethod
    def _with_purpose(tags: list[dict[str, str]], purpose: str) -> list[dict[str, str]]:
        return tags + [{"Key": "Purpose", "Value": purpose}]

    @staticmethod
    def _with_extra(tags: list[dict[str, str]], extra: dict[str, str]) -> list[dict[str, str]]:
        return tags + [{"Key": k, "Value": v} for k, v in extra.items()]


__all__ = ["SpotFleetStrategy"]
