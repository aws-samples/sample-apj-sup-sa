import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as kms from "aws-cdk-lib/aws-kms";
import * as iam from "aws-cdk-lib/aws-iam";
import * as aoss from "aws-cdk-lib/aws-opensearchserverless";
import * as osis from "aws-cdk-lib/aws-osis";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwInteg from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as apigwAuth from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import * as cr from "aws-cdk-lib/custom-resources";
import * as logs from "aws-cdk-lib/aws-logs";

/**
 * Zero-ETL replication: Amazon Aurora PostgreSQL -> Amazon OpenSearch Serverless.
 *
 * AWS delivers Aurora -> OpenSearch "zero-ETL" through an Amazon OpenSearch
 * Ingestion (OSIS) pipeline using the `rds` source (initial snapshot to S3 +
 * change-data-capture via logical replication). This stack provisions:
 *   - a PRIVATE OpenSearch Serverless collection (VPC-only network policy, no
 *     public access) reachable via an OpenSearch Serverless VPC endpoint,
 *   - the S3 export bucket + KMS key + IAM roles the pipeline needs,
 *   - the OSIS pipeline (MinUnits=1, MaxUnits=2) attached to the Aurora VPC.
 *
 * It targets the already-deployed Aurora module (AnyCompanyPayAuroraStack). The Aurora
 * cluster must have logical replication enabled (done in the database module)
 * and be PostgreSQL >= 16.4.
 */
