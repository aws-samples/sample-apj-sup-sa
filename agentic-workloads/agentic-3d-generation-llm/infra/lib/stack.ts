import * as path from 'path';
import { CfnOutput, Duration, RemovalPolicy, SecretValue, Stack, StackProps } from 'aws-cdk-lib';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as route53resolver from 'aws-cdk-lib/aws-route53resolver';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as guardduty from 'aws-cdk-lib/aws-guardduty';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as destinations from 'aws-cdk-lib/aws-lambda-destinations';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';
import { loadModels, loadRetiredModels, runnableModels, usesInferenceProfile } from './models';
import { addNagSuppressions } from './nag-suppressions';

const ROOT = path.join(__dirname, '..', '..');
const ORIGIN_VERIFY_HEADER = 'x-origin-verify';
// Fixed names: Lambda@Edge has no environment variables, so the edge code (edge-auth/src/config.ts) hard-codes these.
const EDGE_CONFIG_SECRET = '3d-generation-llm/edge-auth/config';
const EDGE_NONCE_SECRET = '3d-generation-llm/edge-auth/nonce-signing';
// Client-side routes (/gallery, /how-it-works) are rewritten to index.html by the viewer sign-in gate
// (rewriteSpaRoute in edge-auth/src/viewer.ts), after sign-in, so the API's own 403/404s stay intact.
// Each POST starts four paid model runs; the API refuses new jobs past this many per UTC day.
const DAILY_GENERATION_LIMIT = 100;
// Image to 3D: at most this many uploads per UTC day (each one also starts four model runs).
const DAILY_UPLOAD_LIMIT = 100;
// Removes the background of uploaded images before the models see them.
// AgentCore Runtime VPC mode works only in these AZ IDs (us-east-1 also lists use1-az4, and use1-az6 on platform V2).
const AGENT_AZ_IDS: Record<string, string[]> = { 'us-east-1': ['use1-az1', 'use1-az2'] };
const BACKGROUND_MODEL = { profileId: 'us.stability.stable-image-remove-background-v1:0', baseModelId: 'stability.stable-image-remove-background-v1:0' };

export interface ThreeDGenerationStackProps extends StackProps {
  /** Optional IPv4 CIDRs; when set, WAF blocks every other source IP. */
  readonly allowedCidrs?: string[];
  /** Upload the site to S3 (default true). */
  readonly deploySite?: boolean;
}

