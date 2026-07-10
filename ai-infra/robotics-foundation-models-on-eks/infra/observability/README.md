# AWS Managed Observability

This optional Terraform root deploys the AWS managed observability path for OSMO:

- Amazon Managed Service for Prometheus (AMP) workspace.
- IRSA role that allows in-cluster Prometheus to `remote_write` metrics to AMP.
- `prometheus-community/kube-prometheus-stack` configured with short local retention and AMP SigV4 `remote_write`.
- Amazon Managed Grafana (AMG) workspace using AWS IAM Identity Center (`AWS_SSO`).
- AMG service account used by `infra/observability/deploy.sh` to provision the AMP data source and dashboards.

AMG does not create a local Grafana username or password. Browser access requires IAM Identity Center user or group IDs through `admin_user_ids`, `admin_group_ids`, `editor_group_ids`, or `viewer_group_ids`.

## Browser Login

Do not create an IAM user or an IAM password for Grafana browser login. With `AWS_SSO`, AMG uses AWS IAM Identity Center users and groups. Terraform grants those identities a Grafana role through `aws_grafana_role_association`.

If IAM Identity Center is already enabled, find the identity store and user or group IDs:

```bash
IDENTITY_CENTER_REGION="us-east-1"

aws sso-admin list-instances \
  --region "${IDENTITY_CENTER_REGION}" \
  --query 'Instances[].{InstanceArn:InstanceArn,IdentityStoreId:IdentityStoreId}' \
  --output table

IDENTITY_STORE_ID="d-xxxxxxxxxx"

aws identitystore list-users \
  --identity-store-id "${IDENTITY_STORE_ID}" \
  --region "${IDENTITY_CENTER_REGION}" \
  --query 'Users[].{UserName:UserName,DisplayName:DisplayName,UserId:UserId}' \
  --output table

aws identitystore list-groups \
  --identity-store-id "${IDENTITY_STORE_ID}" \
  --region "${IDENTITY_CENTER_REGION}" \
  --query 'Groups[].{DisplayName:DisplayName,GroupId:GroupId}' \
  --output table
```

Then put the selected IDs into `infra/observability/terraform.tfvars`:

```hcl
admin_user_ids = [
  "11111111-2222-3333-4444-555555555555"
]

admin_group_ids = [
  "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
]
```

After `infra/observability/deploy.sh` completes, open the `amg_workspace_url` output. The browser login goes through IAM Identity Center. The service account token created by the deploy wrapper is only for API provisioning of the data source and dashboards; it is not a human login credential and is intentionally short-lived.

```bash
cp infra/observability/terraform.tfvars.example infra/observability/terraform.tfvars

terraform -chdir=infra/core output -raw cluster_name
terraform -chdir=infra/core output -raw cluster_oidc_issuer_url
terraform -chdir=infra/core output -raw cluster_oidc_provider_arn

infra/observability/deploy.sh -auto-approve
```

The deploy wrapper applies this Terraform root, enables OSMO PodMonitor resources on the existing OSMO Helm releases, creates a short-lived AMG service account token, provisions an AMP data source, imports the pinned OSMO dashboards from `dashboards/`, creates an `AWS OSMO Overview` dashboard, and updates the OSMO backend `grafana_url`.

The dashboard JSON files in `dashboards/` are copied from NVIDIA OSMO `c2c30e55f84969fff55d51cd2044a03d40d6a1a5` under `docs/deployment_guide/dashboards/`.

The upstream dashboard JSONs expect a `cluster` label and use Prometheus Operator exported labels for workload DCGM metrics, for example `exported_namespace`, `exported_pod`, and `exported_container`. This Terraform root sets Prometheus `externalLabels.cluster` to `cluster_name` before AMP remote write. The deploy wrapper leaves the DCGM ServiceMonitor on the Prometheus Operator default `honorLabels: false` behavior so exporter-provided workload labels are exposed as `exported_*` labels instead of replacing target labels.

## Dashboard Scope

`AWS OSMO Overview` is the AWS-facing operations dashboard. It should show data immediately after deployment because it queries scrape health with `up{namespace="osmo"}` through the AMG AMP data source.

