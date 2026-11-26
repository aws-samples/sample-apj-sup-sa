import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';

export interface ConsoleAuthProps {
  userPoolName: string;
  // Cognito groups for the two console tiers. Membership is managed at RUNTIME
  // (admin-add-user-to-group) so granting/revoking access needs no redeploy — the group
  // names are baked in here, but who is in them is not.
  adminGroupName: string;   // full read + write
  viewerGroupName: string;  // read-only
}

/**
 * Cognito User Pool for console auth. The console is allowlist-only: every data API requires
 * a verified id token whose holder is authorized through a runtime-managed Cognito group.
 * Self-signup is disabled and operators provision group membership.
 */
export class ConsoleAuth extends Construct {
  public readonly userPool: cognito.UserPool;
  public readonly appClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: ConsoleAuthProps) {
    super(scope, id);

    this.userPool = new cognito.UserPool(this, 'Pool', {
      userPoolName: props.userPoolName,
      selfSignUpEnabled: false,
      signInAliases: { username: true, email: true },
      passwordPolicy: {
        minLength: 7,
        requireLowercase: false,
        requireDigits: true,
        requireUppercase: false,
        requireSymbols: false,
      },
      mfa: cognito.Mfa.OFF,
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // SPA client: no secret. Operators authenticate with username and password.
    this.appClient = this.userPool.addClient('Client', {
      generateSecret: false,
      authFlows: { userPassword: true, userSrp: true },
      idTokenValidity: cdk.Duration.minutes(60),
      accessTokenValidity: cdk.Duration.minutes(60),
      refreshTokenValidity: cdk.Duration.days(30),
      preventUserExistenceErrors: true,
    });

    // Console-access groups. Add members at runtime with:
    //   aws cognito-idp admin-add-user-to-group --user-pool-id <id> --username <u> --group-name <name>
    // The id token carries these in cognito:groups; the BFF authorizes on them. No redeploy.
    new cognito.CfnUserPoolGroup(this, 'AdminGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: props.adminGroupName,
      description: 'Bedrock Meter console: full read + write.',
    });
    new cognito.CfnUserPoolGroup(this, 'ViewerGroup', {
      userPoolId: this.userPool.userPoolId,
      groupName: props.viewerGroupName,
      description: 'Bedrock Meter console: read-only (no limit changes / unblock).',
    });
  }
}
