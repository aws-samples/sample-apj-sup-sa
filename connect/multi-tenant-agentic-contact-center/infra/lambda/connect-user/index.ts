// ============================================================================
// AnyCompanyPay — Amazon Connect user provisioner (CFN custom resource)
// ============================================================================
// Creates an Amazon Connect (CONNECT_MANAGED) user during the stack deploy, with
// a password GENERATED into Secrets Manager. The plaintext password never appears
// in the CloudFormation template, the CDK synth output, or any script — this
// Lambda reads it from Secrets Manager at deploy time and passes it straight to
// connect:CreateUser.
//
// Invoked by a cr.Provider (the provider framework sends the CloudFormation
// response); this handler only implements the logic and returns/raises.
//
// Convergence model:
//   - Create: if a user with this username already exists (e.g. left over from
//     the legacy provision-agent.sh), DELETE it and recreate, so the live
//     password always matches the secret. Otherwise just create.
//   - Update: ensure the user exists (create if missing); if it exists, only
//     reconcile the routing + security profiles. We do NOT reset the password on
//     update — Connect has no set-password API, and the generated secret value is
//     stable across stack updates, so the two stay in sync.
//   - Delete: best-effort delete the user (ignore if already gone).
//
// SecurityProfileName / RoutingProfileName are resolved to ids by name. If the
// routing profile name is not found, the first available routing profile is used
// (so an Admin user still gets *a* valid routing profile).
// ----------------------------------------------------------------------------
import {
  ConnectClient,
  CreateUserCommand,
  DeleteUserCommand,
  ListUsersCommand,
  ListSecurityProfilesCommand,
  ListRoutingProfilesCommand,
  UpdateUserRoutingProfileCommand,
  UpdateUserSecurityProfilesCommand,
} from "@aws-sdk/client-connect";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";

const region = process.env.AWS_REGION!; // always set by the Lambda runtime
const connect = new ConnectClient({ region });
const secrets = new SecretsManagerClient({ region });

async function findUserId(instanceId: string, username: string): Promise<string | undefined> {
  let next: string | undefined;
  do {
    const r = await connect.send(new ListUsersCommand({ InstanceId: instanceId, MaxResults: 1000, NextToken: next }));
    const hit = (r.UserSummaryList ?? []).find((u) => u.Username === username);
    if (hit?.Id) return hit.Id;
    next = r.NextToken;
  } while (next);
  return undefined;
}

async function findSecurityProfileId(instanceId: string, name: string): Promise<string> {
  let next: string | undefined;
  do {
    const r = await connect.send(
      new ListSecurityProfilesCommand({ InstanceId: instanceId, MaxResults: 1000, NextToken: next })
    );
    const hit = (r.SecurityProfileSummaryList ?? []).find((p) => p.Name === name);
    if (hit?.Id) return hit.Id;
    next = r.NextToken;
  } while (next);
  throw new Error(`Security profile "${name}" not found on instance ${instanceId}`);
}

async function findRoutingProfileId(instanceId: string, name: string): Promise<string> {
  let next: string | undefined;
  let first: string | undefined;
  do {
    const r = await connect.send(
      new ListRoutingProfilesCommand({ InstanceId: instanceId, MaxResults: 1000, NextToken: next })
    );
    for (const p of r.RoutingProfileSummaryList ?? []) {
      if (!first && p.Id) first = p.Id;
      if (p.Name === name && p.Id) return p.Id;
    }
    next = r.NextToken;
  } while (next);
  if (first) {
    console.log(`Routing profile "${name}" not found; falling back to first available (${first}).`);
    return first;
  }
  throw new Error(`No routing profiles found on instance ${instanceId}`);
}

async function getPassword(secretArn: string): Promise<string> {
  const r = await secrets.send(new GetSecretValueCommand({ SecretId: secretArn }));
  const raw = r.SecretString ?? "";
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.password === "string") return parsed.password;
  } catch {
    /* not JSON — treat the whole string as the password */
  }
  if (!raw) throw new Error("Secret has no SecretString");
  return raw;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  const requestType: string = event?.RequestType ?? "Create";
  const p = event?.ResourceProperties ?? {};
  const instanceId: string = p.InstanceId;
  const username: string = p.Username;
  const physicalId = `connect-user:${instanceId}:${username}`;

  if (!instanceId || !username) throw new Error("InstanceId and Username are required");

  if (requestType === "Delete") {
    const id = await findUserId(instanceId, username);
    if (id) {
      await connect.send(new DeleteUserCommand({ InstanceId: instanceId, UserId: id }));
      console.log(`Deleted Connect user ${username} (${id})`);
    }
    return { PhysicalResourceId: event?.PhysicalResourceId ?? physicalId };
  }

  const securityProfileId = await findSecurityProfileId(instanceId, p.SecurityProfileName);
  const routingProfileId = await findRoutingProfileId(instanceId, p.RoutingProfileName);
  const identityInfo: Record<string, string> = { FirstName: p.FirstName || "AnyCompanyPay", LastName: p.LastName || "User" };
  if (p.Email) identityInfo.Email = p.Email;

  const existingId = await findUserId(instanceId, username);

  // On Update, if the user already exists, only reconcile profiles (leave the
  // password — and therefore the secret — untouched).
  if (requestType === "Update" && existingId) {
    await connect.send(
      new UpdateUserRoutingProfileCommand({ InstanceId: instanceId, UserId: existingId, RoutingProfileId: routingProfileId })
    );
    await connect.send(
      new UpdateUserSecurityProfilesCommand({ InstanceId: instanceId, UserId: existingId, SecurityProfileIds: [securityProfileId] })
    );
    console.log(`Reconciled profiles for existing Connect user ${username} (${existingId})`);
    return { PhysicalResourceId: physicalId, Data: { Username: username, UserId: existingId } };
  }

  // Create (or Create-time reconcile of a leftover user): ensure the live
  // password matches the generated secret by (re)creating the user with it.
  if (existingId) {
    await connect.send(new DeleteUserCommand({ InstanceId: instanceId, UserId: existingId }));
    console.log(`Deleted pre-existing Connect user ${username} (${existingId}) to reset password from secret`);
  }

  const password = await getPassword(p.SecretArn);
  const created = await connect.send(
    new CreateUserCommand({
      InstanceId: instanceId,
      Username: username,
      Password: password,
      PhoneConfig: { PhoneType: "SOFT_PHONE", AutoAccept: false },
      SecurityProfileIds: [securityProfileId],
      RoutingProfileId: routingProfileId,
      IdentityInfo: identityInfo,
    })
  );
  console.log(`Created Connect user ${username} (${created.UserId})`);
  return { PhysicalResourceId: physicalId, Data: { Username: username, UserId: created.UserId ?? "" } };
};
