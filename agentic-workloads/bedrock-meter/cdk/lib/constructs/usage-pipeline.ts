import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as destinations from 'aws-cdk-lib/aws-logs-destinations';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { BedrockMeterConfig } from '../config';
import { lambdaCode } from '../lambda-asset';

export interface UsagePipelineProps {
  logGroup: logs.ILogGroup;
  snsTopic: sns.ITopic;
  config: BedrockMeterConfig;
}

export class UsagePipeline extends Construct {
  public readonly usageTable: dynamodb.ITable;
  public readonly configTable: dynamodb.ITable;
  public readonly dedupTable: dynamodb.ITable;
  public readonly aggregatorFn: lambda.IFunction;

  constructor(scope: Construct, id: string, props: UsagePipelineProps) {
    super(scope, id);

    const { logGroup, snsTopic, config } = props;

    const usageTable = new dynamodb.Table(this, 'UsageTable', {
      tableName: config.usageTableName,
      partitionKey: { name: 'UserARN', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expiresAt',
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.usageTable = usageTable;

    // At-least-once Subscription Filter dedup claims (BATCH# rows) live here, not in
    // UsageTable — they were ~90% of UsageTable rows and bloated every enforcement/
    // console scan. Isolating them keeps UsageTable to DAY+STATE so scans stay cheap
    // at 1000+ users. No tableName: generated name avoids the in-place-replacement trap.
    const dedupTable = new dynamodb.Table(this, 'DedupTable', {
      partitionKey: { name: 'UserARN', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expiresAt',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.dedupTable = dedupTable;

    const configTable = new dynamodb.Table(this, 'ConfigTable', {
      tableName: config.configTableName,
      partitionKey: { name: 'ConfigKey', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.configTable = configTable;

    const defaultLimitItem = {
      dailyTokenLimit: { N: String(config.defaultDailyCreditLimit) },
      weeklyTokenLimit: { N: String(config.defaultWeeklyCreditLimit) },
      monthlyTokenLimit: { N: String(config.defaultMonthlyCreditLimit) },
    };

    new cr.AwsCustomResource(this, 'ConfigDefaultSeed', {
      onCreate: {
        service: 'DynamoDB',
        action: 'putItem',
        parameters: {
          TableName: config.configTableName,
          Item: {
            ConfigKey: { S: 'default' },
            ...defaultLimitItem,
            warningThresholdPct: { N: '80' },
            quotaTimeZone: { S: config.initialQuotaTimeZone },
            quotaTimeZoneVersion: { N: '1' },
            // Credit weights: limits are compared against cost-weighted credits,
            // not raw token sums. raw input = 1 credit (fixed); these tune the rest.
            costWeightCacheRead: { N: '0.1' },
            costWeightCacheWrite: { N: '1.25' },
            costWeightOutput: { N: '5' },
          },
          ConditionExpression: 'attribute_not_exists(ConfigKey)',
        },
        physicalResourceId: cr.PhysicalResourceId.of('config-default-seed'),
        ignoreErrorCodesMatching: 'ConditionalCheckFailedException',
      },
      onUpdate: {
        service: 'DynamoDB',
        action: 'updateItem',
        parameters: {
          TableName: config.configTableName,
          Key: { ConfigKey: { S: 'default' } },
          UpdateExpression: [
            'SET dailyTokenLimit = :daily',
            'weeklyTokenLimit = :weekly',
            'monthlyTokenLimit = :monthly',
          ].join(', '),
          ConditionExpression: [
            'dailyTokenLimit = :oldDaily',
            'weeklyTokenLimit = :oldWeekly',
            'monthlyTokenLimit = :oldMonthly',
          ].join(' AND '),
          ExpressionAttributeValues: {
            ':daily': defaultLimitItem.dailyTokenLimit,
            ':weekly': defaultLimitItem.weeklyTokenLimit,
            ':monthly': defaultLimitItem.monthlyTokenLimit,
            ':oldDaily': { N: '1000000' },
            ':oldWeekly': { N: '5000000' },
            ':oldMonthly': { N: '15000000' },
          },
        },
        physicalResourceId: cr.PhysicalResourceId.of('config-default-seed'),
        ignoreErrorCodesMatching: 'ConditionalCheckFailedException',
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['dynamodb:PutItem', 'dynamodb:UpdateItem'],
          resources: [configTable.tableArn],
        }),
      ]),
    }).node.addDependency(configTable);

    const aggregatorFn = new lambda.Function(this, 'UsageAggregator', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambdaCode('usage-aggregator'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      tracing: lambda.Tracing.ACTIVE,
      logGroup: new logs.LogGroup(this, 'UsageAggregatorLogGroup', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        USAGE_TABLE: config.usageTableName,
        CONFIG_TABLE: config.configTableName,
        DEDUP_TABLE: dedupTable.tableName,
        SNS_TOPIC_ARN: snsTopic.topicArn,
        METRIC_NAMESPACE: config.metricNamespace,
        ROLE_ENFORCEMENT_ENABLED: String(config.roleEnforcementEnabled),
        DDB_TRANSACTION_MAX_ATTEMPTS: '5',
        DDB_TRANSACTION_BASE_BACKOFF_MS: '50',
        DDB_TRANSACTION_MAX_BACKOFF_MS: '1000',
        USAGE_TTL_DAYS: String(config.usageRetentionDays),
        DEFAULT_DAILY_CREDIT_LIMIT: String(config.defaultDailyCreditLimit),
        DEFAULT_WEEKLY_CREDIT_LIMIT: String(config.defaultWeeklyCreditLimit),
        DEFAULT_MONTHLY_CREDIT_LIMIT: String(config.defaultMonthlyCreditLimit),
      },
    });
    this.aggregatorFn = aggregatorFn;

    usageTable.grantReadWriteData(aggregatorFn);
    configTable.grantReadData(aggregatorFn);
    // PutItem for the dedup claim + GetItem to read claimedAt (a duplicate redelivery re-checks
    // the threshold against the ORIGINAL ingestion day resolved from the claim).
    dedupTable.grantReadWriteData(aggregatorFn);
    snsTopic.grantPublish(aggregatorFn);

    // grantReadWriteData는 TransactWriteItems를 포함하지 않는다. claim(DedupTable)+DAY ADD(UsageTable)를
    // 한 cross-table 트랜잭션으로 묶기 위해 두 테이블 모두 명시 부여.
    aggregatorFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:TransactWriteItems'],
      resources: [usageTable.tableArn, dedupTable.tableArn],
    }));
    if (config.roleEnforcementEnabled) {
      aggregatorFn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['iam:PutRolePolicy', 'iam:GetRolePolicy'],
        resources: [
          cdk.Arn.format({ service: 'iam', resource: 'role', resourceName: '*', region: '' }, cdk.Stack.of(this)),
        ],
      }));
    }

    aggregatorFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: {
        StringEquals: { 'cloudwatch:namespace': config.metricNamespace },
      },
    }));

    aggregatorFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iam:PutUserPolicy', 'iam:GetUserPolicy'],
      resources: [
        cdk.Arn.format({ service: 'iam', resource: 'user', resourceName: '*', region: '' }, cdk.Stack.of(this)),
      ],
    }));

    new logs.SubscriptionFilter(this, 'SubscriptionFilter', {
      logGroup,
      destination: new destinations.LambdaDestination(aggregatorFn),
      // identity.arn 존재로 필터 — embedding처럼 output 없는 호출도 input 토큰 집계 대상.
      filterPattern: logs.FilterPattern.exists('$.identity.arn'),
    });
  }
}
