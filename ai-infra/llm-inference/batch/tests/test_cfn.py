"""Tests for the CloudFormation template builder.

Strategy: build templates for each capacity mode, assert structural
properties (logical ids present, correct type strings, dependencies).
No live AWS calls, no golden fixtures (too brittle) — instead, shape tests.
"""
from __future__ import annotations

import pytest

from llm_batch_deploy.data import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    ModelSpec,
    QueueConfig,
)
from llm_batch_deploy.deployer.cfn import build_template


def _ms() -> ModelSpec:
    return ModelSpec(
        resource_prefix="medgemma-27b",
        hf_model_id="google/medgemma-27b-text-it",
        served_model_name="medgemma-27b",
        weight_size_gib=55.0,
        gated=True,
    )


def _plan_spot() -> BatchDeploymentPlan:
    return BatchDeploymentPlan(
        model_spec=_ms(),
        compute_environments=[
            ComputeEnvironmentConfig(name_suffix="p4d-spot", instance_types=["p4d.24xlarge"], capacity_mode="spot",
            max_vcpus=96,),
        ],
        queues=[QueueConfig(
            name_suffix="primary", priority=1,
            compute_environment_suffixes=["p4d-spot"],
        )],
        tensor_parallel=2, data_parallel=4,
    )


def _plan_ondemand() -> BatchDeploymentPlan:
    return BatchDeploymentPlan(
        model_spec=_ms(),
        compute_environments=[
            ComputeEnvironmentConfig(name_suffix="p4d-od", instance_types=["p4d.24xlarge"], capacity_mode="on-demand",),
        ],
        queues=[QueueConfig(
            name_suffix="primary", priority=1,
            compute_environment_suffixes=["p4d-od"],
        )],
    )


def _plan_odcr() -> BatchDeploymentPlan:
    return BatchDeploymentPlan(
        model_spec=_ms(),
        compute_environments=[
            ComputeEnvironmentConfig(name_suffix="p4d-odcr", instance_types=["p4d.24xlarge"], capacity_mode="odcr",
            capacity_reservation_id="cr-0123456789abcdef0",),
        ],
        queues=[QueueConfig(
            name_suffix="primary", priority=1,
            compute_environment_suffixes=["p4d-odcr"],
        )],
    )


def _plan_capacity_block() -> BatchDeploymentPlan:
    return BatchDeploymentPlan(
        model_spec=_ms(),
        compute_environments=[
            ComputeEnvironmentConfig(name_suffix="p5-cb", instance_types=["p5.48xlarge"], capacity_mode="capacity-block",
            capacity_reservation_id="cr-0ml1234567890abcd",
            min_vcpus=0, max_vcpus=192,),
        ],
        queues=[QueueConfig(
            name_suffix="primary", priority=1,
            compute_environment_suffixes=["p5-cb"],
        )],
        tensor_parallel=1, data_parallel=8,
    )


def _plan_failover() -> BatchDeploymentPlan:
    """Multi-CE queue: spot-first, on-demand fallback."""
    return BatchDeploymentPlan(
        model_spec=_ms(),
        compute_environments=[
            ComputeEnvironmentConfig(name_suffix="p4d-spot", instance_types=["p4d.24xlarge"], capacity_mode="spot",),
            ComputeEnvironmentConfig(name_suffix="p4d-od", instance_types=["p4d.24xlarge"], capacity_mode="on-demand",),
        ],
        queues=[QueueConfig(
            name_suffix="primary", priority=1,
            compute_environment_suffixes=["p4d-spot", "p4d-od"],
        )],
    )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------
