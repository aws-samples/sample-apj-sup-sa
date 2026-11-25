import * as cdk from 'aws-cdk-lib';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { BedrockMeterConfig } from '../config';
import { lambdaCode } from '../lambda-asset';

export interface BedrockLoggingProps {
  config: BedrockMeterConfig;
}

export class BedrockLogging extends Construct {
  public readonly logGroup: logs.ILogGroup;
  public readonly archiveBucket: s3.IBucket;

  constructor(scope: Construct, id: string, props: BedrockLoggingProps) {
    super(scope, id);

    const { config } = props;

    this.logGroup = new logs.LogGroup(this, 'InvocationLogGroup', {
      logGroupName: config.logGroupName,
      retention: config.logRetentionDays as logs.RetentionDays,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.archiveBucket = new s3.Bucket(this, 'LogArchiveBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [{
        transitions: [{
          storageClass: s3.StorageClass.INFREQUENT_ACCESS,
          transitionAfter: cdk.Duration.days(30),
        }, {
          storageClass: s3.StorageClass.GLACIER,
          // The API permits 366-day Athena ranges. Glacier Flexible Retrieval objects are
          // skipped unless explicitly restored, so keep the whole queryable window online.
          transitionAfter: cdk.Duration.days(400),
        }],
      }],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const accountId = cdk.Stack.of(this).account;

    this.archiveBucket.addToResourcePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      principals: [new iam.ServicePrincipal('bedrock.amazonaws.com')],
      actions: ['s3:PutObject'],
      resources: [this.archiveBucket.arnForObjects('*')],
      conditions: {
        StringEquals: {
          'aws:SourceAccount': accountId,
        },
        ArnLike: {
          'aws:SourceArn': `arn:aws:bedrock:${cdk.Stack.of(this).region}:${accountId}:*`,
        },
      },
    }));

    const bedrockLoggingRole = new iam.Role(this, 'BedrockLoggingRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
        conditions: {
          StringEquals: {
            'aws:SourceAccount': accountId,
          },
          ArnLike: {
            'aws:SourceArn': `arn:aws:bedrock:${cdk.Stack.of(this).region}:${accountId}:*`,
          },
        },
      }),
    });
    bedrockLoggingRole.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);

    this.logGroup.grantWrite(bedrockLoggingRole);
    this.archiveBucket.grantPut(bedrockLoggingRole);

    // putModelInvocationLoggingConfiguration parameters를 한 번만 정의하고 onCreate/onUpdate 양쪽에서 재사용.
    // onUpdate가 없으면 textDataDeliveryEnabled 같은 setting 변경이 기존 stack에 반영되지 않는다.
    const loggingConfig = {
      cloudWatchConfig: {
        logGroupName: config.logGroupName,
        roleArn: bedrockLoggingRole.roleArn,
      },
      s3Config: {
        bucketName: this.archiveBucket.bucketName,
        keyPrefix: 'bedrock-logs/',
      },
      textDataDeliveryEnabled: true,
      imageDataDeliveryEnabled: false,
      embeddingDataDeliveryEnabled: false,
    };

    const configHandler = new lambda.Function(this, 'LoggingConfigHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambdaCode('bedrock-logging-config'),
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      logGroup: new logs.LogGroup(this, 'LoggingConfigHandlerLogGroup', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });
    configHandler.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock:PutModelInvocationLoggingConfiguration',
        'bedrock:DeleteModelInvocationLoggingConfiguration',
        'bedrock:GetModelInvocationLoggingConfiguration',
      ],
      resources: ['*'],
    }));
    configHandler.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'],
      resources: [bedrockLoggingRole.roleArn],
    }));

    const provider = new cr.Provider(this, 'LoggingConfigProvider', {
      onEventHandler: configHandler,
    });
    const resource = new cdk.CustomResource(this, 'LoggingConfig', {
      serviceToken: provider.serviceToken,
      properties: {
        LoggingConfig: loggingConfig,
        AllowOverwrite: config.allowBedrockLoggingOverwrite,
        DeleteOnRemoval: config.deleteBedrockLoggingOnStackRemoval,
      },
    });
    resource.node.addDependency(bedrockLoggingRole);

    // Migration shim for stacks deployed before the ownership-aware provider existed.
    // Keep the old logical ID, replace its mutating update with a read, and retain it so
    // CloudFormation never invokes the old DeleteModelInvocationLoggingConfiguration action
    // after the new resource has taken ownership.
    const legacyConfigShim = new cr.AwsCustomResource(this, 'PutLoggingConfig', {
      onCreate: {
        service: 'Bedrock',
        action: 'getModelInvocationLoggingConfiguration',
        physicalResourceId: cr.PhysicalResourceId.of('bedrock-logging-config-legacy-shim'),
      },
      onUpdate: {
        service: 'Bedrock',
        action: 'getModelInvocationLoggingConfiguration',
        physicalResourceId: cr.PhysicalResourceId.of('bedrock-logging-config-legacy-shim'),
      },
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: cr.AwsCustomResourcePolicy.ANY_RESOURCE,
      }),
    });
    (legacyConfigShim.node.findChild('Resource') as cdk.CustomResource)
      .applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    legacyConfigShim.node.addDependency(resource);
  }
}
