// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Stack, Validations } from 'aws-cdk-lib';
import { ModelConfig, usesInferenceProfile } from './models';

interface Ack {
  id: string;
  reason: string;
  /** Granular findings (cdk-nag "appliesTo"), e.g. 'Resource::*'. */
  appliesTo?: string[];
}

/** Reviewed cdk-nag findings accepted for this demo. Wildcards are acknowledged one by one. */
export function addNagSuppressions(stack: Stack, models: ModelConfig[]): void {
  const account = stack.account;
  const acks: Ack[] = [
    {
      id: 'AwsSolutions-IAM4',
      reason: 'AWS managed policies on CDK-provided roles only: Lambda basic execution logs and the API Gateway account logging role.',
      appliesTo: [
        'Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
        'Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs',
      ],
    },
    {
      id: 'AwsSolutions-IAM5',
      reason: 'Actions that only accept Resource "*": X-Ray tracing, CloudWatch PutMetricData (AgentCore), ECR GetAuthorizationToken, CloudFront invalidations.',
      appliesTo: ['Resource::*'],
    },
    {
      id: 'AwsSolutions-IAM5',
      reason: 'us.* cross-Region inference profiles route to any US Region, so each model\'s foundation-model ARN is granted in every Region.',
      appliesTo: models
        .filter((m) => m.api === 'converse' && usesInferenceProfile(m))
        .map((m) => `Resource::arn:aws:bedrock:*::foundation-model/${m.baseModelId}`),
    },
    {
      id: 'AwsSolutions-IAM5',
      reason: 'Grants created by CDK grant* helpers, scoped to this stack\'s resources: generations/* objects, the table\'s indexes, the worker\'s versions, the runtime\'s endpoints, and the site bucket for the deployment.',
      appliesTo: [
        'Resource::<ArtifactsBucket2AAC5544.Arn>/generations/*',
        'Resource::<Generations7CAB23BD.Arn>/index/*',
        'Resource::<WorkerFunctionACE6A4B0.Arn>:*',
        'Resource::<GeneratorAgent90710AE4.AgentRuntimeArn>/*',
        'Resource::<SiteBucket397A1860.Arn>/*',
        'Action::s3:List*',
        'Action::s3:GetObject*',
        'Action::s3:GetBucket*',
        'Action::s3:Abort*',
        'Action::s3:DeleteObject*',
        `Resource::arn:aws:s3:::cdk-hnb659fds-assets-${account}-us-east-1/*`,
      ],
    },
    {
      id: 'AwsSolutions-IAM5',
      reason: 'AgentCore Runtime L2 construct defaults: its own log groups and streams, and workload identities in the default directory.',
      appliesTo: [
        `Resource::arn:aws:logs:us-east-1:${account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        `Resource::arn:aws:logs:us-east-1:${account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`,
        `Resource::arn:aws:logs:us-east-1:${account}:log-group:*`,
        `Resource::arn:aws:bedrock-agentcore:us-east-1:${account}:workload-identity-directory/default/workload-identity/*`,
      ],
    },
    { id: 'AwsSolutions-L1', reason: 'CDK-managed custom resource Lambdas (BucketDeployment, auto-delete) pick their own runtime.' },
    { id: 'AwsSolutions-S1', reason: 'The access log bucket itself does not log to another bucket.' },
    { id: 'AwsSolutions-SMG4', reason: 'The origin-verify header value is only a CloudFront-to-API routing check, set by CloudFormation; no rotation Lambda.' },
    { id: 'AwsSolutions-APIG2', reason: 'The single Lambda validates every request body (prompt length, JSON content type).' },
    {
      id: 'AwsSolutions-APIG4',
      reason: 'Public by owner decision (sign-in removed 2026-11-18). Only CloudFront can reach the API (regional WAF on the origin header); WAF rate limits and a daily generation cap bound cost.',
    },
    { id: 'AwsSolutions-COG4', reason: 'Public by owner decision: no Cognito authorizer; the API is reachable only through CloudFront.' },
    { id: 'AwsSolutions-CFR1', reason: 'No geo restriction on the public demo; optional WAF IP allowlist via the allowedCidrs context.' },
    { id: 'AwsSolutions-CFR4', reason: 'Uses the default *.cloudfront.net certificate, whose viewer TLS policy cannot be changed without a custom domain.' },
    { id: 'AwsSolutions-CFR7', reason: 'S3 origin uses Origin Access Control (OAC), which supersedes OAI.' },
  ];
  for (const ack of acks) {
    const ids = ack.appliesTo ? ack.appliesTo.map((a) => `${ack.id}[${a}]`) : [ack.id];
    for (const id of ids) Validations.of(stack).acknowledge({ id, reason: ack.reason });
  }
}
