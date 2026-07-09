import { Stack, StackProps, CfnOutput, Fn, RemovalPolicy } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ssm from 'aws-cdk-lib/aws-ssm';

export interface AgentCoreStackProps extends StackProps {
  userPoolId: string;
  userPoolClientId: string;
}

export class AgentCoreStack extends Stack {
  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { userPoolId, userPoolClientId } = props;
    const region = this.region;

    // --- ECR Repositories ---
    const mcpRepo = new ecr.Repository(this, 'McpRepo', {
      repositoryName: 'internalagent/internal-mcp-server',
      removalPolicy: RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      imageScanOnPush: true,
    });
    // CKV_AWS_136: ECR default AES256 encryption is sufficient for sample images; CMK adds cost.
    (mcpRepo.node.defaultChild as ecr.CfnRepository).addMetadata('checkov', {
      skip: [{ id: 'CKV_AWS_136', comment: 'ECR default AES256 encryption is sufficient for sample images; CMK adds cost.' }],
    });

    const a2aRepo = new ecr.Repository(this, 'A2aRepo', {
      repositoryName: 'internalagent/data-analyst-agent',
      removalPolicy: RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      imageScanOnPush: true,
    });
    // CKV_AWS_136: ECR default AES256 encryption is sufficient for sample images; CMK adds cost.
    (a2aRepo.node.defaultChild as ecr.CfnRepository).addMetadata('checkov', {
      skip: [{ id: 'CKV_AWS_136', comment: 'ECR default AES256 encryption is sufficient for sample images; CMK adds cost.' }],
    });

    // --- A2A Agent (Runtime B) ---
    const a2aRole = new iam.Role(this, 'A2aRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      inlinePolicies: {
        BedrockInvoke: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
              resources: ['*'],
            }),
          ],
        }),
        EcrPull: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['ecr:GetDownloadUrlForLayer', 'ecr:BatchGetImage', 'ecr:GetAuthorizationToken'],
              resources: ['*'],
            }),
          ],
        }),
        CloudWatchLogs: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
                'logs:DescribeLogStreams',
              ],
              resources: [
                `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
              ],
            }),
          ],
        }),
      },
    });

    const a2aRuntime = new bedrockagentcore.CfnRuntime(this, 'A2aRuntime', {
      agentRuntimeName: 'internalagent_data_analyst',
      roleArn: a2aRole.roleArn,
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${a2aRepo.repositoryUri}:latest`,
        },
      },
      networkConfiguration: {
        networkMode: 'PUBLIC',
      },
      protocolConfiguration: 'A2A',
    });

    // Store A2A Agent ARN in SSM for MCP Server to discover
    new ssm.StringParameter(this, 'A2aAgentArn', {
      parameterName: '/internalagent/A2A_AGENT_ARN',
      stringValue: a2aRuntime.attrAgentRuntimeArn,
    });

    // --- MCP Server (Runtime A) ---
    const runtimeRole = new iam.Role(this, 'RuntimeRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      inlinePolicies: {
        DataAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['ssm:GetParameters', 'ssm:GetParameter'],
              resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/internalagent/*`],
            }),
            new iam.PolicyStatement({
              actions: ['rds-data:ExecuteStatement', 'rds-data:BatchExecuteStatement'],
              resources: [`arn:aws:rds:${this.region}:${this.account}:cluster:*`],
            }),
            new iam.PolicyStatement({
              actions: ['secretsmanager:GetSecretValue'],
              resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:*`],
            }),
            new iam.PolicyStatement({
              actions: ['s3:GetObject', 's3:ListBucket'],
              resources: ['*'],
            }),
            new iam.PolicyStatement({
              actions: ['bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:InvokeAgentRuntimeForUser'],
              resources: ['*'],
            }),
          ],
        }),
        EcrPull: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['ecr:GetDownloadUrlForLayer', 'ecr:BatchGetImage', 'ecr:GetAuthorizationToken'],
              resources: ['*'],
            }),
          ],
        }),
        CloudWatchLogs: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
                'logs:DescribeLogStreams',
              ],
              resources: [
                `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`,
              ],
            }),
          ],
        }),
      },
    });

    const runtime = new bedrockagentcore.CfnRuntime(this, 'Runtime', {
      agentRuntimeName: 'internalagent_internal_mcp_server',
      roleArn: runtimeRole.roleArn,
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${mcpRepo.repositoryUri}:latest`,
        },
      },
      networkConfiguration: {
        networkMode: 'PUBLIC',
      },
      protocolConfiguration: 'MCP',
    });

    // --- Gateway ---
    const discoveryUrl = `https://cognito-idp.${region}.amazonaws.com/${userPoolId}/.well-known/openid-configuration`;

    const gatewayRole = new iam.Role(this, 'GatewayRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      inlinePolicies: {
        InvokeRuntime: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:InvokeAgentRuntimeForUser'],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    const gateway = new bedrockagentcore.CfnGateway(this, 'Gateway', {
      name: 'internal-agent-gateway',
      protocolType: 'MCP',
      roleArn: gatewayRole.roleArn,
      authorizerType: 'CUSTOM_JWT',
      authorizerConfiguration: {
        customJwtAuthorizer: {
          discoveryUrl,
          allowedClients: [userPoolClientId],
          allowedScopes: ['mcp/access'],
        },
      },
    });

    // Runtime endpoint URL
    const encodedArn = Fn.join('%2F', Fn.split('/',
      Fn.join('%3A', Fn.split(':', runtime.attrAgentRuntimeArn))
    ));
    const runtimeEndpoint = Fn.sub(
      'https://bedrock-agentcore.${AWS::Region}.${AWS::URLSuffix}/runtimes/${EncodedArn}/invocations?qualifier=DEFAULT',
      { EncodedArn: encodedArn }
    );

    const gatewayTarget = new bedrockagentcore.CfnGatewayTarget(this, 'GatewayTarget', {
      name: 'internal-mcp-target',
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      targetConfiguration: {
        mcp: {
          mcpServer: {
            endpoint: runtimeEndpoint,
          },
        },
      },
      credentialProviderConfigurations: [
        {
          credentialProviderType: 'GATEWAY_IAM_ROLE',
          credentialProvider: {
            iamCredentialProvider: {
              service: 'bedrock-agentcore',
              region,
            },
          },
        },
      ],
    });
    gatewayTarget.addDependency(gateway);
    gatewayTarget.addDependency(runtime);

    // --- Outputs ---
    new CfnOutput(this, 'GatewayId', { value: gateway.attrGatewayIdentifier });
    new CfnOutput(this, 'GatewayUrl', { value: gateway.attrGatewayUrl });
    new CfnOutput(this, 'RuntimeId', { value: runtime.attrAgentRuntimeId });
    new CfnOutput(this, 'RuntimeArn', { value: runtime.attrAgentRuntimeArn });
    new CfnOutput(this, 'A2aRuntimeId', { value: a2aRuntime.attrAgentRuntimeId });
    new CfnOutput(this, 'A2aRuntimeArn', { value: a2aRuntime.attrAgentRuntimeArn });
    new CfnOutput(this, 'McpRepoUri', { value: mcpRepo.repositoryUri });
    new CfnOutput(this, 'A2aRepoUri', { value: a2aRepo.repositoryUri });
  }
}
