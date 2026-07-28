"""Spot Fleet strategy — CreateLaunchTemplate + CreateFleet.

Why Fleet (not plain spot RunInstances)? The ``capacity-optimized`` allocation
picks whichever AZ has the deepest spot pool at launch time — much more
robust than guessing an AZ upfront. We feed the Fleet ``Overrides`` for
every AZ where the instance type is offered.

Two waiting modes, selected by ``DeploymentPlan.spot_wait_timeout_s``:

* **Legacy (== 0, the default):** one short built-in backoff sweep
  (~75s total across four tries) then give up. Right for commodity GPUs
  (g5/g6/g6e) whose spot pools are deep — capacity is either there now or
  a brief retry catches a transient gap.
* **Persistent (> 0):** keep re-issuing the Fleet request every
  ``spot_poll_interval_s`` until capacity shows up OR the wait budget
  elapses. Right for scarce accelerators (p4d/p5/p6-B200) whose spot pools
  flicker minute-to-minute, so a one-shot attempt almost always misses a
  momentary opening.

Both modes create the Fleet in ``instant`` mode (single synchronous attempt
per call) — never ``maintain`` — so there is never a background fleet that
could relaunch a replacement after teardown. The wait is bounded and
self-terminating, and it is *capacity-acquisition* time, reported separately
from the benchmark's own run time.
"""
from __future__ import annotations

import logging
import time

from botocore.exceptions import ClientError

from .base import CapacityExhausted, CapacityStrategy, LaunchContext, LaunchResult

LOG = logging.getLogger(__name__)


# Attempt schedule for retrying CreateFleet when every AZ reports ICE, used in
# LEGACY mode (spot_wait_timeout_s == 0). Capacity can shift minute-to-minute,
# so a short backoff sometimes catches a transient gap on deep spot pools.
_SPOT_FLEET_RETRY_BACKOFFS_S: tuple[int, ...] = (0, 5, 20, 50)


