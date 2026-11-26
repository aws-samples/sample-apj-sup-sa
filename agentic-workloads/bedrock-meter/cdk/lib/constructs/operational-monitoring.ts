import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwactions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as events from 'aws-cdk-lib/aws-events';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';

export interface MonitoredFunction {
  name: string;
  fn: lambda.IFunction;
}

export interface MonitoredRule {
  name: string;
  rule: events.IRule;
}

export interface OperationalMonitoringProps {
  functions: MonitoredFunction[];
  rules: MonitoredRule[];
  snsTopic: sns.ITopic;
}

export class OperationalMonitoring extends Construct {
  constructor(scope: Construct, id: string, props: OperationalMonitoringProps) {
    super(scope, id);

    const notify = new cwactions.SnsAction(props.snsTopic);

    for (const monitored of props.functions) {
      const errors = new cloudwatch.Alarm(this, `${monitored.name}Errors`, {
        metric: monitored.fn.metricErrors({
          period: cdk.Duration.minutes(5),
          statistic: 'Sum',
        }),
        threshold: 1,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        alarmDescription: `${monitored.name} Lambda reported an error`,
      });
      errors.addAlarmAction(notify);

      const throttles = new cloudwatch.Alarm(this, `${monitored.name}Throttles`, {
        metric: monitored.fn.metricThrottles({
          period: cdk.Duration.minutes(5),
          statistic: 'Sum',
        }),
        threshold: 1,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        alarmDescription: `${monitored.name} Lambda was throttled`,
      });
      throttles.addAlarmAction(notify);
    }

    for (const monitored of props.rules) {
      const failedInvocations = new cloudwatch.Alarm(this, `${monitored.name}FailedInvocations`, {
        metric: new cloudwatch.Metric({
          namespace: 'AWS/Events',
          metricName: 'FailedInvocations',
          dimensionsMap: { RuleName: monitored.rule.ruleName },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
        }),
        threshold: 1,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        alarmDescription: `${monitored.name} EventBridge rule failed to invoke its target`,
      });
      failedInvocations.addAlarmAction(notify);
    }

    for (const destination of ['CloudWatch', 'S3']) {
      const deliveryFailures = new cloudwatch.Alarm(this, `Bedrock${destination}DeliveryFailures`, {
        metric: new cloudwatch.Metric({
          namespace: 'AWS/Bedrock',
          metricName: `ModelInvocationLogs${destination}DeliveryFailure`,
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
        }),
        threshold: 1,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        alarmDescription: `Bedrock model invocation log delivery to ${destination} failed`,
      });
      deliveryFailures.addAlarmAction(notify);
    }
  }
}
