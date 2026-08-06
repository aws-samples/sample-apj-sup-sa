import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps, Aws } from 'aws-cdk-lib';
import * as path from 'path';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import { CfnResource } from 'aws-cdk-lib';
import { Construct } from 'constructs';

export interface SlackGatewayStackProps extends StackProps {
  /** Slack OAuth2 application client id (from the .env file). */
  readonly slackClientId: string;
  /** Slack OAuth2 application client secret (from the .env file). */
  readonly slackClientSecret: string;
  /**
   * Cognito hosted-UI domain prefix for the user pool (must be globally unique
   * across all AWS accounts). From the .env file (COGNITO_DOMAIN_PREFIX).
   * If omitted, defaults to "agentcore-gateway-slack-<suffix>".
   */
  readonly cognitoDomainPrefix?: string;
  /**
   * Suffix appended to account/region-scoped resource names so multiple
   * independent deployments do not collide. From the .env file (RESOURCE_SUFFIX).
   * If omitted, a stable suffix is derived from the construct address.
   */
  readonly resourceNameSuffix?: string;
  /**
   * Permanent password assigned to the seeded test users (user1, user2).
   * From the .env file (TEST_USER_PASSWORD). Must satisfy the user pool's
   * password policy (8+ chars with upper, lower, digit, and symbol).
   */
  readonly testUserPassword: string;
  /**
   * Bedrock model id the runtime agent uses. From the .env file
   * (BEDROCK_MODEL_ID). Defaults to a cross-region Claude Sonnet profile.
   */
  readonly bedrockModelId?: string;
}

/**
 * Faithful CDK reproduction of the manually-created AgentCore resources:
 *
 *   - AgentCore Identity : OAuth2 credential provider "slack-mcp-server-provider" (SlackOauth2)
 *   - AgentCore Gateway  : "ac-gateway-mcp-server-slack" (MCP, CUSTOM_JWT)
 *   - Gateway Target     : "slack-integration-target" (OpenAPI schema in S3 + OAuth)
 *   - IAM Role           : "agentcore-ac-gateway-mcp-server-slack-role"
 *
 * Modelled on a reference AgentCore + Cognito setup and generalized into reusable
 * sample CDK for standalone deployments: resource names carry a unique suffix to
 * avoid collisions, and the target account/region come from the deployer's
 * environment (no hardcoded account). The Slack client id and secret are sourced
 * from a .env file (see .env.example) and injected via stack props.
 */
