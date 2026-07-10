# Security

The reference defaults favor private networking, scoped identity, and encrypted managed services.

## Defaults

- The Terraform module keeps the EKS endpoint private-only unless
  `cluster_endpoint_public_access_cidrs` is set. The Quick Start
  `scripts/deploy-infra.sh` wrapper sets that allow list to the caller's current
  public IPv4 `/32` by default so post-Terraform `kubectl` and Helm deployment
  commands can run outside the VPC. Set
  `CLUSTER_ENDPOINT_PUBLIC_ACCESS_CIDRS=private` when deploying from a bastion,
  VPN, or CI network that can reach the private endpoint.
- Nodes run in private subnets.
- RDS and ElastiCache are not publicly accessible.
- S3 public access is blocked.
- S3, RDS, Redis, ECR, and Secrets Manager use KMS-backed encryption.
- OSMO service pods use IRSA where the Helm charts support it.
- Karpenter uses EKS Pod Identity for controller AWS permissions and creates GPU nodes only in private subnets selected by cluster discovery tags.
- GPU nodes require IMDSv2, use encrypted gp3 root volumes, and do not receive public IP addresses from the private subnet configuration.
- GPU nodes are tainted with `nvidia.com/gpu=true:NoSchedule`; GPU workloads that request `nvidia.com/gpu` receive the matching toleration through Kubernetes admission.
- The AWS EFA device plugin also tolerates the GPU taint so EFA-capable GPU nodes can register `vpc.amazonaws.com/efa` without weakening the workload taint boundary.
- The EKS node security group allows self-referenced all-traffic ingress and
  egress for EFA/NCCL and MPI workloads. This broadens node-to-node traffic only
  inside the node security group and is required for multi-node EFA validation.
- OSMO image pulls use an NGC Kubernetes image pull secret created by `infra/kubernetes/deploy-osmo.sh`; the key is read from `NGC_API_KEY` or `NGC_API_KEY_FILE` and is not stored in Terraform state.
- OSMO 6.2 workflow runtime storage still requires an access-key credential; Terraform creates a least-privilege IAM user scoped to the artifact bucket and KMS key, stores the secret in AWS Secrets Manager, and the deploy wrapper writes it into OSMO's encrypted workflow config.
- PodMonitor resources stay disabled unless the Prometheus Operator CRD is present and `ENABLE_POD_MONITOR=true`.
- Ephemeral reproducibility defaults enable clean teardown for S3, ECR, and the runtime secret. Override `s3_force_destroy`, `ecr_force_delete`, and `secret_recovery_window_in_days` for shared environments that need retention controls.

## Secrets

Terraform creates runtime credentials and stores them in AWS Secrets Manager. The deployment wrapper reads those values and creates Kubernetes secrets needed by the OSMO Helm charts. Terraform state contains generated secret material, including the OSMO 6.2 workflow data access key, so use an encrypted remote backend for shared environments. Do not commit generated `terraform.tfvars`, Terraform state, kubeconfigs, Helm values, OSMO tokens, NGC API key files, or Hugging Face tokens.

## Public Ingress

This repo does not create public application ingress in the baseline. Use `kubectl port-forward` for initial validation. If public UI/API exposure is needed later, add it through a separate reviewed change with TLS, authentication, narrow source ranges, and WAF where appropriate.

Narrow public EKS API endpoint access for deployment administration is not
application ingress. Keep it limited to administrator `/32` or trusted CIDRs and
do not use `0.0.0.0/0`.

The optional `infra/ingress` Terraform root creates HTTPS access for the OSMO admin UI through AWS Load Balancer Controller, ACM, Route 53, and an ALB-backed Kubernetes Ingress. It requires a non-empty `allowed_cidrs` list and rejects `0.0.0.0/0`; keep this allow list limited to trusted administrator networks.
