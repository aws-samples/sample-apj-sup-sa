"""ODCR strategy — auto-create a targeted On-Demand Capacity Reservation.

Useful when the regional OD pool is saturated but a specific AZ still has
headroom. Creating a CR "reserves" that capacity so :py:meth:`RunInstances`
against the CR id is guaranteed to succeed (until the CR is cancelled).

ODCRs accrue on-demand billing from the moment they become active, so the
runner must cancel auto-created ODCRs on teardown.
"""
from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from .base import CapacityExhausted, CapacityStrategy, LaunchContext, LaunchResult
from .ondemand import _build_run_instances_params

LOG = logging.getLogger(__name__)


class ODCRStrategy(CapacityStrategy):
    """Auto-create a targeted ODCR, then launch against it."""

    mode = "odcr"

    def launch(self, ctx: LaunchContext) -> LaunchResult:
        cfg = ctx.config.deployment
        odcr_id, odcr_az = self._auto_create_odcr(ctx)

        run_params = _build_run_instances_params(ctx)
        run_params["CapacityReservationSpecification"] = {
            "CapacityReservationTarget": {"CapacityReservationId": odcr_id},
        }
        subnets_by_az = ctx.get_subnets_for_preferred_azs()
        if odcr_az in subnets_by_az:
            run_params["SubnetId"] = subnets_by_az[odcr_az]

        LOG.info(
            "[%s] RunInstances against auto-ODCR %s in %s",
            cfg.experiment_id, odcr_id, odcr_az,
        )
        try:
            resp = ctx.ec2.run_instances(**run_params)
        except ClientError:
            # Clean up the orphan ODCR before re-raising
            try:
                ctx.ec2.cancel_capacity_reservation(CapacityReservationId=odcr_id)
            except ClientError:
                pass
            raise

        instance = resp["Instances"][0]
        return LaunchResult(
            instance_id=instance["InstanceId"],
            availability_zone=odcr_az,
            subnet_id=run_params.get("SubnetId", ""),
            capacity_mode=self.mode,
            auto_created_odcr_id=odcr_id,
        )

    # ------------------------------------------------------------------
    def _auto_create_odcr(self, ctx: LaunchContext) -> tuple[str, str]:
        cfg = ctx.config.deployment
        # Prefer explicit preferred_azs, else all offered AZs.
        subnets_by_az = ctx.get_subnets_for_preferred_azs()
        azs_to_try = list(cfg.preferred_azs) or list(subnets_by_az.keys())
        if not azs_to_try:
            raise CapacityExhausted(
                f"No AZs available to try ODCR creation for {cfg.instance_type}"
            )

        last_err: Exception | None = None
        for az in azs_to_try:
            try:
                LOG.info("[%s] Auto-creating ODCR in %s", cfg.experiment_id, az)
                resp = ctx.ec2.create_capacity_reservation(
                    InstanceType=cfg.instance_type,
                    InstancePlatform="Linux/UNIX",
                    AvailabilityZone=az,
                    Tenancy="default",
                    InstanceCount=1,
                    EndDateType="unlimited",
                    InstanceMatchCriteria="targeted",
                    TagSpecifications=[{
                        "ResourceType": "capacity-reservation",
                        "Tags": ctx.tags + [{"Key": "Purpose", "Value": "auto-odcr"}],
                    }],
                )
                cr = resp["CapacityReservation"]
                return cr["CapacityReservationId"], az
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                LOG.warning(
                    "[%s] ODCR creation in %s failed (%s): %s",
                    cfg.experiment_id, az, code, exc,
                )
                last_err = exc
                continue

        raise CapacityExhausted(
            f"Auto-ODCR failed in all AZs {azs_to_try}: {last_err}"
        ) from last_err


__all__ = ["ODCRStrategy"]
