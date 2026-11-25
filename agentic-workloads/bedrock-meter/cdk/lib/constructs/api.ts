import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { Construct } from 'constructs';
import { Analytics } from './analytics';
import { lambdaCode } from '../lambda-asset';

export interface ApiProps {
  usageTable: dynamodb.ITable;
  configTable: dynamodb.ITable;
  auditTable: dynamodb.ITable;
  snsTopic: sns.ITopic;
  archiveBucket: s3.IBucket;
  analytics: Analytics;
  adminUsernames: string[];
  // IAM role names accepted as admin CALLERS (e.g. the console BFF execution role), matched on
  // role name. Enforcement targets stay IAM-user-only.
  adminRoleNames: string[];
  apiName: string;
  usageRetentionDays: number;
}

export class Api extends Construct {
  public readonly api: apigwv2.HttpApi;
  public readonly fn: lambda.IFunction;

  constructor(scope: Construct, id: string, props: ApiProps) {
    super(scope, id);

    const fn = new lambda.Function(this, 'Handler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambdaCode('api-handler'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      tracing: lambda.Tracing.ACTIVE,
      logGroup: new logs.LogGroup(this, 'HandlerLogGroup', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        USAGE_TABLE: props.usageTable.tableName,
        CONFIG_TABLE: props.configTable.tableName,
        AUDIT_TABLE: props.auditTable.tableName,
        SNS_TOPIC_ARN: props.snsTopic.topicArn,
        ATHENA_DATABASE: props.analytics.databaseName,
        ATHENA_TABLE: props.analytics.tableName,
        ATHENA_WORKGROUP: props.analytics.workgroupName,
        ACCOUNT_ID: cdk.Stack.of(this).account,
        ADMIN_USERNAMES: props.adminUsernames.join(','),
        ADMIN_ROLE_NAMES: props.adminRoleNames.join(','),
        USAGE_RETENTION_DAYS: String(props.usageRetentionDays),
      },
    });
    this.fn = fn;

    props.usageTable.grantReadWriteData(fn);
    props.configTable.grantReadWriteData(fn);
    props.auditTable.grantReadData(fn);
    props.snsTopic.grantPublish(fn);
    props.analytics.grantQuery(fn, props.archiveBucket);

    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iam:DeleteUserPolicy', 'iam:GetUserPolicy', 'iam:PutUserPolicy', 'iam:ListUserPolicies'],
      resources: [
        cdk.Arn.format({ service: 'iam', resource: 'user', resourceName: '*', region: '' }, cdk.Stack.of(this)),
      ],
    }));

    const accessLogs = new logs.LogGroup(this, 'AccessLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.api = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: props.apiName,
      defaultAuthorizer: new authorizers.HttpIamAuthorizer(),
    });

    const stage = this.api.defaultStage?.node.defaultChild as apigwv2.CfnStage;
    stage.accessLogSettings = {
      destinationArn: accessLogs.logGroupArn,
      format: JSON.stringify({
        requestId: '$context.requestId',
        ip: '$context.identity.sourceIp',
        userArn: '$context.identity.userArn',
        method: '$context.httpMethod',
        path: '$context.path',
        status: '$context.status',
        latency: '$context.responseLatency',
      }),
    };
    stage.defaultRouteSettings = {
      throttlingRateLimit: 20,
      throttlingBurstLimit: 40,
    };

    const integration = new integrations.HttpLambdaIntegration('LambdaIntegration', fn);

    const routes: { method: apigwv2.HttpMethod; path: string }[] = [
      { method: apigwv2.HttpMethod.GET, path: '/usage/me' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/me/history' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/top' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/by-model' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/by-model/trend' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/aggregate' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/history/all' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/{username}' },
      { method: apigwv2.HttpMethod.GET, path: '/usage/{username}/history' },
      { method: apigwv2.HttpMethod.GET, path: '/users' },
      { method: apigwv2.HttpMethod.GET, path: '/users/{username}' },
      { method: apigwv2.HttpMethod.GET, path: '/users/{username}/history' },
      { method: apigwv2.HttpMethod.GET, path: '/limits/me' },
      { method: apigwv2.HttpMethod.GET, path: '/limits/{username}' },
      { method: apigwv2.HttpMethod.PUT, path: '/limits/{username}' },
      { method: apigwv2.HttpMethod.DELETE, path: '/limits/{username}' },
      { method: apigwv2.HttpMethod.GET, path: '/admin/settings/quota' },
      { method: apigwv2.HttpMethod.PUT, path: '/admin/settings/quota' },
      { method: apigwv2.HttpMethod.GET, path: '/admin/blocked' },
      { method: apigwv2.HttpMethod.GET, path: '/admin/audit' },
      { method: apigwv2.HttpMethod.POST, path: '/admin/unblock/{username}' },
    ];
    for (const r of routes) {
      this.api.addRoutes({ path: r.path, methods: [r.method], integration });
    }
  }
}
