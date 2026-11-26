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

test('Cognito: admin-created users only, strong passwords, code flow with a client secret', () => {
  template.resourceCountIs('AWS::Cognito::UserPool', 1);
  template.hasResourceProperties('AWS::Cognito::UserPool', {
    AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
    Policies: { PasswordPolicy: Match.objectLike({ MinimumLength: 12, RequireSymbols: true }) },
  });
  template.hasResourceProperties('AWS::Cognito::UserPoolClient', {
    GenerateSecret: true,
    AllowedOAuthFlows: ['code'],
    PreventUserExistenceErrors: 'ENABLED',
    SupportedIdentityProviders: ['COGNITO'],
  });
});

test('every behaviour is behind the sign-in gate: pages, generated files and the API', () => {
  const dist = Object.values(template.findResources('AWS::CloudFront::Distribution'))[0] as any;
  const cfg = dist.Properties.DistributionConfig;
  const events = (b: any) => (b.LambdaFunctionAssociations ?? []).map((a: any) => a.EventType);
  expect(events(cfg.DefaultCacheBehavior)).toEqual(['viewer-request']);
  const byPath = Object.fromEntries(cfg.CacheBehaviors.map((b: any) => [b.PathPattern, b]));
  expect(Object.keys(byPath).sort()).toEqual(['/api/*', '/generations/*']);
  expect(events(byPath['/generations/*'])).toEqual(['viewer-request']);
  expect(events(byPath['/api/*'])).toEqual(['origin-request']);
});

test('every API method also requires a Cognito token at API Gateway', () => {
  const methods = Object.values(template.findResources('AWS::ApiGateway::Method')) as any[];
  const real = methods.filter((m) => m.Properties.HttpMethod !== 'OPTIONS');
  expect(real.length).toBe(7); // models, generations GET/POST, generation GET/DELETE, gallery, image-generations
  for (const m of real) expect(m.Properties.AuthorizationType).toBe('COGNITO_USER_POOLS');
});

test('the edge gates read only their own secrets', () => {
  const policies = Object.values(template.findResources('AWS::IAM::Policy')) as any[];
  const edge = policies.flatMap((p) => p.Properties.PolicyDocument.Statement)
    .filter((s: any) => [s.Action].flat().includes('secretsmanager:GetSecretValue') && JSON.stringify(s.Resource).includes('edge-auth'));
  expect(edge.length).toBe(2); // viewer and API gates
  const arn = (name: string) => `arn:aws:secretsmanager:us-east-1:111122223333:secret:3d-generation-llm/edge-auth/${name}-??????`;
  const resources = edge.map((s: any) => [s.Resource].flat().sort());
  // Exact secrets only: the viewer gate reads the config and nonce key, the API gate only the config. No "*".
  expect(resources).toEqual(expect.arrayContaining([
    [arn('config'), arn('nonce-signing')],
    [arn('config')],
  ]));
  for (const r of resources.flat()) expect(r).not.toContain('*');
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
          // Profiles and the project only: no foundation-model ARN, so no direct, unconditioned model call.
          Resource: [...profileArns, 'arn:aws:bedrock:us-east-1:111122223333:project/default'],
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

test('direct foundation-model grants exist only for configured in-Region models', () => {
  const inRegion = runnableModels(loadModels()).filter((m) => m.api === 'converse' && !m.modelId.startsWith('us.'));
  const sids = Object.values(template.findResources('AWS::IAM::Policy')).flatMap(
    (p: any) => p.Properties.PolicyDocument.Statement.map((s: any) => s.Sid),
  );
  expect(sids.includes('InvokeInRegionModels')).toBe(inRegion.length > 0);
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
    'gpt-6-astra',
    'gemma-4-31b',
    'qwen3-32b',
    'grok-4-7',
    'glm-5',
  ]);
});

test('worker async invocations are not retried', () => {
  template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', { MaximumRetryAttempts: 0 });
});

test('app routes are rewritten by the sign-in gate, not by error pages or a separate function', () => {
  // The viewer gate (edge-auth/src/viewer.ts rewriteSpaRoute) rewrites /gallery etc. after sign-in.
  template.resourceCountIs('AWS::CloudFront::Function', 0);
  const dist = Object.values(template.findResources('AWS::CloudFront::Distribution'))[0] as any;
  expect(dist.Properties.DistributionConfig.CustomErrorResponses).toBeUndefined();
});

