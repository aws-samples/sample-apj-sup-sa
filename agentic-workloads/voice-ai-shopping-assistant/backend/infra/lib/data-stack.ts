import * as cdk from 'aws-cdk-lib/core';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Duration } from 'aws-cdk-lib/core';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as assets from 'aws-cdk-lib/aws-s3-assets';
import * as path from 'path';
import { Construct } from 'constructs';

/**
 * Database & Seed.
 *
 * Aurora Serverless v2 PostgreSQL with the Data API (HTTP SQL — no VPC client,
 * no connection pool, so tool Lambdas need no VPC). Auto-pauses to 0 ACU when
 * idle. Seeded on deploy with the synthetic Aisle catalogue (products + specials)
 * via a custom-resource Lambda using rds-data BatchExecuteStatement.
 *
 * Exports (SSM, consumed by the tool Lambdas in ToolsStack):
 *   /aisle/db/cluster_arn · /aisle/db/secret_arn · /aisle/db/name
 */
export class DataStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Minimal VPC. Data API reaches the cluster over the AWS backbone, so the
    // cluster sits in isolated subnets with no NAT/IGW cost.
    const vpc = new ec2.Vpc(this, 'Vpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: 'isolated', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });

    const cluster = new rds.DatabaseCluster(this, 'Cluster', {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_6,
      }),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      serverlessV2MinCapacity: 1, // always-on floor — no cold start + headroom for vector/concurrent queries
      serverlessV2MaxCapacity: 4,
      enableDataApi: true,
      defaultDatabaseName: 'aisle',
      // Demo: single writer, destroy with the stack.
      writer: rds.ClusterInstance.serverlessV2('writer'),
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // MinCapacity >0 disables auto-pause, so the first query after idle doesn't
    // hit DatabaseResumingException — no cold start for the voice demo. Floor of
    // 1 ACU also gives headroom for pgvector/HNSW semantic queries + concurrent
    // tool calls. Costs ~1 ACU always-on (~$86/mo); set minCapacity back to 0
    // (and re-add secondsUntilAutoPause: 300) to restore scale-to-zero off-demo.
    (cluster.node.defaultChild as rds.CfnDBCluster).serverlessV2ScalingConfiguration = {
      minCapacity: 1,
      maxCapacity: 4,
    };

    const dbName = 'aisle';

    // ---- Seed loader (custom resource) ----
    // Bundles backend/db (schema.sql + seed_loader.py + seed/*.json). Re-runs
    // on every change to that asset because the asset hash feeds a CR property.
    const dbAssetPath = path.join(__dirname, '..', '..', 'db');
    const dbAsset = new assets.Asset(this, 'DbAsset', {
      path: dbAssetPath,
      exclude: ['*.pyc', '__pycache__', 'seed/generate_catalogue.py'],
    });
    const seedFn = new lambda.Function(this, 'SeedLoader', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'seed_loader.handler',
      code: lambda.Code.fromAsset(dbAssetPath, {
        exclude: ['*.pyc', '__pycache__', 'seed/generate_catalogue.py'],
      }),
      timeout: Duration.minutes(15),
      memorySize: 512,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        CLUSTER_ARN: cluster.clusterArn,
        SECRET_ARN: cluster.secret!.secretArn,
        DB_NAME: dbName,
      },
    });
    cluster.grantDataApiAccess(seedFn);
    // The loader embeds every product (Cohere Embed v3) at seed time for the
    // pgvector semantic-search column.
    seedFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [`arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`],
    }));

    const provider = new cr.Provider(this, 'SeedProvider', {
      onEventHandler: seedFn,
      logRetention: logs.RetentionDays.ONE_WEEK,
    });

    const seed = new cdk.CustomResource(this, 'SeedData', {
      serviceToken: provider.serviceToken,
      properties: {
        // Re-run the seed whenever schema.sql or the seed JSON changes.
        assetHash: dbAsset.assetHash,
      },
    });
    seed.node.addDependency(cluster);

    // ---- SSM exports (cross-stack handoff) ----
    new ssm.StringParameter(this, 'ClusterArnParam', {
      parameterName: '/aisle/db/cluster_arn',
      stringValue: cluster.clusterArn,
    });
    new ssm.StringParameter(this, 'SecretArnParam', {
      parameterName: '/aisle/db/secret_arn',
      stringValue: cluster.secret!.secretArn,
    });
    new ssm.StringParameter(this, 'DbNameParam', {
      parameterName: '/aisle/db/name',
      stringValue: dbName,
    });

    new cdk.CfnOutput(this, 'ClusterArn', { value: cluster.clusterArn });
    new cdk.CfnOutput(this, 'SecretArn', { value: cluster.secret!.secretArn });
    new cdk.CfnOutput(this, 'DbName', { value: dbName });
  }
}
