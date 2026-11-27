# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Optional AWS Backup plan (``backupPlan=true``): daily backups of both tables and the assets bucket.

Point-in-time recovery covers 35 days of table changes and bucket versioning keeps a deleted document
for 30 days. This plan adds daily recovery points kept for 120 days in a vault encrypted with the data
key, so an object deleted weeks ago, or a table damaged before the PITR window, can still be
restored. Copying recovery points to another account or Region is a vault setting you add in AWS
Backup (cross-account copy needs AWS Organizations); see the implementation guide.

The vault and its recovery points are encrypted with the data key, so the key has to outlive them:
the vault is always kept when the stack is deleted, and ``deployment/purge.py`` keeps it and every
key its recovery points need unless it is run with ``--delete-backups``.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_backup as backup
from aws_cdk import aws_events as events
from cdk_nag import NagPackSuppression, NagSuppressions
from constructs import Construct

from insights_radar_infra.data import Data

RETENTION_DAYS = 120
BACKUP_POLICIES = (
    "AWSBackupServiceRolePolicyForBackup",
    "AWSBackupServiceRolePolicyForRestores",
    "AWSBackupServiceRolePolicyForS3Backup",
    "AWSBackupServiceRolePolicyForS3Restore",
)


class Backups(Construct):
    def __init__(self, scope: Construct, cid: str, data: Data) -> None:
        super().__init__(scope, cid)
        vault = backup.BackupVault(
            self,
            "Vault",
            encryption_key=data.key,
            # Always kept, retainData=false included: a vault that holds recovery points cannot be
            # deleted, and nothing in the stack deletes them, so DESTROY ended every destroy at
            # DELETE_FAILED here, after the data key the points need was scheduled for deletion (P3-011).
            # destroy.sh --purge --delete-backups removes the points, the vault and then the key.
            removal_policy=RemovalPolicy.RETAIN,
        )
        plan = backup.BackupPlan(self, "Plan", backup_vault=vault)
        plan.add_rule(
            backup.BackupPlanRule(
                rule_name="Daily",
                schedule_expression=events.Schedule.cron(hour="3", minute="0"),
                delete_after=Duration.days(RETENTION_DAYS),
            )
        )
        plan.add_selection(
            "Data",
            resources=[
                backup.BackupResource.from_arn(data.graph_table.table_arn),
                backup.BackupResource.from_arn(data.ops_table.table_arn),
                backup.BackupResource.from_arn(data.assets.bucket_arn),
            ],
            allow_restores=True,
        )
        NagSuppressions.add_resource_suppressions(
            self,
            [
                NagPackSuppression(
                    id="AwsSolutions-IAM4",
                    reason="AWS Backup's own service-role policies for backing up and restoring the selected "
                    "tables and bucket; AWS maintains them as the services add backup actions.",
                    applies_to=[
                        f"Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/{name}"
                        for name in BACKUP_POLICIES
                    ],
                )
            ],
            apply_to_children=True,
        )
