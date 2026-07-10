# Observability

OSMO 6.2 expects Prometheus, Grafana, imported OSMO dashboards, and backend config fields named `grafana_url` and `dashboard_url`. On AWS, the deployable minimal mapping is to keep Prometheus-compatible scraping in the EKS cluster, remote write to Amazon Managed Service for Prometheus (AMP), and use Amazon Managed Grafana (AMG) as the Grafana endpoint.

This path follows the AWS-documented [existing Prometheus remote write](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-onboard-ingest-metrics-existing-Prometheus.html) onboarding flow: the in-cluster Prometheus instance remains responsible for scraping OSMO targets, but Grafana queries AMP rather than using the in-cluster Prometheus server as the long-term query and storage backend.

References:

- [OSMO Add Observability](https://nvidia.github.io/OSMO/release/6.2/deployment_guide/install_backend/observability.html)
- [AMP remote write from existing Prometheus](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-onboard-ingest-metrics-existing-Prometheus.html)
- [AMP IRSA setup](https://docs.aws.amazon.com/prometheus/latest/userguide/set-up-irsa.html)
- [AMG Prometheus data source](https://docs.aws.amazon.com/grafana/latest/userguide/prometheus-data-source.html)
- [AMG permissions for AWS data sources and users](https://docs.aws.amazon.com/grafana/latest/userguide/AMG-manage-permissions.html)

Deploy this path through [infra/observability/](../infra/observability/README.md):

```bash
cp infra/observability/terraform.tfvars.example infra/observability/terraform.tfvars
infra/observability/deploy.sh -auto-approve
```

The Terraform root creates AMP, AMG, the Prometheus remote-write IRSA role, kube-prometheus-stack, AMG SSO role associations, and an AMG service account. The deploy wrapper then enables the OSMO PodMonitor resources, creates the DCGM exporter ServiceMonitor when the GPU Operator namespace exists, creates a short-lived service-account token for Grafana API provisioning, configures the AMP data source, imports OSMO dashboards, adds an `AWS OSMO Overview` dashboard with scrape, workflow, and GPU panels, and updates OSMO backend `grafana_url`.

AMG uses AWS IAM Identity Center (`AWS_SSO`). It does not create a local Grafana id/password, and this repo does not create IAM users for Grafana. For human browser login, create or sync users and groups in IAM Identity Center, find their Identity Store `UserId` or `GroupId`, and set `admin_user_ids`, `admin_group_ids`, `editor_group_ids`, or `viewer_group_ids` in `infra/observability/terraform.tfvars`. See [infra/observability/README.md](../infra/observability/README.md#browser-login) for the CLI lookup commands and tfvars example.

The imported OSMO dashboard JSONs expect a Prometheus `cluster` label and, for DCGM workflow metrics, Prometheus Operator's default exported label shape such as `exported_namespace`, `exported_pod`, and `exported_container`. This reference sets Prometheus `externalLabels.cluster` to the EKS cluster name before AMP remote write and leaves the DCGM ServiceMonitor on the default `honorLabels: false` behavior so AMG queries stay compatible with the upstream dashboard filters.

Runtime validation passed on 2026-05-05 against `example-osmo-eks` in `ap-northeast-2`, with GPU observability revalidated on 2026-05-06:

- Installed `prometheus-community/kube-prometheus-stack` chart `84.5.0` in namespace `monitoring` with Prometheus service account `amp-iamproxy-ingest-service-account`.
- Created AMP workspace `ws-example0000-0000-4000-8000-000000000000` with alias `example-osmo-eks-observability`.
- Configured Prometheus `remoteWrite` with SigV4 to `https://aps-workspaces.ap-northeast-2.amazonaws.com/workspaces/ws-example0000-0000-4000-8000-000000000000/api/v1/remote_write`.
- Enabled OSMO service and backend PodMonitor resources: `otel-monitor` and `osmo-backend-otel-monitor`.
- Enabled DCGM exporter ServiceMonitor `nvidia-dcgm-exporter`.
- Verified local Prometheus and direct AMP queries for `up{namespace="osmo"}` returned five healthy OSMO scrape targets.
- Created AMG workspace `g-example1234`, endpoint `https://g-example1234.grafana-workspace.ap-northeast-2.amazonaws.com`, with AMP data source `AMP example-osmo-eks`.
- Verified the AMG data source proxy query for `up{namespace="osmo"}` returned the same five healthy OSMO targets.
- Submitted post-observability workflow `aws-osmo-smoke-9` on 2026-05-06; AMG data source proxy returned 21 `osmo-workflows` pod series over 24h, including `aws-osmo-smoke-9`.
- Submitted post-observability GPU workflow `aws-osmo-gpu-smoke-3` on 2026-05-06; the workflow ran a 120 second CUDA burn and completed in 219 seconds.
- Verified AMG data source proxy queries against AMP returned max-over-1h DCGM values: `GPU_UTIL=100`, `FB_USED=1070`, `POWER_USAGE=537.003`, and `GPU_TEMP=67`.
- Revalidated the dashboard label contract after switching DCGM to Prometheus Operator default label handling: AMP returned `count(kube_pod_info{cluster="example-osmo-eks"})=48`, `count(up{cluster="example-osmo-eks",namespace="osmo"})=5`, and `DCGM_FI_DEV_GPU_UTIL{cluster="example-osmo-eks",exported_namespace="osmo-workflows"}` for a GPU pod in `osmo-workflows`.
- Imported the official OSMO `Workflow Resources` and `Backend Operator` dashboards into AMG.
- Updated OSMO backend `default` so `grafana_url` points at the AMG workspace URL. `dashboard_url` remains empty because Kubernetes Dashboard is not part of this reference path.

A representative GPU panel capture is under [infra/observability/artifacts](../infra/observability/artifacts/). To reproduce the full view, open the AMG `AWS OSMO Overview` dashboard, set the time range around the GPU workflow run, and use the GPU panels that query `exported_namespace="osmo-workflows"` DCGM metrics.

Dashboard behavior:

- `AWS OSMO Overview` is the AWS-facing operations dashboard and should show scrape data immediately with `up{namespace="osmo"}`. Its GPU panels use DCGM metrics from the `nvidia-dcgm-exporter` ServiceMonitor and populate after a GPU workflow runs.
- `Workflow Resources` shows active workflow pod CPU, memory, storage, and GPU/DCGM metrics. It can be empty when no workflow pods are running in `osmo-workflows`.
- `Backend Operator` shows backend pod resources and backend queue, event, and job metrics. Queue and job panels only populate after backend activity emits those series.
- `Observability Dashboard` is the upstream OSMO service dashboard. The pinned JSON assumes upstream namespace and metric conventions, so treat it as an upstream reference unless the local deployment matches those assumptions.

The AMP workspace, AMG workspace, IAM roles, `monitoring` namespace, Prometheus stack, and OSMO PodMonitor settings are intentionally left deployed for manual inspection.
