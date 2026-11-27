import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwactions from 'aws-cdk-lib/aws-cloudwatch-actions';
import { Construct } from 'constructs';
import { BedrockMeterConfig, DEFAULT_CONFIG } from './config';
import { BedrockLogging } from './constructs/bedrock-logging';
import { Notification } from './constructs/notification';
import { UsagePipeline } from './constructs/usage-pipeline';
import { Dashboard } from './constructs/dashboard';
import { Analytics } from './constructs/analytics';
import { PeriodicCheck } from './constructs/periodic-check';
import { Api } from './constructs/api';
import { Audit } from './constructs/audit';
import { ConsoleAuth } from './constructs/console-auth';
import { ConsoleHosting } from './constructs/console-hosting';
import { OperationalMonitoring } from './constructs/operational-monitoring';
import { RegionGuardrail } from './constructs/region-guardrail';

export interface BedrockMeterCoreStackProps extends cdk.StackProps {
  meterConfig?: Partial<BedrockMeterConfig>;
}

export class BedrockMeterCoreStack extends cdk.Stack {
  public readonly apiEndpoint: string;
  public readonly apiInvokeArn: string;

  constructor(scope: Construct, id: string, props?: BedrockMeterCoreStackProps) {
    super(scope, id, props);

    const config: BedrockMeterConfig = {
      ...DEFAULT_CONFIG,
      ...props?.meterConfig,
    };
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: config.initialQuotaTimeZone }).format();
    } catch {
      throw new Error(
        `Invalid initialQuotaTimeZone (${config.initialQuotaTimeZone}). ` +
        'Use an IANA time zone such as Asia/Seoul or UTC.',
      );
    }
    if (config.usageRetentionDays < 31) {
      throw new Error('usageRetentionDays must be at least 31 days for calendar-month enforcement.');
    }
    if (config.auditRetentionDays < 1) {
      throw new Error('auditRetentionDays must be at least 1 day.');
    }
    if (
      config.defaultDailyCreditLimit < 1
      || config.defaultWeeklyCreditLimit < config.defaultDailyCreditLimit
      || config.defaultMonthlyCreditLimit < config.defaultWeeklyCreditLimit
    ) {
      throw new Error('Default credit limits must satisfy monthly >= weekly >= daily > 0.');
    }

    const regionGuardrail = config.createRegionGuardrailPolicy
      ? new RegionGuardrail(this, 'RegionGuardrail', { bedrockRegion: config.bedrockRegion })
      : undefined;

    if (this.region !== config.bedrockRegion) {
      throw new Error(
        `Stack region (${this.region}) does not match config.bedrockRegion (${config.bedrockRegion}). ` +
        `Bedrock invocation logging is regional; deploy this stack into the same region as Bedrock usage.`,
      );
    }

    const logging = new BedrockLogging(this, 'BedrockLogging', { config });

    const analytics = new Analytics(this, 'Analytics', {
      archiveBucket: logging.archiveBucket,
      archiveKeyPrefix: 'bedrock-logs/',
      databaseName: config.analyticsDatabaseName,
      tableName: config.analyticsTableName,
      workgroupName: config.athenaWorkgroupName,
    });

    const notification = new Notification(this, 'Notification', { config });

    const audit = new Audit(this, 'Audit', {
      snsTopic: notification.topic,
      config,
    });

    const pipeline = new UsagePipeline(this, 'UsagePipeline', {
      logGroup: logging.logGroup,
      snsTopic: notification.topic,
      config,
    });

    const periodicCheck = new PeriodicCheck(this, 'PeriodicCheck', {
      usageTable: pipeline.usageTable,
      configTable: pipeline.configTable,
      snsTopic: notification.topic,
      metricNamespace: config.metricNamespace,
      defaultDailyCreditLimit: config.defaultDailyCreditLimit,
      defaultWeeklyCreditLimit: config.defaultWeeklyCreditLimit,
      defaultMonthlyCreditLimit: config.defaultMonthlyCreditLimit,
      roleEnforcementEnabled: config.roleEnforcementEnabled,
      scheduleMinutes: 5,
    });

    const api = new Api(this, 'Api', {
      usageTable: pipeline.usageTable,
      configTable: pipeline.configTable,
      auditTable: audit.auditTable,
      snsTopic: notification.topic,
      archiveBucket: logging.archiveBucket,
      analytics,
      adminUsernames: config.adminUsernames,
      adminRoleNames: [config.consoleBffRoleName],
      apiName: config.coreApiName,
      usageRetentionDays: config.usageRetentionDays,
    });

    this.apiEndpoint = api.api.apiEndpoint;
    this.apiInvokeArn = cdk.Arn.format({
      service: 'execute-api',
      resource: api.api.apiId,
      resourceName: '*/*/*',
    }, this);

    // --- Console: Cognito write-auth + single BFF Lambda (static UI + API) behind a
    //     public HTTP API. No public S3 / Function URL (account guardrails block both). ---
    const consoleAuth = new ConsoleAuth(this, 'ConsoleAuth', {
      userPoolName: config.consoleUserPoolName,
      adminGroupName: config.consoleAdminGroup,
      viewerGroupName: config.consoleViewerGroup,
    });

    const consoleHosting = new ConsoleHosting(this, 'ConsoleHosting', {
      coreApiEndpoint: this.apiEndpoint,
      coreApiArn: this.apiInvokeArn,
      userPool: consoleAuth.userPool,
      appClient: consoleAuth.appClient,
      bffRoleName: config.consoleBffRoleName,
      region: this.region,
      apiName: config.consoleApiName,
      allowedUsers: config.consoleAllowedUsers,
      adminGroup: config.consoleAdminGroup,
      viewerGroup: config.consoleViewerGroup,
      requireAuthForReads: config.consoleRequireAuthForReads,
    });

    new OperationalMonitoring(this, 'OperationalMonitoring', {
      functions: [
        { name: 'UsageAggregator', fn: pipeline.aggregatorFn },
        { name: 'PeriodicCheck', fn: periodicCheck.fn },
        { name: 'CoreApi', fn: api.fn },
        { name: 'AuditWriter', fn: audit.writerFn },
        { name: 'ConsoleBff', fn: consoleHosting.bffFn },
      ],
      rules: [
        { name: 'PeriodicCheck', rule: periodicCheck.rule },
      ],
      snsTopic: notification.topic,
    });

    const athenaScanAlarm = new cloudwatch.Alarm(this, 'AthenaScanAlarm', {
      metric: new cloudwatch.Metric({
        namespace: 'AWS/Athena',
        metricName: 'ProcessedBytes',
        dimensionsMap: { WorkGroup: analytics.workgroupName },
        statistic: 'Sum',
        period: cdk.Duration.hours(1),
      }),
      threshold: 2 * 1024 * 1024 * 1024,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: 'Athena scanned >2GB in 1h on Bedrock Meter workgroup',
    });
    athenaScanAlarm.addAlarmAction(new cwactions.SnsAction(notification.topic));

    if (config.enableDashboard) {
      new Dashboard(this, 'Dashboard', {
        logGroup: logging.logGroup,
        config,
      });
    }

    new cdk.CfnOutput(this, 'CoreApiEndpoint', {
      value: this.apiEndpoint,
    });
    new cdk.CfnOutput(this, 'CoreApiInvokeArn', {
      value: this.apiInvokeArn,
    });
    new cdk.CfnOutput(this, 'UsageTableName', {
      value: pipeline.usageTable.tableName,
    });
    new cdk.CfnOutput(this, 'ConfigTableName', {
      value: pipeline.configTable.tableName,
    });
    new cdk.CfnOutput(this, 'AuditTableName', {
      value: audit.auditTable.tableName,
    });
    new cdk.CfnOutput(this, 'UsageRetentionDays', {
      value: String(config.usageRetentionDays),
    });
    new cdk.CfnOutput(this, 'AuditRetentionDays', {
      value: String(config.auditRetentionDays),
    });
    new cdk.CfnOutput(this, 'SnsTopicArn', {
      value: notification.topic.topicArn,
    });
    new cdk.CfnOutput(this, 'AthenaWorkgroup', {
      value: analytics.workgroupName,
    });
    new cdk.CfnOutput(this, 'GlueDatabase', {
      value: analytics.databaseName,
    });
    new cdk.CfnOutput(this, 'GlueTable', {
      value: analytics.tableName,
    });
    new cdk.CfnOutput(this, 'ConsoleUrl', {
      value: consoleHosting.consoleUrl,
    });
    new cdk.CfnOutput(this, 'ConsoleUserPoolId', {
      value: consoleAuth.userPool.userPoolId,
    });
    new cdk.CfnOutput(this, 'ConsoleAppClientId', {
      value: consoleAuth.appClient.userPoolClientId,
    });
    if (regionGuardrail) {
      new cdk.CfnOutput(this, 'BedrockRegionGuardrailPolicyArn', {
        value: regionGuardrail.policy.managedPolicyArn,
        description: 'Attach explicitly to principals that must not invoke Bedrock outside the metered region',
      });
    }
  }
}
