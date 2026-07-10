"""FoundationStack — VPC, Aurora, KMS, and Secrets Manager."""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Stack
from constructs import Construct

from infra.config import InfraContext
from infra.cdk_constructs.data import DataConstruct
from infra.cdk_constructs.network import NetworkConstruct


class FoundationStack(Stack):
    """Provision foundational networking and data resources."""

    def __init__(self, scope: Construct, construct_id: str, *, context: InfraContext, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.network = NetworkConstruct(self, "Network", context=context)
        self.data = DataConstruct(self, "Data", context=context, network=self.network)

        # --- Outputs ---
        CfnOutput(self, "VpcId", value=self.network.vpc.vpc_id)
        CfnOutput(
            self,
            "PrivateAppSubnetIds",
            value=",".join(s.subnet_id for s in self.network.private_app_subnets),
        )
        CfnOutput(
            self,
            "PrivateDataSubnetIds",
            value=",".join(s.subnet_id for s in self.network.private_data_subnets),
        )
        CfnOutput(self, "AlbSecurityGroupId", value=self.network.alb_sg.security_group_id)
        CfnOutput(self, "DbSecretArn", value=self.data.db_secret.secret_arn)
        CfnOutput(
            self,
            "AnthropicApiKeySecretArn",
            value=self.data.anthropic_api_key_secret.secret_arn,
        )
        CfnOutput(self, "AuroraClusterArn", value=self.data.aurora_cluster.cluster_arn)
        CfnOutput(self, "AuroraClusterEndpoint", value=self.data.db_endpoint)
        CfnOutput(self, "AuroraClusterReadEndpoint", value=self.data.db_read_endpoint)
        CfnOutput(self, "AuroraSecurityGroupId", value=self.data.aurora_security_group.security_group_id)
        CfnOutput(self, "VirtualKeyKmsKeyArn", value=self.data.virtual_key_kms_key.key_arn)
