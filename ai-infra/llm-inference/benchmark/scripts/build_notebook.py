"""Build a per-model benchmark notebook programmatically.

Usage::

    python scripts/build_notebook.py --model medgemma_27b
    python scripts/build_notebook.py --model qwen3_8b
    python scripts/build_notebook.py --all

Notebook content lives in code so changes show up in git diffs and the
.ipynb files stay regenerable. Edit this file (or the per-model
``MODEL_CONFIGS`` registry below) instead of hand-editing notebooks.

The notebook structure is identical across models; the per-model bits are
the title prose, weight footprint, instance experiments and which sample-data
file feeds the load test.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"


# -----------------------------------------------------------------------------
# Per-model config — everything notebook-flavor specific lives here.
# -----------------------------------------------------------------------------
@dataclass
class ModelNotebookConfig:
    package: str               # python package name e.g. "medgemma_27b"
    var_name: str              # the ModelSpec variable, e.g. "MEDGEMMA_27B"
    nb_filename: str           # the output .ipynb filename
    display_name: str          # "MedGemma 27B"
    hf_repo: str               # "google/medgemma-27b-text-it"
    domain: str                # "travel" | "vision"
    sample_data_file: str      # "01-domestic-flight.jsonl"
    weight_gib: float          # weights size in GiB (BF16 default)
    weight_note: str           # one-sentence note on weight footprint
    gated: bool                # gated HF model => HF_TOKEN required
    architecture_note: str     # short architecture/lineage comment
    experiments: list[tuple[str, str, str]]   # (exp_id, title, flavor)


# 7 standard experiments shared by most models (g5/g6/g6e/g7e small/large/p4d/p4de)
_EXPERIMENTS_FULL: list[tuple[str, str, str]] = [
    ("exp_1",  "Experiment 1 — g5.12xlarge (4× A10G / Ampere)", "standard"),
    ("exp_2",  "Experiment 2 — g6.12xlarge (4× L4 / Ada)", "standard"),
    ("exp_3",  "Experiment 3 — g6e.12xlarge (4× L40S / Ada)", "standard"),
    ("exp_4",  "Experiment 4 — g7e.2xlarge (1× Blackwell)", "standard"),
    ("exp_5",  "Experiment 5 — g7e.12xlarge (2× Blackwell)", "standard"),
    ("exp_6",  "Experiment 6 — p4d.24xlarge (8× A100 40GB / Ampere)", "standard"),
    ("exp_7",  "Experiment 7 — p4de.24xlarge (8× A100 80GB / Ampere)", "standard"),
]

# Llama-4-Scout's 218 GiB BF16 weights only fit on p4d/p4de.
_EXPERIMENTS_LLAMA4_SCOUT: list[tuple[str, str, str]] = [
    ("exp_6",  "Experiment 6 — p4d.24xlarge (8× A100 40GB / Ampere) — TP=8, kv-cache fp8", "standard"),
    ("exp_7",  "Experiment 7 — p4de.24xlarge (8× A100 80GB / Ampere) — TP=8, BF16 KV cache", "standard"),
]

# gpt-oss-20b: g7e.{2x,12x}, g6e.{2x,12x}, p4d/p4de — Blackwell native MXFP4
# vs Ampere BF16 + Triton attn backend. 6 experiments.
_EXPERIMENTS_GPT_OSS_20B: list[tuple[str, str, str]] = [
    ("exp_1", "Experiment 1 — g7e.2xlarge (1× Blackwell) — MXFP4 native, 131K ctx", "standard"),
    ("exp_2", "Experiment 2 — g7e.12xlarge (4× Blackwell) — MXFP4, DP=4", "standard"),
    ("exp_3", "Experiment 3 — g6e.2xlarge (1× L40S) — BF16 expansion, 32K", "standard"),
    ("exp_4", "Experiment 4 — g6e.12xlarge (4× L40S) — TP=2 + DP=2", "standard"),
    ("exp_5", "Experiment 5 — p4d.24xlarge (8× A100 40GB) — BF16 + Triton attn", "standard"),
    ("exp_6", "Experiment 6 — p4de.24xlarge (8× A100 80GB) — full 131K context", "standard"),
]

# Qwen3-Coder-Next: 80B MoE, FP8 quant on g6e.12x or BF16 on p4d/p4de.
_EXPERIMENTS_QWEN3_CODER_NEXT: list[tuple[str, str, str]] = [
    ("exp_1", "Experiment 1 — g6e.12xlarge (4× L40S) — FP8 quant, TP=4", "standard"),
    ("exp_2", "Experiment 2 — p4d.24xlarge (8× A100 40GB) — BF16 + fp8 KV", "standard"),
    ("exp_3", "Experiment 3 — p4de.24xlarge (2× A100 80GB) — TP=2", "standard"),
    ("exp_4", "Experiment 4 — p4de.24xlarge (4× A100 80GB) — TP=4", "standard"),
]

MODEL_CONFIGS: dict[str, ModelNotebookConfig] = {
    "medgemma_27b": ModelNotebookConfig(
        package="medgemma_27b",
        var_name="MEDGEMMA_27B",
        nb_filename="medgemma-27b-vllm-ec2-benchmark.ipynb",
        display_name="MedGemma 27B",
        hf_repo="google/medgemma-27b-text-it",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=55.0,
        weight_note="MedGemma-27B (BF16) weights are ~55 GiB. g6.12xl is limited to 1 replica via TP=4; g6e.12xl fits 2 replicas (each 2× L40S = 96 GiB).",
        gated=True,
        architecture_note="MedGemma-27B is built on the Gemma 3 architecture. vLLM's Neuron backend does not currently support Gemma 3, so inf2/trn1 are out of scope.",
        experiments=list(_EXPERIMENTS_FULL),
    ),
    "qwen3_8b": ModelNotebookConfig(
        package="qwen3_8b",
        var_name="QWEN3_8B",
        nb_filename="qwen3-8b-vllm-ec2-benchmark.ipynb",
        display_name="Qwen3 8B",
        hf_repo="Qwen/Qwen3-8B",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=17.0,
        weight_note="Qwen3-8B (BF16) ~ 17 GiB. Fits on a single 24-GiB A10G/L4. On multi-GPU hosts we run DP=N (one replica per GPU) for maximum packing.",
        gated=False,
        architecture_note="Dense 8B Apache-2.0 model. Smallest in this lineup.",
        experiments=list(_EXPERIMENTS_FULL),
    ),
    "mistral_small_3_2_24b": ModelNotebookConfig(
        package="mistral_small_3_2_24b",
        var_name="MISTRAL_SMALL_3_2_24B",
        nb_filename="mistral-small-3-2-24b-vllm-ec2-benchmark.ipynb",
        display_name="Mistral Small 3.2 24B Instruct",
        hf_repo="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=48.0,
        weight_note="Mistral-Small-3.2-24B (BF16) ~ 48 GiB. Needs 2× A10G/L4 (TP=2) on smaller hosts; fits on a single L40S/Blackwell.",
        gated=False,
        architecture_note="Dense 24B Apache-2.0. Mistral tokenizer + custom config flags required by vLLM's Mistral wiring.",
        experiments=list(_EXPERIMENTS_FULL),
    ),
    "qwen3_30b_a3b": ModelNotebookConfig(
        package="qwen3_30b_a3b",
        var_name="QWEN3_30B_A3B",
        nb_filename="qwen3-30b-a3b-vllm-ec2-benchmark.ipynb",
        display_name="Qwen3 30B-A3B Instruct",
        hf_repo="Qwen/Qwen3-30B-A3B-Instruct-2507",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=60.0,
        weight_note="Qwen3-30B-A3B is an MoE: 30B total / 3.3B active per token. Weights ~60 GiB BF16, but compute is dominated by 3.3B active params, so throughput-per-GPU is exceptional on multi-GPU hosts.",
        gated=False,
        architecture_note="MoE 30B/3.3B-active Apache-2.0. Use TP=2 minimum to spread weights; expert parallelism handled by vLLM internally.",
        experiments=list(_EXPERIMENTS_FULL),
    ),
    "gemma_4_31b": ModelNotebookConfig(
        package="gemma_4_31b",
        var_name="GEMMA_4_31B",
        nb_filename="gemma-4-31b-vllm-ec2-benchmark.ipynb",
        display_name="Gemma 4 31B Instruct",
        hf_repo="google/gemma-4-31B-it",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=62.0,
        weight_note="Gemma-4-31B (BF16) ~ 62 GiB. Fits on 4× A10G with TP=4 (tight) or 2× L40S (TP=2).",
        gated=False,
        architecture_note="Dense 31B Apache-2.0 (released April 2026, ungated).",
        experiments=list(_EXPERIMENTS_FULL),
    ),
    "llama_4_scout_17b": ModelNotebookConfig(
        package="llama_4_scout_17b",
        var_name="LLAMA_4_SCOUT_17B",
        nb_filename="llama-4-scout-17b-vllm-ec2-benchmark.ipynb",
        display_name="Llama 4 Scout 17B-16E Instruct",
        hf_repo="meta-llama/Llama-4-Scout-17B-16E-Instruct",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=218.0,
        weight_note="Llama-4-Scout has ~218 GiB BF16 weights; only p4d.24xlarge (8x A100-40GB, 320 GiB) and p4de.24xlarge (8x A100-80GB, 640 GiB) can host it. p4d requires `--kv-cache-dtype fp8` to leave any KV-cache budget at 32K context.",
        gated=True,
        architecture_note="MoE 109B (17B-16E variant). Gated under the Llama-4 license — accept and obtain HF read access.",
        experiments=list(_EXPERIMENTS_LLAMA4_SCOUT),
    ),
    "gpt_oss_20b": ModelNotebookConfig(
        package="gpt_oss_20b",
        var_name="GPT_OSS_20B",
        nb_filename="gpt-oss-20b-vllm-ec2-benchmark.ipynb",
        display_name="gpt-oss 20B",
        hf_repo="openai/gpt-oss-20b",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=42.0,
        weight_note="gpt-oss-20b is 21B/3.6B-A MoE with native MXFP4 on Blackwell (~13 GiB resident). On Ampere it decompresses to BF16 (~42 GiB) and requires the Triton attention backend (FlashInfer's attention path doesn't support attention sinks on non-Blackwell).",
        gated=False,
        architecture_note="21B/3.6B-A MoE Apache-2.0 with attention sinks + sliding-window. Native MXFP4 on Blackwell. OpenAI tool-call + reasoning parsers; vLLM exposes `--reasoning-parser openai_gptoss`.",
        experiments=list(_EXPERIMENTS_GPT_OSS_20B),
    ),
    "qwen3_coder_next": ModelNotebookConfig(
        package="qwen3_coder_next",
        var_name="QWEN3_CODER_NEXT",
        nb_filename="qwen3-coder-next-vllm-ec2-benchmark.ipynb",
        display_name="Qwen3 Coder Next",
        hf_repo="Qwen/Qwen3-Coder-Next",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=160.0,
        weight_note="Qwen3-Coder-Next is 80B/3B-A MoE (~160 GiB BF16). FP8 quant on 4x L40S (g6e.12xlarge) shrinks resident weights to ~80 GiB. Requires vLLM >= 0.15.0 for the qwen3_next hybrid (Gated DeltaNet + Gated Attention) architecture.",
        gated=False,
        architecture_note="80B/3B-A MoE Apache-2.0 coding model (qwen3_next hybrid arch, 512 experts top-10 + 1 shared, 262K native context). Travel-as-coding task: SYSTEM_PROMPT asks for Python code that parses the booking JSON. Does NOT emit `<think>`; recommended sampling temp=1.0, top_p=0.95, top_k=40.",
        experiments=list(_EXPERIMENTS_QWEN3_CODER_NEXT),
    ),
}


# -----------------------------------------------------------------------------
# Cell helpers
# -----------------------------------------------------------------------------
def md(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}


def code(source: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source.splitlines(keepends=True)}


# -----------------------------------------------------------------------------
# Cell content
# -----------------------------------------------------------------------------
def cells_preamble(c: ModelNotebookConfig) -> list[dict]:
    if c.domain == "vision":
        domain_blurb = "image URLs with describe-image prompts"
        task_blurb = "produce a structured JSON description of each image"
    else:
        domain_blurb = "travel booking confirmation emails"
        task_blurb = "extract a structured JSON booking record from each email"
    auth_step = ""
    if c.gated:
        auth_step = (
            f"2. **A Hugging Face token** with access to "
            f"[`{c.hf_repo}`](https://huggingface.co/{c.hf_repo}) "
            f"(a gated model). The notebook stores it in AWS Secrets Manager.\n"
        )
    return [
        md(dedent(f"""\
            # Deploy {c.hf_repo} to EC2 with vLLM and benchmark across instance families

            This notebook provisions Amazon EC2 instances, serves
            [{c.display_name}](https://huggingface.co/{c.hf_repo}) with vLLM in a
            Docker container, then benchmarks each deployment with
            [LLMeter](https://github.com/awslabs/llmeter). At the end it
            compares throughput and cost across experiments.

            **Task under test:** {task_blurb}. Inputs are loaded from
            `sample-data/{c.domain}/{c.sample_data_file}` (synthesized by
            `sample-data/scripts/synthesize.py --domain {c.domain}`).

            > **Notes**
            > * Each row represents the **optimum packing** for {c.display_name} on that instance — maximum model replicas per instance-hour.
            > * {c.weight_note}
            > * {c.architecture_note}
            > * **Default region is us-west-2 (PDX).** Alternates: `us-east-2` (CMH) and `us-east-1` (IAD).

            ### Prerequisites

            1. **AWS credentials** configured for the target account (profile `default` by default). The identity needs EC2 + IAM + SSM permissions.
            {auth_step}{"3" if c.gated else "2"}. **A default VPC** in the target region.

            Everything else — IAM role & instance profile, per-experiment
            security group, subnet discovery, AMI lookup, vLLM Docker setup,
            and teardown — is handled by `DeploymentRunner`.
            """)),
        md("## 0. Preparation"),
        md("### 0.1 Install / verify Python dependencies"),
        code(dedent("""\
            %pip install -q -U \\
                "boto3>=1.34" "botocore>=1.34" \\
                "llmeter>=0.1.11" "openai>=1.50" \\
                "plotly>=5.24" "ipywidgets>=8.1" \\
                "huggingface_hub>=0.26" "pandas>=2.2" "matplotlib>=3.9" \\
                "requests>=2.32" "tenacity>=9.0" "python-dotenv>=1.0" "jmespath>=1.0"
            """)),
        md("### 0.2 Imports and logging"),
        code(dedent(f"""\
            import asyncio
            import json
            import logging
            import os
            import sys
            from datetime import datetime
            from pathlib import Path

            NOTEBOOK_DIR = Path.cwd()
            if (NOTEBOOK_DIR / "models" / "{c.package}").is_dir():
                PROJECT_ROOT = NOTEBOOK_DIR
            elif NOTEBOOK_DIR.name == "{c.package}":
                PROJECT_ROOT = NOTEBOOK_DIR.parents[1]
            else:
                raise RuntimeError(
                    f"Can't locate project root from CWD={{NOTEBOOK_DIR}}. "
                    "Launch Jupyter from the project root or from models/{c.package}/."
                )
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            _SRC = PROJECT_ROOT / "src"
            if _SRC.is_dir() and str(_SRC) not in sys.path:
                sys.path.insert(0, str(_SRC))

            import boto3
            import pandas as pd

            from vllm_ec2_bench import (
                DeploymentRunner,
                ExperimentConfig,
                catalog_meta,
                upsert_hf_token,
            )
            from vllm_ec2_bench.cleanup import (
                terminate_all_tagged_instances,
                cleanup_tagged_security_groups,
            )
            from vllm_ec2_bench.endpoint import VLLMEndpoint
            from models.{c.package} import (
                {c.var_name},
                EXPERIMENTS,
                INSTANCE_TYPES,
                CATALOG_CACHE,
                DEFAULT_REGIONS,
                SYSTEM_PROMPT,
                SEED_INPUT,
                development_experiments,
                get as get_experiment,
                load_catalog,
                refresh_catalog,
            )

            from llmeter.experiments import LoadTest

            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
            LOG = logging.getLogger("notebook")
            LOG.info("boto3=%s, pandas=%s", boto3.__version__, pd.__version__)
            """)),
        md("### 0.3 Region + Hugging Face token configuration"),
        md(dedent(f"""\
            * `REGION` — default region for every experiment (`us-west-2`, PDX).
            * `ALT_REGION_1` — first fallback (`us-east-2`, CMH).
            * `ALT_REGION_2` — second fallback (`us-east-1`, IAD).
            {"* `HF_TOKEN` — read access to `" + c.hf_repo + "` (gated repo)." if c.gated else "* No HF token required: `" + c.hf_repo + "` is ungated."}
            """)),
        code(dedent(f"""\
            REGION = "us-west-2"
            ALT_REGION_1 = "us-east-2"
            ALT_REGION_2 = "us-east-1"

            HF_TOKEN = "PLACEHOLDER_PASTE_YOUR_HF_TOKEN"
            HF_SECRET_NAME = f"{{{c.var_name}.resource_prefix}}-benchmark/hf-token"

            N_BENCHMARK_SAMPLES = 1000
            N_WARMUP_SAMPLES = 5
            BENCHMARK_SAMPLE_SEED = 42

            assert 1 <= N_BENCHMARK_SAMPLES <= 100_000
            assert 0 <= N_WARMUP_SAMPLES <= 1000

            print(f"REGION              = {{REGION}}")
            print(f"HF_SECRET_NAME      = {{HF_SECRET_NAME}}")
            print(f"N_BENCHMARK_SAMPLES = {{N_BENCHMARK_SAMPLES}}")
            print(f"N_WARMUP_SAMPLES    = {{N_WARMUP_SAMPLES}}")
            """)),
        *(
            [
                md("**Upsert HF token into Secrets Manager.** Each EC2 instance fetches it via Secrets Manager at boot."),
                code(dedent("""\
                    assert HF_TOKEN != "PLACEHOLDER_PASTE_YOUR_HF_TOKEN", \\
                        "Scroll up and paste your HF token before running this cell."
                    assert HF_TOKEN.startswith("hf_"), \\
                        f"{HF_TOKEN[:8]}... doesn't look like an HF token."

                    secret_arn = upsert_hf_token(HF_SECRET_NAME, HF_TOKEN, region=REGION)
                    print(f"HF token stored in: {secret_arn}")
                    HF_TOKEN = "(stored in Secrets Manager)"
                    """)),
            ] if c.gated else []
        ),
        md("### 0.4 Preflight — AWS identity"),
        code(dedent("""\
            sts = boto3.client("sts")
            identity = sts.get_caller_identity()
            print(f"Account: {identity['Account']}")
            print(f"ARN:     {identity['Arn']}")
            """)),
        md("### 0.5 Refresh the hardware catalog (pricing + specs from AWS APIs)"),
        code(dedent("""\
            CATALOG = load_catalog(offline_ok=False, max_age_hours_prices=24)
            _meta = catalog_meta(CATALOG_CACHE)
            print(f"Cache:             {CATALOG_CACHE}")
            print(f"Catalog entries:   {len(CATALOG.instance_types())}")
            print(f"Prices refreshed:  {_meta.get('prices_refreshed_at', '(just now)')}")
            for it in INSTANCE_TYPES[:3]:
                hw = CATALOG.hardware(it)
                prices = CATALOG.price_od_all(it)
                if prices:
                    region_prices = ", ".join(f"{r}=${p:.4f}" for r, p in sorted(prices.items()))
                else:
                    region_prices = "(price unavailable)"
                print(f"  {it:<18} {hw.num_accelerators}× {hw.accelerator_model:<20} {region_prices}")
            """)),
        md("### 0.6 Load synthesized benchmark data"),
        md(dedent(f"""\
            Benchmark inputs come from `sample-data/{c.domain}/{c.sample_data_file}`
            (~10,000 synthesized {domain_blurb}). We randomly sample
            `N_BENCHMARK_SAMPLES` prompts and reuse the same sample across all
            experiments + concurrency tiers so the workload is apples-to-apples.
            """)),
        code(dedent(f"""\
            import random

            synth_file = PROJECT_ROOT.parent / "sample-data" / "{c.domain}" / "{c.sample_data_file}"
            assert synth_file.exists(), (
                f"{{synth_file}} is missing. Run "
                f"`python sample-data/scripts/synthesize.py --domain {c.domain}` first."
            )

            all_texts: list[str] = []
            with synth_file.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = obj.get("text")
                    if text:
                        all_texts.append(text)

            assert len(all_texts) >= N_BENCHMARK_SAMPLES, (
                f"Synthesis pool has only {{len(all_texts)}} records; "
                f"need at least N_BENCHMARK_SAMPLES={{N_BENCHMARK_SAMPLES}}"
            )

            rng = random.Random(BENCHMARK_SAMPLE_SEED)
            INPUTS: list[str] = rng.sample(all_texts, N_BENCHMARK_SAMPLES)

            print(f"Synthesis pool:    {{len(all_texts):,}} records from {{synth_file.name}}")
            print(f"Sampled:           {{len(INPUTS):,}} prompts (seed={{BENCHMARK_SAMPLE_SEED}})")
            print()
            print("--- system prompt ---")
            print(SYSTEM_PROMPT)
            print()
            print("--- first input (abbreviated) ---")
            print(INPUTS[0][:320] + ("..." if len(INPUTS[0]) > 320 else ""))
            """)),
        code(dedent("""\
            BENCH_TOTAL_REQUESTS_PER_TIER = N_BENCHMARK_SAMPLES
            MAX_NEW_TOKENS = 512
            CONCURRENCY_TIERS = [1, 10, 30, 50, 100]
            print(f"Per-tier request budget: {BENCH_TOTAL_REQUESTS_PER_TIER}")
            print(f"Concurrency tiers:       {CONCURRENCY_TIERS}")
            print(f"Warmup requests:         {N_WARMUP_SAMPLES} at c=1 (discarded)")
            """)),
        md("### 0.7 Shared helper to run one experiment end-to-end"),
        code(dedent(f"""\
            EXPERIMENTS_STATE: dict[str, dict] = {{}}

            OUTPUT_BASE = Path("outputs")
            OUTPUT_BASE.mkdir(exist_ok=True)


            async def run_experiment(
                exp_id: str,
                *,
                concurrency_tiers: list[int],
                capacity_reservation_id: str | None = None,
                region_override: str | None = None,
            ) -> dict:
                cfg = get_experiment(exp_id)
                if region_override:
                    new_plan = cfg.deployment.model_copy(update={{"region": region_override}})
                    cfg = cfg.model_copy(update={{"deployment": new_plan}})

                hw = CATALOG.hardware(cfg.deployment.instance_type)
                print(f"[{{exp_id}}] spec: {{cfg.deployment.instance_type}} in {{cfg.deployment.region}} "
                      f"(TP={{cfg.deployment.tensor_parallel}} DP={{cfg.deployment.data_parallel}})")
                print(f"[{{exp_id}}] total VRAM = {{hw.vram_gib_total:.1f}} GiB across "
                      f"{{hw.num_accelerators}} accelerators")

                runner = DeploymentRunner(
                    cfg,
                    catalog=CATALOG,
                    hf_secret_name={"HF_SECRET_NAME" if c.gated else "None"},
                    capacity_reservation_id=capacity_reservation_id,
                )
                state = runner.launch()

                endpoint = VLLMEndpoint(
                    base_url=state.base_url,
                    api_key=state.api_key,
                    model_id=cfg.model_spec.served_model_name,
                )

                # Smoke test
                payload = VLLMEndpoint.create_payload(
                    SYSTEM_PROMPT, INPUTS[0], max_tokens=MAX_NEW_TOKENS
                )
                smoke = endpoint.invoke(payload)
                print(f"[{{exp_id}}] smoke: "
                      f"input_tokens={{smoke.num_tokens_input}} "
                      f"output_tokens={{smoke.num_tokens_output}} "
                      f"latency_s={{smoke.time_to_last_token:.2f}}")

                payloads = [
                    VLLMEndpoint.create_payload(SYSTEM_PROMPT, x, max_tokens=MAX_NEW_TOKENS)
                    for x in INPUTS[:BENCH_TOTAL_REQUESTS_PER_TIER]
                ]

                if N_WARMUP_SAMPLES > 0:
                    n_warmup = min(N_WARMUP_SAMPLES, len(payloads))
                    print(f"[{{exp_id}}] warmup: {{n_warmup}} passes at c=1 (discarded)")
                    warmup_payloads = payloads[:n_warmup]
                    warmup = LoadTest(
                        endpoint=endpoint,
                        payload=warmup_payloads,
                        sequence_of_clients=[1],
                        output_path=str(OUTPUT_BASE / exp_id / "warmup"),
                        min_requests_per_run=n_warmup,
                        min_requests_per_client=n_warmup,
                    )
                    await warmup.run()
                    print(f"[{{exp_id}}] warmup done")

                load_test = LoadTest(
                    endpoint=endpoint,
                    payload=payloads,
                    sequence_of_clients=concurrency_tiers,
                    output_path=str(OUTPUT_BASE / exp_id / "load_test"),
                    min_requests_per_run=BENCH_TOTAL_REQUESTS_PER_TIER,
                    min_requests_per_client=max(1, BENCH_TOTAL_REQUESTS_PER_TIER // max(concurrency_tiers)),
                )
                results = await load_test.run()

                EXPERIMENTS_STATE[exp_id] = {{
                    "spec": cfg,
                    "runner": runner,
                    "state": state,
                    "endpoint": endpoint,
                    "load_test": load_test,
                    "results": results,
                    "concurrency_tiers": concurrency_tiers,
                    "completed_at": datetime.utcnow().isoformat(),
                }}
                return EXPERIMENTS_STATE[exp_id]


            def teardown_experiment(exp_id: str) -> None:
                entry = EXPERIMENTS_STATE.get(exp_id)
                if not entry:
                    print(f"[{{exp_id}}] no state; nothing to tear down.")
                    return
                runner: DeploymentRunner = entry["runner"]
                print(f"[{{exp_id}}] terminating {{runner.state.instance_id}} in {{runner.state.region}}...")
                runner.terminate()
                print(f"[{{exp_id}}] terminated.")
            """)),
    ]


def cells_experiment(exp_id: str, title: str) -> list[dict]:
    return [
        md(f"## {title}"),
        md(dedent(f"""\
            Capacity strategy: **spot (Fleet) → on-demand → auto-ODCR**.
            The deployer walks each mode in order; the final mode is recorded
            in `state.capacity_mode` and shown in the comparison table.

            Concurrency tiers: `CONCURRENCY_TIERS` (defined in section 0).
            """)),
        code(dedent(f"""\
            cfg = get_experiment("{exp_id}")
            print(cfg.model_dump())
            concurrency_tiers = CONCURRENCY_TIERS
            concurrency_tiers
            """)),
        code(dedent(f"""\
            state_{exp_id} = await run_experiment(
                "{exp_id}",
                concurrency_tiers=concurrency_tiers,
            )
            """)),
        md(f"**Teardown for {exp_id}** — also deletes Spot Fleet, Launch Template, and any auto-created ODCR."),
        code(f'# teardown_experiment("{exp_id}")  # <-- uncomment to tear down this experiment'),
    ]


def cells_analysis() -> list[dict]:
    return [
        md("## Performance and cost analysis"),
        md("Per-tier throughput, $/1M-tokens, and percentile-latency comparison across experiments."),
        code(dedent("""\
            STAT_VARIANT = "average"  # "p50" | "p90" | "p99"

            def _get_per_tier_stats(entry: dict) -> dict[int, dict]:
                results_obj = entry.get("results")
                if results_obj is None:
                    return {}
                results_dict = getattr(results_obj, "results", None) or {}
                per_tier = {}
                for clients, result in results_dict.items():
                    stats = getattr(result, "stats", None)
                    if stats is None:
                        continue
                    per_tier[int(clients)] = stats
                return per_tier


            def build_comparison_df(stat_variant: str = "average") -> pd.DataFrame:
                rows: list[dict] = []
                all_tiers: set[int] = set()
                for entry in EXPERIMENTS_STATE.values():
                    all_tiers.update(entry["concurrency_tiers"])
                all_tiers_sorted = sorted(all_tiers)

                for exp_id, entry in EXPERIMENTS_STATE.items():
                    cfg = entry["spec"]
                    dep = cfg.deployment
                    hw = CATALOG.hardware(dep.instance_type)
                    capacity_mode = entry["state"].capacity_mode
                    od_price = CATALOG.price_od(dep.instance_type, dep.region)

                    actual_hourly = od_price
                    price_source = "OD"
                    if capacity_mode == "spot":
                        actual_hourly = CATALOG.estimated_spot(dep.instance_type, dep.region) or od_price
                        price_source = "spot (estimated, 0.7×OD)*"

                    row = {
                        "Experiment": exp_id,
                        "Instance": dep.instance_type,
                        "Region": dep.region,
                        "GPUs": hw.num_accelerators,
                        "GPU Model": hw.accelerator_model,
                        "TP": dep.tensor_parallel,
                        "DP": dep.data_parallel,
                        "Replicas": cfg.model_replicas,
                        "$/hr": round(actual_hourly, 4) if actual_hourly else None,
                        "$/hr source": price_source,
                        "Capacity": capacity_mode,
                    }
                    per_tier = _get_per_tier_stats(entry)
                    for tier in all_tiers_sorted:
                        stats = per_tier.get(tier, {})
                        in_tpm = stats.get("average_input_tokens_per_minute") or 0
                        out_tpm = stats.get("average_output_tokens_per_minute") or 0
                        total_tpm = in_tpm + out_tpm
                        cost_per_1m = None
                        if total_tpm and actual_hourly:
                            cost_per_1m = round(actual_hourly / (total_tpm * 60) * 1_000_000, 4)
                        row[f"c={tier} tok/min"] = round(total_tpm, 1) if total_tpm else None
                        row[f"c={tier} $/1M"] = cost_per_1m
                    rows.append(row)
                return pd.DataFrame(rows) if rows else pd.DataFrame()

            df_compare = build_comparison_df(stat_variant=STAT_VARIANT)
            csv_path = OUTPUT_BASE / "comparison_table.csv"
            df_compare.to_csv(csv_path, index=False)
            print(f"Comparison table saved -> {csv_path}")
            df_compare
            """)),
    ]


def cells_cleanup(c: ModelNotebookConfig) -> list[dict]:
    return [
        md("## Cleanup"),
        md(dedent(f"""\
            After benchmarking, make sure **every** instance is terminated. The
            emergency sweep below terminates any running instance tagged
            `Project={{{c.var_name}.project_tag_value}}` in all 3 regions.
            """)),
        code(dedent(f"""\
            # for eid in list(EXPERIMENTS_STATE.keys()):
            #     try:
            #         teardown_experiment(eid)
            #     except Exception as e:
            #         print(f"[{{eid}}] teardown error: {{e}}")
            """)),
        code(dedent(f"""\
            PROJECT_TAG = {c.var_name}.project_tag_value
            for r in [REGION, ALT_REGION_1, ALT_REGION_2]:
                killed = terminate_all_tagged_instances(r, PROJECT_TAG)
                print(f"{{r}}: terminated {{len(killed)}} instance(s): {{killed}}")
                deleted = cleanup_tagged_security_groups(r, PROJECT_TAG)
                print(f"{{r}}: deleted {{len(deleted)}} security group(s): {{deleted}}")
            """)),
    ]


def build(c: ModelNotebookConfig) -> dict:
    cells: list[dict] = []
    cells += cells_preamble(c)
    for exp_id, title, _flavor in c.experiments:
        cells += cells_experiment(exp_id, title)
    cells += cells_analysis()
    cells += cells_cleanup(c)
    return {
        "cells": cells,
        "metadata": {
            # Use the universal "python3" kernel that ipykernel ships in every
            # environment, rather than a custom per-model named kernel that
            # would require out-of-band `ipykernel install` registration
            # pointing at an absolute, machine-specific .venv path. Launched
            # from the project venv (per the README), "python3" resolves to
            # that venv's interpreter; on managed envs (SageMaker Studio,
            # Colab) it maps to their default kernel. Keeps notebooks
            # self-contained.
            "kernelspec": {
                "display_name": "Python 3 (ipykernel)",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def write_notebook(c: ModelNotebookConfig) -> Path:
    nb = build(c)
    out = MODELS_DIR / c.package / c.nb_filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(nb, indent=1, ensure_ascii=False))
    print(f"  -> {out} ({out.stat().st_size / 1024:.1f} KiB, {len(nb['cells'])} cells)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODEL_CONFIGS), help="single model to build")
    parser.add_argument("--all", action="store_true", help="build all 6 model notebooks")
    args = parser.parse_args()

    if args.all:
        targets = list(MODEL_CONFIGS.values())
    elif args.model:
        targets = [MODEL_CONFIGS[args.model]]
    else:
        parser.print_help()
        return

    for c in targets:
        print(f"Building notebook for {c.package}...")
        write_notebook(c)


if __name__ == "__main__":
    main()
