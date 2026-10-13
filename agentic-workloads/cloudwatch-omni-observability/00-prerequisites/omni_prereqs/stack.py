"""Step 0: everything the samples need before they can send telemetry to Omni."""

import os
from pathlib import Path

from aws_cdk import (
    Annotations,
    CfnOutput,
    CfnResource,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_xray as xray
from aws_cdk import custom_resources as cr
from constructs import Construct

from .bundling import handler_code

HANDLER_DIR = Path(__file__).resolve().parent.parent / "lambda" / "space_handler"
OMNI_REGIONS = {"us-east-1", "us-west-2", "eu-west-1"}


class OmniPrereqsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        ctx = self._context()
        Tags.of(self).add("project", "omni-samples")

        # 1. Space access role, which Omni assumes to operate the space. create-space
        #    never verifies it can be assumed, so the trust policy has to be exact: all
        #    three sts actions plus both confused-deputy conditions, with Region and space
        #    ID wildcarded because the space doesn't exist yet when the role is created.
        space_role = iam.Role(
            self, "SpaceAccessRole",
            description="Assumed by CloudWatch Omni to operate the omni-samples space",
            assumed_by=iam.ServicePrincipal("cloudwatch.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchOmniSpaceAccessPolicy")],
        )
        space_role.node.default_child.add_property_override("AssumeRolePolicyDocument", {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "cloudwatch.amazonaws.com"},
                "Action": ["sts:AssumeRole", "sts:TagSession", "sts:SetContext"],
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {"aws:SourceArn": f"arn:{self.partition}:cloudwatch:*:{self.account}:space/*"},
                },
            }],
        })
        if ctx["attachModelInferencePolicy"]:
            # Prompt playground and evaluations that invoke a judge model.
            space_role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchOmniModelInferencePolicy"))
        if ctx["attachAwsIntegrationPolicy"]:
            # Context graph resource discovery. It reads resource metadata account-wide.
            space_role.add_managed_policy(
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchOmniAWSIntegrationPolicy"))

        # 2. Dataset integration, which forwards CloudWatch logs and traces into the
        #    space's Dataset. One per account per Region. The console creates it
        #    alongside the space; with the API it's a separate resource. Because the
        #    quota is one per account per Region, creating a second one fails: when the
        #    stack adopts a space that already has an integration (anything created in
        #    the console), createDatasetIntegration defaults to false.
        dataset_role = None
        dataset_integration = None
        if ctx["createDatasetIntegration"]:
            dataset_role, dataset_integration = self._dataset_integration()
            # CDK's bundled spec doesn't know this registry type yet, so synth warns
            # "Unknown resource type". The warning is expected and harmless.
        else:
            # Skipping it silently would deploy green with no forwarding at all, and the
            # only symptom is empty Dataset queries later.
            Annotations.of(self).add_warning(
                "Not creating a dataset integration (createDatasetIntegration=false). The space will see "
                "no logs or traces unless this account and Region already has one. Check with: "
                "aws observabilityadmin list-dataset-integrations. If there is none, redeploy with "
                "-c createDatasetIntegration=true.")

        # 3. Transaction Search: spans land in aws/spans. Account-level, so it's off by
        #    default; turn it on only if preflight shows it disabled.
        if ctx["enableTransactionSearch"]:
            spans_policy = logs.CfnResourcePolicy(
                self, "XRayToLogsPolicy",
                policy_name="omni-samples-transaction-search",
                policy_document=self.to_json_string({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Sid": "TransactionSearchXRayAccess",
                        "Effect": "Allow",
                        "Principal": {"Service": "xray.amazonaws.com"},
                        "Action": "logs:PutLogEvents",
                        "Resource": [
                            f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:aws/spans:*",
                            f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:/aws/application-signals/data:*",
                        ],
                        "Condition": {
                            "ArnLike": {"aws:SourceArn": f"arn:{self.partition}:xray:{self.region}:{self.account}:*"},
                            "StringEquals": {"aws:SourceAccount": self.account},
                        },
                    }],
                }),
            )
            ts = xray.CfnTransactionSearchConfig(self, "TransactionSearch", indexing_percentage=1)
            ts.add_dependency(spans_policy)
            # Account-wide settings other workloads may rely on: never turn them off on stack
            # delete or rollback. (Deleting while activation is PENDING also fails.) After
            # the first deploy, redeploy without enableTransactionSearch.
            for resource in (spans_policy, ts):
                resource.apply_removal_policy(RemovalPolicy.RETAIN)

        # 4. The space and an admin grant for you. Because the custom resource creates
        #    the space, the service-managed SPACE_ADMIN grant goes to the Lambda role,
        #    not to you. The handler grants the Identity Center groups from enable_sso.py
        #    (omni-space-admins: SPACE_ADMIN, omni-viewers: READ), plus an optional IAM
        #    principal for break-glass or automation.
        on_event = lambda_.Function(
            self, "SpaceHandler",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.on_event",
            code=handler_code(HANDLER_DIR),
            timeout=Duration.minutes(10),
            description="Creates the CloudWatch Omni space (no CloudFormation type yet)",
            log_group=logs.LogGroup(self, "SpaceHandlerLogs", retention=logs.RetentionDays.ONE_WEEK,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        on_event.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "cloudwatch:ListDomains", "cloudwatch:GetDomain", "cloudwatch:GetDomainForOrganization",
                "cloudwatch:ListSpaces", "cloudwatch:GetSpace", "cloudwatch:CreateSpace",
                "cloudwatch:UpdateSpace", "cloudwatch:DeleteSpace", "cloudwatch:TagResource",
                "cloudwatch:ListAccessGrants", "cloudwatch:CreateAccessGrant", "cloudwatch:DeleteAccessGrant",
            ],
            resources=["*"],  # space and grant ARNs don't exist until creation
        ))
        on_event.add_to_role_policy(iam.PolicyStatement(
            actions=["cloudformation:DescribeStacks"], resources=[self.stack_id],
        ))
        # With an Identity Center domain, CreateSpace checks (as the caller) that Identity Center
        # is available in the space's Region (sso:ListRegions), and IDC group grants are resolved
        # against the identity store. Read-only, account-wide by nature.
        on_event.add_to_role_policy(iam.PolicyStatement(
            actions=["sso:ListRegions", "sso:ListInstances", "sso:DescribeInstance",
                     "identitystore:DescribeGroup", "identitystore:DescribeUser"],
            resources=["*"],
        ))
        passable = [space_role.role_arn] + ([ctx["agentCoreEvaluationRoleArn"]] if ctx["agentCoreEvaluationRoleArn"] else [])
        on_event.add_to_role_policy(iam.PolicyStatement(actions=["iam:PassRole"], resources=passable))

        provider = cr.Provider(
            self, "SpaceProvider", on_event_handler=on_event,
            # The Provider's own framework Lambda logs too. Without a managed group it
            # creates one with no retention that cdk destroy leaves behind.
            log_group=logs.LogGroup(self, "SpaceProviderLogs", retention=logs.RetentionDays.ONE_WEEK,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        space = CustomResource(
            self, "Space",
            service_token=provider.service_token,
            resource_type="Custom::OmniSpace",
            properties={
                "DomainId": ctx["domainId"],
                "SpaceName": ctx["spaceName"],
                "DataAccessRoleArn": space_role.role_arn,
                "AgentCoreEvaluationRoleArn": ctx["agentCoreEvaluationRoleArn"],
                "AdminPrincipalArn": ctx["adminPrincipalArn"],
                "AdminGroupId": ctx["adminGroupId"],
                "ViewerGroupId": ctx["viewerGroupId"],
                "AdoptExistingSpace": str(ctx["adoptExistingSpace"]).lower(),
                "RetainOnDelete": str(ctx["retainSpaceOnDelete"]).lower(),
            },
        )
        # Forwarding should exist before the space starts reading the Dataset.
        if dataset_integration:
            space.node.add_dependency(dataset_integration)
        if ctx["retainSpaceOnDelete"]:
            # A retained space still needs its role and forwarding, so keep them on stack
            # delete. RETAIN_ON_UPDATE_OR_DELETE (not RETAIN) still cleans them up when a
            # first create fails and rolls back.
            retained = [space_role] + ([dataset_role, dataset_integration] if dataset_integration else [])
            for resource in retained:
                resource.apply_removal_policy(RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE)

        # 5. Shared resources the samples use.
        shop_logs = logs.LogGroup(
            self, "ShopLogGroup",
            log_group_name=ctx["shopLogGroupName"],
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        logs.LogStream(self, "ShopLogStream", log_group=shop_logs, log_stream_name="default",
                       removal_policy=RemovalPolicy.DESTROY)

        topic = sns.Topic(self, "AlertsTopic", topic_name="omni-samples-alerts", enforce_ssl=True)
        # Omni alerts publish as cloudwatch.amazonaws.com. Nothing checks this at alert
        # creation, so without it notifications silently never arrive.
        topic.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
            actions=["sns:Publish"],
            resources=[topic.topic_arn],
            conditions={"StringEquals": {"aws:SourceAccount": self.account}},
        ))
        if ctx["alertEmail"]:
            topic.add_subscription(subs.EmailSubscription(ctx["alertEmail"]))

        CfnOutput(self, "SpaceId", value=space.get_att_string("SpaceId"))
        CfnOutput(self, "SpaceArn", value=space.get_att_string("SpaceArn"))
        CfnOutput(self, "DomainEndpointUrl", value=space.get_att_string("DomainEndpointUrl"),
                  description="Sign-in URL for the Omni web UI")
        CfnOutput(self, "SpaceAccessRoleArn", value=space_role.role_arn)
        CfnOutput(self, "AlertsTopicArn", value=topic.topic_arn)
        CfnOutput(self, "ShopLogGroupName", value=shop_logs.log_group_name)

    def _dataset_integration(self):
        role = iam.Role(
            self, "DatasetIntegrationRole",
            description="Assumed by CloudWatch Logs to forward logs and traces into the Omni Dataset",
            assumed_by=iam.ServicePrincipal("logs.amazonaws.com").with_conditions({
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {"aws:SourceArn":
                            f"arn:{self.partition}:observabilityadmin:{self.region}:{self.account}:dataset-integration/default"},
            }),
            inline_policies={"forward": iam.PolicyDocument(statements=[
                # log-group:* forwards every log group except Delivery-class ones.
                iam.PolicyStatement(actions=["logs:IntegrateWithDataset"],
                                    resources=[f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:*"]),
                iam.PolicyStatement(actions=["cloudwatch:PutRecords"], resources=["*"]),
            ])},
        )
        integration = CfnResource(
            self, "DatasetIntegration",
            type="AWS::ObservabilityAdmin::DatasetIntegration",
            properties={"RoleArn": role.role_arn},
        )
        return role, integration

    def _context(self) -> dict:
        def flag(name, default):
            value = self.node.try_get_context(name)
            if value is None or value == "":
                return default
            return value if isinstance(value, bool) else str(value).lower() == "true"

        def text(name, default="", env=None):
            # -c on the command line (or cdk.json) wins, then ../.env, then the default.
            value = self.node.try_get_context(name)
            if value in (None, "") and env:
                value = os.environ.get(env)
            return default if value in (None, "") else str(value)

        ctx = {
            "domainId": text("domainId", env="OMNI_DOMAIN_ID"),
            "spaceName": text("spaceName", "omni-samples"),
            "adminPrincipalArn": text("adminPrincipalArn", env="OMNI_ADMIN_PRINCIPAL_ARN"),
            "adminGroupId": text("adminGroupId", env="OMNI_ADMIN_GROUP_ID"),
            "viewerGroupId": text("viewerGroupId", env="OMNI_VIEWER_GROUP_ID"),
            "agentCoreEvaluationRoleArn": text("agentCoreEvaluationRoleArn"),
            "shopLogGroupName": text("shopLogGroupName", "/omni-samples/shop", env="SHOP_LOG_GROUP"),
            "alertEmail": text("alertEmail"),
            "enableTransactionSearch": flag("enableTransactionSearch", False),
            "attachModelInferencePolicy": flag("attachModelInferencePolicy", True),
            "attachAwsIntegrationPolicy": flag("attachAwsIntegrationPolicy", True),
            "adoptExistingSpace": flag("adoptExistingSpace", False),
            "retainSpaceOnDelete": flag("retainSpaceOnDelete", True),
        }
        # One dataset integration per account per Region. A space you adopt already has one
        # (the console creates it with the space), so adopting skips it unless you say otherwise.
        ctx["createDatasetIntegration"] = flag("createDatasetIntegration", not ctx["adoptExistingSpace"])
        if not ctx["domainId"]:
            raise ValueError("No Omni domain set. Add OMNI_DOMAIN_ID=<name-or-id> to ../.env, or pass "
                             "-c domainId=<name-or-id>. Find it with: aws cloudwatchomni list-domains")
        if not self.region.startswith("${") and self.region not in OMNI_REGIONS:
            raise ValueError(f"Omni is available in {sorted(OMNI_REGIONS)}; this stack targets {self.region}")
        return ctx
