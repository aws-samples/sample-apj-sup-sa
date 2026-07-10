# gpt-oss-20b benchmark

Per-model benchmark config for
[`openai/gpt-oss-20b`](https://huggingface.co/openai/gpt-oss-20b)
(Apache-2.0, 21B/3.6B-A MoE, native MXFP4 on Blackwell).

## Files

```
benchmark/models/gpt_oss_20b/
├── __init__.py
├── model_spec.py       # GPT_OSS_20B
├── experiments.py      # EXPERIMENTS dict (exp_1..exp_6)
├── prompts.py          # SYSTEM_PROMPT + SEED_INPUT (travel JSON extract)
├── catalog_cache.json  # checked-in instance/price snapshot
└── README.md
```

## Experiments

| id | instance | TP | DP | precision | notes |
|---|---|---|---|---|---|
| exp_1 | g7e.2xlarge | 1 | 1 | MXFP4 (Blackwell native) | full 131K context |
| exp_2 | g7e.12xlarge | 1 | 4 | MXFP4 | 4 replicas |
| exp_3 | g6e.2xlarge | 1 | 1 | BF16 (Ada decompress) | 32K cap; cheapest |
| exp_4 | g6e.12xlarge | 2 | 2 | BF16 | 2 replicas at TP=2 |
| exp_5 | p4d.24xlarge | 8 | 1 | BF16 + Triton attn | required attn backend on Ampere |
| exp_6 | p4de.24xlarge | 8 | 1 | BF16 + Triton attn | full 131K context |

## Required env vars per experiment

* exp_1, exp_2 (Blackwell): `VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8=1`
* exp_5, exp_6 (Ampere):    `VLLM_ATTENTION_BACKEND=TRITON_ATTN_VLLM_V1`

Threaded through `DeploymentPlan.extra_env_vars` and rendered into the
EC2 user-data `docker run -e KEY=VAL` flags.

## Required serve flags

`--tool-call-parser openai`, `--enable-auto-tool-choice`,
`--reasoning-parser openai_gptoss`, `--kv-cache-dtype fp8`. Ampere plans
add `--async-scheduling`.

## Task

Travel-booking JSON extraction (sample-data domain `travel/`).