class TestTemplateStructure:
    def test_has_required_top_level_keys(self) -> None:
        tpl = build_template(_plan_spot())
        assert tpl["AWSTemplateFormatVersion"] == "2010-09-09"
        assert "Description" in tpl
        assert "Parameters" in tpl
        assert "Resources" in tpl
        assert "Outputs" in tpl

    def test_parameters_are_correct(self) -> None:
        tpl = build_template(_plan_spot())
        params = tpl["Parameters"]
        assert set(params) == {
            "VpcId", "SubnetIds", "ContainerImageUri",
            "ExistingStagingBucketName",
        }
        assert params["VpcId"]["Type"] == "AWS::EC2::VPC::Id"
        assert params["SubnetIds"]["Type"] == "List<AWS::EC2::Subnet::Id>"
        assert params["ExistingStagingBucketName"]["Default"] == ""

    def test_iam_resources_present(self) -> None:
        tpl = build_template(_plan_spot())
        resources = tpl["Resources"]
        for rid in ("BatchServiceRole", "EcsInstanceRole", "EcsInstanceProfile",
                    "JobRole", "JobExecutionRole"):
            assert rid in resources
            assert resources[rid]["Type"] == "AWS::IAM::Role" or resources[rid]["Type"] == "AWS::IAM::InstanceProfile"

    def test_storage_resources_present(self) -> None:
        tpl = build_template(_plan_spot())
        r = tpl["Resources"]
        assert r["StagingBucket"]["Type"] == "AWS::S3::Bucket"
        assert r["StagingBucket"]["DeletionPolicy"] == "Retain"
        assert r["BatchSecurityGroup"]["Type"] == "AWS::EC2::SecurityGroup"
        assert r["JobLogGroup"]["Type"] == "AWS::Logs::LogGroup"

    def test_job_definition_present(self) -> None:
        tpl = build_template(_plan_spot())
        jd = tpl["Resources"]["JobDefinition"]
        assert jd["Type"] == "AWS::Batch::JobDefinition"
        assert jd["Properties"]["PlatformCapabilities"] == ["EC2"]
        # Env vars include the critical model-shape defaults
        env_names = {e["Name"] for e in jd["Properties"]["ContainerProperties"]["Environment"]}
        assert "HF_MODEL_ID" in env_names
        assert "TENSOR_PARALLEL_SIZE" in env_names
        assert "IN_FLIGHT_PER_JOB" in env_names
        assert "ENABLE_PREFIX_CACHING" in env_names

    def test_prefix_caching_default_true(self) -> None:
        """Plan default enable_prefix_caching=True → env var string 'true'."""
        tpl = build_template(_plan_spot())
        env = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]["Environment"]
        entry = next(e for e in env if e["Name"] == "ENABLE_PREFIX_CACHING")
        assert entry["Value"] == "true"

    def test_prefix_caching_honors_plan_override(self) -> None:
        """When the plan sets enable_prefix_caching=False, the env reflects it."""
        plan = _plan_spot().model_copy(update={"enable_prefix_caching": False})
        tpl = build_template(plan)
        env = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]["Environment"]
        entry = next(e for e in env if e["Name"] == "ENABLE_PREFIX_CACHING")
        assert entry["Value"] == "false"

    def test_extra_serve_flags_default_empty(self) -> None:
        """Default plan ships an empty EXTRA_SERVE_FLAGS env var."""
        tpl = build_template(_plan_spot())
        env = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]["Environment"]
        entry = next(e for e in env if e["Name"] == "EXTRA_SERVE_FLAGS")
        assert entry["Value"] == ""

    def test_extra_serve_flags_honors_plan_override(self) -> None:
        """A plan that sets extra_serve_flags propagates it verbatim into the env."""
        plan = _plan_spot().model_copy(update={"extra_serve_flags": "--kv-cache-dtype fp8"})
        tpl = build_template(plan)
        env = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]["Environment"]
        entry = next(e for e in env if e["Name"] == "EXTRA_SERVE_FLAGS")
        assert entry["Value"] == "--kv-cache-dtype fp8"

    def test_extra_env_vars_render_into_jobdef_environment(self) -> None:
        """extra_env_vars on the plan render as Environment entries on the JobDef.

        The runtime image's run.sh expects model-specific env vars
        (VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8 for gpt-oss-20b on Blackwell,
        VLLM_ATTENTION_BACKEND for Ampere fallback) to already be in the
        container's environ when vllm starts. Threading them through the
        JobDef env block is how AWS Batch hands them to the container.
        """
        plan = _plan_spot().model_copy(
            update={"extra_env_vars": {
                "VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8": "1",
            }}
        )
        tpl = build_template(plan)
        env = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]["Environment"]
        entry = next(e for e in env if e["Name"] == "VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8")
        assert entry["Value"] == "1"

    def test_extra_env_vars_cannot_shadow_reserved_names(self) -> None:
        """Reserved framework env-var names cannot be set via extra_env_vars.

        If a plan author tries to override e.g. HF_TOKEN or HF_MODEL_ID, the
        constructor must reject it instead of silently shadowing the
        framework's own contract with the runtime.
        """
        from pydantic import ValidationError

        from llm_batch_deploy.data import (
            BatchDeploymentPlan,
            ComputeEnvironmentConfig,
            QueueConfig,
        )
        ce = ComputeEnvironmentConfig(
            name_suffix="ce",
            instance_types=["g7e.2xlarge"],
            capacity_mode="spot",
            min_vcpus=0,
            max_vcpus=64,
            desired_vcpus=0,
        )
        q = QueueConfig(
            name_suffix="primary",
            priority=1,
            compute_environment_suffixes=["ce"],
        )
        with pytest.raises(ValidationError, match="reserved"):
            BatchDeploymentPlan(
                model_spec=_plan_spot().model_spec,
                region="us-west-2",
                compute_environments=[ce],
                queues=[q],
                extra_env_vars={"HF_TOKEN": "leaked"},
            )

    def test_extra_env_vars_cannot_have_invalid_names(self) -> None:
        """extra_env_vars names must match [A-Z_][A-Z0-9_]*."""
        from pydantic import ValidationError

        from llm_batch_deploy.data import (
            BatchDeploymentPlan,
            ComputeEnvironmentConfig,
            QueueConfig,
        )
        ce = ComputeEnvironmentConfig(
            name_suffix="ce",
            instance_types=["g7e.2xlarge"],
            capacity_mode="spot",
            min_vcpus=0,
            max_vcpus=64,
            desired_vcpus=0,
        )
        q = QueueConfig(
            name_suffix="primary",
            priority=1,
            compute_environment_suffixes=["ce"],
        )
        with pytest.raises(ValidationError, match=r"\[A-Z_\]\[A-Z0-9_\]\*"):
            BatchDeploymentPlan(
                model_spec=_plan_spot().model_spec,
                region="us-west-2",
                compute_environments=[ce],
                queues=[q],
                extra_env_vars={"lowercase-bad": "value"},
            )

    def test_request_timeout_default_is_120(self) -> None:
        """Default plan ships REQUEST_TIMEOUT_S=120 — matches httpx default."""
        tpl = build_template(_plan_spot())
        env = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]["Environment"]
        entry = next(e for e in env if e["Name"] == "REQUEST_TIMEOUT_S")
        assert entry["Value"] == "120"

    def test_request_timeout_honors_plan_override(self) -> None:
        """A plan that lifts request_timeout_seconds propagates into the env.

        Audit-shape regression: the framework default (120s) clips long
        generations on slow GPUs at high concurrency. Plans must be able
        to opt up; this pins the wiring from plan -> CFN env so the env
        variable is read at runtime by the container's run.sh + entrypoint.
        """
        plan = _plan_spot().model_copy(update={"request_timeout_seconds": 600})
        tpl = build_template(plan)
        env = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]["Environment"]
        entry = next(e for e in env if e["Name"] == "REQUEST_TIMEOUT_S")
        assert entry["Value"] == "600"


