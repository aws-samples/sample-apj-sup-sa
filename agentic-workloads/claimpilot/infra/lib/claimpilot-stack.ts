import {
  Aws,
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
} from 'aws-cdk-lib';
import {
  AttributeType,
  BillingMode,
  ProjectionType,
  Table,
} from 'aws-cdk-lib/aws-dynamodb';
import {
  BlockPublicAccess,
  Bucket,
  BucketEncryption,
  CorsRule,
  HttpMethods,
  ObjectOwnership,
} from 'aws-cdk-lib/aws-s3';
import {
  AccountPrincipal,
  Effect,
  ManagedPolicy,
  PolicyStatement,
  Role,
} from 'aws-cdk-lib/aws-iam';
import {
  Architecture,
  Code,
  Function,
  Runtime,
} from 'aws-cdk-lib/aws-lambda';
import {
  LogGroup,
  RetentionDays,
} from 'aws-cdk-lib/aws-logs';
import {
  LambdaIntegration,
  RestApi,
} from 'aws-cdk-lib/aws-apigateway';
import {
  Secret,
} from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

interface ClaimPilotContext {
  tableName?: string;
  bucketNamePrefix?: string;
  allowedOrigins?: string[] | string;
  removalPolicy?: 'destroy' | 'retain';
}

function listContext(value: string[] | string | undefined, fallback: string[]) {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string') {
    return value
      .split(',')
      .map((origin) => origin.trim())
      .filter(Boolean);
  }
  return fallback;
}

function removalPolicyFromContext(value: ClaimPilotContext['removalPolicy']) {
  return value === 'retain' ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY;
}

