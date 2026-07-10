"""DeploymentRunner — orchestrates end-to-end deployment of one experiment.

Responsibilities:

1. Ensure shared AWS plumbing exists (via :class:`ResourceManager`).
2. Render user-data (via :class:`UserDataRenderer`).
3. Launch the instance using the first capacity strategy that works.
4. Wait for the public IP, then poll vLLM's /v1/models until ready.
5. On teardown: terminate instance, delete SG / launch template / fleet,
   cancel auto-created ODCR (but not CB — those are non-cancellable).

The runner is model-agnostic — it takes an :class:`ExperimentConfig` and
uses the ``ModelSpec`` inside to derive resource names.
"""
from __future__ import annotations

import logging
import secrets
import socket
import string
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import ClientError

from ..data import CapacityMode, ExperimentConfig

if TYPE_CHECKING:
    from ..data.catalog import Catalog
from .capacity import (
    CapacityExhausted,
    CapacityStrategy,
    LaunchContext,
    LaunchResult,
    ODCRStrategy,
    OnDemandStrategy,
    SpotFleetStrategy,
    StrategyMisconfigured,
)
from .resources import ResourceManager
from .state import DeploymentState
from .user_data import UserDataRenderer

LOG = logging.getLogger(__name__)


class DeploymentRunner:
    """End-to-end deployment of one experiment.

    Usage::

        runner = DeploymentRunner(cfg, catalog=catalog, hf_secret_name="medgemma-27b-benchmark/hf-token")
        state = runner.launch()          # provision + wait for vLLM ready
        # ... use state.base_url / state.api_key for inference ...
        runner.terminate()               # clean up all resources

    Parameters
    ----------
    config
        :class:`ExperimentConfig` — the single source of truth for what to deploy.
    hf_secret_name
        AWS Secrets Manager secret name holding the HuggingFace access token.
        Required if ``config.model_spec.gated``. The EC2 instance fetches
        the token at boot via the instance role's ``GetSecretValue``
        permission; the token value never appears in the EC2 user-data
        blob or in any launch-time API call.
    caller_ip_cidr
        Inbound :8000 is restricted to this CIDR. ``None`` → auto-discover
        via ``https://checkip.amazonaws.com`` and use a /32.
    capacity_reservation_id
        If provided, short-circuits straight to the Capacity-Block strategy
        against this pre-existing reservation. Useful for manually-purchased CBs.
    confirm_cb_purchase
        Required ``True`` to let the CapacityBlockStrategy actually purchase
        new blocks (each purchase is non-refundable, ~$1000+).
    cb_max_start_delay_hours, cb_duration_hours
        Forwarded to :class:`CapacityBlockStrategy`.
    ready_timeout_s
        How long to wait for vLLM to answer /v1/models with 200.
    """

    def __init__(
        self,
        config: ExperimentConfig,
        *,
        catalog: "Catalog",
        hf_secret_name: str | None = None,
        caller_ip_cidr: str | None = None,
        capacity_reservation_id: str | None = None,
        confirm_cb_purchase: bool = False,
        cb_max_start_delay_hours: int = 3,
        cb_duration_hours: int = 24,
        ready_timeout_s: int | None = None,
    ) -> None:
        if config.model_spec.gated and not hf_secret_name:
            raise ValueError(
                f"hf_secret_name required for gated model {config.model_spec.hf_model_id}. "
                "Upsert your token via upsert_hf_token() then pass the secret name here."
            )
        if not catalog.is_loaded:
            raise ValueError("catalog must be loaded before constructing a DeploymentRunner")
        self.config = config
        self.catalog = catalog
        self.hf_secret_name = hf_secret_name
        self.caller_ip_cidr = caller_ip_cidr
        self.capacity_reservation_id = capacity_reservation_id
        self.confirm_cb_purchase = confirm_cb_purchase
        self.cb_max_start_delay_hours = cb_max_start_delay_hours
        self.cb_duration_hours = cb_duration_hours
        # Per-experiment timeout (DeploymentPlan.vllm_ready_timeout_s) is the
        # default; an explicit ready_timeout_s passed at construction time
        # overrides it. This matters for Llama-4-Scout's exp_6/exp_7, where
        # the 40-min runner-default would expire while HF is still downloading
        # the 218 GiB BF16 weights — the experiments themselves bump the field.
        self.ready_timeout_s = (
            ready_timeout_s
            if ready_timeout_s is not None
            else config.deployment.vllm_ready_timeout_s
        )

        # Structural validation now that we have a catalog
        config.validate_against(catalog)

        self._session = boto3.Session(region_name=config.deployment.region)
        self.ec2 = self._session.client("ec2")
        self.iam = self._session.client("iam")

        self._renderer = UserDataRenderer()
        self._resources = ResourceManager(
            config=config, catalog=catalog,
            ec2_client=self.ec2, iam_client=self.iam,
        )

        # Initial state shell
        self.state = DeploymentState(
            experiment_id=config.deployment.experiment_id,
            instance_type=config.deployment.instance_type,
            region=config.deployment.region,
            api_key=self._generate_api_key(),
            capacity_reservation_id=capacity_reservation_id,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def launch(self) -> DeploymentState:
        """Provision + wait for vLLM. Returns populated state."""
        cfg = self.config.deployment
        LOG.info(
            "[%s] launching %s in %s", cfg.experiment_id,
            cfg.instance_type, cfg.region,
        )

        # 1. AWS plumbing
        self._resources.ensure_all(caller_ip_cidr=self.caller_ip_cidr)
        self.state.security_group_id = self._resources.security_group_id
        self.state.ami_id = self._resources.ami_id
        self.state.caller_ip_cidr = self.caller_ip_cidr

        # 2. Render user-data
        user_data_b64 = self._renderer.render_b64(
            self.config, self.catalog,
            hf_secret_name=self.hf_secret_name, vllm_api_key=self.state.api_key,
        )

        # 3. Try each capacity strategy in order
        ctx = self._build_launch_context(user_data_b64)
        result = self._try_strategies(ctx)
        self._record_result(result)

        # 4. Wait for public IP + vLLM ready
        self._wait_for_public_ip()
        self._wait_for_vllm_ready()

        self.state.base_url = f"http://{self.state.public_ip}:8000/v1"
        self.state.mark_launched()
        LOG.info(
            "[%s] ready at %s (api key prefix: %s)",
            cfg.experiment_id, self.state.base_url, self.state.api_key[:8],
        )
        return self.state

    def terminate(self) -> None:
        """Clean up everything the runner created. Idempotent."""
        cfg = self.config.deployment

        # 1. Instance
        if self.state.instance_id:
            try:
                LOG.info("[%s] terminating %s", cfg.experiment_id, self.state.instance_id)
                self.ec2.terminate_instances(InstanceIds=[self.state.instance_id])
                self._wait_for_terminated(self.state.instance_id)
            except ClientError as exc:
                LOG.warning("Terminate instance failed: %s", exc)
            self.state.instance_id = None

        # 2. Spot Fleet
        if self.state.spot_fleet_id:
            try:
                self.ec2.delete_fleets(
                    FleetIds=[self.state.spot_fleet_id], TerminateInstances=False,
                )
            except ClientError as exc:
                LOG.warning("Delete fleet failed: %s", exc)
            self.state.spot_fleet_id = None

        # 3. Launch template
        if self.state.launch_template_id:
            try:
                self.ec2.delete_launch_template(
                    LaunchTemplateId=self.state.launch_template_id,
                )
            except ClientError as exc:
                LOG.warning("Delete launch template failed: %s", exc)
            self.state.launch_template_id = None

        # 4. Auto-created ODCR (skip Capacity Blocks — not cancellable)
        if self.state.auto_created_odcr_id and self.state.capacity_mode != "capacity-block":
            try:
                self.ec2.cancel_capacity_reservation(
                    CapacityReservationId=self.state.auto_created_odcr_id,
                )
            except ClientError as exc:
                LOG.warning("Cancel ODCR failed: %s", exc)
            self.state.auto_created_odcr_id = None

        # 5. Security group (ResourceManager retries on ENI cleanup)
        self._resources.teardown()
        self.state.security_group_id = None
        self.state.mark_terminated()

    # ------------------------------------------------------------------
    # Strategy orchestration
    # ------------------------------------------------------------------
    def _try_strategies(self, ctx: LaunchContext) -> LaunchResult:
        cfg = self.config.deployment
        last_err: Exception | None = None
        for mode in cfg.capacity_preference:
            strategy = self._strategy_for(mode)
            LOG.info("[%s] trying capacity mode=%s", cfg.experiment_id, mode)
            try:
                return strategy.launch(ctx)
            except CapacityExhausted as exc:
                LOG.warning("[%s] %s exhausted: %s", cfg.experiment_id, mode, exc)
                last_err = exc
                continue
            except StrategyMisconfigured:
                # CB missing confirm → propagate immediately; don't silently
                # skip to next mode.
                raise

        raise RuntimeError(
            f"[{cfg.experiment_id}] All capacity modes {cfg.capacity_preference} failed. "
            f"Last error: {last_err}"
        ) from last_err

    def _strategy_for(self, mode: CapacityMode) -> CapacityStrategy:
        match mode:
            case "spot":
                return SpotFleetStrategy()
            case "on-demand":
                return OnDemandStrategy(
                    capacity_reservation_id=self.capacity_reservation_id,
                )
            case "odcr":
                return ODCRStrategy()
            case _:
                raise ValueError(f"Unknown capacity mode: {mode!r}")

    def _build_launch_context(self, user_data_b64: str) -> LaunchContext:
        ms = self.config.model_spec
        tags = [
            {"Key": "Project", "Value": ms.project_tag_value},
            {"Key": "Experiment", "Value": self.config.deployment.experiment_id},
            {"Key": "Model", "Value": ms.display_name},
        ]
        return LaunchContext(
            config=self.config,
            security_group_id=self.state.security_group_id or "",
            ami_id=self.state.ami_id or "",
            user_data_b64=user_data_b64,
            iam_instance_profile_name=ms.iam_instance_profile_name,
            tags=tags,
            ec2=self.ec2,
            iam=self.iam,
            get_subnets_for_preferred_azs=self._resources.get_subnets_for_preferred_azs,
            cleanup_partial_launch=self._cleanup_partial_launch,
        )

    def _record_result(self, result: LaunchResult) -> None:
        self.state.instance_id = result.instance_id
        self.state.placement_az = result.availability_zone
        self.state.capacity_mode = result.capacity_mode
        self.state.spot_fleet_id = result.spot_fleet_id
        self.state.launch_template_id = result.launch_template_id
        self.state.auto_created_odcr_id = result.auto_created_odcr_id

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def _wait_for_public_ip(self, timeout_s: int = 120) -> None:
        if not self.state.instance_id:
            raise RuntimeError("no instance_id to wait on")
        waiter = self.ec2.get_waiter("instance_running")
        waiter.wait(
            InstanceIds=[self.state.instance_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": max(6, timeout_s // 10)},
        )
        resp = self.ec2.describe_instances(InstanceIds=[self.state.instance_id])
        inst = resp["Reservations"][0]["Instances"][0]
        self.state.public_ip = inst.get("PublicIpAddress")
        if not self.state.public_ip:
            raise RuntimeError(f"Instance {self.state.instance_id} has no public IP.")
        LOG.info(
            "[%s] instance %s public IP %s",
            self.config.deployment.experiment_id,
            self.state.instance_id, self.state.public_ip,
        )

    def _wait_for_vllm_ready(self) -> None:
        if not self.state.public_ip:
            raise RuntimeError("no public_ip to poll")
        url = f"http://{self.state.public_ip}:8000/v1/models"
        # Scheme allowlist: this poll targets a vLLM endpoint we just
        # provisioned on EC2; the IP is captured at deployment time.
        if not url.startswith(("http://", "https://")):  # pragma: no cover
            raise ValueError(f"unexpected scheme in vLLM ready URL: {url!r}")
        start = time.time()
        deadline = start + self.ready_timeout_s
        last_error = "<no attempt>"
        while time.time() < deadline:
            try:
                req = urllib.request.Request(
                    url, headers={"Authorization": f"Bearer {self.state.api_key}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                    if resp.status == 200:
                        elapsed = int(time.time() - start)
                        LOG.info(
                            "[%s] vLLM ready after %ds",
                            self.config.deployment.experiment_id, elapsed,
                        )
                        return
            except (urllib.error.URLError, socket.timeout, ConnectionResetError) as exc:
                last_error = str(exc)
            except Exception as exc:  # noqa: BLE001
                last_error = repr(exc)
            time.sleep(20)
        raise TimeoutError(
            f"vLLM did not become ready on {url} within {self.ready_timeout_s}s. "
            f"Last error: {last_error}"
        )

    def _wait_for_terminated(self, instance_id: str, timeout_s: int = 300) -> None:
        waiter = self.ec2.get_waiter("instance_terminated")
        try:
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 15, "MaxAttempts": timeout_s // 15},
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Wait-for-terminate timed out: %s", exc)

    def _cleanup_partial_launch(
        self, instance_id: str | None, fleet_id: str | None,
    ) -> None:
        """Best-effort teardown if a strategy partially succeeded."""
        if instance_id:
            try:
                self.ec2.terminate_instances(InstanceIds=[instance_id])
            except ClientError:
                pass
        if fleet_id:
            try:
                self.ec2.delete_fleets(FleetIds=[fleet_id], TerminateInstances=False)
            except ClientError:
                pass

    @staticmethod
    def _generate_api_key() -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(40))


__all__ = ["DeploymentRunner"]
