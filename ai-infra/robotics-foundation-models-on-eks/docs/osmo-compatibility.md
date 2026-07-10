# OSMO Compatibility

This repo tracks NVIDIA OSMO as an external dependency. It does not carry local OSMO patches or invoke upstream Kubernetes deployment scripts as the primary AWS path.

## NVIDIA/OSMO PR #894

[NVIDIA/OSMO PR #894](https://github.com/NVIDIA/OSMO/pull/894) addresses AWS-relevant deployment behavior in the upstream script path.

Until the fix is present in the pinned OSMO release, this repo avoids the problematic path by installing the Helm charts directly with AWS-owned values and explicit bootstrap steps:

- Do not invoke upstream `deploy-k8s.sh` as the primary deployment mechanism.
- Set `podMonitor.enabled=false` unless Prometheus Operator CRDs are present and `ENABLE_POD_MONITOR=true`.
- Create the `backend-operator` user before generating the backend token.
- Generate the backend token with `--user backend-operator`.
- Fail fast if user creation, token generation, Helm install, or rollout checks fail.

## Workflow Storage Credential

OSMO 6.2 workflow configuration accepts static data credentials for workflow data, logs, and apps. It does not yet propagate AWS SDK default credentials or session tokens into the workflow runtime. For a reproducible AWS smoke path, this repo creates a least-privilege IAM access key limited to the artifact bucket and KMS key, then configures OSMO workflow storage through the deploy wrapper.

When a pinned OSMO release supports keyless AWS workflow storage, replace this compatibility key with IRSA or pod identity for workflow pods.

## Workflow Callback URL

OSMO 6.2 uses `SERVICE.service_base_url` for `osmo-ctrl` workflow callbacks, including JWT refresh and workflow log websocket connections. The baseline deployment intentionally avoids public ingress. In that mode, `infra/kubernetes/deploy-osmo.sh` sets the callback URL to the in-cluster `osmo-logger` service because the direct API service does not serve the logger websocket route.

If a later phase adds an authenticated ingress or supported Envoy routing path, update `OSMO_WORKFLOW_CALLBACK_URL` to that unified service URL and validate the CPU smoke workflow again.

## PodGroup CRD

OSMO 6.2 emits `scheduling.run.ai/v2alpha2` `PodGroup` objects from the KAI scheduler path even for the CPU-only smoke workflow. This repo installs the pinned KAI Scheduler chart by default before OSMO and sets the backend scheduler name to `kai-scheduler`.

KAI Scheduler is pinned to `v0.13.0` for OSMO 6.2 compatibility. `v0.13.0` is also the minimum version named in NVIDIA's April 2026 KAI security bulletin. Upgrade KAI only through an explicit PR that reruns the OSMO smoke workflow through real KAI scheduling.

The CPU baseline sets KAI `admission.gpuPodRuntimeClassName` to an empty string so CPU-only OSMO workflow pods are not mutated to `runtimeClassName: nvidia`. Set `KAI_GPU_POD_RUNTIME_CLASS_NAME` only after the target worker nodes have the matching container runtime handler.

For constrained local experiments, set `OSMO_INSTALL_KAI=false` before `infra/kubernetes/deploy-osmo.sh` to use the Kubernetes `default-scheduler` path with the minimal PodGroup compatibility CRD. Do not use that fallback for the AWS reference validation path.

## Autoscaler-Aware GPU Capacity

OSMO 6.2 validates workflow resources against capacity already visible to the OSMO backend. Karpenter, by design, creates GPU nodes only after Kubernetes sees pending pods. That means an OSMO workflow that requires a large GPU node can be rejected before Karpenter has a chance to provision the node.

This reference works around that boundary by using `infra/kubernetes/prewarm-gpu-node.sh` before OSMO-submitted GPU workflows. The prewarm pod is not part of the training pipeline; it only makes the target GPU platform visible so OSMO resource validation can pass. After the workflow completes, `infra/kubernetes/wait-gpu-node-cleanup.sh` deletes the prewarm pod, removes residual OSMO workflow pods on the target GPU nodes, and verifies that Karpenter removes empty GPU nodes.

A useful upstream OSMO contribution would be an autoscaler-aware capacity provider or deferred resource validation mode. For Karpenter, OSMO could inspect `NodePool` and `EC2NodeClass` constraints, model provisionable instance capacity, and submit the Kubernetes workload so Karpenter can provision nodes from the resulting pending pods. That would remove the need for prewarm pods while preserving OSMO's resource validation model.

After PR #894 is included in a pinned OSMO release, keep this note for traceability but remove any temporary compatibility branching that is no longer needed.