# ---------------------------------------------------------------------------
# Per-capacity-mode shape
# ---------------------------------------------------------------------------
class TestSpotCE:
    def test_spot_ce_has_right_shape(self) -> None:
        tpl = build_template(_plan_spot())
        r = tpl["Resources"]
        assert "ComputeEnvP4DSpot" in r
        ce = r["ComputeEnvP4DSpot"]
        cr = ce["Properties"]["ComputeResources"]
        assert cr["Type"] == "SPOT"
        assert cr["AllocationStrategy"] == "SPOT_PRICE_CAPACITY_OPTIMIZED"
        assert "SpotIamFleetRole" in cr
        assert "SpotFleetRole" in r  # because any CE is spot


class TestOnDemandCE:
    def test_ondemand_ce_has_right_shape(self) -> None:
        tpl = build_template(_plan_ondemand())
        r = tpl["Resources"]
        assert "ComputeEnvP4DOd" in r
        cr = r["ComputeEnvP4DOd"]["Properties"]["ComputeResources"]
        assert cr["Type"] == "EC2"
        assert "SpotFleetRole" not in r  # not needed for OD-only stack


class TestOdcrCE:
    def test_odcr_ce_has_right_shape(self) -> None:
        tpl = build_template(_plan_odcr())
        cr = tpl["Resources"]["ComputeEnvP4DOdcr"]["Properties"]["ComputeResources"]
        assert cr["Type"] == "EC2"
        assert cr["CapacityReservationOptions"]["CapacityReservationTarget"][
            "CapacityReservationId"] == "cr-0123456789abcdef0"


