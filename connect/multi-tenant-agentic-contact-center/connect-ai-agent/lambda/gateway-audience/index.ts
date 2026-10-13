// ============================================================================
// AnyCompanyPay AgentCore Gateway — allowedAudience converger (CFN custom resource)
// ============================================================================
// CloudFormation cannot set the gateway's CUSTOM_JWT `allowedAudience` to the
// gateway's OWN id (Amazon Connect puts the gateway id in the token `aud`), so
// the AWS::BedrockAgentCore::Gateway resource is created WITHOUT an audience and
// this custom resource fills it in immediately after.
//
// It is a read-modify-write of the SAME gateway: GetGateway -> add the gateway id
// to allowedAudience -> UpdateGateway. Nothing about the gateway config is
// duplicated in CDK — we echo back whatever GetGateway returns and only touch the
// one field. On Delete it is a no-op (CloudFormation deletes the gateway itself).
//
// Invoked by a cr.Provider (the provider framework sends the CloudFormation
// response); this handler only implements the logic and returns/raises.
// ----------------------------------------------------------------------------
import {
  BedrockAgentCoreControlClient,
  GetGatewayCommand,
  UpdateGatewayCommand,
} from "@aws-sdk/client-bedrock-agentcore-control";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  const requestType: string = event?.RequestType ?? "Create";
  const props = event?.ResourceProperties ?? {};
  const gatewayId: string = props.GatewayId;
  // Region is supplied by the stack (Region: this.region) and always present in
  // the Lambda env (AWS_REGION). No hardcoded fallback.
  const region: string = props.Region || process.env.AWS_REGION!;

  // On delete, do nothing — the Gateway resource is removed by CloudFormation.
  if (requestType === "Delete") {
    return { PhysicalResourceId: gatewayId || event?.PhysicalResourceId };
  }
  if (!gatewayId) throw new Error("GatewayId property is required");

  const client = new BedrockAgentCoreControlClient({ region });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g: any = await client.send(new GetGatewayCommand({ gatewayIdentifier: gatewayId }));

  const authorizer = g.authorizerConfiguration ?? {};
  const jwt = authorizer.customJWTAuthorizer ?? {};
  // The gateway is created with a placeholder audience (CUSTOM_JWT must define at
  // least one of allowedAudience/allowedClients/allowedScopes/CustomClaims at
  // create). The desired end state is exactly the gateway's own id (Amazon Connect
  // puts the gateway id in the token `aud`), so replace rather than merge — this
  // drops the create-time placeholder and any stale values.
  const audience: string[] = [gatewayId];

  // Echo the current desired state back, changing only allowedAudience. Only
  // include optional top-level fields when present so we don't send undefineds.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const input: any = {
    gatewayIdentifier: gatewayId,
    name: g.name,
    roleArn: g.roleArn,
    protocolType: g.protocolType,
    protocolConfiguration: g.protocolConfiguration,
    authorizerType: g.authorizerType,
    authorizerConfiguration: { customJWTAuthorizer: { ...jwt, allowedAudience: audience } },
  };
  if (g.interceptorConfigurations) input.interceptorConfigurations = g.interceptorConfigurations;
  if (g.description) input.description = g.description;
  if (g.exceptionLevel) input.exceptionLevel = g.exceptionLevel;
  if (g.kmsKeyArn) input.kmsKeyArn = g.kmsKeyArn;

  await client.send(new UpdateGatewayCommand(input));
  console.log(`Set allowedAudience for gateway ${gatewayId}: ${audience.join(", ")}`);

  return {
    PhysicalResourceId: gatewayId,
    Data: { GatewayId: gatewayId, AllowedAudience: audience.join(",") },
  };
};
