# Qwen3-Coder-Next benchmark

Per-model benchmark config for
[`Qwen/Qwen3-Coder-Next`](https://huggingface.co/Qwen/Qwen3-Coder-Next)
(Apache-2.0, 80B/3B-A MoE, qwen3_next hybrid arch).

## Files

```
benchmark/models/qwen3_coder_next/
├── __init__.py
├── model_spec.py
├── experiments.py
├── prompts.py
├── catalog_cache.json
└── README.md
```

## Experiments

| id | instance | TP | precision | notes |
|---|---|---|---|---|
| exp_1 | g6e.12xlarge | 4 | FP8 quant | recommended cheapest path |
| exp_2 | p4d.24xlarge | 8 | BF16 + fp8 KV | A100-40G; 16K context |
| exp_3 | p4de.24xlarge | 2 | BF16 | cleanest hybrid sharding |
| exp_4 | p4de.24xlarge | 4 | BF16 | best Ampere throughput |

## vLLM compatibility

Requires vLLM **>= 0.15.0** for the `qwen3_next` architecture. The repo
pin (v0.20.2) is well past that floor.

## Required serve flags

`--enable-auto-tool-choice`, `--tool-call-parser qwen3_coder`. exp_1 adds
`--quantization fp8`; exp_2 adds `--kv-cache-dtype fp8` to fit on A100-40G.

## Sampling

Recommended `temperature=1.0`, `top_p=0.95`, `top_k=40`. Does NOT emit
`<think>` traces; do not pass `enable_thinking=True`.

## Task

The travel sample-data is reused; the SYSTEM_PROMPT asks for a Python
parser as **code**, not the JSON object itself.
