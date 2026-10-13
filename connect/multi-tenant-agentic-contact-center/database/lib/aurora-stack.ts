import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as rds from "aws-cdk-lib/aws-rds";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs";
import * as triggers from "aws-cdk-lib/triggers";

/**
 * A self-contained Aurora PostgreSQL module.
 *
 * - Latest Aurora PostgreSQL (18.4), one writer instance on db.t4g.large.
 * - Runs in a dedicated VPC using ONLY private, ISOLATED subnets (no Internet
 *   Gateway, no NAT) — the database is not reachable from the public internet.
 * - Credentials are generated into Secrets Manager (no password in the template).
 * - A `transactions` table (transaction_id PRIMARY KEY) is created and seeded
 *   with 200 multi-tenant records by a Lambda that runs INSIDE the VPC on deploy,
 *   so seeding never requires exposing the database. The Lambda reaches Secrets
 *   Manager through an interface VPC endpoint (still fully private).
 */
export class AuroraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const DB_NAME = "anycompanypay";
    const SEED_COUNT = 200;

    // ------------------------------------------------------------------
    // Dedicated VPC — isolated private subnets only (no IGW / no NAT).
    // ------------------------------------------------------------------
    const vpc = new ec2.Vpc(this, "DbVpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: "db", subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
    });

    // Interface endpoint so the in-VPC seed Lambda can read the DB secret from
    // Secrets Manager without any route to the internet.
    vpc.addInterfaceEndpoint("SecretsManagerEndpoint", {
      service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
    });

    // ------------------------------------------------------------------
    // Aurora PostgreSQL cluster (private, encrypted).
    // ------------------------------------------------------------------
    const engine = rds.DatabaseClusterEngine.auroraPostgres({
      // Latest Aurora PostgreSQL available in ap-southeast-1 at build time.
      version: rds.AuroraPostgresEngineVersion.of("18.4", "18"),
    });

    // Cluster parameter group enabling LOGICAL REPLICATION — required for the
    // OpenSearch zero-ETL / OpenSearch Ingestion CDC source (see the
    // opensearch-zeroetl module). Static params: the cluster needs a reboot
    // after this is first associated for them to take effect.
    const clusterParams = new rds.ParameterGroup(this, "ClusterParams", {
      engine,
      description: "AnyCompanyPay Aurora PG logical replication for OpenSearch zero-ETL",
      parameters: {
        "rds.logical_replication": "1",
        "aurora.enhanced_logical_replication": "1",
        "aurora.logical_replication_backup": "0",
        "aurora.logical_replication_globaldb": "0",
      },
    });

    const cluster = new rds.DatabaseCluster(this, "Aurora", {
      engine,
      parameterGroup: clusterParams,
      writer: rds.ClusterInstance.provisioned("writer", {
        instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.LARGE),
        publiclyAccessible: false,
      }),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      credentials: rds.Credentials.fromGeneratedSecret("anycompanypay_admin"),
      defaultDatabaseName: DB_NAME,
      storageEncrypted: true,
      // Demo settings — flip these for anything real.
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ------------------------------------------------------------------
    // Seed Lambda — runs inside the VPC, connects to Aurora, creates the
    // transactions table and inserts 200 multi-tenant rows (idempotent).
    // ------------------------------------------------------------------
    const seedFn = new lambdaNode.NodejsFunction(this, "SeedFn", {
      entry: path.join(__dirname, "..", "lambda", "seed", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      // Install `pg` into the bundle (avoids esbuild issues with its dynamic
      // requires); @aws-sdk/* is provided by the Node 22 runtime.
      bundling: { nodeModules: ["pg"], externalModules: ["@aws-sdk/*"] },
      environment: {
        DB_HOST: cluster.clusterEndpoint.hostname,
        DB_PORT: cdk.Token.asString(cluster.clusterEndpoint.port),
        DB_NAME,
        DB_SECRET_ARN: cluster.secret!.secretArn,
        SEED_COUNT: String(SEED_COUNT),
      },
    });
    cluster.secret!.grantRead(seedFn);
    // Allow the Lambda's security group to reach the cluster on 5432.
    cluster.connections.allowDefaultPortFrom(seedFn, "seed lambda");

    // Invoke the seed Lambda on deploy, after the cluster is available.
    new triggers.Trigger(this, "SeedTrigger", {
      handler: seedFn,
      executeAfter: [cluster],
      executeOnHandlerChange: true,
    });

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, "ClusterEndpoint", { value: cluster.clusterEndpoint.hostname });
    new cdk.CfnOutput(this, "ReaderEndpoint", {
      value: cluster.clusterReadEndpoint.hostname,
    });
    new cdk.CfnOutput(this, "Port", { value: cdk.Token.asString(cluster.clusterEndpoint.port) });
    new cdk.CfnOutput(this, "DatabaseName", { value: DB_NAME });
    new cdk.CfnOutput(this, "SecretArn", { value: cluster.secret!.secretArn });
    new cdk.CfnOutput(this, "SecretName", { value: cluster.secret!.secretName });
    new cdk.CfnOutput(this, "VpcId", { value: vpc.vpcId });
    new cdk.CfnOutput(this, "ClusterIdentifier", { value: cluster.clusterIdentifier });
    new cdk.CfnOutput(this, "DbSecurityGroupId", {
      value: cluster.connections.securityGroups[0].securityGroupId,
    });
  }
}