class SpotFleetStrategy(CapacityStrategy):
    """Provision one spot instance via EC2 Fleet in ``instant`` mode."""

    mode = "spot"

    # Clock + sleep indirections call through the module ``time`` at call time
    # (not bound at class-definition time) so unit tests can drive the wait
    # loop deterministically via ``monkeypatch.setattr(spot.time, ...)``.
    @staticmethod
    def _now() -> float:
        return time.monotonic()

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

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

        wait_budget_s = cfg.spot_wait_timeout_s
        if wait_budget_s > 0:
            result = self._launch_persistent(
                ctx, lt_id, overrides, subnets_by_az,
                wait_budget_s=wait_budget_s,
                poll_interval_s=cfg.spot_poll_interval_s,
            )
        else:
            result = self._launch_legacy(ctx, lt_id, overrides, subnets_by_az)

        if result is not None:
            return result

        # No capacity — clean up the launch template before falling through.
        try:
            ctx.ec2.delete_launch_template(LaunchTemplateId=lt_id)
        except ClientError:
            pass

        if wait_budget_s > 0:
            raise CapacityExhausted(
                f"[{cfg.experiment_id}] Spot Fleet found no {cfg.instance_type} "
                f"capacity within the {wait_budget_s}s wait budget across AZs "
                f"{list(subnets_by_az)}."
            )
        raise CapacityExhausted(
            f"Spot Fleet exhausted {len(_SPOT_FLEET_RETRY_BACKOFFS_S)} attempts "
            f"across AZs {list(subnets_by_az)}."
        )

    # ------------------------------------------------------------------
    # Waiting modes
    # ------------------------------------------------------------------
    def _launch_legacy(
        self, ctx: LaunchContext, lt_id: str,
        overrides: list[dict], subnets_by_az: dict[str, str],
    ) -> LaunchResult | None:
        """One short backoff sweep (~75s), then give up. Deep-pool default."""
        cfg = ctx.config.deployment
        for attempt, backoff_s in enumerate(_SPOT_FLEET_RETRY_BACKOFFS_S):
            if backoff_s > 0:
                LOG.info(
                    "[%s] Spot Fleet retry %d/%d after %ds backoff",
                    cfg.experiment_id, attempt,
                    len(_SPOT_FLEET_RETRY_BACKOFFS_S) - 1, backoff_s,
                )
                self._sleep(backoff_s)
            result = self._try_create_fleet(ctx, lt_id, overrides, attempt + 1)
            if result is not None:
                return result
        return None

    def _launch_persistent(
        self, ctx: LaunchContext, lt_id: str,
        overrides: list[dict], subnets_by_az: dict[str, str],
        *, wait_budget_s: int, poll_interval_s: int,
    ) -> LaunchResult | None:
        """Poll EC2 Fleet until capacity appears OR the wait budget elapses.

        Scarce-accelerator mode: p4d/p5/p6-B200 spot capacity flickers, so we
        keep re-issuing the instant Fleet request on a fixed cadence within a
        bounded, self-terminating deadline. No 'maintain' fleet is ever
        created, so nothing can relaunch after teardown.
        """
        cfg = ctx.config.deployment
        deadline = self._now() + wait_budget_s
        attempt = 0
        LOG.info(
            "[%s] Spot Fleet persistent wait: up to %ds for %s across AZs %s "
            "(poll every %ds)",
            cfg.experiment_id, wait_budget_s, cfg.instance_type,
            list(subnets_by_az), poll_interval_s,
        )
        while True:
            attempt += 1
            result = self._try_create_fleet(ctx, lt_id, overrides, attempt)
            if result is not None:
                LOG.info(
                    "[%s] Spot Fleet acquired %s after %d attempt(s)",
                    cfg.experiment_id, result.instance_id, attempt,
                )
                return result

            remaining = deadline - self._now()
            if remaining <= 0:
                LOG.warning(
                    "[%s] Spot Fleet wait budget (%ds) elapsed after %d attempt(s) "
                    "with no capacity", cfg.experiment_id, wait_budget_s, attempt,
                )
                return None
            sleep_s = min(poll_interval_s, max(1, int(remaining)))
            LOG.info(
                "[%s] no spot capacity yet (attempt %d); %ds remaining, "
                "retrying in %ds", cfg.experiment_id, attempt,
                int(remaining), sleep_s,
            )
            self._sleep(sleep_s)

    # ------------------------------------------------------------------
    # Single Fleet attempt (shared by both modes)
    # ------------------------------------------------------------------
    def _try_create_fleet(
        self, ctx: LaunchContext, lt_id: str,
        overrides: list[dict], attempt: int,
    ) -> LaunchResult | None:
        """Issue one instant Fleet request. Returns a result or None (retry)."""
        cfg = ctx.config.deployment
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
            LOG.warning("[%s] attempt %d: CreateFleet API error: %s",
                        cfg.experiment_id, attempt, exc)
            return None

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
                metadata={"attempts": attempt},
            )

        # Fleet created but no instances — drop the empty fleet and signal retry.
        if fleet_id:
            try:
                ctx.ec2.delete_fleets(FleetIds=[fleet_id], TerminateInstances=False)
            except ClientError:
                pass
        error_summary = "; ".join(
            f"{e.get('ErrorCode')}: {e.get('ErrorMessage')}" for e in errors
        ) or "<no instances launched, no errors reported>"
        LOG.warning("[%s] attempt %d: Fleet %s returned no instances: %s",
                    cfg.experiment_id, attempt, fleet_id, error_summary)
        return None

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
            # NOTE: InstanceInitiatedShutdownBehavior=terminate is NOT valid for
            # spot instances — EC2 Fleet rejects the whole request with
            # UnfulfillableCapacity. The self-terminate backstop is instead done
            # entirely in-guest (user-data schedules a delayed self-terminate via
            # the EC2 API using the instance's own id from IMDS). (throwaway branch)
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
                    # Max gp3 throughput/IOPS so DP=8 (8 vLLM engines each reading
                    # a ~50 GiB weight copy off this root volume) isn't I/O-bound.
                    # Default gp3 is 125 MB/s — the bottleneck that wedged p6 DP=8.
                    "Throughput": 1000,
                    "Iops": 16000,
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
