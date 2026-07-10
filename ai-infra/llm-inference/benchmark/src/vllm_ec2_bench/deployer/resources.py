"""ResourceManager — ephemeral AWS plumbing for one experiment.

Handles everything that's not "launch the actual instance":

* IAM role + instance profile (shared across experiments, idempotent create)
* Per-experiment security group (created at launch, deleted at teardown)
* Default VPC / subnet discovery with instance-type-offering filter
* Deep Learning AMI lookup

Each experiment gets its own :class:`ResourceManager` so state doesn't leak
between runs.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from ..data import ExperimentConfig, ModelSpec

if TYPE_CHECKING:
    from ..data.catalog import Catalog

LOG = logging.getLogger(__name__)


# Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.x (Ubuntu 22.04) — matches
# what we validated MIG and user-data on 2026-05-02.
_DLAMI_NAME_PATTERN = "Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.* (Ubuntu 22.04)*"
# AWS Neuron DLAMI for inf2/trn1
_NEURON_DLAMI_NAME_PATTERN = "Deep Learning AMI Neuron (Ubuntu 22.04)*"
_AMI_OWNER = "amazon"


class ResourceManager:
    """Create / discover / delete AWS plumbing for an experiment.

    The manager is stateful: after :meth:`ensure_all` succeeds, the attributes
    :attr:`security_group_id`, :attr:`ami_id`, :attr:`vpc_id` are populated.
    :meth:`teardown` deletes resources it created; shared resources (IAM
    profile) are left alone.
    """

    def __init__(
        self,
        *,
        config: ExperimentConfig,
        catalog: "Catalog",
        ec2_client: Any,
        iam_client: Any,
    ) -> None:
        self.config = config
        self.catalog = catalog
        self.ec2 = ec2_client
        self.iam = iam_client

        # Populated by ensure_all()
        self.security_group_id: str | None = None
        self.ami_id: str | None = None
        self.vpc_id: str | None = None

        # Caches
        self._offered_azs_cache: set[str] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def ensure_all(self, *, caller_ip_cidr: str | None = None) -> None:
        """Create IAM profile (shared), SG (per-experiment), pick AMI.

        ``caller_ip_cidr`` limits inbound :8000 to one IP. If None, we
        look up the notebook's public IP via checkip.amazonaws.com.
        """
        self._ensure_instance_profile()
        if not caller_ip_cidr:
            caller_ip_cidr = f"{self._discover_public_ip()}/32"
        self.security_group_id = self._create_security_group(caller_ip_cidr)
        self.ami_id = self._pick_ami()

    def teardown(self) -> None:
        """Delete the per-experiment SG. IAM profile is left in place (shared)."""
        if self.security_group_id:
            self._delete_security_group_with_retry(self.security_group_id)
            self.security_group_id = None

    # ------------------------------------------------------------------
    # IAM
    # ------------------------------------------------------------------
    def _ensure_instance_profile(self) -> None:
        """Create the IAM role + instance profile if they don't already exist.

        The role gets:
        * ``AmazonSSMManagedInstanceCore`` — SSM Session Manager for
          debugging without SSH keys.
        * ``AmazonEC2ContainerRegistryReadOnly`` — ECR pull (e.g. Neuron DLC).
        * Inline policy ``ReadHfTokenSecret`` — ``GetSecretValue`` on the
          model's HF token secret (namespaced by resource_prefix).
        """
        ms: ModelSpec = self.config.model_spec
        profile_name = ms.iam_instance_profile_name
        role_name = ms.iam_role_name
        # Tag policy: every IAM resource carries both Project and Model
        # tags so cleanup automation can sweep by model.
        iam_tags = [
            {"Key": "Project", "Value": ms.project_tag_value},
            {"Key": "Model", "Value": ms.resource_prefix},
        ]

        # Fast path: profile already exists
        try:
            self.iam.get_instance_profile(InstanceProfileName=profile_name)
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchEntity":
                raise

        LOG.info("Creating IAM role %s + profile %s", role_name, profile_name)

        # 1. Role
        try:
            self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }],
                }),
                Description=f"EC2 instance role for {ms.display_name} benchmark.",
                Tags=iam_tags,
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "EntityAlreadyExists":
                raise

        for policy_arn in (
            "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        ):
            self.iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

        # Inline policy: read-only access to the model's HF token secret.
        # Scoped to secrets named '<resource_prefix>-benchmark/*' so one
        # model's role can't read another model's secret.
        # The wildcard is intentional — the actual secret name is chosen at
        # notebook runtime via the upsert helper; IAM evaluation is still
        # least-privilege because all secrets in that namespace belong to
        # this project.
        self.iam.put_role_policy(
            RoleName=role_name,
            PolicyName="ReadHfTokenSecret",
            PolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": "secretsmanager:GetSecretValue",
                    "Resource": (
                        f"arn:aws:secretsmanager:*:*:secret:"
                        f"{ms.resource_prefix}-benchmark/*"
                    ),
                }],
            }),
        )

        # 2. Instance profile
        try:
            self.iam.create_instance_profile(
                InstanceProfileName=profile_name, Tags=iam_tags
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "EntityAlreadyExists":
                raise

        # 3. Attach role to profile (idempotent on LimitExceeded)
        try:
            self.iam.add_role_to_instance_profile(
                InstanceProfileName=profile_name, RoleName=role_name,
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "LimitExceeded":
                raise

        # Instance profiles take ~5-10s to propagate to the EC2 control plane.
        time.sleep(10)

    # ------------------------------------------------------------------
    # Security group
    # ------------------------------------------------------------------
    def _create_security_group(self, caller_ip_cidr: str) -> str:
        ms = self.config.model_spec
        exp_id = self.config.deployment.experiment_id
        vpc_id = self._default_vpc_id()
        self.vpc_id = vpc_id

        timestamp = int(time.time())
        group_name = f"{ms.resource_prefix}-{exp_id}-{timestamp}"

        resp = self.ec2.create_security_group(
            GroupName=group_name,
            Description=f"{ms.display_name} benchmark SG for {self.config.deployment.instance_type}",
            VpcId=vpc_id,
            TagSpecifications=[{
                "ResourceType": "security-group",
                "Tags": self._base_tags(),
            }],
        )
        sg_id = resp["GroupId"]

        # Inbound: vLLM port 8000 from caller only
        self.ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": 8000,
                "ToPort": 8000,
                "IpRanges": [{
                    "CidrIp": caller_ip_cidr,
                    "Description": "vLLM HTTP from notebook",
                }],
            }],
        )
        LOG.info("Created SG %s in VPC %s (inbound :8000 from %s)", sg_id, vpc_id, caller_ip_cidr)
        return sg_id

    def _delete_security_group_with_retry(self, sg_id: str) -> None:
        """ENI release is async; retry for up to 5 minutes."""
        for attempt in range(30):
            try:
                self.ec2.delete_security_group(GroupId=sg_id)
                LOG.info("Deleted SG %s", sg_id)
                return
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code == "InvalidGroup.NotFound":
                    return
                if code == "DependencyViolation":
                    LOG.debug("SG %s still has dependencies (attempt %d); retrying", sg_id, attempt)
                    time.sleep(10)
                    continue
                LOG.warning("Could not delete SG %s: %s", sg_id, exc)
                return
        LOG.warning("Gave up deleting SG %s after retries", sg_id)

    # ------------------------------------------------------------------
    # VPC / subnet discovery
    # ------------------------------------------------------------------
    def _default_vpc_id(self) -> str:
        resp = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        vpcs = resp.get("Vpcs", [])
        if not vpcs:
            raise RuntimeError(
                f"No default VPC in {self.config.deployment.region}; "
                "create one or override VPC selection."
            )
        return vpcs[0]["VpcId"]

    def get_subnets_for_preferred_azs(self) -> dict[str, str]:
        """Return ``{az: subnet_id}`` for AZs where the instance type is offered.

        If ``deployment.preferred_azs`` is set, we trust the caller and only
        return those AZs (no offering filter).
        """
        cfg = self.config.deployment
        filters: list[dict[str, Any]] = [{"Name": "default-for-az", "Values": ["true"]}]
        if cfg.preferred_azs:
            filters.append({"Name": "availability-zone", "Values": list(cfg.preferred_azs)})
        resp = self.ec2.describe_subnets(Filters=filters)
        raw: dict[str, str] = {}
        for subnet in resp.get("Subnets", []):
            az = subnet["AvailabilityZone"]
            if az not in raw:
                raw[az] = subnet["SubnetId"]

        if cfg.preferred_azs:
            # Preserve priority order from preferred_azs.
            return {az: raw[az] for az in cfg.preferred_azs if az in raw}

        # No preferred_azs → intersect with AZs where the instance type is offered.
        offered = self._offered_azs()
        return {az: sub for az, sub in raw.items() if az in offered}

    def _offered_azs(self) -> set[str]:
        if self._offered_azs_cache is not None:
            return self._offered_azs_cache
        instance_type = self.config.deployment.instance_type
        resp = self.ec2.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [instance_type]}],
        )
        self._offered_azs_cache = {o["Location"] for o in resp.get("InstanceTypeOfferings", [])}
        if not self._offered_azs_cache:
            LOG.warning(
                "%s not offered in any AZ of %s — launches will fail",
                instance_type, self.config.deployment.region,
            )
        return self._offered_azs_cache

    # ------------------------------------------------------------------
    # AMI
    # ------------------------------------------------------------------
    def _pick_ami(self) -> str:
        family = self.catalog.hardware(self.config.deployment.instance_type).family
        pattern = _DLAMI_NAME_PATTERN if family == "gpu" else _NEURON_DLAMI_NAME_PATTERN

        resp = self.ec2.describe_images(
            Owners=[_AMI_OWNER],
            Filters=[
                {"Name": "name", "Values": [pattern]},
                {"Name": "state", "Values": ["available"]},
                {"Name": "architecture", "Values": ["x86_64"]},
            ],
        )
        images = resp.get("Images", [])
        if not images:
            raise RuntimeError(
                f"No AMI found matching {pattern!r} in {self.config.deployment.region}"
            )
        # Pick newest by CreationDate
        newest = max(images, key=lambda i: i["CreationDate"])
        LOG.info("Selected AMI %s (%s)", newest["ImageId"], newest["Name"])
        return newest["ImageId"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _base_tags(self) -> list[dict[str, str]]:
        ms = self.config.model_spec
        return [
            {"Key": "Project", "Value": ms.project_tag_value},
            # Tag policy: every AWS resource carries Model=<resource_prefix>
            # so cleanup automation can sweep all benchmark resources for a
            # given model without having to know which experiment_id was used.
            {"Key": "Model", "Value": ms.resource_prefix},
            {"Key": "Experiment", "Value": self.config.deployment.experiment_id},
        ]

    @staticmethod
    def _discover_public_ip() -> str:
        # Hardcoded HTTPS URL — checkip.amazonaws.com over TLS. The
        # startswith() guard is for static-analysis clarity; the constant
        # itself already enforces https.
        url = "https://checkip.amazonaws.com"
        if not url.startswith("https://"):  # pragma: no cover
            raise ValueError("public-IP lookup must use https")
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310
                return resp.read().decode().strip()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                "Couldn't auto-discover caller public IP. "
                "Pass caller_ip_cidr explicitly."
            ) from exc


__all__ = ["ResourceManager"]
