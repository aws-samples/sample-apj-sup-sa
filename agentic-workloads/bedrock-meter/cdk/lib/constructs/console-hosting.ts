import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import { Construct } from 'constructs';

export interface ConsoleHostingProps {
  coreApiEndpoint: string;       // https://<id>.execute-api.<region>.amazonaws.com
  coreApiArn: string;            // execute-api ARN for the BFF's invoke permission
  userPool: cognito.IUserPool;
  appClient: cognito.IUserPoolClient;
  // Pinned role name for the BFF execution role. Core accepts it as an admin caller
  // (ADMIN_ROLE_NAMES) so the BFF signs Core calls with its own assumed-role creds.
  bffRoleName: string;
  region: string;
  apiName: string;
  allowedUsers: string[];
  adminGroup: string;
  viewerGroup: string;
  // false → anonymous GET reads (writes still admin-only). Default true at the config layer.
  requireAuthForReads: boolean;
}

/**
 * Console hosting: a SINGLE BFF Lambda (static console UI + /api/console BFF) fronted by a
 * public HTTP API ($default route, no authorizer). One public origin → no CORS, no public S3
 * (the account enforces Block-Public-Access) and no CloudFront.
 *
 * Why HTTP API instead of a Lambda Function URL: the account's SecurityHub control
 * `lambda-function-public-access-prohibited` blocks any Lambda resource policy with
 * Principal:* (which a public Function URL requires) at the edge. API Gateway invokes the
 * Lambda through the apigateway.amazonaws.com service principal (scoped by SourceArn), so it
 * is never a public principal and sidesteps that control. HTTP API payload format 2.0 has the
 * same event/response shape as a Function URL, so the handler is unchanged.
 *
 * The BFF signs Core API calls with its own execution-role creds (SigV4); Core accepts the
 * pinned role name as an admin caller. It verifies Cognito id tokens + allowlist on every data
 * route (read and write); only /api/console/config and the static UI are anonymous.
 */
export class ConsoleHosting extends Construct {
  public readonly consoleUrl: string;
  public readonly bffFn: lambda.IFunction;

  constructor(scope: Construct, id: string, props: ConsoleHostingProps) {
    super(scope, id);

    const stack = cdk.Stack.of(this);

    // dist/ holds index.cjs (bundled BFF) + public/ (the Next static export). CDK packages it
    // as-is — it does NOT build it. Fail loudly at synth if the artifact is missing/incomplete
    // so a bare `cdk deploy` can't ship a broken console. Build it with:
    //   (cd client/app && npm run build:console-artifact)   [scripts/deploy-all.sh does this]
    const distDir = path.join(__dirname, '../../../client/app/bff-lambda/dist');
    for (const required of ['index.cjs', path.join('public', 'index.html')]) {
      if (!fs.existsSync(path.join(distDir, required))) {
        throw new Error(
          `Console artifact missing: ${path.join('client/app/bff-lambda/dist', required)} not found. ` +
          `Run "(cd client/app && npm run build:console-artifact)" before cdk deploy ` +
          `(scripts/deploy-all.sh does this automatically).`,
        );
      }
    }

    // Pinned execution role so the role NAME is stable across redeploys — Core's
    // ADMIN_ROLE_NAMES matches on the name, not the session ARN. The BFF SigV4-signs Core
    // calls with this role's temporary creds (no long-lived IAM-user keys).
    const bffRole = new iam.Role(this, 'BffRole', {
      roleName: props.bffRoleName,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
    bffRole.addToPolicy(new iam.PolicyStatement({
      actions: ['execute-api:Invoke'],
      resources: [props.coreApiArn],
    }));

    // id 'BffFn' (was 'BffHandler'): the previous function was deleted out-of-band, leaving
    // CloudFormation unable to UPDATE it (NotFound). A new logical id forces CFN to CREATE a
    // fresh function instead of updating the ghost.
    const bffFn = new lambda.Function(this, 'BffFn', {
      runtime: lambda.Runtime.NODEJS_24_X,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(distDir),
      role: bffRole,
      timeout: cdk.Duration.seconds(30),
      memorySize: 1024,
      tracing: lambda.Tracing.ACTIVE,
      logGroup: new logs.LogGroup(this, 'BffLogGroup', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        BEDROCK_METER_API_ENDPOINT: props.coreApiEndpoint,
        BEDROCK_METER_AWS_REGION: props.region,
        COGNITO_USER_POOL_ID: props.userPool.userPoolId,
        COGNITO_APP_CLIENT_ID: props.appClient.userPoolClientId,
        CONSOLE_ALLOWED_USERS: props.allowedUsers.join(','),
        CONSOLE_ADMIN_GROUP: props.adminGroup,
        CONSOLE_VIEWER_GROUP: props.viewerGroup,
        // String 'true'/'false'; the BFF parses fail-closed (only literal 'false' opens reads).
        CONSOLE_REQUIRE_AUTH_FOR_READS: String(props.requireAuthForReads),
      },
    });
    this.bffFn = bffFn;

    // Public HTTP API: $default route → BFF Lambda, no authorizer (auth is in-app: Cognito on
    // writes). The integration grants invoke to apigateway.amazonaws.com scoped by SourceArn,
    // not a public principal, so the lambda-public-access guardrail does not fire.
    const api = new apigwv2.HttpApi(this, 'ConsoleApi', {
      apiName: props.apiName,
      defaultIntegration: new integrations.HttpLambdaIntegration('BffIntegration', bffFn),
    });

    const accessLogs = new logs.LogGroup(this, 'AccessLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const stage = api.defaultStage?.node.defaultChild as apigwv2.CfnStage;
    stage.accessLogSettings = {
      destinationArn: accessLogs.logGroupArn,
      format: JSON.stringify({
        requestId: '$context.requestId',
        ip: '$context.identity.sourceIp',
        method: '$context.httpMethod',
        path: '$context.path',
        status: '$context.status',
        latency: '$context.responseLatency',
      }),
    };
    stage.defaultRouteSettings = {
      throttlingRateLimit: 50,
      throttlingBurstLimit: 100,
    };

    // HTTP API $default stage has no stage path prefix, so apiEndpoint is the site root.
    this.consoleUrl = api.apiEndpoint;
  }
}
