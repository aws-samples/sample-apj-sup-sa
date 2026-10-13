import { SSMClient, GetParameterCommand, PutParameterCommand } from "@aws-sdk/client-ssm";
import { ECSClient, UpdateServiceCommand } from "@aws-sdk/client-ecs";

// CloudFormation custom-resource handler that MERGES a patch into the shared SSM
// runtime-config parameter (the JSON the SPA reads as /auth-config.json), then
// forces an ECS redeploy so running tasks pick it up. Same pattern the app and
// connect stacks use — a deep get/merge/put keeps each module's keys intact.
const ssm = new SSMClient({});
const ecs = new ECSClient({});

type Props = { ParameterName: string; Patch: string; EcsCluster?: string; EcsService?: string };

async function readParam(name: string): Promise<Record<string, unknown>> {
  try {
    const res = await ssm.send(new GetParameterCommand({ Name: name }));
    return JSON.parse(res.Parameter?.Value ?? "{}");
  } catch (e) {
    if ((e as { name?: string }).name === "ParameterNotFound") return {};
    throw e;
  }
}

function deepMerge(base: Record<string, unknown>, patch: Record<string, unknown>) {
  const out = { ...base, ...patch };
  if (base.connect && patch.connect && typeof base.connect === "object" && typeof patch.connect === "object") {
    out.connect = { ...(base.connect as object), ...(patch.connect as object) };
  }
  return out;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const handler = async (event: any) => {
  const type: string = event.RequestType;
  const props = (event.ResourceProperties ?? {}) as Props;
  const name = props.ParameterName;
  if (type === "Delete") return { PhysicalResourceId: event.PhysicalResourceId ?? name };

  const patch = JSON.parse(props.Patch || "{}") as Record<string, unknown>;
  const merged = deepMerge(await readParam(name), patch);
  await ssm.send(
    new PutParameterCommand({ Name: name, Type: "String", Overwrite: true, Value: JSON.stringify(merged) })
  );

  if (props.EcsCluster && props.EcsService) {
    try {
      await ecs.send(
        new UpdateServiceCommand({ cluster: props.EcsCluster, service: props.EcsService, forceNewDeployment: true })
      );
    } catch (e) {
      console.error("force ECS redeploy failed (non-fatal)", e);
    }
  }
  return { PhysicalResourceId: name, Data: { ParameterName: name } };
};