export class SlackGatewayStack extends Stack {
  constructor(scope: Construct, id: string, props: SlackGatewayStackProps) {
    super(scope, id, props);

    // ---------------------------------------------------------------------
    // Naming convention
    // ---------------------------------------------------------------------
    // A unique suffix is appended to every account/region-scoped resource name so
    // this sample can be deployed multiple times (or alongside existing resources)
    // without name collisions. Supply RESOURCE_SUFFIX in .env for a fixed value, or
    // let it default to a stable hash derived from the construct address.
    const uniqueSuffix = (props.resourceNameSuffix ?? this.node.addr.substring(0, 8)).toLowerCase();

    const GATEWAY_NAME = `ac-gateway-mcp-server-slack-${uniqueSuffix}`;
    const GATEWAY_DESCRIPTION = 'AgentCore Gateway with MCP Server target';
    const TARGET_NAME = 'slack-integration-target';
    const PROVIDER_NAME = `slack-mcp-server-provider-${uniqueSuffix}`;
    const ROLE_NAME = `agentcore-ac-gateway-mcp-server-slack-role-${uniqueSuffix}`;
    const USER_POOL_NAME = `agentcore-gateway-pool-slack-${uniqueSuffix}`;
    const USER_POOL_CLIENT_NAME = `agentcore-gateway-client-slack-${uniqueSuffix}`;

    // OpenAPI schema backing the MCP target.
    const OPENAPI_SCHEMA_S3_URI =
      's3://amazonbedrockagentcore-built-sampleschemas455e0815-oj7jujcd8xiu/slack-open-api.json';

    // Slack user scopes requested during the 3-legged OAuth flow.
    const SLACK_USER_SCOPE =
      'chat:write,im:write,search:read,users:read,channels:history,channels:read,channels:write';
    const OAUTH_DEFAULT_RETURN_URL = 'http://localhost:8080/callback';

    const cognitoDomainPrefix = props.cognitoDomainPrefix ?? `agentcore-gateway-slack-${uniqueSuffix}`;

    // ---------------------------------------------------------------------
    // 0. Cognito user pool (JWT issuer for the gateway's CUSTOM_JWT authorizer)
    //    Mirrors the live "sample-agentcore-gateway-pool-slack" configuration.
    // ---------------------------------------------------------------------
    const invokeScope = new cognito.ResourceServerScope({
      scopeName: 'invoke',
      scopeDescription: 'Scope for invoking the agentcore gateway',
    });

    const userPool = new cognito.UserPool(this, 'GatewayUserPool', {
      userPoolName: USER_POOL_NAME,
      // Self sign-up is disabled: this pool is the trusted JWT issuer for both the
      // gateway (CUSTOM_JWT) and the internet-facing PUBLIC runtime, and downstream
      // Slack calls act as the signed-in user. Allowing public self-registration
      // would let anyone mint a valid token and invoke the agent. Users are created
      // administratively (see the seeded CfnUserPoolUser accounts below).
      selfSignUpEnabled: false,
      signInAliases: { username: true },
      autoVerify: { email: false, phone: false },
      mfa: cognito.Mfa.OFF,
      accountRecovery: cognito.AccountRecovery.EMAIL_AND_PHONE_WITHOUT_MFA,
      deletionProtection: false,
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
        tempPasswordValidity: Duration.days(7),
      },
      signInPolicy: {
        allowedFirstAuthFactors: { password: true },
      },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const resourceServer = userPool.addResourceServer('GatewayResourceServer', {
      identifier: 'agentcore-gateway-id-slack',
      userPoolResourceServerName: 'agentcore-gateway-name-slack',
      scopes: [invokeScope],
    });

    const userPoolDomain = userPool.addDomain('GatewayUserPoolDomain', {
      cognitoDomain: { domainPrefix: cognitoDomainPrefix },
    });

    const userPoolClient = userPool.addClient('GatewayUserPoolClient', {
      userPoolClientName: USER_POOL_CLIENT_NAME,
      generateSecret: true,
      authFlows: { adminUserPassword: true },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.PROFILE,
          cognito.OAuthScope.resourceServer(resourceServer, invokeScope),
        ],
        callbackUrls: ['http://localhost:8080/callback'],
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      refreshTokenValidity: Duration.days(30),
      authSessionValidity: Duration.minutes(3),
      enableTokenRevocation: true,
    });

    // Discovery URL + allowed client wired into the gateway authorizer below.
    const jwtDiscoveryUrl = `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}/.well-known/openid-configuration`;

    // ---------------------------------------------------------------------
    // 0b. Seed test users
    // ---------------------------------------------------------------------
    // Two fictitious users for testing the OAuth/JWT flow. CfnUserPoolUser only
    // creates the account with a temporary password (FORCE_CHANGE_PASSWORD state),
    // so each user is paired with an AwsCustomResource that calls
    // AdminSetUserPassword with Permanent=true to make the password usable
    // immediately (compatible with the adminUserPassword auth flow). MessageAction
    // SUPPRESS avoids sending any invitation email — no email verification needed.
    const TEST_USER_PASSWORD = props.testUserPassword;
    const testUsernames = ['user1', 'user2'];

    testUsernames.forEach((username, index) => {
      const cfnUser = new cognito.CfnUserPoolUser(this, `TestUser${index + 1}`, {
        userPoolId: userPool.userPoolId,
        username,
        messageAction: 'SUPPRESS',
      });

      const setPassword = new cr.AwsCustomResource(this, `TestUser${index + 1}SetPassword`, {
        onCreate: {
          service: 'CognitoIdentityServiceProvider',
          action: 'adminSetUserPassword',
          parameters: {
            UserPoolId: userPool.userPoolId,
            Username: username,
            Password: TEST_USER_PASSWORD,
            Permanent: true,
          },
          physicalResourceId: cr.PhysicalResourceId.of(`${username}-password`),
        },
        policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
          resources: [userPool.userPoolArn],
        }),
        installLatestAwsSdk: false,
      });

      setPassword.node.addDependency(cfnUser);
    });

    // ---------------------------------------------------------------------
    // 1. AgentCore Identity — OAuth2 credential provider (Slack)
    // ---------------------------------------------------------------------
    // Client id + secret come from the .env file via props (see bin/app.ts).
    // The live provider stores its client secret as an AgentCore-MANAGED secret,
    // so AgentCore re-stores the supplied value in Secrets Manager for you
    // (ClientSecretSource = MANAGED).
    const oauthProvider = new CfnResource(this, 'SlackOAuthProvider', {
      type: 'AWS::BedrockAgentCore::OAuth2CredentialProvider',
      properties: {
        Name: PROVIDER_NAME,
        CredentialProviderVendor: 'SlackOauth2',
        Oauth2ProviderConfigInput: {
          SlackOauth2ProviderConfig: {
            ClientId: props.slackClientId,
            ClientSecret: props.slackClientSecret,
            ClientSecretSource: 'MANAGED',
          },
        },
      },
    });

    const providerArn = oauthProvider.getAtt('CredentialProviderArn').toString();

    // The provider's MANAGED client secret is created by AgentCore in Secrets Manager
    // under a predictable name: "bedrock-agentcore-identity!default/oauth2/<provider>-*".
    // The ClientSecretArn attribute is a composite object (not a plain string), so it
    // cannot be used directly in an IAM Resource; scope to the managed name pattern.
    const providerSecretArnPattern =
      `arn:aws:secretsmanager:${Aws.REGION}:${Aws.ACCOUNT_ID}:secret:bedrock-agentcore-identity!default/oauth2/${PROVIDER_NAME}-*`;

    // ---------------------------------------------------------------------
    // 2. IAM role assumed by the gateway
    // ---------------------------------------------------------------------
    const gatewayRole = new iam.CfnRole(this, 'GatewayRole', {
      roleName: ROLE_NAME,
      maxSessionDuration: 3600,
      assumeRolePolicyDocument: {
        Version: '2012-10-17',
        Statement: [
          {
            Sid: 'AssumeRolePolicy',
            Effect: 'Allow',
            Principal: { Service: 'bedrock-agentcore.amazonaws.com' },
            Action: 'sts:AssumeRole',
            Condition: {
              StringEquals: { 'aws:SourceAccount': Aws.ACCOUNT_ID },
              ArnLike: { 'aws:SourceArn': `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:*` },
            },
          },
        ],
      },
      policies: [
        {
          policyName: 'AgentCoreGatewayPolicy',
          policyDocument: {
            Version: '2012-10-17',
            Statement: [
              {
                Sid: 'GetGateway',
                Effect: 'Allow',
                Action: ['bedrock-agentcore:GetGateway'],
                Resource: [
                  `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:gateway/${GATEWAY_NAME}-*`,
                ],
              },
            ],
          },
        },
        {
          policyName: 'AgentCoreIdentityPolicy',
          policyDocument: {
            Version: '2012-10-17',
            Statement: [
              {
                Sid: 'GetWorkloadAccessToken',
                Effect: 'Allow',
                Action: [
                  'bedrock-agentcore:GetWorkloadAccessToken',
                  'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
                ],
                Resource: [
                  `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:workload-identity-directory/default`,
                  `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:workload-identity-directory/default/workload-identity/${GATEWAY_NAME}-*`,
                ],
              },
              {
                Sid: 'GetResourceOauth2Token',
                Effect: 'Allow',
                Action: ['bedrock-agentcore:GetResourceOauth2Token'],
                Resource: [
                  `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:token-vault/default`,
                  `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:token-vault/default/oauth2credentialprovider/${PROVIDER_NAME}`,
                  `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:workload-identity-directory/default`,
                  `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:workload-identity-directory/default/workload-identity/${GATEWAY_NAME}-*`,
                ],
              },
              {
                Sid: 'GetSecretValue',
                Effect: 'Allow',
                Action: ['secretsmanager:GetSecretValue'],
                Resource: [providerSecretArnPattern],
              },
            ],
          },
        },
      ],
    });

    // ---------------------------------------------------------------------
    // 3. AgentCore Gateway (MCP, CUSTOM_JWT)
    // ---------------------------------------------------------------------
    const gateway = new CfnResource(this, 'SlackGateway', {
      type: 'AWS::BedrockAgentCore::Gateway',
      properties: {
        Name: GATEWAY_NAME,
        Description: GATEWAY_DESCRIPTION,
        RoleArn: gatewayRole.attrArn,
        ProtocolType: 'MCP',
        ProtocolConfiguration: {
          Mcp: {
            SupportedVersions: ['2025-11-25'],
            SearchType: 'SEMANTIC',
          },
        },
        AuthorizerType: 'CUSTOM_JWT',
        AuthorizerConfiguration: {
          CustomJWTAuthorizer: {
            DiscoveryUrl: jwtDiscoveryUrl,
            AllowedClients: [userPoolClient.userPoolClientId],
          },
        },
      },
    });

    const gatewayIdentifier = gateway.getAtt('GatewayIdentifier').toString();

    // ---------------------------------------------------------------------
    // 4. Gateway target (OpenAPI schema in S3, OAuth credentials)
    // ---------------------------------------------------------------------
    const target = new CfnResource(this, 'SlackGatewayTarget', {
      type: 'AWS::BedrockAgentCore::GatewayTarget',
      properties: {
        Name: TARGET_NAME,
        GatewayIdentifier: gatewayIdentifier,
        TargetConfiguration: {
          Mcp: {
            OpenApiSchema: {
              S3: { Uri: OPENAPI_SCHEMA_S3_URI },
            },
          },
        },
        CredentialProviderConfigurations: [
          {
            CredentialProviderType: 'OAUTH',
            CredentialProvider: {
              OauthCredentialProvider: {
                ProviderArn: providerArn,
                Scopes: [],
                GrantType: 'AUTHORIZATION_CODE',
                CustomParameters: { user_scope: SLACK_USER_SCOPE },
                DefaultReturnUrl: OAUTH_DEFAULT_RETURN_URL,
              },
            },
          },
        ],
      },
    });
    target.addResourceDependency(gateway);
    target.addResourceDependency(oauthProvider);

    // ---------------------------------------------------------------------
    // 5. AgentCore Runtime (Strands agent, CUSTOM_JWT inbound auth)
    // ---------------------------------------------------------------------
    // The runtime is deployed with a CUSTOM_JWT authorizer wired to the SAME
    // Cognito user pool + app client the gateway trusts. Callers invoke the
    // runtime with their Cognito access token; AgentCore validates it, then the
    // agent code (runtime/runtime_agent.py) forwards that same token to the
    // gateway MCP endpoint — a 3LO passthrough where downstream Slack calls act
    // as the signed-in user. No credentials live in the deployed code.
    //
    // Runtime names must match ^[a-zA-Z][a-zA-Z0-9_]*$ (no hyphens), so the
    // shared suffix is sanitized before use.
    const RUNTIME_NAME = `slack_gateway_agent_${uniqueSuffix.replace(/[^a-z0-9]/g, '')}`;
    const MCP_PROTOCOL_VERSION = '2025-11-25';
    const BEDROCK_MODEL_ID = props.bedrockModelId ?? 'global.anthropic.claude-sonnet-4-6';

    // Build the agent container image and push it to the CDK-managed ECR repo.
    // AgentCore Runtime executes on linux/arm64, so the image is built for that
    // platform (requires Docker with buildx at synth/deploy time). The Dockerfile
    // (runtime/Dockerfile) installs deps and serves the AgentCore HTTP contract
    // on port 8080.
    const runtimeImage = new ecrAssets.DockerImageAsset(this, 'RuntimeImage', {
      directory: path.join(__dirname, '..', 'runtime'),
      platform: ecrAssets.Platform.LINUX_ARM64,
    });

    // Runtime execution role: Bedrock model invocation + runtime logs/traces +
    // ECR pull for the agent image. No Cognito or Secrets Manager permissions
    // are needed — the inbound token is supplied by the caller and forwarded,
    // not minted here. The gateway is reached over HTTPS with that bearer token,
    // so no IAM grant is required to invoke it either.
    const runtimeRole = new iam.Role(this, 'RuntimeExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': Aws.ACCOUNT_ID },
          ArnLike: { 'aws:SourceArn': `arn:aws:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:*` },
        },
      }),
      description: 'AgentCore Runtime execution role for the Slack gateway agent',
      maxSessionDuration: Duration.hours(1),
    });

    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeBedrockModel',
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:CountTokens',
        ],
        resources: [
          `arn:aws:bedrock:*:${Aws.ACCOUNT_ID}:inference-profile/*`,
          'arn:aws:bedrock:*::foundation-model/*',
        ],
      })
    );

    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RuntimeObservability',
        actions: ['logs:DescribeLogGroups', 'xray:PutTelemetryRecords', 'xray:PutTraceSegments'],
        resources: ['*'],
      })
    );

    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RuntimeLogs',
        actions: [
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:DescribeLogStreams',
          'logs:FilterLogEvents',
          'logs:GetLogEvents',
          'logs:PutLogEvents',
          'logs:PutResourcePolicy',
        ],
        resources: [`arn:aws:logs:${Aws.REGION}:${Aws.ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*`],
      })
    );

    // Allow the runtime to pull its agent image from ECR. grantPull adds the
    // repo-scoped BatchGetImage/GetDownloadUrlForLayer actions plus the
    // account-wide ecr:GetAuthorizationToken needed to authenticate.
    runtimeImage.repository.grantPull(runtimeRole);

    const runtime = new CfnResource(this, 'SlackRuntime', {
      type: 'AWS::BedrockAgentCore::Runtime',
      properties: {
        AgentRuntimeName: RUNTIME_NAME,
        Description: `AgentCore Runtime forwarding Cognito JWT to ${GATEWAY_NAME}`,
        RoleArn: runtimeRole.roleArn,
        AgentRuntimeArtifact: {
          ContainerConfiguration: {
            ContainerUri: runtimeImage.imageUri,
          },
        },
        NetworkConfiguration: { NetworkMode: 'PUBLIC' },
        // Forward the caller's Authorization header into the container so the
        // agent can read the bearer token and pass it to the gateway.
        RequestHeaderConfiguration: {
          RequestHeaderAllowlist: ['Authorization'],
        },
        // Inbound JWT auth wired to the SAME Cognito pool + client as the gateway.
        AuthorizerConfiguration: {
          CustomJWTAuthorizer: {
            DiscoveryUrl: jwtDiscoveryUrl,
            AllowedClients: [userPoolClient.userPoolClientId],
          },
        },
        EnvironmentVariables: {
          GATEWAY_URL: gateway.getAtt('GatewayUrl').toString(),
          BEDROCK_MODEL_ID: BEDROCK_MODEL_ID,
          MCP_PROTOCOL_VERSION: MCP_PROTOCOL_VERSION,
        },
      },
    });
    runtime.node.addDependency(runtimeRole);
    runtime.addResourceDependency(gateway);

    // ---------------------------------------------------------------------
    // Outputs
    // ---------------------------------------------------------------------
    new CfnOutput(this, 'GatewayId', { value: gatewayIdentifier });
    new CfnOutput(this, 'GatewayUrl', { value: gateway.getAtt('GatewayUrl').toString() });
    new CfnOutput(this, 'GatewayArn', { value: gateway.getAtt('GatewayArn').toString() });
    new CfnOutput(this, 'GatewayRoleArn', { value: gatewayRole.attrArn });
    new CfnOutput(this, 'OAuthProviderArn', { value: providerArn });
    new CfnOutput(this, 'TargetId', { value: target.getAtt('TargetId').toString() });

    // Runtime outputs
    new CfnOutput(this, 'RuntimeArn', { value: runtime.getAtt('AgentRuntimeArn').toString() });
    new CfnOutput(this, 'RuntimeId', { value: runtime.getAtt('AgentRuntimeId').toString() });
    new CfnOutput(this, 'RuntimeRoleArn', { value: runtimeRole.roleArn });

    // Cognito outputs
    new CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new CfnOutput(this, 'UserPoolClientId', { value: userPoolClient.userPoolClientId });
    new CfnOutput(this, 'JwtDiscoveryUrl', { value: jwtDiscoveryUrl });
    new CfnOutput(this, 'CognitoDomainBaseUrl', { value: userPoolDomain.baseUrl() });
    new CfnOutput(this, 'UserPoolClientSecretHint', {
      description: 'Retrieve the app client secret with the AWS CLI (not exported for security).',
      value: `aws cognito-idp describe-user-pool-client --user-pool-id ${userPool.userPoolId} --client-id ${userPoolClient.userPoolClientId} --query UserPoolClient.ClientSecret --output text`,
    });
  }
}
