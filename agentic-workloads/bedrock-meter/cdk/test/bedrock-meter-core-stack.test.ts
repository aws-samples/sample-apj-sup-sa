import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { BedrockMeterCoreStack } from '../lib/bedrock-meter-core-stack';

function synthCoreTemplate(): Template {
  const app = new cdk.App();
  const stack = new BedrockMeterCoreStack(app, 'TestBedrockMeterCoreStack', {
    env: { account: '123456789012', region: 'ap-northeast-2' },
    meterConfig: {
      adminEmails: [],
      adminUsernames: ['admin'],
    },
  });
  return Template.fromStack(stack);
}

test('console BFF uses Node.js 24 with runtime configuration', () => {
  synthCoreTemplate().hasResourceProperties('AWS::Lambda::Function', {
    Runtime: 'nodejs24.x',
    Handler: 'index.handler',
    Environment: {
      Variables: Match.objectLike({
        COGNITO_USER_POOL_ID: Match.anyValue(),
        BEDROCK_METER_AWS_REGION: 'ap-northeast-2',
      }),
    },
  });
});

test('creates Bedrock Meter core storage', () => {
  const template = synthCoreTemplate();

  template.hasResourceProperties('AWS::DynamoDB::Table', {
    TableName: 'BedrockMeterUsageTable',
    KeySchema: Match.arrayWith([
      { AttributeName: 'UserARN', KeyType: 'HASH' },
      { AttributeName: 'SK', KeyType: 'RANGE' },
    ]),
    BillingMode: 'PAY_PER_REQUEST',
    TimeToLiveSpecification: {
      AttributeName: 'expiresAt',
      Enabled: true,
    },
  });
  template.hasResource('AWS::DynamoDB::Table', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
    Properties: {
      TableName: 'BedrockMeterUsageTable',
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    },
  });

  template.hasResourceProperties('AWS::DynamoDB::Table', {
    TableName: 'BedrockMeterConfigTable',
    KeySchema: [{ AttributeName: 'ConfigKey', KeyType: 'HASH' }],
    BillingMode: 'PAY_PER_REQUEST',
  });
  template.hasResource('AWS::DynamoDB::Table', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
    Properties: {
      TableName: 'BedrockMeterConfigTable',
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    },
  });
});

test('exposes core API routes needed by custom clients', () => {
  const template = synthCoreTemplate();

  template.hasResourceProperties('AWS::ApiGatewayV2::Api', {
    Name: 'bedrock-meter-api',
    ProtocolType: 'HTTP',
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    RouteKey: 'GET /users',
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    RouteKey: 'GET /users/{username}',
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    RouteKey: 'GET /users/{username}/history',
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    RouteKey: 'GET /admin/settings/quota',
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    RouteKey: 'PUT /admin/settings/quota',
  });
});

test('seeds a new installation with an explicit quota timezone policy', () => {
  const template = synthCoreTemplate();
  const rendered = JSON.stringify(template.toJSON());
  expect(rendered).toContain('quotaTimeZone');
  expect(rendered).toContain('quotaTimeZoneVersion');
  expect(rendered).toContain('UTC');
});

test('uses the configured retention and default credit policies', () => {
  const template = synthCoreTemplate();

  template.hasResourceProperties('AWS::Lambda::Function', {
    Environment: {
      Variables: Match.objectLike({
        USAGE_TTL_DAYS: '90',
        DEFAULT_DAILY_CREDIT_LIMIT: '3000000',
        DEFAULT_WEEKLY_CREDIT_LIMIT: '15000000',
        DEFAULT_MONTHLY_CREDIT_LIMIT: '45000000',
      }),
    },
  });
  template.hasResourceProperties('AWS::Lambda::Function', {
    Environment: {
      Variables: Match.objectLike({
        AUDIT_TTL_DAYS: '730',
      }),
    },
  });
  template.hasResourceProperties('AWS::Lambda::Function', {
    Environment: {
      Variables: Match.objectLike({
        USAGE_RETENTION_DAYS: '90',
      }),
    },
  });

  const rendered = JSON.stringify(template.toJSON());
  expect(rendered).toContain('3000000');
  expect(rendered).toContain('15000000');
  expect(rendered).toContain('45000000');

  const customResources = template.findResources('Custom::AWS');
  const seed = Object.values(customResources).find((resource: any) =>
    resource.Properties?.Create?.includes('"ConfigKey":{"S":"default"}'),
  ) as any;
  expect(seed).toBeDefined();
  const update = JSON.parse(seed.Properties.Update);
  expect(update.action).toBe('updateItem');
  expect(update.parameters.ConditionExpression).toBe(
    'dailyTokenLimit = :oldDaily AND weeklyTokenLimit = :oldWeekly AND monthlyTokenLimit = :oldMonthly',
  );
  expect(update.parameters.ExpressionAttributeValues).toMatchObject({
    ':daily': { N: '3000000' },
    ':weekly': { N: '15000000' },
    ':monthly': { N: '45000000' },
    ':oldDaily': { N: '1000000' },
    ':oldWeekly': { N: '5000000' },
    ':oldMonthly': { N: '15000000' },
  });
});

