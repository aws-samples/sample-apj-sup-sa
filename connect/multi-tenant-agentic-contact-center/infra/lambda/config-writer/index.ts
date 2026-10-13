import {
  SSMClient,
  GetParameterCommand,
  PutParameterCommand,
} from "@aws-sdk/client-ssm";
import { ECSClient, UpdateServiceCommand } from "@aws-sdk/client-ecs";

// CloudFormation custom-resource handler that MERGES a patch into the SSM
// runtime-config parameter (the JSON the SPA reads as /auth-config.json).
//
// Both stacks use this: the app stack writes Cognito keys, the connect stack
// writes the `connect` sub-object. Each does get -> deep-merge(own keys) -> put,
// so neither clobbers the other's keys and the two modules stay decoupled.
//
// Optionally forces a new ECS deployment afterwards so running tasks re-read the
// parameter (used by the connect stack once it has written the Connect config).

const ssm = new SSMClient({});
const ecs = new ECSClient({});

type Props = {
  ParameterName: string;
  // JSON string of the patch to merge (top-level keys + optional connect object)
  Patch: string;
  // optional: force ECS redeploy after writing
  EcsCluster?: string;
  EcsService?: string;
};

async function readParam(name: string): Promise<Record<string, unknown>> {
  try {
    const res = await ssm.send(new GetParameterCommand({ Name: name }));
    return JSON.parse(res.Parameter?.Value ?? "{}");
  } catch (e) {
    // Parameter not found on first write -> start empty.
    if ((e as { name?: string }).name === "ParameterNotFound") return {};
    throw e;
  }
}

function deepMergeConfig(
  base: Record<string, unknown>,
  patch: Record<string, unknown>
): Record<string, unknown> {
  const out = { ...base, ...patch };
  // Merge the nested `connect` object rather than replacing it.
  if (
    base.connect &&
    patch.connect &&
    typeof base.connect === "object" &&
    typeof patch.connect === "object"
  ) {
    out.connect = { ...(base.connect as object), ...(patch.connect as object) };
  }
  return out;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  const type: string = event.RequestType;
  const props = (event.ResourceProperties ?? {}) as Props;
  const name = props.ParameterName;

  // On delete we intentionally leave the parameter in place (the other stack may
  // still need its keys). The parameter itself is not owned by either stack.
  if (type === "Delete") {
    return { PhysicalResourceId: event.PhysicalResourceId ?? name };
  }

  const patch = JSON.parse(props.Patch || "{}") as Record<string, unknown>;
  const current = await readParam(name);
  const merged = deepMergeConfig(current, patch);

  await ssm.send(
    new PutParameterCommand({
      Name: name,
      Type: "String",
      Overwrite: true,
      Value: JSON.stringify(merged),
    })
  );

  // Best-effort: force running tasks to pick up the new config.
  if (props.EcsCluster && props.EcsService) {
    try {
      await ecs.send(
        new UpdateServiceCommand({
          cluster: props.EcsCluster,
          service: props.EcsService,
          forceNewDeployment: true,
        })
      );
    } catch (e) {
      console.error("force ECS redeploy failed (non-fatal)", e);
    }
  }

  return { PhysicalResourceId: name, Data: { ParameterName: name } };
};