export class ThreeDGenerationStack extends Stack {
  constructor(scope: Construct, id: string, props: ThreeDGenerationStackProps) {
    super(scope, id, props);
    const models = loadModels();
    const modelsJson = JSON.stringify(models);

    // ---------- Storage ----------
    const logBucket = new s3.Bucket(this, 'AccessLogBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED, // CloudFront standard logs need ACLs
      lifecycleRules: [{ expiration: Duration.days(90) }],
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const artifacts = new s3.Bucket(this, 'ArtifactsBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      serverAccessLogsBucket: logBucket,
      serverAccessLogsPrefix: 's3-artifacts/',
      lifecycleRules: [{ expiration: Duration.days(30) }],
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // Image to 3D uploads land here first. GuardDuty Malware Protection scans every object; the prepare
    // function only reads an upload after a clean scan, keeps a background-removed copy in the artifacts
    // bucket, and deletes the original. Nothing serves this bucket.
    const uploads = new s3.Bucket(this, 'UploadsBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      serverAccessLogsBucket: logBucket,
      serverAccessLogsPrefix: 's3-uploads/',
      lifecycleRules: [{ expiration: Duration.days(1) }],
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
    this.addMalwareProtection(uploads);

    const table = new dynamodb.TableV2(this, 'Generations', {
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      billing: dynamodb.Billing.onDemand(),
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      timeToLiveAttribute: 'expiresAt',
      globalSecondaryIndexes: [
        {
          indexName: 'byOwner',
          partitionKey: { name: 'owner', type: dynamodb.AttributeType.STRING },
          sortKey: { name: 'createdAt', type: dynamodb.AttributeType.STRING },
        },
      ],
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // ---------- AgentCore Runtime (LLM -> Blender script -> GLB) ----------
    // IAM covers every model a slot can run, including dropdown options.
    const runnable = runnableModels(models);
    const agentNetwork = this.addAgentNetwork(runnable.some((m) => m.api === 'mantle'));
    const runtime = new agentcore.Runtime(this, 'GeneratorAgent', {
      runtimeName: 'threed_generation_agent',
      description: 'Writes Blender scripts with an LLM and runs them headless to produce GLB files',
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset(path.join(ROOT, 'agent'), {
        platform: Platform.LINUX_ARM64,
      }),
      // No internet: the generated Blender scripts run in here, so the runtime reaches only the VPC endpoints.
      networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingVpc(this, {
        vpc: agentNetwork.vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
        securityGroups: [agentNetwork.securityGroup],
      }),
      lifecycleConfiguration: { idleRuntimeSessionTimeout: Duration.minutes(5), maxLifetime: Duration.hours(1) },
      environmentVariables: { MAX_ATTEMPTS: '3', BLENDER_TIMEOUT_SECONDS: '180', MAX_OUTPUT_TOKENS: '16000' },
    });

    const converseModels = runnable.filter((m) => m.api === 'converse');
    if (converseModels.length) {
      const invokeActions = ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'];
      const profileModels = converseModels.filter((m) => usesInferenceProfile(m));
      const profileArns = profileModels.map(
        (m) => `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${m.modelId}`,
      );
      runtime.role.addToPrincipalPolicy(
        new iam.PolicyStatement({
          sid: 'InvokeConverseModels',
          actions: invokeActions,
          resources: [
            ...profileArns,
            // Some models (Grok 4.7) also authorise each call against the account's default Bedrock project.
            `arn:aws:bedrock:${this.region}:${this.account}:project/default`,
          ],
        }),
      );
      // In-Region models (for example Qwen3 32B) have no inference profile, so they are called directly by their
      // foundation-model ID. Granted only for those models, in this Region, and only when one is configured.
      const inRegionModels = converseModels.filter((m) => !usesInferenceProfile(m));
      if (inRegionModels.length) {
        runtime.role.addToPrincipalPolicy(
          new iam.PolicyStatement({
            sid: 'InvokeInRegionModels',
            actions: invokeActions,
            resources: inRegionModels.map((m) => `arn:aws:bedrock:${this.region}::foundation-model/${m.modelId}`),
          }),
        );
      }
      if (profileModels.length) {
        // us.* profiles route to any US Region, so the foundation model is granted in every Region,
        // but only when the call comes through one of the profiles above, never as a direct model call.
        runtime.role.addToPrincipalPolicy(
          new iam.PolicyStatement({
            sid: 'InvokeConverseModelsViaProfile',
            actions: invokeActions,
            resources: profileModels.map((m) => `arn:aws:bedrock:*::foundation-model/${m.baseModelId}`),
            conditions: { StringEquals: { 'bedrock:InferenceProfileArn': profileArns } },
          }),
        );
      }
    }
    if (runnable.some((m) => m.api === 'mantle')) {
      runtime.role.addToPrincipalPolicy(
        new iam.PolicyStatement({
          sid: 'InvokeMantleModels',
          actions: ['bedrock-mantle:CreateInference'],
          // The agent sends no project ID, so bedrock-mantle bills and authorises every call against the default project.
          resources: [`arn:aws:bedrock-mantle:${this.region}:${this.account}:project/default`],
        }),
      );
    }
    // The model VPC endpoints accept only the runtime role and only the inference calls it makes.
    // Which models it may call is set by the role's own policy above.
    agentNetwork.bedrockRuntime.addToPolicy(new iam.PolicyStatement({
      sid: 'RuntimeRoleModelCallsOnly',
      principals: [new iam.ArnPrincipal(runtime.role.roleArn)],
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['*'],
    }));
    agentNetwork.bedrockMantle?.addToPolicy(new iam.PolicyStatement({
      sid: 'RuntimeRoleModelCallsOnly',
      principals: [new iam.ArnPrincipal(runtime.role.roleArn)],
      actions: ['bedrock-mantle:CreateInference'],
      resources: ['*'],
    }));

    // ---------- Lambdas ----------
    const fnDefaults = {
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      tracing: lambda.Tracing.ACTIVE,
    };

    const workerFn = new lambda.Function(this, 'WorkerFunction', {
      ...fnDefaults,
      description: 'Runs one model for one job through the AgentCore runtime and stores the GLB',
      code: lambda.Code.fromAsset(path.join(ROOT, 'backend', 'worker')),
      memorySize: 512,
      timeout: Duration.minutes(15),
      logGroup: new logs.LogGroup(this, 'WorkerLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: RemovalPolicy.DESTROY }),
      environment: {
        TABLE_NAME: table.tableName,
        BUCKET_NAME: artifacts.bucketName,
        AGENT_RUNTIME_ARN: runtime.agentRuntimeArn,
        MODELS_JSON: modelsJson,
      },
    });
    // Records a model as failed when its worker event is dropped (older than maxEventAge) or the worker
    // crashes or times out, so the job never waits on it forever.
    const workerFailureFn = new lambda.Function(this, 'WorkerFailureFunction', {
      ...fnDefaults,
      handler: 'handler.on_failure',
      description: 'On-failure destination for the worker: marks a model that never started or crashed as failed',
      code: lambda.Code.fromAsset(path.join(ROOT, 'backend', 'worker')),
      memorySize: 256,
      timeout: Duration.seconds(30),
      logGroup: new logs.LogGroup(this, 'WorkerFailureLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: RemovalPolicy.DESTROY }),
      environment: {
        TABLE_NAME: table.tableName,
        BUCKET_NAME: artifacts.bucketName,
        AGENT_RUNTIME_ARN: runtime.agentRuntimeArn,
        MODELS_JSON: modelsJson,
      },
    });
    table.grant(workerFailureFn, 'dynamodb:UpdateItem');
    // A retry would re-run (and re-bill) the whole generation; failures are recorded on the job instead.
    // maxEventAge must match WORKER_MAX_EVENT_AGE_SECONDS in backend/api/handler.py.
    workerFn.configureAsyncInvoke({
      retryAttempts: 0,
      maxEventAge: Duration.minutes(9), // a model still pending after 10 minutes shows as failed
      onFailure: new destinations.LambdaDestination(workerFailureFn, { responseOnly: false }),
    });
    table.grantReadWriteData(workerFn);
    artifacts.grantPut(workerFn, 'generations/*');
    artifacts.grantRead(workerFn, 'generations/*/input.png'); // Image to 3D: the prepared image
    runtime.grantInvokeRuntime(workerFn);

    // Runs after GuardDuty has scanned an upload: background removal, then one worker per model.
    const prepareFn = new lambda.Function(this, 'PrepareImageFunction', {
      ...fnDefaults,
      description: 'Image to 3D: after a clean malware scan, removes the background and starts the models',
      code: lambda.Code.fromAsset(path.join(ROOT, 'backend', 'prepare')),
      memorySize: 512,
      timeout: Duration.minutes(3),
      logGroup: new logs.LogGroup(this, 'PrepareLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: RemovalPolicy.DESTROY }),
      environment: {
        TABLE_NAME: table.tableName,
        UPLOADS_BUCKET_NAME: uploads.bucketName,
        BUCKET_NAME: artifacts.bucketName,
        WORKER_FUNCTION_NAME: workerFn.functionName,
        BACKGROUND_MODEL_ID: BACKGROUND_MODEL.profileId,
      },
    });
    table.grantReadWriteData(prepareFn);
    uploads.grantRead(prepareFn, 'uploads/*');
    uploads.grantDelete(prepareFn, 'uploads/*');
    artifacts.grantPut(prepareFn, 'generations/*');
    workerFn.grantInvoke(prepareFn);
    const bgProfileArn = `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${BACKGROUND_MODEL.profileId}`;
    prepareFn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RemoveBackgroundProfile',
      actions: ['bedrock:InvokeModel'],
      resources: [bgProfileArn],
    }));
    prepareFn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RemoveBackgroundViaProfile',
      actions: ['bedrock:InvokeModel'],
      resources: [`arn:aws:bedrock:*::foundation-model/${BACKGROUND_MODEL.baseModelId}`],
      conditions: { StringEquals: { 'bedrock:InferenceProfileArn': bgProfileArn } },
    }));
    // GuardDuty's scan result for this bucket starts the prepare function (any result: clean, threat or skipped).
    new events.Rule(this, 'UploadScanned', {
      description: 'GuardDuty malware scan result for an Image to 3D upload',
      eventPattern: {
        source: ['aws.guardduty'],
        detailType: ['GuardDuty Malware Protection Object Scan Result'],
        detail: { s3ObjectDetails: { bucketName: [uploads.bucketName] } },
      },
      targets: [new targets.LambdaFunction(prepareFn, { retryAttempts: 2, maxEventAge: Duration.minutes(30) })],
    });

    const apiFn = new lambda.Function(this, 'ApiFunction', {
      ...fnDefaults,
      description: 'Generations REST API',
      code: lambda.Code.fromAsset(path.join(ROOT, 'backend', 'api')),
      memorySize: 512, // decodes uploads of up to 4 MB
      timeout: Duration.seconds(15),
      logGroup: new logs.LogGroup(this, 'ApiLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: RemovalPolicy.DESTROY }),
      environment: {
        TABLE_NAME: table.tableName,
        BUCKET_NAME: artifacts.bucketName,
        WORKER_FUNCTION_NAME: workerFn.functionName,
        MODELS_JSON: modelsJson,
        RETIRED_MODELS_JSON: JSON.stringify(loadRetiredModels()),
        DAILY_GENERATION_LIMIT: String(DAILY_GENERATION_LIMIT),
        DAILY_UPLOAD_LIMIT: String(DAILY_UPLOAD_LIMIT),
        UPLOADS_BUCKET_NAME: uploads.bucketName,
        GALLERY_MODEL_KEYS: 'gpt-6-2-astra,gpt-6-astra',
      },
    });
    table.grantReadWriteData(apiFn);
    workerFn.grantInvoke(apiFn);
    // Deleting a gallery object removes its files: list and delete under generations/ only.
    artifacts.grantDelete(apiFn, 'generations/*');
    // Image to 3D: write uploads for scanning (and delete them with their job).
    uploads.grantPut(apiFn, 'uploads/*');
    uploads.grantDelete(apiFn, 'uploads/*');
    // Deleting a job lists exactly "generations/<job UUID>/". Each "?" matches one character and there is no "*",
    // so the role can list one job's folder at a time, never the bucket root or all generations.
    apiFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [artifacts.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['generations/????????-????-????-????-????????????/'] } },
    }));

