# MedGemma-27B benchmark

Per-model configuration for [`google/medgemma-27b-text-it`](https://huggingface.co/google/medgemma-27b-text-it),
a 27B-parameter clinical-NLP model. This folder is the **only place**
MedGemma-specific facts live — everything else is generic infrastructure
in [`src/vllm_ec2_bench/`](../../src/vllm_ec2_bench/).

## Layout

```
models/medgemma_27b/
├── __init__.py                             # Re-exports MEDGEMMA_27B, EXPERIMENTS,
│                                           #   INSTANCE_TYPES, CATALOG_CACHE,
│                                           #   DEFAULT_REGIONS, load_catalog(),
│                                           #   refresh_catalog(), prompts
├── model_spec.py                           # MEDGEMMA_27B: ModelSpec
├── experiments.py                          # EXPERIMENTS: 7 ExperimentConfig instances
├── prompts.py                              # Clinical NLP SYSTEM_PROMPT + SEED_INPUT
├── catalog_cache.json                      # Hardware + prices cache (schema v2, checked-in)
└── medgemma-27b-vllm-ec2-benchmark.ipynb   # Generated notebook — edit build_notebook.py
```

## The 7 experiments

Each experiment deploys MedGemma-27B at the **optimum packing** for its
instance type (maximum replicas per instance-hour). No p5/p5e/inf2/trn1
or MIG in the lineup

| # | Instance | GPUs / accel. | TP | DP | PP | Replicas | Capacity ladder |
|---|---|---|---|---|---|---|---|
| exp_1 | `g5.12xlarge`   | 4× A10G        | 4 | 1 | 1 | 1 | spot → OD |
| exp_2 | `g6.12xlarge`   | 4× L4          | 4 | 1 | 1 | 1 | spot → OD |
| exp_3 | `g6e.12xlarge`  | 4× L40S        | 2 | 2 | 1 | 2 | spot → OD |
| exp_4 | `g7e.2xlarge`   | 1× Blackwell   | 1 | 1 | 1 | 1 | spot → OD |
| exp_5 | `g7e.12xlarge`  | 2× Blackwell   | 1 | 2 | 1 | 2 | spot → OD |
| exp_6 | `p4d.24xlarge`  | 8× A100 40 GB  | 2 | 4 | 1 | 4 | spot → OD |
| exp_7 | `p4de.24xlarge` | 8× A100 80 GB  | 1 | 8 | 1 | 8 | spot → OD |

All experiments default to `us-west-2`. Alternates: `us-east-2`, `us-east-1`.

## Usage

```python
from vllm_ec2_bench import DeploymentRunner
from models.medgemma_27b import EXPERIMENTS, SYSTEM_PROMPT, load_catalog

CATALOG = load_catalog(offline_ok=False)   # auto-refreshes if stale
cfg = EXPERIMENTS["exp_1"]
runner = DeploymentRunner(cfg, catalog=CATALOG, hf_token=HF_TOKEN)
state = runner.launch()
# ... use state.base_url / state.api_key ...
runner.terminate()
```

Or via the generated notebook (edit [`scripts/build_notebook.py`](../../scripts/build_notebook.py)
to change content, then regenerate).

## Prerequisites

The notebook provisions everything it needs automatically (IAM role,
instance profile, security group, subnets, AMI). You only need:

1. **AWS credentials** with EC2, IAM, SSM, and Bedrock permissions.
2. **Hugging Face token** with access to `google/medgemma-27b-text-it`
   (accept the license at the HF model page first).
3. **Default VPC** in `us-west-2` (standard on all AWS accounts).
4. **Python ≥ 3.11** locally.
5. **Bedrock model access** in `us-east-1` (only needed to regenerate
   sample data via `sample-data/scripts/synthesize.py`).

## Regenerating the notebook

```bash
python scripts/build_notebook.py --model medgemma_27b
# or build all 6 model notebooks at once:
python scripts/build_notebook.py --all
```

This writes `models/medgemma_27b/medgemma-27b-vllm-ec2-benchmark.ipynb`.
Do NOT edit the `.ipynb` directly — your changes will be overwritten.

## Regional availability (May 2026)

| Instance | us-west-2 (default) | us-east-2 | us-east-1 |
|---|---|---|---|
| g5, g6, g6e, p4d, p4de | ✅ | ✅ | ✅ |
| g7e | ✅ | ✅ (limited) | ✅ (limited) |
