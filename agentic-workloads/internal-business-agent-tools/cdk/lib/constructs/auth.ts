import { Construct } from 'constructs';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { RemovalPolicy, CfnOutput, Stack } from 'aws-cdk-lib';

export class Auth extends Construct {
  public readonly userPool: cognito.UserPool;
  public readonly client: cognito.UserPoolClient;
  public readonly domain: cognito.UserPoolDomain;
  public readonly resourceServer: cognito.UserPoolResourceServer;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    const stack = Stack.of(this);

    this.userPool = new cognito.UserPool(this, 'UserPool', {
      removalPolicy: RemovalPolicy.DESTROY,
    });

    this.resourceServer = this.userPool.addResourceServer('McpResourceServer', {
      identifier: 'mcp',
      scopes: [
        { scopeName: 'access', scopeDescription: 'Access MCP tools' },
      ],
    });

    this.domain = this.userPool.addDomain('Domain', {
      cognitoDomain: {
        domainPrefix: `internal-agent-mcp-${stack.account}`,
      },
    });

    this.client = this.userPool.addClient('McpClient', {
      generateSecret: true,
      oAuth: {
        flows: { clientCredentials: true },
        scopes: [
          cognito.OAuthScope.custom('mcp/access'),
        ],
      },
    });
    // Client must wait for ResourceServer (scope won't exist otherwise)
    this.client.node.addDependency(this.resourceServer);

    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'ClientId', { value: this.client.userPoolClientId });
    new CfnOutput(this, 'TokenEndpoint', {
      value: `https://${this.domain.domainName}.auth.${stack.region}.amazoncognito.com/oauth2/token`,
    });
  }
}
