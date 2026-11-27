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
      reason: 'Grants created by CDK grant* helpers, scoped to this stack\'s resources: generations/* objects, the table\'s indexes, the worker\'s versions, the worker failure destination\'s versions, the runtime\'s endpoints, and the site bucket for the deployment.',
      appliesTo: [
        'Resource::<ArtifactsBucket2AAC5544.Arn>/generations/*',
        'Resource::<Generations7CAB23BD.Arn>/index/*',
        'Resource::<WorkerFunctionACE6A4B0.Arn>:*',
        'Resource::<WorkerFailureFunction5CF06FF8.Arn>:*',
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
    {
      id: 'AwsSolutions-IAM5',
      reason: 'Image to 3D: per-object grants on random job IDs. The API writes and deletes uploads/*, the prepare function reads and deletes them, and the worker reads only each job\'s generations/<jobId>/input.png.',
      appliesTo: [
        'Resource::<UploadsBucket5E5E9B64.Arn>/uploads/*',
        'Resource::<ArtifactsBucket2AAC5544.Arn>/generations/*/input.png',
      ],
    },
    {
      id: 'AwsSolutions-IAM5',
      reason: 'GuardDuty Malware Protection role, from the GuardDuty user guide template: it scans and tags any new object in the uploads bucket, and manages its own EventBridge rule, whose name GuardDuty generates under the DO-NOT-DELETE-AmazonGuardDutyMalwareProtectionS3 prefix.',
      appliesTo: [
        'Resource::<UploadsBucket5E5E9B64.Arn>/*',
        `Resource::arn:aws:events:us-east-1:${account}:rule/DO-NOT-DELETE-AmazonGuardDutyMalwareProtectionS3*`,
      ],
    },
    {
      id: 'AwsSolutions-IAM5',
      reason: 'The background-removal model is called through its us.* inference profile, which routes to any US Region; the foundation model is granted in every Region only via that profile (condition on bedrock:InferenceProfileArn).',
      appliesTo: ['Resource::arn:aws:bedrock:*::foundation-model/stability.stable-image-remove-background-v1:0'],
    },
    { id: 'AwsSolutions-L1', reason: 'CDK-managed custom resource Lambdas (BucketDeployment, auto-delete) pick their own runtime.' },
    { id: 'AwsSolutions-S1', reason: 'The access log bucket itself does not log to another bucket.' },
    { id: 'AwsSolutions-SMG4', reason: 'The origin-verify header value, the Cognito client secret and the OAuth nonce key are managed by CloudFormation; no rotation Lambda.' },
    { id: 'AwsSolutions-APIG2', reason: 'The single Lambda validates every request body (prompt length, JSON content type).' },
    { id: 'AwsSolutions-COG2', reason: 'Users are admin-created; TOTP MFA is optional for this demo.' },
    { id: 'AwsSolutions-COG8', reason: 'Essentials plan is enough for an admin-created user list; Plus (threat protection) is not needed for this demo.' },
    { id: 'AwsSolutions-CFR1', reason: 'No geo restriction: Cognito sign-in gates every request; optional WAF IP allowlist via the allowedCidrs context.' },
    { id: 'AwsSolutions-CFR4', reason: 'Uses the default *.cloudfront.net certificate, whose viewer TLS policy cannot be changed without a custom domain.' },
    { id: 'AwsSolutions-CFR7', reason: 'S3 origin uses Origin Access Control (OAC), which supersedes OAI.' },
  ];
  for (const ack of acks) {
    const ids = ack.appliesTo ? ack.appliesTo.map((a) => `${ack.id}[${a}]`) : [ack.id];
    for (const id of ids) Validations.of(stack).acknowledge({ id, reason: ack.reason });
  }
}
