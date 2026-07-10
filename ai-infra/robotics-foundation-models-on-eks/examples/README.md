# Examples

Each directory is a self-contained OSMO or AWS platform example. Directory
names use lowercase kebab-case and focus on the workload, dataset, task, or
scaling property. Execution details belong in the example README and scripts,
not as the primary folder-name axis.

Top-level directories are categories:

| Directory | Contents |
| --- | --- |
| [policy-training](policy-training/) | Robot policy fine-tuning and training examples. |
| [simulation](simulation/) | Simulation training and multistage task pipelines. |
| [smoke](smoke/) | Minimal CPU and GPU workflow checks. |
| [workflow-patterns](workflow-patterns/) | Small workflows that demonstrate OSMO DAG patterns. |
| [world-models](world-models/) | World model inference and reconstruction examples. |

Single-workflow OSMO examples use `workflow.yaml`. Long bootstrap or workload
logic should live in a local script such as `entry.sh` and be mounted by the
workflow, rather than embedded inline in YAML. Multistage OSMO examples keep
ordered workflow YAMLs under `workflows/`. Keep `run.sh` only where an existing
example needs a compatibility wrapper for parameter handling, multi-step
submission, waiting, cleanup, or artifact collection.

Example-specific run notes stay next to the example. Each example README should
include a complete run sequence from the repository root, including any required
prewarm, credentials, submission, and cleanup notes. Selected examples keep a
small `artifacts/` directory with representative output; detailed sample run
results are summarized in the contribution pull request.

## Smoke And Workflow Basics

| Example | Purpose | Output |
| --- | --- | --- |
| [cpu-workflow](smoke/cpu-workflow/README.md) | CPU-only smoke workflow. | Logs only. |
| [gpu-workflow](smoke/gpu-workflow/README.md) | GPU smoke workflow that runs `nvidia-smi`. | Logs only. |

## Workflow Patterns

| Example | Purpose | Output |
| --- | --- | --- |
| [parallel-eval](workflow-patterns/parallel-eval/README.md) | OSMO `groups` fan-out/fan-in reference. | Synthetic summary dataset. |
| [sequential-policy](workflow-patterns/sequential-policy/README.md) | CPU dataset inspect, GPU policy checkpoint task, CPU package step. | Synthetic policy artifact dataset. |

## Policy Training

| Example | Purpose | Output |
| --- | --- | --- |
| [gr00t-so100-finetune](policy-training/gr00t-so100-finetune/README.md) | PASK-aligned GR00T fine-tune on the SO100 cube_to_bowl_5 dataset. | [Before/after replay preview](policy-training/gr00t-so100-finetune/artifacts/result/traj_0_step_1_vs_10k.jpeg). |
| [gr00t-so100-efa-multinode-finetune](policy-training/gr00t-so100-efa-multinode-finetune/README.md) | OSMO GR00T SO100 fine-tune scaled across two G6e EFA nodes. | Rank-0 runtime logs and output dataset artifacts. |
| [openpi-libero-lora](policy-training/openpi-libero-lora/README.md) | PASK-aligned OpenPI LIBERO LoRA workflow. | [Action replay GIF](policy-training/openpi-libero-lora/artifacts/result/openpi-action-output-replay.gif). |

## Simulation And Task Pipelines

| Example | Purpose | Output |
| --- | --- | --- |
| [isaaclab-rsl-rl-video](simulation/isaaclab-rsl-rl-video/README.md) | Isaac Lab RSL-RL training with before/after videos. | [Before/after GIF](simulation/isaaclab-rsl-rl-video/artifacts/result/videos/before-after-comparison.gif). |
| [nut-pouring-pipeline](simulation/nut-pouring-pipeline/README.md) | Multistage upstream OSMO nut pouring pipeline with MimicGen, Cosmos Transfer, LeRobot conversion, and GR00T fine-tuning. | [Policy rollout GIF](simulation/nut-pouring-pipeline/artifacts/nutpouring-policy-rollout-before-after.gif). |

## World Models

| Example | Purpose | Output |
| --- | --- | --- |
| [cosmos-reason2-nim](world-models/cosmos-reason2-nim/README.md) | World model VLM workflow using Cosmos Reason2 NIM and NVIDIA OSMO's NIM client/server pattern. | [Input preview](world-models/cosmos-reason2-nim/artifacts/input-preview.gif). |
| [hyworld2-worldmirror-recon](world-models/hyworld2-worldmirror-recon/README.md) | World model reconstruction workflow using HY-World 2.0 WorldMirror on the upstream Dining Table sample. | [Point-cloud preview](world-models/hyworld2-worldmirror-recon/artifacts/points-ply-preview.png). |
| [lyra2-dmd-single](world-models/lyra2-dmd-single/README.md) | World model generation workflow using Lyra-2.0 DMD and Gaussian-scene trajectory rendering. | [Generated-scene preview](world-models/lyra2-dmd-single/artifacts/output-combined-preview.gif). |

Submit single-workflow examples directly with
`osmo workflow submit examples/<category>/<name>/workflow.yaml`, or use
`examples/run-workflow.sh` when you want the repo wrapper to handle OSMO login,
credentials, submission, logs, and timeout handling. For multistage examples,
follow the example README or submit the numbered workflow files in order.

GPU and EFA workflows need visible G7e or G6e capacity before OSMO resource
validation. On a scale-to-zero cluster, OSMO rejects these submissions before a
pending pod can trigger Karpenter provisioning, with errors such as
`There are no resources in platform g7e-rtx-pro-6000 and pool default!`.
Use `infra/kubernetes/prewarm-gpu-node.sh` before submission and
`infra/kubernetes/wait-gpu-node-cleanup.sh` after completion. CPU-only examples
such as `cpu-workflow` and `parallel-eval` do not require prewarm.