    // ---------- CloudFront-only access to the API ----------
    // CloudFront adds this secret header to every /api/* request; a regional WAF on the API stage
    // blocks anything without it, so calls straight to execute-api get 403.
    const originVerify = new secretsmanager.Secret(this, 'OriginVerifySecret', {
      description: 'Header value CloudFront sends to API Gateway; the API WAF blocks requests without it',
      generateSecretString: { passwordLength: 48, excludePunctuation: true },
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const originVerifyValue = originVerify.secretValue.unsafeUnwrap();

    // ---------- Cognito (admin-created users only; add them in the Cognito console) ----------
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: '3d-generation-llm-users',
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      passwordPolicy: { minLength: 12, requireDigits: true, requireLowercase: true, requireUppercase: true, requireSymbols: true },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { otp: true, sms: false },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const domainPrefix = `threed-gen-${this.account}`;
    userPool.addDomain('Domain', { cognitoDomain: { domainPrefix } });
    const cognitoDomain = `${domainPrefix}.auth.${this.region}.amazoncognito.com`;

    // ---------- API Gateway (signed-in users only, reachable only through CloudFront) ----------
    const accessLogs = new logs.LogGroup(this, 'ApiAccessLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: RemovalPolicy.DESTROY });
    const restApi = new apigw.RestApi(this, 'RestApi', {
      restApiName: '3d-generation-llm',
      endpointTypes: [apigw.EndpointType.REGIONAL],
      cloudWatchRole: true,
      cloudWatchRoleRemovalPolicy: RemovalPolicy.DESTROY,
      deployOptions: {
        stageName: 'prod',
        throttlingRateLimit: 10,
        throttlingBurstLimit: 20,
        loggingLevel: apigw.MethodLoggingLevel.ERROR,
        dataTraceEnabled: false,
        metricsEnabled: true,
        tracingEnabled: true,
        accessLogDestination: new apigw.LogGroupLogDestination(accessLogs),
        accessLogFormat: apigw.AccessLogFormat.jsonWithStandardFields(),
      },
      // The CloudFront API gate verifies the session cookie and forwards its ID token as Authorization;
      // API Gateway checks it again, so the origin-verify header alone never reaches the Lambda.
      defaultMethodOptions: {
        authorizationType: apigw.AuthorizationType.COGNITO,
        authorizer: new apigw.CognitoUserPoolsAuthorizer(this, 'Authorizer', {
          cognitoUserPools: [userPool],
          identitySource: apigw.IdentitySource.header('Authorization'),
          resultsCacheTtl: Duration.minutes(5),
        }),
      },
    });
    const integration = new apigw.LambdaIntegration(apiFn);
    const apiRoot = restApi.root.addResource('api');
    apiRoot.addResource('models').addMethod('GET', integration);
    const gens = apiRoot.addResource('generations');
    gens.addMethod('GET', integration);
    gens.addMethod('POST', integration);
    const genById = gens.addResource('{jobId}');
    genById.addMethod('GET', integration);
    genById.addMethod('DELETE', integration);
    apiRoot.addResource('gallery').addMethod('GET', integration);
    apiRoot.addResource('image-generations').addMethod('POST', integration);

    const apiAcl = new wafv2.CfnWebACL(this, 'ApiWebAcl', {
      scope: 'REGIONAL',
      description: 'Allows only requests that came through CloudFront, checked by a secret origin header',
      defaultAction: { block: {} },
      visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: 'ThreeDGenerationApiAcl', sampledRequestsEnabled: true },
      rules: [
        {
          name: 'AllowFromCloudFront',
          priority: 0,
          action: { allow: {} },
          statement: {
            byteMatchStatement: {
              fieldToMatch: { singleHeader: { Name: ORIGIN_VERIFY_HEADER } },
              positionalConstraint: 'EXACTLY',
              searchString: originVerifyValue,
              textTransformations: [{ priority: 0, type: 'NONE' }],
            },
          },
          visibilityConfig: { cloudWatchMetricsEnabled: true, metricName: 'AllowFromCloudFront', sampledRequestsEnabled: true },
        },
      ],
    });
    new wafv2.CfnWebACLAssociation(this, 'ApiWebAclAssociation', {
      resourceArn: `arn:aws:apigateway:${this.region}::/restapis/${restApi.restApiId}/stages/${restApi.deploymentStage.stageName}`,
      webAclArn: apiAcl.attrArn,
    });

    // ---------- Lambda@Edge sign-in gates (edge-auth/) ----------
    // By exact name, not by reference: the secret holds the app client, which needs the distribution. Secrets
    // Manager appends "-" and 6 random characters to every secret ARN, matched here by six single-character "?".
    const edgeSecretArn = (name: string) => `arn:aws:secretsmanager:us-east-1:${this.account}:secret:${name}-??????`;
    const edgeFn = (id: string, dir: string, description: string, timeout: Duration, secretNames: string[]) => {
      const fn = new cloudfront.experimental.EdgeFunction(this, id, {
        runtime: lambda.Runtime.NODEJS_22_X,
        handler: 'index.handler',
        code: lambda.Code.fromAsset(path.join(ROOT, 'edge-auth', 'dist', dir)),
        memorySize: 128,
        timeout,
        description,
      });
      fn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: secretNames.map(edgeSecretArn),
      }));
      return fn;
    };
    // The viewer gate signs OAuth nonces; the API gate only verifies tokens, so it reads the config alone.
    const viewerAuth = edgeFn('ViewerAuthFunction', 'viewer', 'Redirects unauthenticated viewers to Cognito sign-in', Duration.seconds(5),
      [EDGE_CONFIG_SECRET, EDGE_NONCE_SECRET]);
    const apiAuth = edgeFn('ApiAuthFunction', 'api', 'Verifies the Cognito ID token cookie for /api/*', Duration.seconds(10),
      [EDGE_CONFIG_SECRET]);
    const viewerGate = [{ functionVersion: viewerAuth.currentVersion, eventType: cloudfront.LambdaEdgeEventType.VIEWER_REQUEST }];

    // ---------- CloudFront + WAF ----------
    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      serverAccessLogsBucket: logBucket,
      serverAccessLogsPrefix: 's3-site/',
      lifecycleRules: [{ noncurrentVersionExpiration: Duration.days(30) }],
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const securityHeaders = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeaders', {
      comment: '3D generation security headers',
      securityHeadersBehavior: {
        contentSecurityPolicy: {
          contentSecurityPolicy: [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'", // model-viewer sets inline styles in its shadow DOM
            "img-src 'self' data: blob:",
            "connect-src 'self'", // GLBs and scripts come through this distribution too
            "worker-src 'self' blob:",
            "font-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "frame-ancestors 'none'",
            "form-action 'self'",
          ].join('; '),
          override: true,
        },
        strictTransportSecurity: { accessControlMaxAge: Duration.days(365), includeSubdomains: true, override: true },
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override: true },
      },
    });

    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: '3D generation with LLMs',
      defaultRootObject: 'index.html',
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      webAclId: this.createWebAcl(props.allowedCidrs ?? []).attrArn,
      enableLogging: true,
      logBucket,
      logFilePrefix: 'cloudfront/',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        // The gate runs on viewer request, before the cache, so a cached page is never served without it.
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: securityHeaders,
        compress: true,
        edgeLambdas: viewerGate,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.RestApiOrigin(restApi, { customHeaders: { [ORIGIN_VERIFY_HEADER]: originVerifyValue } }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: new cloudfront.OriginRequestPolicy(this, 'ApiOriginRequestPolicy', {
            comment: 'Content-Type, gallery paging, and the session cookie (the edge gate turns it into Authorization)',
            headerBehavior: cloudfront.OriginRequestHeaderBehavior.allowList('Content-Type'),
            queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.allowList('nextToken'), // gallery paging
            cookieBehavior: cloudfront.OriginRequestCookieBehavior.all(),
          }),
          responseHeadersPolicy: securityHeaders,
          edgeLambdas: [{ functionVersion: apiAuth.currentVersion, eventType: cloudfront.LambdaEdgeEventType.ORIGIN_REQUEST }],
        },
        // Generated GLB files and scripts, read from the private artifacts bucket through OAC; signed-in users only.
        '/generations/*': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(artifacts),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
          responseHeadersPolicy: securityHeaders,
          compress: true,
          edgeLambdas: viewerGate,
        },
      },
    });
    const siteUrl = `https://${distribution.distributionDomainName}`;

    // ---------- Cognito app client + edge config (needs the distribution's domain) ----------
    const client = userPool.addClient('WebClient', {
      userPoolClientName: '3d-generation-llm-web',
      generateSecret: true,
      preventUserExistenceErrors: true,
      authFlows: { userSrp: true },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        // cognito-at-edge sends https://<host> with no trailing slash for both redirects.
        callbackUrls: [siteUrl],
        logoutUrls: [siteUrl],
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      idTokenValidity: Duration.hours(1),
      accessTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(1),
    });

    new secretsmanager.Secret(this, 'EdgeAuthConfig', {
      secretName: EDGE_CONFIG_SECRET,
      description: 'Cognito settings for the Lambda@Edge sign-in gate',
      secretObjectValue: {
        region: SecretValue.unsafePlainText(this.region),
        userPoolId: SecretValue.unsafePlainText(userPool.userPoolId),
        clientId: SecretValue.unsafePlainText(client.userPoolClientId),
        clientSecret: client.userPoolClientSecret,
        domain: SecretValue.unsafePlainText(cognitoDomain),
      },
      removalPolicy: RemovalPolicy.DESTROY,
    });
    new secretsmanager.Secret(this, 'EdgeNonceSecret', {
      secretName: EDGE_NONCE_SECRET,
      description: 'Signs the OAuth state nonce (CSRF protection) for the sign-in gate',
      generateSecretString: { passwordLength: 64, excludePunctuation: true },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    if (props.deploySite ?? true) {
      new s3deploy.BucketDeployment(this, 'DeploySite', {
        sources: [s3deploy.Source.asset(path.join(ROOT, 'frontend', 'dist'))],
        destinationBucket: siteBucket,
        distribution,
        distributionPaths: ['/*'],
        memoryLimit: 512,
        prune: true,
      });
    }

    new CfnOutput(this, 'SiteUrl', { value: siteUrl });
    new CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new CfnOutput(this, 'AgentRuntimeArn', { value: runtime.agentRuntimeArn });
    new CfnOutput(this, 'TableName', { value: table.tableName });
    new CfnOutput(this, 'ArtifactsBucketName', { value: artifacts.bucketName });
    new CfnOutput(this, 'WorkerFunctionName', { value: workerFn.functionName });
    new CfnOutput(this, 'UploadsBucketName', { value: uploads.bucketName });
    new CfnOutput(this, 'ApiExecuteUrl', { value: restApi.url });

    addNagSuppressions(this, runnable);
  }

  /**
   * A VPC with no route out: isolated subnets only (no internet or NAT gateway), VPC endpoints for the
   * AWS services the agent calls, and a DNS Firewall that answers NXDOMAIN for anything outside AWS, so
   * the generated scripts can't reach the internet or tunnel data out through DNS lookups.
   */
  private addAgentNetwork(withMantle: boolean): {
    vpc: ec2.Vpc;
    securityGroup: ec2.SecurityGroup;
    bedrockRuntime: ec2.InterfaceVpcEndpoint;
    bedrockMantle?: ec2.InterfaceVpcEndpoint;
  } {
    const azIds = AGENT_AZ_IDS[this.region];
    if (!azIds) throw new Error(`No AgentCore VPC Availability Zone IDs configured for ${this.region}`);

    const flowLogs = new logs.LogGroup(this, 'AgentVpcFlowLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const vpc = new ec2.Vpc(this, 'AgentVpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.40.0.0/16'),
      maxAzs: azIds.length,
      natGateways: 0,
      subnetConfiguration: [{ name: 'agent', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 }],
      restrictDefaultSecurityGroup: true,
      flowLogs: { all: { destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogs), trafficType: ec2.FlowLogTrafficType.ALL } },
    });
    // AgentCore supports only some AZs, named by ID (AZ names map to different IDs in each account).
    vpc.isolatedSubnets.forEach((subnet, i) => {
      const cfn = subnet.node.defaultChild as ec2.CfnSubnet;
      cfn.addPropertyDeletionOverride('AvailabilityZone');
      cfn.addPropertyOverride('AvailabilityZoneId', azIds[i]);
    });

    const securityGroup = new ec2.SecurityGroup(this, 'AgentSecurityGroup', {
      vpc,
      description: 'AgentCore runtime ENIs: HTTPS out only; the subnets route nowhere but the VPC endpoints',
      allowAllOutbound: false,
    });
    securityGroup.addEgressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443), 'HTTPS to the VPC endpoints and the S3 gateway endpoint');
    const endpointSg = new ec2.SecurityGroup(this, 'AgentEndpointSecurityGroup', {
      vpc,
      description: 'Interface VPC endpoints for the AgentCore runtime',
      allowAllOutbound: false,
    });
    endpointSg.addIngressRule(securityGroup, ec2.Port.tcp(443), 'HTTPS from the AgentCore runtime');

    const subnets = { subnetType: ec2.SubnetType.PRIVATE_ISOLATED };
    const endpoint = (id: string, service: ec2.IInterfaceVpcEndpointService) =>
      vpc.addInterfaceEndpoint(id, { service, subnets, securityGroups: [endpointSg], privateDnsEnabled: true, open: false });
    // Policies for the model endpoints are added once the runtime role exists (see allowModelCallsFrom).
    const bedrockRuntime = endpoint('BedrockRuntimeEndpoint', ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME);
    const bedrockMantle = withMantle
      ? endpoint('BedrockMantleEndpoint', new ec2.InterfaceVpcEndpointService(`com.amazonaws.${this.region}.bedrock-mantle`, 443))
      : undefined;
    // Container agents in VPC mode need ECR, its S3 layer bucket and CloudWatch Logs.
    endpoint('EcrApiEndpoint', ec2.InterfaceVpcEndpointAwsService.ECR);
    endpoint('EcrDockerEndpoint', ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER);
    endpoint('LogsEndpoint', ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS);
    const s3Endpoint = vpc.addGatewayEndpoint('S3Endpoint', { service: ec2.GatewayVpcEndpointAwsService.S3, subnets: [subnets] });
    s3Endpoint.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowEcrLayerAccess',
      principals: [new iam.AnyPrincipal()],
      actions: ['s3:GetObject'],
      resources: [`arn:aws:s3:::prod-${this.region}-starport-layer-bucket/*`],
    }));

    // DNS Firewall: AWS names resolve; everything else gets NXDOMAIN (blocks DNS tunnelling).
    const allowed = new route53resolver.CfnFirewallDomainList(this, 'AgentDnsAllowList', {
      domains: ['amazonaws.com.', '*.amazonaws.com.', 'api.aws.', '*.api.aws.', 'ec2.internal.', '*.ec2.internal.'],
    });
    const everything = new route53resolver.CfnFirewallDomainList(this, 'AgentDnsBlockList', { domains: ['*.'] });
    const ruleGroup = new route53resolver.CfnFirewallRuleGroup(this, 'AgentDnsFirewall', {
      firewallRules: [
        { firewallDomainListId: allowed.attrId, priority: 100, action: 'ALLOW', firewallDomainRedirectionAction: 'TRUST_REDIRECTION_DOMAIN' },
        { firewallDomainListId: everything.attrId, priority: 200, action: 'BLOCK', blockResponse: 'NXDOMAIN' },
      ],
    });
    new route53resolver.CfnFirewallRuleGroupAssociation(this, 'AgentDnsFirewallAssociation', {
      firewallRuleGroupId: ruleGroup.attrId,
      vpcId: vpc.vpcId,
      priority: 1000,
      mutationProtection: 'DISABLED',
    });

    return { vpc, securityGroup, bedrockRuntime, bedrockMantle };
  }

  /** GuardDuty Malware Protection for S3 on the uploads bucket: scans each new object and tags the result. */
  private addMalwareProtection(bucket: s3.Bucket): void {
    const role = new iam.Role(this, 'MalwareProtectionRole', {
      description: 'Lets GuardDuty Malware Protection scan and tag objects in the Image to 3D uploads bucket',
      assumedBy: new iam.ServicePrincipal('malware-protection-plan.guardduty.amazonaws.com'),
    });
    const UPLOAD_PREFIX = 'uploads/'; // the API only writes uploads/<job>/... (grantPut 'uploads/*')
    const managedRule = `arn:aws:events:${this.region}:${this.account}:rule/DO-NOT-DELETE-AmazonGuardDutyMalwareProtectionS3*`;
    // From the GuardDuty user guide's role policy template, scoped to this bucket.
    const policy = new iam.Policy(this, 'MalwareProtectionPolicy', {
      statements: [
        new iam.PolicyStatement({
          sid: 'AllowManagedRuleToSendS3EventsToGuardDuty',
          actions: ['events:PutRule', 'events:DeleteRule', 'events:PutTargets', 'events:RemoveTargets'],
          resources: [managedRule],
          conditions: { StringLike: { 'events:ManagedBy': 'malware-protection-plan.guardduty.amazonaws.com' } },
        }),
        new iam.PolicyStatement({
          sid: 'AllowGuardDutyToMonitorEventBridgeManagedRule',
          actions: ['events:DescribeRule', 'events:ListTargetsByRule'],
          resources: [managedRule],
        }),
        new iam.PolicyStatement({
          sid: 'AllowPostScanTag',
          actions: ['s3:PutObjectTagging', 's3:GetObjectTagging', 's3:PutObjectVersionTagging', 's3:GetObjectVersionTagging'],
          resources: [bucket.arnForObjects(`${UPLOAD_PREFIX}*`)],
        }),
        new iam.PolicyStatement({
          sid: 'AllowEnableS3EventBridgeEvents',
          actions: ['s3:PutBucketNotification', 's3:GetBucketNotification'],
          resources: [bucket.bucketArn],
        }),
        new iam.PolicyStatement({
          sid: 'AllowPutValidationObject',
          actions: ['s3:PutObject'],
          resources: [bucket.arnForObjects('malware-protection-resource-validation-object')],
        }),
        new iam.PolicyStatement({ sid: 'AllowCheckBucketOwnership', actions: ['s3:ListBucket'], resources: [bucket.bucketArn] }),
        new iam.PolicyStatement({
          sid: 'AllowMalwareScan',
          actions: ['s3:GetObject', 's3:GetObjectVersion'],
          resources: [bucket.arnForObjects(`${UPLOAD_PREFIX}*`)],
        }),
      ],
    });
    policy.attachToRole(role);
    const plan = new guardduty.CfnMalwareProtectionPlan(this, 'UploadsMalwareProtection', {
      role: role.roleArn,
      // Only the uploads/ prefix is scanned, so the role above can read and tag only that prefix.
      protectedResource: { s3Bucket: { bucketName: bucket.bucketName, objectPrefixes: [UPLOAD_PREFIX] } },
      actions: { tagging: { status: 'ENABLED' } },
    });
    plan.node.addDependency(policy); // GuardDuty checks the role's permissions when the plan is created
  }

  private createWebAcl(allowedCidrs: string[]): wafv2.CfnWebACL {
    const visibility = (name: string) => ({ cloudWatchMetricsEnabled: true, metricName: name, sampledRequestsEnabled: true });
    const managed = (name: string, priority: number, countOnly: string[] = []): wafv2.CfnWebACL.RuleProperty => ({
      name,
      priority,
      overrideAction: { none: {} },
      statement: {
        managedRuleGroupStatement: {
          vendorName: 'AWS',
          name,
          ruleActionOverrides: countOnly.length ? countOnly.map((rule) => ({ name: rule, actionToUse: { count: {} } })) : undefined,
        },
      },
      visibilityConfig: visibility(name),
    });
    const rules: wafv2.CfnWebACL.RuleProperty[] = [
      managed('AWSManagedRulesAmazonIpReputationList', 10),
      // The common rule set blocks bodies over 8 KB, which would stop image uploads. That one rule only
      // counts here; the next rule blocks large bodies everywhere except the upload route.
      managed('AWSManagedRulesCommonRuleSet', 20, ['SizeRestrictions_BODY']),
      {
        name: 'BlockLargeBodiesExceptUploads',
        priority: 25,
        action: { block: {} },
        statement: {
          andStatement: {
            statements: [
              {
                sizeConstraintStatement: {
                  fieldToMatch: { body: { oversizeHandling: 'MATCH' } },
                  comparisonOperator: 'GT',
                  size: 8192,
                  textTransformations: [{ priority: 0, type: 'NONE' }],
                },
              },
              {
                notStatement: {
                  statement: {
                    byteMatchStatement: {
                      fieldToMatch: { uriPath: {} },
                      positionalConstraint: 'EXACTLY',
                      searchString: '/api/image-generations',
                      textTransformations: [{ priority: 0, type: 'NONE' }],
                    },
                  },
                },
              },
            ],
          },
        },
        visibilityConfig: visibility('BlockLargeBodiesExceptUploads'),
      },
      managed('AWSManagedRulesKnownBadInputsRuleSet', 30),
      {
        // Each POST (prompt or image upload) starts four paid model runs: at most 10 per IP in 5 minutes.
        name: 'RateLimitGenerationsPerIp',
        priority: 35,
        action: { block: {} },
        statement: {
          rateBasedStatement: {
            limit: 10,
            evaluationWindowSec: 300,
            aggregateKeyType: 'IP',
            scopeDownStatement: {
              byteMatchStatement: {
                fieldToMatch: { method: {} },
                positionalConstraint: 'EXACTLY',
                searchString: 'POST',
                textTransformations: [{ priority: 0, type: 'NONE' }],
              },
            },
          },
        },
        visibilityConfig: visibility('RateLimitGenerationsPerIp'),
      },
      {
        // The site is public: at most 10 deletes per IP in 5 minutes.
        name: 'RateLimitDeletesPerIp',
        priority: 36,
        action: { block: {} },
        statement: {
          rateBasedStatement: {
            limit: 10,
            evaluationWindowSec: 300,
            aggregateKeyType: 'IP',
            scopeDownStatement: {
              byteMatchStatement: {
                fieldToMatch: { method: {} },
                positionalConstraint: 'EXACTLY',
                searchString: 'DELETE',
                textTransformations: [{ priority: 0, type: 'NONE' }],
              },
            },
          },
        },
        visibilityConfig: visibility('RateLimitDeletesPerIp'),
      },
      {
        name: 'RateLimitPerIp',
        priority: 40,
        action: { block: {} },
        statement: { rateBasedStatement: { limit: 2000, aggregateKeyType: 'IP' } },
        visibilityConfig: visibility('RateLimitPerIp'),
      },
    ];
    if (allowedCidrs.length > 0) {
      const ipSet = new wafv2.CfnIPSet(this, 'AllowedIps', { scope: 'CLOUDFRONT', ipAddressVersion: 'IPV4', addresses: allowedCidrs });
      rules.push({
        name: 'BlockOutsideAllowedCidrs',
        priority: 0,
        action: { block: {} },
        statement: { notStatement: { statement: { ipSetReferenceStatement: { arn: ipSet.attrArn } } } },
        visibilityConfig: visibility('BlockOutsideAllowedCidrs'),
      });
    }
    return new wafv2.CfnWebACL(this, 'WebAcl', {
      scope: 'CLOUDFRONT',
      defaultAction: { allow: {} },
      visibilityConfig: visibility('ThreeDGenerationWebAcl'),
      rules,
    });
  }
}