class TestCapacityBlockCE:
    def test_cb_ce_has_right_shape(self) -> None:
        tpl = build_template(_plan_capacity_block())
        cr = tpl["Resources"]["ComputeEnvP5Cb"]["Properties"]["ComputeResources"]
        assert cr["Type"] == "CAPACITY_BLOCK"
        assert cr["CapacityReservationOptions"]["CapacityReservationTarget"][
            "CapacityReservationId"] == "cr-0ml1234567890abcd"
        assert "SpotFleetRole" not in tpl["Resources"]  # not spot


# ---------------------------------------------------------------------------
# Queue + multi-CE
# ---------------------------------------------------------------------------
class TestQueue:
    def test_single_ce_queue(self) -> None:
        tpl = build_template(_plan_spot())
        q = tpl["Resources"]["JobQueuePrimary"]
        assert q["Type"] == "AWS::Batch::JobQueue"
        ceo = q["Properties"]["ComputeEnvironmentOrder"]
        assert len(ceo) == 1
        assert ceo[0]["Order"] == 1

    def test_multi_ce_failover(self) -> None:
        tpl = build_template(_plan_failover())
        q = tpl["Resources"]["JobQueuePrimary"]
        ceo = q["Properties"]["ComputeEnvironmentOrder"]
        assert [c["Order"] for c in ceo] == [1, 2]
        # First CE referenced is spot
        assert ceo[0]["ComputeEnvironment"] == {"Ref": "ComputeEnvP4DSpot"}


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
class TestOutputs:
    def test_outputs_present(self) -> None:
        tpl = build_template(_plan_spot())
        o = tpl["Outputs"]
        assert "StagingBucketName" in o
        assert "JobDefinitionArn" in o
        assert "JobQueuePrimaryArn" in o
        assert "EcrRepositoryUri" in o  # include_ecr default True
        assert "HfTokenSecretArn" in o

    def test_no_ecr_output_when_excluded(self) -> None:
        tpl = build_template(_plan_spot(), include_ecr=False)
        assert "EcrRepositoryUri" not in tpl["Outputs"]
        assert "EcrRepository" not in tpl["Resources"]

    def test_no_staging_bucket_resource_when_excluded(self) -> None:
        # Re-deploy path: prior bucket carries Retain policy, so the new
        # stack must consume it via the parameter rather than create one.
        tpl = build_template(_plan_spot(), include_staging_bucket=False)
        assert "StagingBucket" not in tpl["Resources"]
        # Output still surfaces the bucket name — but sourced from the
        # parameter so the deployer's StackOutputs surface the consumed name.
        out = tpl["Outputs"]["StagingBucketName"]
        assert out["Value"] == {"Ref": "ExistingStagingBucketName"}
        # JobRole policy must still grant S3 R/W on the consumed bucket
        # (not the in-stack logical resource) — otherwise the container
        # gets AccessDenied at job start.
        job_role = tpl["Resources"]["JobRole"]
        policy_doc = job_role["Properties"]["Policies"][0]["PolicyDocument"]
        resources = policy_doc["Statement"][0]["Resource"]
        # Both must reference the parameter, not StagingBucket.Arn
        for r in resources:
            assert "Fn::GetAtt" not in r, (
                f"JobRole still references in-stack StagingBucket via "
                f"GetAtt: {r}"
            )
            assert "${StagingBucket." not in str(r), (
                f"JobRole still references StagingBucket logical id: {r}"
            )
            assert "ExistingStagingBucketName" in str(r), (
                f"JobRole policy resource doesn't reference parameter: {r}"
            )