test('console is hosted on a public HTTP API, never a public Lambda Function URL', () => {
  const template = synthCoreTemplate();

  // The console BFF is fronted by its own HTTP API ($default route, no authorizer).
  template.hasResourceProperties('AWS::ApiGatewayV2::Api', {
    Name: 'bedrock-meter-console',
    ProtocolType: 'HTTP',
  });
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    RouteKey: '$default',
    AuthorizationType: 'NONE',
  });

  // API Gateway invokes the BFF via the service principal (scoped by SourceArn), NOT Principal:*.
  // A public Function URL would need Principal:* + InvokeFunctionUrl, which the account's
  // SecurityHub control `lambda-function-public-access-prohibited` blocks at the edge.
  template.hasResourceProperties('AWS::Lambda::Permission', {
    Action: 'lambda:InvokeFunction',
    Principal: 'apigateway.amazonaws.com',
  });
  template.resourceCountIs('AWS::Lambda::Url', 0);
  const publicUrlPerms = template.findResources('AWS::Lambda::Permission', {
    Properties: { Action: 'lambda:InvokeFunctionUrl' },
  });
  expect(Object.keys(publicUrlPerms)).toHaveLength(0);
});

test('BFF signs Core as its pinned execution role; no static IAM-user signing keys', () => {
  const template = synthCoreTemplate();

  // BFF execution role has a pinned name so Core's ADMIN_ROLE_NAMES match survives redeploys.
  template.hasResourceProperties('AWS::IAM::Role', {
    RoleName: 'BedrockMeterConsoleBffRole',
  });

  // Core accepts that role name as an admin caller.
  template.hasResourceProperties('AWS::Lambda::Function', {
    Environment: { Variables: Match.objectLike({ ADMIN_ROLE_NAMES: 'BedrockMeterConsoleBffRole' }) },
  });

  // The old IAM-user (choichul) signing path is gone: no ssm:GetParameter grant and no
  // BEDROCK_METER_SIGNING_* env anywhere. (Static long-lived keys are the thing we removed.)
  const ssmGet = template.findResources('AWS::IAM::Policy', {
    Properties: {
      PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Action: 'ssm:GetParameter' })]) },
    },
  });
  expect(Object.keys(ssmGet)).toHaveLength(0);

  const fns = template.findResources('AWS::Lambda::Function');
  for (const fn of Object.values(fns)) {
    const vars = (fn as any).Properties?.Environment?.Variables ?? {};
    for (const key of Object.keys(vars)) {
      expect(key).not.toMatch(/^BEDROCK_METER_SIGNING/);
    }
  }
});

test('console BFF has the two-tier auth env wired (admin + viewer groups, read-auth toggle)', () => {
  const template = synthCoreTemplate();

  // Both Cognito groups exist (full read+write vs read-only).
  template.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'console-admins' });
  template.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'console-viewers' });

  // BFF env carries the tier groups + the fail-closed read toggle (default true).
  template.hasResourceProperties('AWS::Lambda::Function', {
    Environment: { Variables: Match.objectLike({
      CONSOLE_ADMIN_GROUP: 'console-admins',
      CONSOLE_VIEWER_GROUP: 'console-viewers',
      CONSOLE_REQUIRE_AUTH_FOR_READS: 'true',
    }) },
  });

  template.hasResourceProperties('AWS::Cognito::UserPool', {
    MfaConfiguration: 'OFF',
    Policies: {
      PasswordPolicy: Match.objectLike({
        MinimumLength: 7,
        RequireLowercase: false,
        RequireUppercase: false,
        RequireNumbers: true,
        RequireSymbols: false,
      }),
    },
  });
});

