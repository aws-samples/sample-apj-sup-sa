import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';
import { lambdaCode } from '../lambda-asset';

export interface PeriodicCheckProps {
  usageTable: dynamodb.ITable;
  configTable: dynamodb.ITable;
  snsTopic: sns.ITopic;
  metricNamespace: string;
  defaultDailyCreditLimit: number;
  defaultWeeklyCreditLimit: number;
  defaultMonthlyCreditLimit: number;
  roleEnforcementEnabled?: boolean;
  scheduleMinutes?: number;
}

export class PeriodicCheck extends Construct {
  public readonly fn: lambda.IFunction;
  public readonly rule: events.IRule;

  constructor(scope: Construct, id: string, props: PeriodicCheckProps) {
    super(scope, id);

    const { usageTable, configTable, snsTopic, metricNamespace } = props;
    const scheduleMinutes = props.scheduleMinutes ?? 15;

    const fn = new lambda.Function(this, 'Handler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambdaCode('periodic-quota-check'),
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      tracing: lambda.Tracing.ACTIVE,
      logGroup: new logs.LogGroup(this, 'PeriodicCheckLogGroup', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        USAGE_TABLE: usageTable.tableName,
        CONFIG_TABLE: configTable.tableName,
        SNS_TOPIC_ARN: snsTopic.topicArn,
        METRIC_NAMESPACE: metricNamespace,
        ROLE_ENFORCEMENT_ENABLED: String(props.roleEnforcementEnabled ?? false),
        DEFAULT_DAILY_CREDIT_LIMIT: String(props.defaultDailyCreditLimit),
        DEFAULT_WEEKLY_CREDIT_LIMIT: String(props.defaultWeeklyCreditLimit),
        DEFAULT_MONTHLY_CREDIT_LIMIT: String(props.defaultMonthlyCreditLimit),
      },
    });
    this.fn = fn;

    // Enforcement derives week/month by scanning DAY rows in DynamoDB — no Athena, no rollup.
    usageTable.grantReadWriteData(fn);
    configTable.grantReadData(fn);
    snsTopic.grantPublish(fn);

    // PutUserPolicy blocks; DeleteUserPolicy is needed for the auto-unblock re-eval pass.
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['iam:PutUserPolicy', 'iam:GetUserPolicy', 'iam:DeleteUserPolicy'],
      resources: [
        cdk.Arn.format({ service: 'iam', resource: 'user', resourceName: '*', region: '' }, cdk.Stack.of(this)),
      ],
    }));
    if (props.roleEnforcementEnabled) {
      fn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['iam:PutRolePolicy', 'iam:GetRolePolicy', 'iam:DeleteRolePolicy'],
        resources: [
          cdk.Arn.format({ service: 'iam', resource: 'role', resourceName: '*', region: '' }, cdk.Stack.of(this)),
        ],
      }));
    }

    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: {
        StringEquals: { 'cloudwatch:namespace': metricNamespace },
      },
    }));

    this.rule = new events.Rule(this, 'Schedule', {
      schedule: events.Schedule.rate(cdk.Duration.minutes(scheduleMinutes)),
      targets: [new targets.LambdaFunction(fn)],
    });
  }
}
