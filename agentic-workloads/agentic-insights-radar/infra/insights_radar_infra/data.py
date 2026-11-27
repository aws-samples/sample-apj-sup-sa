# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Keys, tables and buckets."""

from __future__ import annotations

from aws_cdk import Aws, Duration, RemovalPolicy
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct

from insights_radar_infra.config import DeployConfig

DUE_INDEX = "due"


class Data(Construct):
    def __init__(self, scope: Construct, cid: str, config: DeployConfig) -> None:
        super().__init__(scope, cid)
        # RETAIN_ON_UPDATE_OR_DELETE: a failed first deployment still cleans up, so a retry does not collide.
        removal = (
            RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE
            if config.retain_data
            else RemovalPolicy.DESTROY
        )
        self.removal = (
            removal  # for the log groups, so retainData=false really removes everything
        )

        # The alias and the description carry the prefix: with retainData=true a destroy leaves the
        # key behind, and two deployments otherwise leave keys nobody can tell apart (P2-049).
        self.key = kms.Key(
            self,
            "DataKey",
            alias=f"{config.prefix}-data",
            description=f"Agentic Insights Radar ({config.prefix}) data at rest (tables, buckets, logs)",
            enable_key_rotation=True,
            removal_policy=removal,
        )
        # Signs and verifies module run tokens. The key never leaves KMS.
        self.run_key = kms.Key(
            self,
            "RunTokenKey",
            alias=f"{config.prefix}-run-token",
            description=f"Agentic Insights Radar ({config.prefix}) run-token HMAC",
            key_spec=kms.KeySpec.HMAC_256,
            key_usage=kms.KeyUsage.GENERATE_VERIFY_MAC,
            removal_policy=RemovalPolicy.DESTROY,
        )

        def index(name: str, pk: str, sk: str) -> ddb.GlobalSecondaryIndexPropsV2:
            return ddb.GlobalSecondaryIndexPropsV2(
                index_name=name,
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name=sk, type=ddb.AttributeType.STRING),
            )

        def table(
            name: str, stream: bool, extra: list[ddb.GlobalSecondaryIndexPropsV2]
        ) -> ddb.TableV2:
            return ddb.TableV2(
                self,
                name,
                partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
                billing=ddb.Billing.on_demand(),
                encryption=ddb.TableEncryptionV2.customer_managed_key(self.key),
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True
                ),
                deletion_protection=config.protect,
                dynamo_stream=ddb.StreamViewType.NEW_AND_OLD_IMAGES if stream else None,
                time_to_live_attribute="ttl",
                global_secondary_indexes=[index("gsi1", "gsi1pk", "gsi1sk"), *extra],
                removal_policy=removal,
            )

        self.graph_table = table("GraphTable", stream=True, extra=[])
        # Digest delivery: the subscriptions that are due, by time (a sparse index: only a delivery
        # subscription carries due_pk="DELIVERY_DUE" and due_sk="<next_at>#<sub>#<id>").
        self.ops_table = table(
            "OpsTable",
            stream=False,
            extra=[index(DUE_INDEX, "due_pk", "due_sk")]
            if config.digest.enabled
            else [],
        )

        self.access_logs = s3.Bucket(
            self,
            "AccessLogs",
            encryption=s3.BucketEncryption.S3_MANAGED,  # S3 server access logging cannot write to SSE-KMS
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=Duration.days(config.log_retention_days))
            ],
            removal_policy=removal,
            auto_delete_objects=not config.retain_data,
        )
        # A fixed name lets the CDN's CSP name the bucket without depending on it (the bucket's
        # CORS rule depends on the CDN, so a reference the other way would be a cycle).
        self.assets_bucket_name = (
            f"{config.prefix}-assets-{Aws.ACCOUNT_ID}-{Aws.REGION}"
        )
        self.assets = s3.Bucket(
            self,
            "Assets",
            bucket_name=self.assets_bucket_name,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            server_access_logs_bucket=self.access_logs,
            server_access_logs_prefix="assets/",
            lifecycle_rules=[
                s3.LifecycleRule(noncurrent_version_expiration=Duration.days(30)),
                s3.LifecycleRule(
                    abort_incomplete_multipart_upload_after=Duration.days(1)
                ),
            ],
            removal_policy=removal,
            auto_delete_objects=not config.retain_data,
        )
        # Everything here except ``key`` waits for the AgentCore chat memory (see stack.py). The
        # memory is encrypted with ``key``, so it depends on the key and the key cannot depend back.
        self.waits_for_memory = [
            self.run_key,
            self.graph_table,
            self.ops_table,
            self.access_logs,
            self.assets,
        ]