It also includes a recent workflow pod view based on `count_over_time(kube_pod_info{namespace="osmo-workflows"}[24h])`.

The deploy wrapper also creates a `nvidia-dcgm-exporter` ServiceMonitor when the GPU Operator namespace exists, so `AWS OSMO Overview` can show DCGM GPU utilization, framebuffer, power, and temperature metrics after a GPU workflow runs.

The imported upstream OSMO dashboards are workload specific:

- `Workflow Resources` shows resource and GPU metrics for active workflow pods, normally in `osmo-workflows`. It is expected to be empty when no workflow pods are running.
- `Backend Operator` shows backend operator pod resources plus queue, event, and job metrics. Backend resource panels should populate when the OSMO backend pods are scraped; queue and job panels only populate after backend activity emits those metrics.
- `Observability Dashboard` is the upstream OSMO service dashboard. Its pinned JSON assumes upstream namespace and metric conventions, so use it as an upstream reference unless the local deployment matches those assumptions.

## Runtime Validation

Status: Passed manually on 2026-05-05 before this Terraform root was added, then revalidated with GPU metrics on 2026-05-06.

Scope validated:

- AMP workspace `ws-example0000-0000-4000-8000-000000000000` in `ap-northeast-2`.
- In-cluster Prometheus `remoteWrite` configured to the AMP workspace endpoint with SigV4.
- OSMO PodMonitor resources `otel-monitor` and `osmo-backend-otel-monitor`.
- DCGM exporter ServiceMonitor `nvidia-dcgm-exporter`.
- Direct AMP query for `up{namespace="osmo"}` returned five healthy OSMO targets.
- AMG workspace `g-example1234` with an AMP Prometheus data source.
- AMG data source proxy query for `up{namespace="osmo"}` returned the same five healthy OSMO targets.
- Submitted post-observability workflow `aws-osmo-smoke-9`; AMG data source proxy returned 21 `osmo-workflows` pod series over 24h, including `aws-osmo-smoke-9`.
- Submitted post-observability GPU workflow `aws-osmo-gpu-smoke-3`; the workflow ran a 120 second CUDA burn on `NVIDIA RTX PRO 6000 Blackwell Server Edition` and completed in 219 seconds.
- AMG data source proxy queries against AMP returned max-over-1h DCGM values: `DCGM_FI_DEV_GPU_UTIL=100`, `DCGM_FI_DEV_FB_USED=1070`, `DCGM_FI_DEV_POWER_USAGE=537.003`, and `DCGM_FI_DEV_GPU_TEMP=67`.
- Revalidated the upstream dashboard label shape after removing DCGM `honorLabels`: AMP returned `cluster="example-osmo-eks"` on Kubernetes metrics and `exported_namespace="osmo-workflows"`, `exported_pod`, and `exported_container` on DCGM metrics for a GPU pod in `osmo-workflows`.
- Imported OSMO `Workflow Resources` and `Backend Operator` dashboards plus the `AWS OSMO Overview` dashboard.
- OSMO backend `default` configured with `grafana_url` set to the AMG workspace URL.

The live validation used `AWS_SSO` for AMG authentication and a short-lived service account token for API provisioning. No local AMG id/password exists.

## GPU Visual Validation

The representative AMG panel capture below was taken from the `AWS OSMO Overview` dashboard after `aws-osmo-gpu-smoke-3` completed. Raw query JSON and extra panel captures are intentionally omitted from this sample copy.

To reproduce the view manually:

- Open AMG workspace `g-example1234` and select the `AWS OSMO Overview` dashboard.
- Set the time range to `Last 1 hour` shortly after a GPU smoke run, or use the absolute range around `2026-05-06 14:04:27-14:06:28 KST` for `aws-osmo-gpu-smoke-3`.
- The GPU panels query DCGM metrics with `exported_namespace="osmo-workflows"` so they show workload pod metrics rather than the exporter pod itself.
- In Grafana Explore, the equivalent filter is `DCGM_FI_DEV_GPU_UTIL{exported_namespace="osmo-workflows"}` against the AMP data source.

![GPU utilization](artifacts/10-grafana-gpu-utilization-panel.png)