export class ClaimPilotStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const context = (this.node.tryGetContext('claimpilot') ?? {}) as ClaimPilotContext;
    const removalPolicy = removalPolicyFromContext(context.removalPolicy);
    const autoDeleteObjects = removalPolicy === RemovalPolicy.DESTROY;
    const tableName = context.tableName ?? 'ClaimPilotClaims';
    const bucketNamePrefix = context.bucketNamePrefix ?? 'claimpilot-evidence';
    const allowedOrigins = listContext(context.allowedOrigins, [
      'http://localhost:5173',
      'http://127.0.0.1:5173',
    ]);

    const claimsTable = new Table(this, 'ClaimsTable', {
      tableName,
      partitionKey: {
        name: 'claimId',
        type: AttributeType.STRING,
      },
      billingMode: BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      removalPolicy,
    });

    claimsTable.addGlobalSecondaryIndex({
      indexName: 'status-updatedAt-index',
      partitionKey: {
        name: 'status',
        type: AttributeType.STRING,
      },
      sortKey: {
        name: 'updatedAt',
        type: AttributeType.STRING,
      },
      projectionType: ProjectionType.ALL,
    });

    const corsRules: CorsRule[] = [
      {
        allowedOrigins,
        allowedMethods: [HttpMethods.PUT, HttpMethods.GET, HttpMethods.HEAD],
        allowedHeaders: ['*'],
        exposedHeaders: ['ETag'],
        maxAge: Duration.hours(1).toSeconds(),
      },
    ];

    const evidenceBucket = new Bucket(this, 'EvidenceBucket', {
      bucketName: `${bucketNamePrefix}-${Aws.ACCOUNT_ID}-${Aws.REGION}`,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
      encryption: BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: ObjectOwnership.BUCKET_OWNER_ENFORCED,
      cors: corsRules,
      removalPolicy,
      autoDeleteObjects,
    });

    const runtimeLogGroup = new LogGroup(this, 'RuntimeLogGroup', {
      logGroupName: '/claimpilot/runtime',
      retention: RetentionDays.ONE_WEEK,
      removalPolicy,
    });

    const runtimePolicy = new ManagedPolicy(this, 'RuntimePolicy', {
      managedPolicyName: `ClaimPilotRuntimePolicy-${Aws.REGION}`,
      description: 'Permissions used by the local ClaimPilot Pipecat bot runtime.',
      statements: [
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: [
            'dynamodb:GetItem',
            'dynamodb:PutItem',
            'dynamodb:UpdateItem',
            'dynamodb:Query',
          ],
          resources: [
            claimsTable.tableArn,
            `${claimsTable.tableArn}/index/*`,
          ],
        }),
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: [
            's3:GetObject',
            's3:PutObject',
          ],
          resources: [evidenceBucket.arnForObjects('*')],
        }),
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: ['s3:ListBucket'],
          resources: [evidenceBucket.bucketArn],
        }),
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: [
            'bedrock:InvokeModel',
            'bedrock:InvokeModelWithResponseStream',
          ],
          resources: ['*'],
        }),
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: [
            'logs:CreateLogGroup',
            'logs:CreateLogStream',
            'logs:DescribeLogStreams',
            'logs:PutLogEvents',
          ],
          resources: ['*'],
        }),
      ],
    });

    const runtimeRole = new Role(this, 'RuntimeRole', {
      assumedBy: new AccountPrincipal(Aws.ACCOUNT_ID),
      description: 'Optional role for running the ClaimPilot bot locally or in a hosted runtime.',
      managedPolicies: [runtimePolicy],
      maxSessionDuration: Duration.hours(8),
    });

    const runtimeApiKey = new Secret(this, 'RuntimeApiKey', {
      description: 'Shared secret used by the Pipecat Cloud bot to call the ClaimPilot AWS runtime API.',
      generateSecretString: {
        excludePunctuation: true,
        passwordLength: 40,
      },
    });

    const runtimeApiHandler = new Function(this, 'RuntimeApiHandler', {
      runtime: Runtime.PYTHON_3_12,
      architecture: Architecture.ARM_64,
      handler: 'index.handler',
      code: Code.fromAsset('lambda/claimpilot-runtime-api'),
      timeout: Duration.seconds(30),
      memorySize: 512,
      environment: {
        CLAIMPILOT_CLAIMS_TABLE_NAME: claimsTable.tableName,
        CLAIMPILOT_EVIDENCE_BUCKET_NAME: evidenceBucket.bucketName,
        CLAIMPILOT_FINAL_PACKET_BUCKET_NAME: evidenceBucket.bucketName,
        CLAIMPILOT_EVIDENCE_ANALYSIS_MODEL:
          'global.anthropic.claude-haiku-4-5-20251001-v1:0',
        CLAIMPILOT_RUNTIME_API_KEY_SECRET_NAME: runtimeApiKey.secretName,
        CLAIMPILOT_PRESIGNED_UPLOAD_EXPIRES_SECONDS: '900',
      },
    });

    runtimeApiKey.grantRead(runtimeApiHandler);
    runtimeApiHandler.addToRolePolicy(new PolicyStatement({
      effect: Effect.ALLOW,
      actions: ['dynamodb:PutItem'],
      resources: [claimsTable.tableArn],
    }));
    runtimeApiHandler.addToRolePolicy(new PolicyStatement({
      effect: Effect.ALLOW,
      actions: [
        's3:GetObject',
        's3:PutObject',
      ],
      resources: [evidenceBucket.arnForObjects('claims/*')],
    }));
    runtimeApiHandler.addToRolePolicy(new PolicyStatement({
      effect: Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
      ],
      resources: ['*'],
    }));

    const runtimeApi = new RestApi(this, 'RuntimeApi', {
      restApiName: 'ClaimPilotRuntimeApi',
      description: 'AWS operations proxy for the Pipecat Cloud-hosted ClaimPilot bot.',
      deployOptions: {
        stageName: 'prod',
      },
      defaultCorsPreflightOptions: {
        allowHeaders: ['content-type', 'x-claimpilot-api-key'],
        allowMethods: ['POST', 'OPTIONS'],
        allowOrigins: ['*'],
      },
    });
    runtimeApi.root.addResource('runtime').addMethod(
      'POST',
      new LambdaIntegration(runtimeApiHandler)
    );

    const serverEnv = [
      `AWS_REGION=${Aws.REGION}`,
      `CLAIMPILOT_CLAIMS_TABLE_NAME=${claimsTable.tableName}`,
      `CLAIMPILOT_EVIDENCE_BUCKET_NAME=${evidenceBucket.bucketName}`,
      `CLAIMPILOT_FINAL_PACKET_BUCKET_NAME=${evidenceBucket.bucketName}`,
      'CLAIMPILOT_EVIDENCE_ANALYSIS_MODE=auto',
      `CLAIMPILOT_CLOUDWATCH_LOG_GROUP_NAME=${runtimeLogGroup.logGroupName}`,
      'CLAIMPILOT_CLOUDWATCH_LOG_LEVEL=INFO',
      `CLAIMPILOT_AWS_RUNTIME_API_URL=${runtimeApi.url}runtime`,
      `CLAIMPILOT_AWS_RUNTIME_API_KEY_SECRET_NAME=${runtimeApiKey.secretName}`,
    ].join('\n');

    new CfnOutput(this, 'ClaimsTableName', {
      value: claimsTable.tableName,
      description: 'DynamoDB table used for draft and submitted claim state.',
    });
    new CfnOutput(this, 'EvidenceBucketName', {
      value: evidenceBucket.bucketName,
      description: 'S3 bucket used for uploaded evidence and final packet JSON.',
    });
    new CfnOutput(this, 'RuntimePolicyArn', {
      value: runtimePolicy.managedPolicyArn,
      description: 'Attach this policy to the IAM principal that runs server/bot.py.',
    });
    new CfnOutput(this, 'RuntimeLogGroupName', {
      value: runtimeLogGroup.logGroupName,
      description: 'CloudWatch Logs group for ClaimPilot bot runtime diagnostics.',
    });
    new CfnOutput(this, 'RuntimeRoleArn', {
      value: runtimeRole.roleArn,
      description: 'Optional role ARN for running the ClaimPilot bot runtime.',
    });
    new CfnOutput(this, 'RuntimeApiUrl', {
      value: `${runtimeApi.url}runtime`,
      description: 'Set CLAIMPILOT_AWS_RUNTIME_API_URL to this value for Pipecat Cloud.',
    });
    new CfnOutput(this, 'RuntimeApiKeySecretName', {
      value: runtimeApiKey.secretName,
      description: 'Retrieve this secret value and store it as CLAIMPILOT_AWS_RUNTIME_API_KEY in Pipecat Cloud.',
    });
    new CfnOutput(this, 'ServerEnv', {
      value: serverEnv,
      description: 'Copy these values into server/.env.',
    });
  }
}
