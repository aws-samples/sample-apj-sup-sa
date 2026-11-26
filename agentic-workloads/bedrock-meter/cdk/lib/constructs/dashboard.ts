import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { BedrockMeterConfig } from '../config';

export interface DashboardProps {
  logGroup: logs.ILogGroup;
  config: BedrockMeterConfig;
}

export class Dashboard extends Construct {
  constructor(scope: Construct, id: string, props: DashboardProps) {
    super(scope, id);

    const { logGroup, config } = props;
    const logGroupName = config.logGroupName;
    const metricNamespace = config.metricNamespace;

    const dashboard = new cloudwatch.Dashboard(this, 'MeterDashboard', {
      dashboardName: config.dashboardName,
    });

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Token Usage Trend (1min)',
        left: [
          new cloudwatch.Metric({ namespace: metricNamespace, metricName: 'InputTokens', statistic: 'Sum', period: cdk.Duration.minutes(1) }),
          new cloudwatch.Metric({ namespace: metricNamespace, metricName: 'OutputTokens', statistic: 'Sum', period: cdk.Duration.minutes(1) }),
        ],
        width: 8,
        height: 6,
        view: cloudwatch.GraphWidgetView.TIME_SERIES,
        setPeriodToTimeRange: false,
      }),
      new cloudwatch.LogQueryWidget({
        title: 'Token Usage Trend (1h)',
        logGroupNames: [logGroupName],
        queryLines: [
          'filter ispresent(input.inputTokenCount)',
          // input_tokens 는 enforcement-aligned (raw + cache read + cache write)
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0)) as input_tokens, sum(coalesce(output.outputTokenCount,0)) as output_tokens by bin(1h)',
        ],
        width: 8,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.LINE,
      }),
      new cloudwatch.LogQueryWidget({
        title: 'Today Total Tokens',
        logGroupNames: [logGroupName],
        queryLines: [
          'filter ispresent(input.inputTokenCount)',
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0) + coalesce(output.outputTokenCount,0)) as total_tokens',
        ],
        width: 4,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.TABLE,
      }),
      new cloudwatch.LogQueryWidget({
        title: 'Today Total Invocations',
        logGroupNames: [logGroupName],
        queryLines: [
          'filter ispresent(input.inputTokenCount)',
          'stats count(*) as invocations',
        ],
        width: 4,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.TABLE,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.LogQueryWidget({
        title: 'Per-User Token Usage (1min)',
        logGroupNames: [logGroupName],
        queryLines: [
          'fields @timestamp, identity.arn, input.inputTokenCount, input.cacheReadInputTokenCount, input.cacheWriteInputTokenCount, output.outputTokenCount',
          'filter ispresent(input.inputTokenCount)',
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0) + coalesce(output.outputTokenCount,0)) as tokens by bin(1m), identity.arn',
        ],
        width: 24,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.LINE,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Per-Model Input Tokens (1min)',
        left: [
          new cloudwatch.MathExpression({
            expression: `SEARCH('{${metricNamespace},ModelId} MetricName="InputTokens"', 'Sum', 60)`,
            label: '',
          }),
        ],
        width: 8,
        height: 6,
        view: cloudwatch.GraphWidgetView.TIME_SERIES,
        setPeriodToTimeRange: false,
      }),
      new cloudwatch.GraphWidget({
        title: 'Per-Model Output Tokens (1min)',
        left: [
          new cloudwatch.MathExpression({
            expression: `SEARCH('{${metricNamespace},ModelId} MetricName="OutputTokens"', 'Sum', 60)`,
            label: '',
          }),
        ],
        width: 8,
        height: 6,
        view: cloudwatch.GraphWidgetView.TIME_SERIES,
        setPeriodToTimeRange: false,
      }),
      new cloudwatch.LogQueryWidget({
        title: 'Per-Model Total Usage',
        logGroupNames: [logGroupName],
        queryLines: [
          'filter ispresent(input.inputTokenCount)',
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0)) as input_tokens, sum(coalesce(output.outputTokenCount,0)) as output_tokens, sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0) + coalesce(output.outputTokenCount,0)) as total, count(*) as invocations by modelId',
          'sort total desc',
        ],
        width: 8,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.TABLE,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.LogQueryWidget({
        title: 'Top User-Model Combinations',
        logGroupNames: [logGroupName],
        queryLines: [
          'filter ispresent(input.inputTokenCount)',
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0) + coalesce(output.outputTokenCount,0)) as total by identity.arn, modelId',
          'sort total desc',
          'limit 20',
        ],
        width: 12,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.BAR,
      }),
      new cloudwatch.LogQueryWidget({
        title: 'Per-User Model Breakdown',
        logGroupNames: [logGroupName],
        queryLines: [
          'filter ispresent(input.inputTokenCount)',
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0)) as input_tokens, sum(coalesce(output.outputTokenCount,0)) as output_tokens, sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0) + coalesce(output.outputTokenCount,0)) as total, count(*) as invocations by identity.arn, modelId',
          'sort identity.arn asc, total desc',
          'limit 50',
        ],
        width: 12,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.TABLE,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.LogQueryWidget({
        title: 'Top 10 Users by Token Usage',
        logGroupNames: [logGroupName],
        queryLines: [
          'fields identity.arn, input.inputTokenCount, input.cacheReadInputTokenCount, input.cacheWriteInputTokenCount, output.outputTokenCount',
          'filter ispresent(input.inputTokenCount)',
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0) + coalesce(output.outputTokenCount,0)) as total by identity.arn',
          'sort total desc',
          'limit 10',
        ],
        width: 12,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.BAR,
      }),
      new cloudwatch.LogQueryWidget({
        title: 'Per-User Detail (Input / Output / Invocations)',
        logGroupNames: [logGroupName],
        queryLines: [
          'fields identity.arn, input.inputTokenCount, input.cacheReadInputTokenCount, input.cacheWriteInputTokenCount, output.outputTokenCount',
          'filter ispresent(input.inputTokenCount)',
          'stats sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0)) as total_input, sum(coalesce(output.outputTokenCount,0)) as total_output, sum(coalesce(input.inputTokenCount,0) + coalesce(input.cacheReadInputTokenCount,0) + coalesce(input.cacheWriteInputTokenCount,0) + coalesce(output.outputTokenCount,0)) as total_combined, count(*) as invocations by identity.arn',
          'sort total_combined desc',
          'limit 50',
        ],
        width: 12,
        height: 6,
        view: cloudwatch.LogQueryVisualizationType.TABLE,
      }),
    );
  }
}