test('API Lambda may delete only generation files', () => {
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([
        Match.objectLike({
          Action: 's3:ListBucket',
          Condition: { StringLike: { 's3:prefix': ['generations/????????-????-????-????-????????????/'] } },
        }),
      ]),
    },
  });
  const methods = Object.values(template.findResources('AWS::ApiGateway::Method')) as any[];
  expect(methods.filter((m) => m.Properties.HttpMethod === 'DELETE')).toHaveLength(1);
});

test('Image to 3D: GuardDuty scans the uploads bucket and its scan result starts the prepare function', () => {
  const uploads = Object.entries(template.findResources('AWS::S3::Bucket')).find(([id]) => id.startsWith('UploadsBucket'));
  expect(uploads).toBeDefined();
  const [uploadsId, bucket] = uploads as [string, any];
  expect(bucket.Properties.LifecycleConfiguration.Rules[0].ExpirationInDays).toBe(1);
  template.hasResourceProperties('AWS::GuardDuty::MalwareProtectionPlan', {
    ProtectedResource: { S3Bucket: { BucketName: { Ref: uploadsId }, ObjectPrefixes: ['uploads/'] } },
    Actions: { Tagging: { Status: 'ENABLED' } },
  });
  // The scan role reads and tags objects under uploads/ only.
  const gdPolicy = Object.values(template.findResources('AWS::IAM::Policy')).find((p: any) =>
    p.Properties.PolicyDocument.Statement.some((s: any) => s.Sid === 'AllowMalwareScan')) as any;
  for (const sid of ['AllowMalwareScan', 'AllowPostScanTag']) {
    const st = gdPolicy.Properties.PolicyDocument.Statement.find((s: any) => s.Sid === sid);
    expect(JSON.stringify(st.Resource)).toMatch(/\/uploads\/\*"/);
  }
  template.hasResourceProperties('AWS::IAM::Role', {
    AssumeRolePolicyDocument: Match.objectLike({
      Statement: [Match.objectLike({ Principal: { Service: 'malware-protection-plan.guardduty.amazonaws.com' } })],
    }),
  });
  template.hasResourceProperties('AWS::Events::Rule', {
    EventPattern: {
      source: ['aws.guardduty'],
      'detail-type': ['GuardDuty Malware Protection Object Scan Result'],
      detail: { s3ObjectDetails: { bucketName: [{ Ref: uploadsId }] } },
    },
  });
  // CloudFront never serves the uploads bucket: only the artifacts bucket and the site are origins.
  const dist = Object.values(template.findResources('AWS::CloudFront::Distribution'))[0] as any;
  expect(JSON.stringify(dist.Properties.DistributionConfig.Origins)).not.toContain(uploadsId);
});

test('WAF allows large request bodies only on the image upload route', () => {
  template.hasResourceProperties('AWS::WAFv2::WebACL', {
    Scope: 'CLOUDFRONT',
    Rules: Match.arrayWith([
      Match.objectLike({
        Name: 'AWSManagedRulesCommonRuleSet',
        Statement: { ManagedRuleGroupStatement: Match.objectLike({ RuleActionOverrides: [{ Name: 'SizeRestrictions_BODY', ActionToUse: { Count: {} } }] }) },
      }),
      Match.objectLike({
        Name: 'BlockLargeBodiesExceptUploads',
        Action: { Block: {} },
        Statement: {
          AndStatement: {
            Statements: [
              Match.objectLike({ SizeConstraintStatement: Match.objectLike({ ComparisonOperator: 'GT', Size: 8192 }) }),
              { NotStatement: { Statement: { ByteMatchStatement: Match.objectLike({ SearchString: '/api/image-generations' }) } } },
            ],
          },
        },
      }),
    ]),
  });
});

test('the AgentCore runtime runs in a VPC with no route to the internet', () => {
  const runtime = Object.values(template.findResources('AWS::BedrockAgentCore::Runtime'))[0] as any;
  expect(runtime.Properties.NetworkConfiguration.NetworkMode).toBe('VPC');
  // Isolated subnets only: no internet gateway, NAT gateway, or route to 0.0.0.0/0.
  template.resourceCountIs('AWS::EC2::InternetGateway', 0);
  template.resourceCountIs('AWS::EC2::NatGateway', 0);
  for (const r of Object.values(template.findResources('AWS::EC2::Route')) as any[]) {
    expect(r.Properties.DestinationCidrBlock).not.toBe('0.0.0.0/0');
  }
  // Subnets pinned to AZ IDs that AgentCore supports.
  const subnets = Object.values(template.findResources('AWS::EC2::Subnet')) as any[];
  expect(subnets.map((s) => s.Properties.AvailabilityZoneId).sort()).toEqual(['use1-az1', 'use1-az2']);
  for (const s of subnets) {
    expect(s.Properties.AvailabilityZone).toBeUndefined();
    expect(s.Properties.MapPublicIpOnLaunch).toBe(false);
  }
  template.hasResourceProperties('AWS::EC2::FlowLog', { TrafficType: 'ALL' });
});

test('the agent reaches only VPC endpoints for the AWS services it uses', () => {
  const services = (Object.values(template.findResources('AWS::EC2::VPCEndpoint')) as any[])
    .map((e) => JSON.stringify(e.Properties.ServiceName));
  for (const s of ['bedrock-runtime', 'logs', 'ecr.api', 'ecr.dkr', 's3']) {
    expect(services.filter((x) => x.includes(`.${s}"`))).toHaveLength(1);
  }
  expect(services).toHaveLength(5);
  // The runtime's security group allows HTTPS out only.
  const sgs = Object.values(template.findResources('AWS::EC2::SecurityGroup')) as any[];
  const agentSg = sgs.find((g) => String(g.Properties.GroupDescription).startsWith('AgentCore runtime ENIs'));
  expect(agentSg.Properties.SecurityGroupEgress).toEqual([
    expect.objectContaining({ IpProtocol: 'tcp', FromPort: 443, ToPort: 443, CidrIp: '0.0.0.0/0' }),
  ]);
  // The Bedrock endpoint accepts only the runtime role, and only model invocations.
  const roles = template.findResources('AWS::IAM::Role');
  const runtimeRoleId = Object.keys(roles).find((id) => id.startsWith('GeneratorAgentExecutionRole'))!;
  const bedrock = (Object.values(template.findResources('AWS::EC2::VPCEndpoint')) as any[])
    .find((e) => JSON.stringify(e.Properties.ServiceName).includes('.bedrock-runtime"'));
  expect(bedrock.Properties.PolicyDocument.Statement).toEqual([
    expect.objectContaining({
      Effect: 'Allow',
      Principal: { AWS: { 'Fn::GetAtt': [runtimeRoleId, 'Arn'] } },
      Action: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
    }),
  ]);
});

test('DNS Firewall resolves AWS names only and fails closed', () => {
  template.hasResourceProperties('AWS::Route53Resolver::FirewallRuleGroup', {
    FirewallRules: [
      Match.objectLike({ Priority: 100, Action: 'ALLOW' }),
      Match.objectLike({ Priority: 200, Action: 'BLOCK', BlockResponse: 'NXDOMAIN' }),
    ],
  });
  template.hasResourceProperties('AWS::Route53Resolver::FirewallDomainList', { Domains: ['*.'] });
  template.resourceCountIs('AWS::Route53Resolver::FirewallRuleGroupAssociation', 1);
});

test('a dropped or crashed worker event goes to the failure function, which can only update the table', () => {
  const fns = template.findResources('AWS::Lambda::Function');
  const failureId = Object.keys(fns).find((id) => (fns[id] as any).Properties.Handler === 'handler.on_failure')!;
  expect(failureId).toBeDefined();
  template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', {
    MaximumRetryAttempts: 0,
    MaximumEventAgeInSeconds: 540, // must match WORKER_MAX_EVENT_AGE_SECONDS in backend/api/handler.py
    DestinationConfig: { OnFailure: { Destination: { 'Fn::GetAtt': [failureId, 'Arn'] } } },
  });
  const role = (fns[failureId] as any).Properties.Role['Fn::GetAtt'][0];
  const policies = Object.values(template.findResources('AWS::IAM::Policy')) as any[];
  const actions = policies
    .filter((p) => p.Properties.Roles.some((r: any) => r.Ref === role))
    .flatMap((p) => p.Properties.PolicyDocument.Statement.flatMap((s: any) => [s.Action].flat()));
  // Tracing (X-Ray) comes from the shared Lambda defaults; the only data access is UpdateItem.
  expect(actions.filter((a: string) => !a.startsWith('xray:'))).toEqual(['dynamodb:UpdateItem']);
});