# ---------------------------------------------------------------------------
# HF token secret + secure token plumbing (commit C2)
# ---------------------------------------------------------------------------
class TestHfTokenSecret:
    def test_secret_resource_present(self) -> None:
        tpl = build_template(_plan_spot())
        r = tpl["Resources"]
        assert "HfTokenSecret" in r
        assert r["HfTokenSecret"]["Type"] == "AWS::SecretsManager::Secret"
        # Secret is created with a placeholder that user must overwrite.
        props = r["HfTokenSecret"]["Properties"]
        assert "PLACEHOLDER" in props["SecretString"]
        # Stack-specific name
        assert "medgemma-27b-batch/hf-token" == props["Name"]

    def test_job_definition_uses_secrets_block_not_plain_env(self) -> None:
        tpl = build_template(_plan_spot())
        cp = tpl["Resources"]["JobDefinition"]["Properties"]["ContainerProperties"]

        # Secrets block must exist and map both env var names
        assert "Secrets" in cp
        secret_names = {s["Name"] for s in cp["Secrets"]}
        assert secret_names == {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}
        for s in cp["Secrets"]:
            assert s["ValueFrom"] == {"Ref": "HfTokenSecret"}

        # HF_TOKEN must NOT be in the plain Environment block
        env_names = {e["Name"] for e in cp["Environment"]}
        assert "HF_TOKEN" not in env_names
        assert "HUGGING_FACE_HUB_TOKEN" not in env_names

    def test_job_execution_role_has_secret_read_permission(self) -> None:
        tpl = build_template(_plan_spot())
        role = tpl["Resources"]["JobExecutionRole"]
        inline = role["Properties"].get("Policies", [])
        assert len(inline) == 1
        stmt = inline[0]["PolicyDocument"]["Statement"][0]
        assert stmt["Action"] == "secretsmanager:GetSecretValue"
        assert stmt["Resource"] == {"Ref": "HfTokenSecret"}

    def test_job_definition_depends_on_secret(self) -> None:
        tpl = build_template(_plan_spot())
        assert "HfTokenSecret" in tpl["Resources"]["JobDefinition"]["DependsOn"]


# ---------------------------------------------------------------------------
# Launch template — root EBS volume sizing
# ---------------------------------------------------------------------------
class TestLaunchTemplateRootVolume:
    def test_default_root_volume_fits_lineup(self) -> None:
        """Default plan.root_volume_gib (300) must reach the launch template
        and must be >=300 GiB so a Llama-4-Scout-class (~218 GiB BF16) pull
        does not ENOSPC mid-download."""
        tpl = build_template(_plan_spot())
        lt = tpl["Resources"]["BatchLaunchTemplate"]
        assert lt["Type"] == "AWS::EC2::LaunchTemplate"
        bdm = lt["Properties"]["LaunchTemplateData"]["BlockDeviceMappings"][0]
        assert bdm["DeviceName"] == "/dev/xvda"
        assert bdm["Ebs"]["VolumeSize"] == 300, (
            "default root volume must be >=300 GiB to fit the lineup; "
            "Llama-4-Scout is ~218 GiB BF16 alone"
        )
        assert bdm["Ebs"]["VolumeType"] == "gp3"
        assert bdm["Ebs"]["Encrypted"] is True

    def test_root_volume_size_overridable_per_plan(self) -> None:
        """Plans (e.g. Llama-4-Scout) can opt into a larger root volume."""
        plan = _plan_spot().model_copy(update={"root_volume_gib": 400})
        tpl = build_template(plan)
        bdm = tpl["Resources"]["BatchLaunchTemplate"]["Properties"][
            "LaunchTemplateData"]["BlockDeviceMappings"][0]
        assert bdm["Ebs"]["VolumeSize"] == 400


