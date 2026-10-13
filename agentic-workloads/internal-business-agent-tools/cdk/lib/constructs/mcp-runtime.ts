import { Construct } from 'constructs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';

export interface McpRuntimeProps {
  cluster: rds.DatabaseCluster;
  bucket: s3.IBucket;
  ssmPrefix: string;
}

/**
 * Writes runtime configuration to SSM Parameter Store so that the
 * MCP Runtime container can discover its data sources at startup.
 * IAM permissions are managed in agentcore-stack.ts (scoped to
 * specific resources via cross-stack references).
 */
export class McpRuntime extends Construct {
  constructor(scope: Construct, id: string, props: McpRuntimeProps) {
    super(scope, id);

    const { cluster, bucket, ssmPrefix } = props;

    new ssm.StringParameter(this, 'ClusterArn', {
      parameterName: `${ssmPrefix}CLUSTER_ARN`,
      stringValue: cluster.clusterArn,
    });

    new ssm.StringParameter(this, 'SecretArn', {
      parameterName: `${ssmPrefix}SECRET_ARN`,
      stringValue: cluster.secret!.secretArn,
    });

    new ssm.StringParameter(this, 'Database', {
      parameterName: `${ssmPrefix}DATABASE`,
      stringValue: 'internal_db',
    });

    new ssm.StringParameter(this, 'S3Bucket', {
      parameterName: `${ssmPrefix}S3_BUCKET`,
      stringValue: bucket.bucketName,
    });
  }
}
