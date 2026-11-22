// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as path from 'path';
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
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
// Client-side routes (/gallery, /gallery/<jobId>, /how-it-works) have no file in S3: serve the app.
// Done per behaviour, not with distribution error pages, so the API's own 403/404s stay intact.
const SPA_ROUTES = `
function handler(event) {
  var request = event.request;
  var last = request.uri.split('/').pop();
  if (request.uri !== '/' && last.indexOf('.') === -1) {
    request.uri = '/index.html';
  }
  return request;
}
`;
// Each POST starts four paid model runs; the API refuses new jobs past this many per UTC day.
const DAILY_GENERATION_LIMIT = 100;

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
    const runtime = new agentcore.Runtime(this, 'GeneratorAgent', {
      runtimeName: 'threed_generation_agent',
      description: 'Writes Blender scripts with an LLM and runs them headless to produce GLB files',
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset(path.join(ROOT, 'agent'), {
        platform: Platform.LINUX_ARM64,
      }),
      networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      lifecycleConfiguration: { idleRuntimeSessionTimeout: Duration.minutes(5), maxLifetime: Duration.hours(1) },
      environmentVariables: { MAX_ATTEMPTS: '3', BLENDER_TIMEOUT_SECONDS: '180', MAX_OUTPUT_TOKENS: '16000' },
    });

    // IAM covers every model a slot can run, including dropdown options.
    const runnable = runnableModels(models);
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
            // In-Region models (for example Qwen3 32B) are called by their foundation-model ID here only.
            ...converseModels
              .filter((m) => !usesInferenceProfile(m))
              .map((m) => `arn:aws:bedrock:${this.region}::foundation-model/${m.modelId}`),
            // Some models (Grok 4.7) also authorise each call against the account's default Bedrock project.
            `arn:aws:bedrock:${this.region}:${this.account}:project/default`,
          ],
        }),
      );
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
    // A retry would re-run (and re-bill) the whole generation; failures are recorded on the job instead.
    workerFn.configureAsyncInvoke({ retryAttempts: 0, maxEventAge: Duration.hours(1) });
    table.grantReadWriteData(workerFn);
    artifacts.grantPut(workerFn, 'generations/*');
    runtime.grantInvokeRuntime(workerFn);

    const apiFn = new lambda.Function(this, 'ApiFunction', {
      ...fnDefaults,
      description: 'Generations REST API',
      code: lambda.Code.fromAsset(path.join(ROOT, 'backend', 'api')),
      memorySize: 256,
      timeout: Duration.seconds(15),
      logGroup: new logs.LogGroup(this, 'ApiLogs', { retention: logs.RetentionDays.ONE_MONTH, removalPolicy: RemovalPolicy.DESTROY }),
      environment: {
        TABLE_NAME: table.tableName,
        BUCKET_NAME: artifacts.bucketName,
        WORKER_FUNCTION_NAME: workerFn.functionName,
        MODELS_JSON: modelsJson,
        RETIRED_MODELS_JSON: JSON.stringify(loadRetiredModels()),
        DAILY_GENERATION_LIMIT: String(DAILY_GENERATION_LIMIT),
        GALLERY_MODEL_KEY: 'gpt-6-astra',
      },
    });
    table.grantReadWriteData(apiFn);
    workerFn.grantInvoke(apiFn);
    // Deleting a gallery object removes its files: list and delete under generations/ only.
    artifacts.grantDelete(apiFn, 'generations/*');
    apiFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [artifacts.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['generations/*'] } },
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

    // ---------- API Gateway (public, reachable only through CloudFront) ----------
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
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: securityHeaders,
        compress: true,
        functionAssociations: [
          {
            eventType: cloudfront.FunctionEventType.VIEWER_REQUEST,
            function: new cloudfront.Function(this, 'SpaRoutes', {
              comment: 'Serve index.html for app routes such as /gallery',
              runtime: cloudfront.FunctionRuntime.JS_2_0,
              code: cloudfront.FunctionCode.fromInline(SPA_ROUTES),
            }),
          },
        ],
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.RestApiOrigin(restApi, { customHeaders: { [ORIGIN_VERIFY_HEADER]: originVerifyValue } }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: new cloudfront.OriginRequestPolicy(this, 'ApiOriginRequestPolicy', {
            comment: 'Forward Content-Type so the API can check JSON bodies',
            headerBehavior: cloudfront.OriginRequestHeaderBehavior.allowList('Content-Type'),
            queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.allowList('nextToken'), // gallery paging
            cookieBehavior: cloudfront.OriginRequestCookieBehavior.none(),
          }),
          responseHeadersPolicy: securityHeaders,
        },
        // Generated GLB files and scripts, read from the private artifacts bucket through OAC.
        '/generations/*': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(artifacts),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
          responseHeadersPolicy: securityHeaders,
          compress: true,
        },
      },
    });
    const siteUrl = `https://${distribution.distributionDomainName}`;

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
    new CfnOutput(this, 'AgentRuntimeArn', { value: runtime.agentRuntimeArn });
    new CfnOutput(this, 'TableName', { value: table.tableName });
    new CfnOutput(this, 'ArtifactsBucketName', { value: artifacts.bucketName });
    new CfnOutput(this, 'WorkerFunctionName', { value: workerFn.functionName });
    new CfnOutput(this, 'ApiExecuteUrl', { value: restApi.url });

    addNagSuppressions(this, runnable);
  }

  private createWebAcl(allowedCidrs: string[]): wafv2.CfnWebACL {
    const visibility = (name: string) => ({ cloudWatchMetricsEnabled: true, metricName: name, sampledRequestsEnabled: true });
    const managed = (name: string, priority: number): wafv2.CfnWebACL.RuleProperty => ({
      name,
      priority,
      overrideAction: { none: {} },
      statement: { managedRuleGroupStatement: { vendorName: 'AWS', name } },
      visibilityConfig: visibility(name),
    });
    const rules: wafv2.CfnWebACL.RuleProperty[] = [
      managed('AWSManagedRulesAmazonIpReputationList', 10),
      managed('AWSManagedRulesCommonRuleSet', 20),
      managed('AWSManagedRulesKnownBadInputsRuleSet', 30),
      {
        // Each POST starts four paid model runs: at most 10 per IP in 5 minutes.
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