test('alarms on runtime and Bedrock logging failures', () => {
  const template = synthCoreTemplate();

  const alarms = template.findResources('AWS::CloudWatch::Alarm');
  expect(Object.keys(alarms)).toHaveLength(14);

  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    Namespace: 'AWS/Bedrock',
    MetricName: 'ModelInvocationLogsCloudWatchDeliveryFailure',
    Threshold: 1,
  });
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    Namespace: 'AWS/Bedrock',
    MetricName: 'ModelInvocationLogsS3DeliveryFailure',
    Threshold: 1,
  });
  template.hasResourceProperties('AWS::CloudWatch::Alarm', {
    Namespace: 'AWS/Events',
    MetricName: 'FailedInvocations',
    Threshold: 1,
  });
});

test('creates an unattached single-region Bedrock guardrail and keeps role enforcement opt-in', () => {
  const template = synthCoreTemplate();

  template.hasResourceProperties('AWS::IAM::ManagedPolicy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Effect: 'Deny',
          Condition: {
            StringNotEquals: {
              'aws:RequestedRegion': 'ap-northeast-2',
            },
          },
        }),
      ]),
    },
  });
  template.hasOutput('BedrockRegionGuardrailPolicyArn', {});

  const rolePolicyActions = JSON.stringify(template.findResources('AWS::IAM::Policy'));
  expect(rolePolicyActions).not.toContain('iam:PutRolePolicy');
  template.hasResourceProperties('AWS::Lambda::Function', {
    Environment: {
      Variables: Match.objectLike({
        ROLE_ENFORCEMENT_ENABLED: 'false',
      }),
    },
  });
});

test('retains the account-level Bedrock logging configuration and scopes its trust', () => {
  const template = synthCoreTemplate();

  template.hasResourceProperties('AWS::CloudFormation::CustomResource', {
    AllowOverwrite: false,
    DeleteOnRemoval: false,
  });
  template.hasResource('AWS::CloudFormation::CustomResource', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
    Properties: Match.objectLike({ LoggingConfig: Match.anyValue() }),
  });
  template.hasResource('AWS::IAM::Policy', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
    Properties: Match.objectLike({
      PolicyName: Match.stringLikeRegexp('BedrockLoggingBedrockLoggingRoleDefaultPolicy'),
    }),
  });
  template.hasResource('AWS::S3::BucketPolicy', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
    Properties: Match.objectLike({
      Bucket: { Ref: Match.stringLikeRegexp('BedrockLoggingLogArchiveBucket') },
    }),
  });
  template.hasResource('Custom::AWS', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
  });
  template.hasResourceProperties('AWS::IAM::Role', {
    AssumeRolePolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Principal: { Service: 'bedrock.amazonaws.com' },
          Condition: {
            StringEquals: { 'aws:SourceAccount': '123456789012' },
            ArnLike: {
              'aws:SourceArn': 'arn:aws:bedrock:ap-northeast-2:123456789012:*',
            },
          },
        }),
      ]),
    },
  });
  template.hasResource('AWS::IAM::Role', {
    DeletionPolicy: 'Retain',
    UpdateReplacePolicy: 'Retain',
    Properties: {
      AssumeRolePolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Principal: { Service: 'bedrock.amazonaws.com' },
          }),
        ]),
      },
    },
  });
  template.resourceCountIs('AWS::Events::Rule', 1);
});

test('dashboard can be disabled for a lean deployment', () => {
  const app = new cdk.App();
  const stack = new BedrockMeterCoreStack(app, 'LeanStack', {
    env: { account: '123456789012', region: 'ap-northeast-2' },
    meterConfig: {
      enableDashboard: false,
    },
  });
  Template.fromStack(stack).resourceCountIs('AWS::CloudWatch::Dashboard', 0);
});