# ---------------------------------------------------------------------------
# Templates are valid Python dicts that serialize to JSON cleanly
# ---------------------------------------------------------------------------
class TestJsonSerializable:
    def test_all_templates_json_serialize(self) -> None:
        import json
        for plan_fn in (_plan_spot, _plan_ondemand, _plan_odcr,
                        _plan_capacity_block, _plan_failover):
            tpl = build_template(plan_fn())
            assert json.dumps(tpl)  # Raises if anything isn't JSON-compatible


# ---------------------------------------------------------------------------
# DependsOn ordering
# ---------------------------------------------------------------------------
class TestDependsOn:
    def test_ce_depends_on_iam_roles(self) -> None:
        tpl = build_template(_plan_spot())
        ce = tpl["Resources"]["ComputeEnvP4DSpot"]
        assert "BatchServiceRole" in ce["DependsOn"]
        assert "EcsInstanceProfile" in ce["DependsOn"]
        assert "SpotFleetRole" in ce["DependsOn"]

    def test_queue_depends_on_ce(self) -> None:
        tpl = build_template(_plan_spot())
        q = tpl["Resources"]["JobQueuePrimary"]
        assert "ComputeEnvP4DSpot" in q["DependsOn"]

    def test_job_definition_depends_on_roles(self) -> None:
        tpl = build_template(_plan_spot())
        jd = tpl["Resources"]["JobDefinition"]
        for dep in ("JobRole", "JobExecutionRole", "JobLogGroup"):
            assert dep in jd["DependsOn"]


# ---------------------------------------------------------------------------
# Per-model tagging: every taggable resource carries Project + Model tags on every
# resource so cleanup automation can sweep by model. The CFN stack-level
# Tags propagate to most resources, but TagSpecifications inside launch
# templates and per-resource Tags lists must carry both tags themselves.
# ---------------------------------------------------------------------------
class TestPerModelTagging:
    def _all_tagged_resources(self, tpl: dict) -> list[tuple[str, list[dict]]]:
        """Return [(logical_id, tags_list), ...] for every resource that
        declared a Tags list (skipping resources that don't carry tags)."""
        out: list[tuple[str, list[dict]]] = []
        for lid, res in tpl["Resources"].items():
            props = res.get("Properties") or {}
            tags = props.get("Tags")
            if isinstance(tags, list):
                out.append((lid, tags))
        return out

    def test_tagged_resources_carry_both_project_and_model(self) -> None:
        tpl = build_template(_plan_spot())
        offenders: list[str] = []
        for lid, tags in self._all_tagged_resources(tpl):
            keys = {t.get("Key") for t in tags if isinstance(t, dict)}
            if "Project" in keys and "Model" not in keys:
                offenders.append(f"{lid} ({tpl['Resources'][lid]['Type']})")
        assert not offenders, (
            f"Resources with Project tag but missing Model=<resource_prefix> "
            f"tag: {offenders}. Per-model tagging policy requires this so "
            f"cleanup automation can sweep by model."
        )

    def test_launch_template_instance_tags_include_model(self) -> None:
        """Launch-template TagSpecifications get applied to spawned EC2
        instances; CFN stack-level tags do NOT propagate to instances
        spawned by Batch via this template, so the launch template must
        include Model itself."""
        tpl = build_template(_plan_spot())
        lt = tpl["Resources"]["BatchLaunchTemplate"]
        tag_specs = lt["Properties"]["LaunchTemplateData"]["TagSpecifications"]
        instance_tags = next(
            spec["Tags"] for spec in tag_specs
            if spec["ResourceType"] == "instance"
        )
        keys = {t["Key"] for t in instance_tags}
        assert "Project" in keys
        assert "Model" in keys, (
            "BatchLaunchTemplate's instance tag spec must include Model — "
            "EC2 instances spawned by Batch don't inherit stack-level tags."
        )