export class ZeroEtlStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const region = this.region;

    // --- Inputs ---
    // No environment-specific values are hardcoded. Every resource identifier
    // (Aurora VPC/subnets/SG/cluster/secret/host, Cognito pool+client, the SPA
    // CloudFront URL, the ECS cluster/service, the Cases domain) is REQUIRED and
    // supplied by this module's deploy.sh, which DISCOVERS them from the upstream
    // stack outputs (AnyCompanyPayAuroraStack / AnyCompanyPayAppStack-<env> /
    // AnyCompanyPayConnectStack-<env>). Running `cdk deploy` bare (no deploy.sh, no -c)
    // fails fast with a clear message instead of silently wiring this stack to a
    // different environment's / region's resources.
    // Optional env discriminator. Empty → no suffix (clean common names).
    const envName = ((this.node.tryGetContext("envName") as string) ?? "").trim();
    const sfx = envName ? `-${envName}` : ""; // name suffix: "-foo" or ""
    const seg = envName ? `/${envName}` : ""; // path segment: "/foo" or ""
    const ctx = (k: string, d: string) => (this.node.tryGetContext(k) as string) ?? d;
    const reqCtx = (k: string, hint: string): string => {
      const v = this.node.tryGetContext(k) as string | undefined;
      if (!v) {
        throw new Error(
          `Missing required context "-c ${k}=...": ${hint} ` +
            `Run this module's deploy.sh (it discovers every required value from the ` +
            `upstream stack outputs), or pass -c ${k}=... explicitly.`
        );
      }
      return v;
    };

    const dbVpcId = reqCtx("dbVpcId", "Aurora VPC id (AnyCompanyPayAuroraStack output VpcId).");
    const dbSubnetIds = reqCtx(
      "dbSubnetIds",
      "comma-separated Aurora isolated subnet ids."
    ).split(",");
    const dbSgId = reqCtx("dbSgId", "Aurora DB security group id (AnyCompanyPayAuroraStack output DbSecurityGroupId).");
    const dbClusterId = reqCtx("dbClusterId", "Aurora cluster identifier (AnyCompanyPayAuroraStack output ClusterIdentifier).");
    const dbSecretArn = reqCtx("dbSecretArn", "Aurora credentials secret ARN (AnyCompanyPayAuroraStack output SecretArn).");
    const dbName = ctx("dbName", "anycompanypay");
    const dbHost = reqCtx("dbHost", "Aurora writer endpoint host (AnyCompanyPayAuroraStack output ClusterEndpoint).");
    const dbPort = ctx("dbPort", "5432");
    // Amazon Connect Cases domain that dispute-opened cases are filed into. The
    // Commerce API resolves field/template IDs at runtime (ListFields/ListTemplates)
    // so nothing breaks if the connect stack is recreated with new generated IDs.
    const casesDomainId = reqCtx("casesDomainId", "Amazon Connect Cases domain id (AnyCompanyPayConnectStack-<env> output CasesDomainId).");
    const casesTemplateName = ctx("casesTemplateName", "AnyCompanyPaySupport");
    const dbVpcCidr = reqCtx("dbVpcCidr", "Aurora VPC CIDR block (from ec2 describe-vpcs).");
    // /24 reserved for the OSIS ENIs — must NOT overlap the Aurora VPC CIDR.
    const osiCidr = ctx("osiCidr", "172.16.0.0/24");

    // For the merchant transaction Search API (GET-only, Cognito JWT authorized)
    // and for merging searchApiUrl into the SPA's SSM runtime config.
    const cognitoPoolId = reqCtx("cognitoPoolId", "Cognito user pool id (AnyCompanyPayAppStack-<env> output UserPoolId).");
    const cognitoClientId = reqCtx("cognitoClientId", "Cognito app client id (AnyCompanyPayAppStack-<env> output UserPoolClientId).");
    const distUrl = reqCtx("distUrl", "SPA CloudFront URL (AnyCompanyPayAppStack-<env> output CloudFrontUrl).");
    const runtimeConfigParamName = ctx("runtimeConfigParamName", `/anycompany-pay${seg}/runtime-config`);
    const ecsCluster = reqCtx("ecsCluster", "App ECS cluster name (AnyCompanyPayAppStack-<env> output ClusterName).");
    const ecsService = reqCtx("ecsService", "App ECS service name (AnyCompanyPayAppStack-<env> output ServiceName).");

    const collectionName = ctx("collectionName", "anycompany-pay-tx"); // 3-32 chars, lowercase
    // Bumping the pipeline name forces a brand-new OSIS pipeline with FRESH source
    // coordination state. Needed because a pipeline that once recorded a
    // "completed" export (even one that enumerated 0 data files) will neither
    // re-export nor complete its initial load — it deadlocks. A new pipeline runs
    // the full initial load from scratch (with the corrected slash-free s3_prefix).
    // Derived from envName so a new environment gets its own pipeline automatically.
    // OSIS caps PipelineName at 28 characters, so the base is kept short enough to
    // leave room for the env suffix; the guard below fails fast with a clear message
    // instead of surfacing as an opaque CloudFormation validation error.
    const pipelineName = ctx("pipelineName", `anycompany-pay-zeroetl${sfx}`);
    if (pipelineName.length > 28) {
      throw new Error(
        `pipelineName "${pipelineName}" is ${pipelineName.length} chars; OSIS allows at most 28. ` +
          `Use a shorter envName, or pass -c pipelineName=<name> explicitly.`
      );
    }
    const indexName = ctx("indexName", "transactions");
    const tableInclude = "public.transactions";

    const vpc = ec2.Vpc.fromVpcAttributes(this, "DbVpc", {
      vpcId: dbVpcId,
      availabilityZones: [`${region}a`, `${region}b`],
      isolatedSubnetIds: dbSubnetIds,
    });

    // ------------------------------------------------------------------
    // Security groups: OSIS pipeline ENIs + the OpenSearch Serverless VPC endpoint.
    // ------------------------------------------------------------------
    const pipelineSg = new ec2.SecurityGroup(this, "PipelineSg", {
      vpc,
      description: "OSIS zero-ETL pipeline ENIs",
      allowAllOutbound: true,
    });
    const aossSg = new ec2.SecurityGroup(this, "AossEndpointSg", {
      vpc,
      description: "OpenSearch Serverless VPC endpoint",
      allowAllOutbound: true,
    });
    // The collection endpoint accepts 443 from the pipeline (and anything else
    // private in the VPC / the OSIS ENI CIDR).
    for (const peer of [
      ec2.Peer.securityGroupId(pipelineSg.securityGroupId),
      ec2.Peer.ipv4(dbVpcCidr),
      ec2.Peer.ipv4(osiCidr),
    ]) {
      aossSg.addIngressRule(peer, ec2.Port.tcp(443), "aoss endpoint");
    }
    // Let the pipeline reach the private Aurora cluster on 5432.
    const dbSg = ec2.SecurityGroup.fromSecurityGroupId(this, "DbSg", dbSgId, { mutable: true });
    dbSg.addIngressRule(ec2.Peer.securityGroupId(pipelineSg.securityGroupId), ec2.Port.tcp(5432), "OSIS pipeline");
    dbSg.addIngressRule(ec2.Peer.ipv4(osiCidr), ec2.Port.tcp(5432), "OSIS ENI CIDR");

    // ------------------------------------------------------------------
    // S3 export bucket + KMS key for the initial snapshot export.
    // ------------------------------------------------------------------
    const exportKey = new kms.Key(this, "ExportKey", {
      description: "AnyCompanyPay zero-ETL Aurora-to-S3 export",
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const exportBucket = new s3.Bucket(this, "ExportBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Role assumed by RDS to export the snapshot to S3.
    const exportRole = new iam.Role(this, "ExportRole", {
      assumedBy: new iam.ServicePrincipal("export.rds.amazonaws.com"),
      description: "Aurora snapshot export to S3 for zero-ETL",
    });
    exportBucket.grantReadWrite(exportRole);
    // RDS snapshot-export to S3 needs the FULL KMS action set on the export CMK,
    // including kms:CreateGrant (grantEncryptDecrypt alone is not enough — without
    // CreateGrant, StartExportTask fails and the pipeline loops re-snapshotting).
    exportRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*",
          "kms:DescribeKey",
          "kms:CreateGrant",
          "kms:RetireGrant",
        ],
        resources: [exportKey.keyArn],
      })
    );

    // ------------------------------------------------------------------
    // OpenSearch Serverless collection (PRIVATE) + policies + VPC endpoint.
    // ------------------------------------------------------------------
    const vpce = new aoss.CfnVpcEndpoint(this, "CollectionVpce", {
      name: `${collectionName}-vpce`,
      vpcId: dbVpcId,
      subnetIds: dbSubnetIds,
      securityGroupIds: [aossSg.securityGroupId],
    });

    const encPolicy = new aoss.CfnSecurityPolicy(this, "EncPolicy", {
      name: `${collectionName}-enc`,
      type: "encryption",
      policy: JSON.stringify({
        Rules: [{ ResourceType: "collection", Resource: [`collection/${collectionName}`] }],
        AWSOwnedKey: true,
      }),
    });

    // VPC-only network policy: AllowFromPublic=false + the collection's VPC endpoint.
    const netPolicy = new aoss.CfnSecurityPolicy(this, "NetPolicy", {
      name: `${collectionName}-net`,
      type: "network",
      policy: JSON.stringify([
        {
          Rules: [
            { ResourceType: "collection", Resource: [`collection/${collectionName}`] },
            { ResourceType: "dashboard", Resource: [`collection/${collectionName}`] },
          ],
          AllowFromPublic: false,
          SourceVPCEs: [vpce.attrId],
        },
      ]),
    });

    const collection = new aoss.CfnCollection(this, "Collection", {
      name: collectionName,
      type: "SEARCH",
      description: "AnyCompanyPay transactions replicated from Aurora via zero-ETL",
    });
    collection.addDependency(encPolicy);
    collection.addDependency(netPolicy);

    // ------------------------------------------------------------------
    // OSIS pipeline role, and the collection data-access policy for it.
    // ------------------------------------------------------------------
    const pipelineRole = new iam.Role(this, "PipelineRole", {
      assumedBy: new iam.ServicePrincipal("osis-pipelines.amazonaws.com"),
      description: "OSIS zero-ETL pipeline role",
    });
    // Read the DB secret.
    pipelineRole.addToPolicy(
      new iam.PolicyStatement({ actions: ["secretsmanager:GetSecretValue"], resources: [dbSecretArn] })
    );
    // RDS snapshot export + describe.
    pipelineRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "rds:DescribeDBClusters",
          "rds:DescribeDBInstances",
          "rds:DescribeDBSubnetGroups",
          "rds:CreateDBClusterSnapshot",
          "rds:DescribeDBClusterSnapshots",
          "rds:StartExportTask",
          "rds:DescribeExportTasks",
          "rds:AddTagsToResource",
        ],
        resources: ["*"],
      })
    );
    // Pass the export role to RDS StartExportTask.
    pipelineRole.addToPolicy(
      new iam.PolicyStatement({ actions: ["iam:PassRole"], resources: [exportRole.roleArn] })
    );
    exportBucket.grantReadWrite(pipelineRole);
    // The pipeline role is the CALLER of rds:StartExportTask. RDS validates the
    // export KMS key against the caller's permissions, so the pipeline role needs
    // kms:DescribeKey + kms:CreateGrant on the export CMK (not just encrypt/decrypt).
    // Without DescribeKey, RDS reports the misleading "KMS key doesn't exist or is
    // disabled" and the export loops forever. Mirror the export role's full set.
    pipelineRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey*",
          "kms:ReEncrypt*",
          "kms:DescribeKey",
          "kms:CreateGrant",
          "kms:RetireGrant",
        ],
        resources: [exportKey.keyArn],
      })
    );
    // EC2 networking so OSIS can attach the pipeline to the VPC (manage its ENIs).
    pipelineRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "ec2:AttachNetworkInterface",
          "ec2:CreateNetworkInterface",
          "ec2:CreateNetworkInterfacePermission",
          "ec2:DeleteNetworkInterface",
          "ec2:DeleteNetworkInterfacePermission",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcs",
          "ec2:DescribeDhcpOptions",
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeRouteTables",
          "ec2:CreateTags",
        ],
        resources: ["*"],
      })
    );
    // Data-plane access to the serverless collection.
    pipelineRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["aoss:APIAccessAll", "aoss:BatchGetCollection"],
        resources: [collection.attrArn],
      })
    );
    // Control-plane access so OSIS's ServerlessNetworkPolicyUpdater can inject its
    // own PrivateLink VPC endpoint into the collection's network policy (required
    // for a PRIVATE/VPC-only collection sink). aoss security-policy actions are
    // account-scoped and do not support resource-level ARNs, so use "*".
    pipelineRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "aoss:GetSecurityPolicy",
          "aoss:UpdateSecurityPolicy",
          "aoss:CreateSecurityPolicy",
          "aoss:ListSecurityPolicies",
        ],
        resources: ["*"],
      })
    );

    const dataPolicy = new aoss.CfnAccessPolicy(this, "DataPolicy", {
      name: `${collectionName}-data`,
      type: "data",
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: "index",
              Resource: [`index/${collectionName}/*`],
              Permission: [
                "aoss:CreateIndex",
                "aoss:UpdateIndex",
                "aoss:DescribeIndex",
                "aoss:ReadDocument",
                "aoss:WriteDocument",
              ],
            },
            {
              ResourceType: "collection",
              Resource: [`collection/${collectionName}`],
              Permission: [
                "aoss:CreateCollectionItems",
                "aoss:UpdateCollectionItems",
                "aoss:DescribeCollectionItems",
              ],
            },
          ],
          Principal: [pipelineRole.roleArn],
        },
      ]),
    });

    // ------------------------------------------------------------------
    // OSIS pipeline (MinUnits=1, MaxUnits=2) — Aurora rds source -> serverless sink.
    // ------------------------------------------------------------------
    const collectionEndpoint = collection.attrCollectionEndpoint;
    const cfg = [
      'version: "2"',
      "anycompany-pay-tx-pipeline:",
      "  source:",
      "    rds:",
      `      db_identifier: "${dbClusterId}"`,
      "      engine: aurora-postgresql",
      `      database: "${dbName}"`,
      "      tables:",
      "        include:",
      `          - "${tableInclude}"`,
      `      s3_bucket: "${exportBucket.bucketName}"`,
      `      s3_region: "${region}"`,
      // No trailing slash: OSIS normalizes the export WRITE path correctly either
      // way, but its data-file ENUMERATION reconstructs the S3 LIST prefix as
      // "<s3_prefix>/<uniqueId>/..." — a trailing slash there yields a double
      // slash ("zeroetl//...") that matches no real object key, so enumeration
      // returns 0 data files even though the export succeeded. Keep it slash-free.
      '      s3_prefix: "zeroetl"',
      "      export:",
      `        kms_key_id: "${exportKey.keyId}"`,
      `        iam_role_arn: "${exportRole.roleArn}"`,
      "      stream: true",
      "      aws:",
      `        sts_role_arn: "${pipelineRole.roleArn}"`,
      `        region: "${region}"`,
      "      authentication:",
      "        username: ${{aws_secrets:db-secret:username}}",
      "        password: ${{aws_secrets:db-secret:password}}",
      "  sink:",
      "    - opensearch:",
      `        hosts: ["${collectionEndpoint}"]`,
      `        index: "${indexName}"`,
      "        index_type: custom",
      "        document_id: '${getMetadata(\"primary_key\")}'",
      "        action: '${getMetadata(\"opensearch_action\")}'",
      "        document_version: '${getMetadata(\"document_version\")}'",
      '        document_version_type: "external"',
      "        aws:",
      "          serverless: true",
      // For a PRIVATE (VPC-only) collection sink, OSIS must know the collection's
      // network policy name. It then auto-creates its own AWS PrivateLink endpoint
      // to the collection and injects a rule ("Created by Data Prepper") granting
      // that endpoint access -- all while AllowFromPublic stays false. Without this
      // the sink cannot reach the private collection and loops on HTTP 401.
      "          serverless_options:",
      `            network_policy_name: "${collectionName}-net"`,
      `          sts_role_arn: "${pipelineRole.roleArn}"`,
      `          region: "${region}"`,
      "extension:",
      "  aws:",
      "    secrets:",
      "      db-secret:",
      `        secret_id: "${dbSecretArn}"`,
      `        region: "${region}"`,
      `        sts_role_arn: "${pipelineRole.roleArn}"`,
      "        refresh_interval: PT1H",
    ].join("\n");

    const pipelineLogs = new logs.LogGroup(this, "PipelineLogs", {
      logGroupName: `/aws/vendedlogs/OpenSearchIngestion/${pipelineName}/audit-logs`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const pipeline = new osis.CfnPipeline(this, "Pipeline", {
      pipelineName: pipelineName,
      minUnits: 1,
      maxUnits: 2,
      pipelineConfigurationBody: cfg,
      logPublishingOptions: {
        isLoggingEnabled: true,
        cloudWatchLogDestination: { logGroup: pipelineLogs.logGroupName },
      },
      vpcOptions: {
        subnetIds: dbSubnetIds,
        securityGroupIds: [pipelineSg.securityGroupId],
        vpcAttachmentOptions: { attachToVpc: true, cidrBlock: osiCidr },
        vpcEndpointManagement: "SERVICE",
      },
    });
    pipeline.node.addDependency(collection, netPolicy, dataPolicy, vpce, pipelineRole, exportRole);

    // ==================================================================
    // Merchant transaction SEARCH API (GET-only): API Gateway (Cognito JWT)
    // -> Lambda in the Aurora VPC -> private OpenSearch Serverless collection.
    // Merchant isolation is enforced in the Lambda from the JWT claim.
    // ==================================================================
    const searchFn = new lambdaNode.NodejsFunction(this, "SearchApiFn", {
      entry: path.join(__dirname, "..", "lambda", "search-api", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(20),
      memorySize: 256,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      // Bundle the OpenSearch client (+ its AWS SigV4 signer deps) into the asset.
      bundling: {
        nodeModules: ["@opensearch-project/opensearch", "@aws-sdk/credential-provider-node"],
        externalModules: [],
      },
      environment: {
        COLLECTION_ENDPOINT: collection.attrCollectionEndpoint,
        INDEX_NAME: indexName,
      },
    });
    // Sign/authorize requests to the collection data plane.
    searchFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["aoss:APIAccessAll", "aoss:BatchGetCollection"],
        resources: [collection.attrArn],
      })
    );
    // The search Lambda's ENIs sit in the VPC (10.x); the collection endpoint SG
    // already admits 443 from the VPC CIDR, so no extra SG rule is needed.

    // Read-only data-access policy for the search role.
    new aoss.CfnAccessPolicy(this, "ReadPolicy", {
      name: `${collectionName}-read`,
      type: "data",
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: "index",
              Resource: [`index/${collectionName}/*`],
              Permission: ["aoss:DescribeIndex", "aoss:ReadDocument"],
            },
            {
              ResourceType: "collection",
              Resource: [`collection/${collectionName}`],
              Permission: ["aoss:DescribeCollectionItems"],
            },
          ],
          Principal: [searchFn.role!.roleArn],
        },
      ]),
    });

    // HTTP API + Cognito JWT authorizer — GET only.
    const jwtAuthorizer = new apigwAuth.HttpJwtAuthorizer(
      "SearchJwtAuthorizer",
      `https://cognito-idp.${region}.amazonaws.com/${cognitoPoolId}`,
      { jwtAudience: [cognitoClientId] }
    );
    const api = new apigwv2.HttpApi(this, "SearchApi", {
      corsPreflight: {
        allowOrigins: [distUrl],
        allowMethods: [apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST],
        allowHeaders: ["authorization", "content-type"],
      },
    });
    const searchInteg = new apigwInteg.HttpLambdaIntegration("SearchInteg", searchFn);
    api.addRoutes({
      path: "/transactions",
      methods: [apigwv2.HttpMethod.GET],
      integration: searchInteg,
      authorizer: jwtAuthorizer,
    });
    api.addRoutes({
      path: "/transactions/{id}",
      methods: [apigwv2.HttpMethod.GET],
      integration: searchInteg,
      authorizer: jwtAuthorizer,
    });
    const searchApiUrl = api.apiEndpoint;

    // ==================================================================
    // Commerce write API: POST /transactions, refunds + disputes as their own
    // first-class resources. Runs in the Aurora VPC and talks to
    // Postgres directly via `pg`. Opening a dispute auto-files an Amazon Connect
    // Case (tenant-tagged), with Cases field/template IDs resolved at runtime.
    // Tenant isolation + idempotency + the no-refund-on-disputed rule live in the
    // Lambda; the Cognito JWT authorizer rejects unauthenticated calls (401).
    // ==================================================================
    const commerceSg = new ec2.SecurityGroup(this, "CommerceApiSg", {
      vpc,
      description: "Commerce API lambda to Aurora",
      allowAllOutbound: true,
    });
    // Let the commerce Lambda reach the private Aurora cluster on 5432.
    dbSg.addIngressRule(
      ec2.Peer.securityGroupId(commerceSg.securityGroupId),
      ec2.Port.tcp(5432),
      "commerce api lambda"
    );

    // The Aurora VPC is isolated (no NAT/IGW), so the commerce Lambda has no
    // route to the public Amazon Connect Cases endpoint. Add a PrivateLink
    // interface endpoint (private DNS on) so cases.<region>.amazonaws.com
    // resolves to in-VPC ENIs — mirrors the Secrets Manager endpoint pattern.
    const casesEndpointSg = new ec2.SecurityGroup(this, "CasesEndpointSg", {
      vpc,
      description: "Amazon Connect Cases VPC endpoint",
      allowAllOutbound: true,
    });
    casesEndpointSg.addIngressRule(
      ec2.Peer.securityGroupId(commerceSg.securityGroupId),
      ec2.Port.tcp(443),
      "commerce api lambda to cases"
    );
    const casesEndpoint = new ec2.InterfaceVpcEndpoint(this, "CasesEndpoint", {
      vpc,
      service: new ec2.InterfaceVpcEndpointService(`com.amazonaws.${this.region}.cases`, 443),
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [casesEndpointSg],
      privateDnsEnabled: true,
      // We manage ingress on casesEndpointSg ourselves; `open:true` would try to
      // add a VPC-CIDR rule, which needs a CIDR the imported VPC doesn't carry.
      open: false,
    });

    const commerceFn = new lambdaNode.NodejsFunction(this, "CommerceApiFn", {
      entry: path.join(__dirname, "..", "lambda", "commerce-api", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [commerceSg],
      // `pg` is installed into the bundle (dynamic requires break esbuild);
      // the connectcases + secrets-manager SDK clients are installed too so we
      // don't depend on which @aws-sdk clients the Node 22 runtime happens to ship.
      bundling: {
        nodeModules: ["pg", "@aws-sdk/client-connectcases", "@aws-sdk/client-secrets-manager"],
      },
      environment: {
        DB_HOST: dbHost,
        DB_PORT: dbPort,
        DB_NAME: dbName,
        DB_SECRET_ARN: dbSecretArn,
        CASES_DOMAIN_ID: casesDomainId,
        CASES_TEMPLATE_NAME: casesTemplateName,
      },
    });
    commerceFn.node.addDependency(casesEndpoint);
    // Read the Aurora credentials secret (Secrets Manager reached via the VPC
    // endpoint the database module provisioned).
    commerceFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ["secretsmanager:GetSecretValue"], resources: [dbSecretArn] })
    );
    // Amazon Connect Cases: resolve fields/template + create the dispute case.
    const casesDomainArn = `arn:aws:cases:${this.region}:${this.account}:domain/${casesDomainId}`;
    commerceFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "cases:ListFields",
          "cases:ListTemplates",
          "cases:GetTemplate",
          "cases:CreateCase",
          "cases:CreateRelatedItem",
          "cases:GetCase",
        ],
        resources: [casesDomainArn, `${casesDomainArn}/*`],
      })
    );

    const commerceInteg = new apigwInteg.HttpLambdaIntegration("CommerceInteg", commerceFn);
    const commerceRoutes: { path: string; methods: apigwv2.HttpMethod[] }[] = [
      { path: "/transactions", methods: [apigwv2.HttpMethod.POST] },
      { path: "/refunds", methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST] },
      { path: "/refunds/{id}", methods: [apigwv2.HttpMethod.GET] },
      { path: "/disputes", methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST] },
      { path: "/disputes/{id}", methods: [apigwv2.HttpMethod.GET] },
      { path: "/disputes/{id}/evidence", methods: [apigwv2.HttpMethod.POST] },
    ];
    for (const r of commerceRoutes) {
      api.addRoutes({
        path: r.path,
        methods: r.methods,
        integration: commerceInteg,
        authorizer: jwtAuthorizer,
      });
    }
    // Same HTTP API base as the search API — exposed explicitly so the SPA can
    // address the commerce resources.
    const commerceApiUrl = api.apiEndpoint;

    // Merge searchApiUrl into the SPA's SSM runtime config + force an ECS redeploy.
    const paramArn = `arn:aws:ssm:${this.region}:${this.account}:parameter${runtimeConfigParamName}`;
    const serviceArn = cdk.Arn.format(
      { service: "ecs", resource: "service", resourceName: `${ecsCluster}/${ecsService}` },
      this
    );
    const configWriterFn = new lambdaNode.NodejsFunction(this, "ConfigWriterFn", {
      entry: path.join(__dirname, "..", "lambda", "config-writer", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(30),
      bundling: { minify: true, target: "node22", externalModules: ["@aws-sdk/*"] },
    });
    configWriterFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ["ssm:GetParameter", "ssm:PutParameter"], resources: [paramArn] })
    );
    configWriterFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ["ecs:UpdateService"], resources: [serviceArn] })
    );
    const configProvider = new cr.Provider(this, "ConfigWriterProvider", { onEventHandler: configWriterFn });
    new cdk.CustomResource(this, "SearchRuntimeConfig", {
      serviceToken: configProvider.serviceToken,
      properties: {
        ParameterName: runtimeConfigParamName,
        Patch: JSON.stringify({ connect: { searchApiUrl, commerceApiUrl } }),
        EcsCluster: ecsCluster,
        EcsService: ecsService,
        PatchHash: `${searchApiUrl}|${commerceApiUrl}|commerce-v1`,
      },
    });

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, "CollectionName", { value: collectionName });
    new cdk.CfnOutput(this, "CollectionEndpoint", { value: collection.attrCollectionEndpoint });
    new cdk.CfnOutput(this, "DashboardEndpoint", { value: collection.attrDashboardEndpoint });
    new cdk.CfnOutput(this, "PipelineName", { value: pipelineName });
    new cdk.CfnOutput(this, "IndexName", { value: indexName });
    new cdk.CfnOutput(this, "ExportBucketName", { value: exportBucket.bucketName });
    new cdk.CfnOutput(this, "SearchApiUrl", { value: searchApiUrl });
    new cdk.CfnOutput(this, "CommerceApiUrl", { value: commerceApiUrl });
  }
}
