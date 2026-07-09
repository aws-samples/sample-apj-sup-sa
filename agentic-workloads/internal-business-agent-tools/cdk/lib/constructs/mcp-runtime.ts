import { Construct } from 'constructs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { CfnOutput } from 'aws-cdk-lib';

export interface McpRuntimeProps {
  cluster: rds.DatabaseCluster;
  bucket: s3.IBucket;
  ssmPrefix: string;
}

/**
 * Creates SSM parameters and IAM policies needed by the MCP Runtime.
 * The actual Runtime resource is deployed via AgentCore CDK L3 construct
 * (managed by agentcore.json + cdk-stack.ts in agentcore/cdk/).
 *
 * This construct bridges the gap: it writes config to SSM and outputs
 * the IAM policy ARN that the Runtime execution role needs.
 */
export class McpRuntime extends Construct {
  public readonly runtimePolicy: iam.ManagedPolicy;

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

    this.runtimePolicy = new iam.ManagedPolicy(this, 'RuntimePolicy', {
      statements: [
        new iam.PolicyStatement({
          actions: ['ssm:GetParameters', 'ssm:GetParameter'],
          resources: [`arn:aws:ssm:*:*:parameter${ssmPrefix}*`],
        }),
        new iam.PolicyStatement({
          actions: ['rds-data:ExecuteStatement', 'rds-data:BatchExecuteStatement'],
          resources: [cluster.clusterArn],
        }),
        new iam.PolicyStatement({
          actions: ['secretsmanager:GetSecretValue'],
          resources: [cluster.secret!.secretArn],
        }),
        new iam.PolicyStatement({
          actions: ['s3:GetObject', 's3:ListBucket'],
          resources: [bucket.bucketArn, `${bucket.bucketArn}/*`],
        }),
      ],
    });

    new CfnOutput(this, 'RuntimePolicyArn', {
      value: this.runtimePolicy.managedPolicyArn,
      description: 'Attach this policy to the AgentCore Runtime execution role',
    });

    new CfnOutput(this, 'SsmPrefix', { value: ssmPrefix });
  }
}
