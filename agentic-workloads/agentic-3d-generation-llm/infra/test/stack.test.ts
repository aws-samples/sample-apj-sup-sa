// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ThreeDGenerationStack } from '../lib/stack';
import { loadModels, runnableModels } from '../lib/models';

// Needs ../frontend/dist (make build).
const app = new cdk.App();
const stack = new ThreeDGenerationStack(app, 'Test', { env: { account: '111122223333', region: 'us-east-1' } });
const template = Template.fromStack(stack);

test('API is reachable only through CloudFront: regional WAF blocks requests without the origin header', () => {
  template.hasResourceProperties('AWS::WAFv2::WebACL', {
    Scope: 'REGIONAL',
    DefaultAction: { Block: {} },
    Rules: [Match.objectLike({
      Action: { Allow: {} },
      Statement: { ByteMatchStatement: Match.objectLike({ FieldToMatch: { SingleHeader: { Name: 'x-origin-verify' } }, PositionalConstraint: 'EXACTLY' }) },
    })],
  });
  template.resourceCountIs('AWS::WAFv2::WebACLAssociation', 1);
  template.hasResourceProperties('AWS::CloudFront::Distribution', {
    DistributionConfig: Match.objectLike({
      Origins: Match.arrayWith([Match.objectLike({ OriginCustomHeaders: [Match.objectLike({ HeaderName: 'x-origin-verify' })] })]),
    }),
  });
});

test('no sign-in remains: no Cognito or Lambda@Edge resources', () => {
  template.resourceCountIs('AWS::Cognito::UserPool', 0);
  const fns = template.findResources('AWS::Lambda::Function');
  for (const f of Object.values(fns) as any[]) expect(String(f.Properties.Runtime)).not.toMatch(/^nodejs.*edge/);
});

test('all buckets block public access and enforce TLS', () => {
  const buckets = template.findResources('AWS::S3::Bucket');
  for (const b of Object.values(buckets) as any[]) {
    expect(b.Properties.PublicAccessBlockConfiguration).toEqual({
      BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true,
    });
  }
  const policies = template.findResources('AWS::S3::BucketPolicy');
  for (const p of Object.values(policies) as any[]) {
    for (const st of p.Properties.PolicyDocument.Statement) {
      if (st.Effect === 'Allow') expect(st.Principal).not.toEqual('*');
    }
  }
});

test('GLB files are served by CloudFront from the private artifacts bucket, not presigned S3 URLs', () => {
  template.hasResourceProperties('AWS::CloudFront::Distribution', {
    DistributionConfig: Match.objectLike({
      CacheBehaviors: Match.arrayWith([Match.objectLike({ PathPattern: '/generations/*' })]),
    }),
  });
  const buckets = template.findResources('AWS::S3::Bucket');
  for (const b of Object.values(buckets) as any[]) expect(b.Properties.CorsConfiguration).toBeUndefined();
});

test('agent role can invoke exactly the configured converse models', () => {
  // Includes the Claude dropdown options (Sonnet 5.5, Haiku 5.5), not just the default slots.
  const models = runnableModels(loadModels()).filter((m) => m.api === 'converse');
  expect(models.map((m) => m.key)).toEqual(expect.arrayContaining(['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5']));
  const profiles = models.filter((m) => m.modelId.startsWith('us.'));
  const profileArns = profiles.map((m) => `arn:aws:bedrock:us-east-1:111122223333:inference-profile/${m.modelId}`);
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Sid: 'InvokeConverseModels',
          Resource: [
            ...profileArns,
            ...models.filter((m) => !m.modelId.startsWith('us.')).map((m) => `arn:aws:bedrock:us-east-1::foundation-model/${m.modelId}`),
            'arn:aws:bedrock:us-east-1:111122223333:project/default',
          ],
        }),
        // Any-Region foundation-model ARNs are usable only through the inference profiles.
        Match.objectLike({
          Sid: 'InvokeConverseModelsViaProfile',
          Resource: profiles.map((m) => `arn:aws:bedrock:*::foundation-model/${m.baseModelId}`),
          Condition: { StringEquals: { 'bedrock:InferenceProfileArn': profileArns } },
        }),
      ]),
    },
  });
});

test('no unconditioned any-Region foundation-model grant', () => {
  const statements = Object.values(template.findResources('AWS::IAM::Policy')).flatMap(
    (p: any) => p.Properties.PolicyDocument.Statement,
  );
  for (const st of statements) {
    const resources = [st.Resource].flat().filter((r) => typeof r === 'string');
    if (resources.some((r: string) => r.startsWith('arn:aws:bedrock:*::foundation-model/'))) {
      expect(st.Condition?.StringEquals?.['bedrock:InferenceProfileArn']).toBeDefined();
    }
  }
});

test('retired models lose their grants: no bedrock-mantle, Qwen, Grok or Gemma access', () => {
  const policies = JSON.stringify(template.findResources('AWS::IAM::Policy'));
  expect(policies).not.toContain('bedrock-mantle');
  expect(policies).not.toContain('qwen.');
  expect(policies).not.toContain('xai.grok');
  expect(policies).not.toContain('gemma');
  expect(policies).toContain('inference-profile/us.openai.gpt-6-luna');
});

test('retired models stay labelled for past runs', () => {
  const apiFn = Object.values(template.findResources('AWS::Lambda::Function')).find(
    (f: any) => f.Properties.Environment?.Variables?.RETIRED_MODELS_JSON,
  ) as any;
  expect(JSON.parse(apiFn.Properties.Environment.Variables.RETIRED_MODELS_JSON).map((m: any) => m.key)).toEqual([
    'gemma-4-31b',
    'qwen3-32b',
    'grok-4-7',
    'glm-5',
  ]);
});

test('worker async invocations are not retried', () => {
  template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', { MaximumRetryAttempts: 0 });
});

test('app routes are rewritten to index.html on the site behaviour only', () => {
  template.hasResourceProperties('AWS::CloudFront::Function', {
    FunctionCode: Match.stringLikeRegexp("request.uri = '/index.html'"),
  });
  const dist = Object.values(template.findResources('AWS::CloudFront::Distribution'))[0] as any;
  const cfg = dist.Properties.DistributionConfig;
  expect(cfg.DefaultCacheBehavior.FunctionAssociations).toHaveLength(1);
  for (const b of cfg.CacheBehaviors) expect(b.FunctionAssociations).toBeUndefined();
  expect(cfg.CustomErrorResponses).toBeUndefined();
});

test('API Lambda may delete only generation files', () => {
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({ Action: 's3:ListBucket', Condition: { StringLike: { 's3:prefix': ['generations/*'] } } }),
      ]),
    },
  });
  const methods = Object.values(template.findResources('AWS::ApiGateway::Method')) as any[];
  expect(methods.filter((m) => m.Properties.HttpMethod === 'DELETE')).toHaveLength(1);
});
