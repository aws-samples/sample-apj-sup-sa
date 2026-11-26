import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { BedrockMeterConfig } from '../config';
import { lambdaCode } from '../lambda-asset';

export interface AuditProps {
  snsTopic: sns.ITopic;
  config: BedrockMeterConfig;
}

/**
 * Persistent audit log. Every governance event already converges on a single
 * SNS publish (common/enforcement.publish_alert), so one SNS-subscribed writer
 * captures admin actions (LIMIT_CHANGED/REMOVED, QUOTA_TIMEZONE_CHANGED, MANUAL_UNBLOCK) and system
 * decisions (BLOCKED, WARNING, RESOLVED) without touching any emitter.
 */
export class Audit extends Construct {
  public readonly auditTable: dynamodb.ITable;
  public readonly writerFn: lambda.IFunction;

  constructor(scope: Construct, id: string, props: AuditProps) {
    super(scope, id);

    const { snsTopic, config } = props;

    // PK=AUDIT#yyyy-mm spreads writes across monthly partitions; SK=timestamp#eventId
    // sorts newest-last within a month so a descending query yields recent-first.
    const auditTable = new dynamodb.Table(this, 'AuditTable', {
      tableName: config.auditTableName,
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expiresAt',
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.auditTable = auditTable;

    const writerFn = new lambda.Function(this, 'AuditWriter', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambdaCode('audit-writer'),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      tracing: lambda.Tracing.ACTIVE,
      logGroup: new logs.LogGroup(this, 'AuditWriterLogGroup', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        AUDIT_TABLE: config.auditTableName,
        AUDIT_TTL_DAYS: String(config.auditRetentionDays),
      },
    });
    this.writerFn = writerFn;

    auditTable.grantWriteData(writerFn);
    snsTopic.addSubscription(new subscriptions.LambdaSubscription(writerFn));
  }
}
