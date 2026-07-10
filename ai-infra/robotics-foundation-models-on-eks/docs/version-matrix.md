# Version Matrix

`versions.yaml` is the source of truth for pinned and tested versions.

| Component | Version |
| --- | --- |
| OSMO | `6.2.10` |
| OSMO Helm repo | `https://helm.ngc.nvidia.com/nvidia/osmo` |
| OSMO Helm charts | `1.2.1` |
| OSMO image tag | `6.2` |
| Isaac Lab image | `nvcr.io/nvidia/isaac-lab:2.2.0` |
| KAI Scheduler Helm chart | `oci://ghcr.io/kai-scheduler/kai-scheduler/kai-scheduler` |
| KAI Scheduler | `v0.13.0` |
| Karpenter Helm chart | `oci://public.ecr.aws/karpenter/karpenter` |
| Karpenter | `1.12.0` |
| NVIDIA GPU Operator | `v26.3.1` |
| AWS EFA device plugin | `v0.5.26` |
| EFA NCCL benchmark image | `public.ecr.aws/hpc-cloud/nccl-tests:latest` |
| EFA NCCL benchmark NCCL package | `2.28.9-1+cuda13.0` |
| EFA DDP benchmark image | `public.ecr.aws/deep-learning-containers/pytorch-training:2.9.0-gpu-py312-cu130-ubuntu22.04-ec2-v1.11` |
| EKS AL2023 NVIDIA AMI | `amazon-eks-node-al2023-x86_64-nvidia-1.35-v20260423` |
| G7e GPU pool | `g7e.2xlarge`, `g7e.4xlarge`, `g7e.8xlarge`, `g7e.12xlarge`, `g7e.24xlarge`, `g7e.48xlarge` |
| EFA-capable G7e sizes | `g7e.8xlarge`, `g7e.12xlarge`, `g7e.24xlarge`, `g7e.48xlarge` |
| G6e GPU/EFA pool | `g6e.8xlarge` |
| OSMO cookbook ref | `c2c30e55f84969fff55d51cd2044a03d40d6a1a5` |
| GR00T repository ref | `NVIDIA/Isaac-GR00T@ead52833afbbf4243f8cd5e7664f48a94de03b19` |
| GR00T runtime image | `nvcr.io/nvidia/pytorch:25.03-py3` |
| OpenPI repository ref | `Physical-Intelligence/openpi@650c5b0283a49c42784fb5055a0507da2c6d347d` |
| OpenPI runtime image | `nvcr.io/nvidia/pytorch:25.03-py3` |
| Cosmos Reason2 NIM image | `nvcr.io/nim/nvidia/cosmos-reason2-2b:1.7.0` |
| Cosmos Reason2 model | `nvidia/cosmos-reason2-2b` |
| HY-World 2.0 repository ref | `Tencent-Hunyuan/HY-World-2.0@49c1ab648b251814e984cdfb6eb8707705375920` |
| HY-World 2.0 model ID | `tencent/HY-World-2.0` |
| HY-World 2.0 model subfolder | `HY-WorldMirror-2.0` |
| HY-World 2.0 runtime image | `nvcr.io/nvidia/pytorch:25.03-py3` |
| Lyra repository ref | `nv-tlabs/lyra@52e507988ebcccb9bd5e039f31d2b985adc310c7` |
| Lyra model ID | `nvidia/Lyra-2.0` |
| Lyra runtime image | `nvcr.io/nvidia/pytorch:25.03-py3` |
| MoGe repository ref | `microsoft/MoGe@07444410f1e33f402353b99d6ccd26bd31e469e8` |
| Physical AI scaffolding sample ref | `8dd3a27eaf00adab7f437fcb8a1a8f9c715cb050` |
| Embodied AI platform sample ref | `09c788feb6203ac40ddb44d892c01c4a278cedcb` |
| EKS | `1.35` |
| Terraform CLI | `>= 1.5.0, < 2.0.0` |
| AWS provider | `~> 5.0` |
| VPC module | `~> 5.21` |
| EKS module | `~> 20.37` |
| RDS module | `~> 6.10` |

Upgrade policy:

- Update `versions.yaml`.
- Run the clean-account E2E sequence from `docs/reproducibility.md`.
- Document any behavior change in the PR.
- Keep OSMO compatibility notes even after temporary workarounds are removed.
