"""Generate per-model batch notebooks from this script.

Edit this file, not the notebook. Run:

    python scripts/build_notebook.py --model medgemma_27b
    python scripts/build_notebook.py --model qwen3_8b
    python scripts/build_notebook.py --all

To regenerate. The notebook structure is identical across models; the
per-model bits are pinned in the `MODEL_CONFIGS` registry below.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class BatchModelConfig:
    package: str               # e.g. "medgemma_27b"
    var_name: str              # ModelSpec var, e.g. "MEDGEMMA_27B"
    nb_filename: str           # output .ipynb name
    display_name: str          # "MedGemma 27B"
    hf_repo: str               # "google/medgemma-27b-text-it"
    domain: str                # "travel" | "vision"
    sample_data_file: str      # "01-domestic-flight.jsonl"
    weight_gib: float
    weight_note: str
    gated: bool
    plans: list[str]           # plan factory function names exported from package
    default_plan: str          # the one notebook activates by default
    concurrency_default: int   # in-flight requests per container
    primary_instance: str      # instance type the wall-clock estimate references


MODEL_CONFIGS: dict[str, BatchModelConfig] = {
    "medgemma_27b": BatchModelConfig(
        package="medgemma_27b",
        var_name="MEDGEMMA_27B",
        nb_filename="medgemma_27b_batch.ipynb",
        display_name="MedGemma 27B",
        hf_repo="google/medgemma-27b-text-it",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=55.0,
        weight_note="MedGemma-27B BF16 weights are ~55 GiB. g7e.2xlarge fits 1 replica with ~40 GiB KV-cache budget.",
        gated=True,
        plans=["g7e_spot_single_queue", "g7e_family_spot_with_od_failover", "p4d_spot_single_queue", "p4d_spot_and_on_demand_failover"],
        default_plan="g7e_spot_single_queue",
        concurrency_default=100,
        primary_instance="g7e.2xlarge",
    ),
    "qwen3_8b": BatchModelConfig(
        package="qwen3_8b",
        var_name="QWEN3_8B",
        nb_filename="qwen3_8b_batch.ipynb",
        display_name="Qwen3 8B",
        hf_repo="Qwen/Qwen3-8B",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=17.0,
        weight_note="Qwen3-8B BF16 weights are ~17 GiB. Easy fit on a single 24-GiB A10G/L4 or any L40S/Blackwell.",
        gated=False,
        plans=["g6e_spot_single_queue", "g7e_spot_single_queue"],
        default_plan="g7e_spot_single_queue",
        concurrency_default=120,
        primary_instance="g7e.2xlarge",
    ),
    "mistral_small_3_2_24b": BatchModelConfig(
        package="mistral_small_3_2_24b",
        var_name="MISTRAL_SMALL_3_2_24B",
        nb_filename="mistral_small_3_2_24b_batch.ipynb",
        display_name="Mistral Small 3.2 24B Instruct",
        hf_repo="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=48.0,
        weight_note="Mistral-Small-3.2-24B BF16 weights are ~48 GiB. Fits on a single L40S or 96-GiB Blackwell.",
        gated=False,
        plans=["g7e_spot_single_queue", "g6e_spot_single_queue"],
        default_plan="g7e_spot_single_queue",
        concurrency_default=80,
        primary_instance="g7e.2xlarge",
    ),
    "qwen3_30b_a3b": BatchModelConfig(
        package="qwen3_30b_a3b",
        var_name="QWEN3_30B_A3B",
        nb_filename="qwen3_30b_a3b_batch.ipynb",
        display_name="Qwen3 30B-A3B Instruct",
        hf_repo="Qwen/Qwen3-30B-A3B-Instruct-2507",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=60.0,
        weight_note="MoE 30B/3.3B-active. ~60 GiB BF16 weights but compute is dominated by 3.3B active params.",
        gated=False,
        plans=["g7e_spot_single_queue", "g6e_spot_single_queue"],
        default_plan="g7e_spot_single_queue",
        concurrency_default=120,
        primary_instance="g7e.2xlarge",
    ),
    "gemma_4_31b": BatchModelConfig(
        package="gemma_4_31b",
        var_name="GEMMA_4_31B",
        nb_filename="gemma_4_31b_batch.ipynb",
        display_name="Gemma 4 31B Instruct",
        hf_repo="google/gemma-4-31B-it",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=62.0,
        weight_note="Gemma-4-31B BF16 weights are ~62 GiB. Fits on a single 96-GiB Blackwell with abundant KV-cache headroom.",
        gated=False,
        plans=["g7e_spot_single_queue", "g6e_spot_single_queue"],
        default_plan="g7e_spot_single_queue",
        concurrency_default=80,
        primary_instance="g7e.2xlarge",
    ),
    "llama_4_scout_17b": BatchModelConfig(
        package="llama_4_scout_17b",
        var_name="LLAMA_4_SCOUT_17B",
        nb_filename="llama_4_scout_17b_batch.ipynb",
        display_name="Llama 4 Scout 17B-16E Instruct",
        hf_repo="meta-llama/Llama-4-Scout-17B-16E-Instruct",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=218.0,
        weight_note="Llama-4-Scout BF16 weights are ~218 GiB. Only p4d.24xlarge (8x A100-40GB, requires --kv-cache-dtype fp8) and p4de.24xlarge (8x A100-80GB) can host it.",
        gated=True,
        plans=["p4d_spot_single_queue", "p4de_spot_single_queue"],
        default_plan="p4d_spot_single_queue",
        concurrency_default=60,
        primary_instance="p4d.24xlarge",
    ),
    "gpt_oss_20b": BatchModelConfig(
        package="gpt_oss_20b",
        var_name="GPT_OSS_20B",
        nb_filename="gpt_oss_20b_batch.ipynb",
        display_name="gpt-oss 20B",
        hf_repo="openai/gpt-oss-20b",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=42.0,
        weight_note="21B/3.6B-A MoE; native MXFP4 on Blackwell (~13 GiB resident) or BF16 (~42 GiB) on Ampere. Plan threads VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8 (g7e) or VLLM_ATTENTION_BACKEND=TRITON_ATTN_VLLM_V1 (p4d) via extra_env_vars.",
        gated=False,
        plans=["g7e_spot_single_queue", "p4d_spot_single_queue"],
        default_plan="g7e_spot_single_queue",
        concurrency_default=120,
        primary_instance="g7e.2xlarge",
    ),
    "qwen3_coder_next": BatchModelConfig(
        package="qwen3_coder_next",
        var_name="QWEN3_CODER_NEXT",
        nb_filename="qwen3_coder_next_batch.ipynb",
        display_name="Qwen3 Coder Next",
        hf_repo="Qwen/Qwen3-Coder-Next",
        domain="travel",
        sample_data_file="01-domestic-flight.jsonl",
        weight_gib=160.0,
        weight_note="80B/3B-A MoE (qwen3_next hybrid). FP8 quant on g6e.12xlarge (4xL40S) shrinks to ~80 GiB resident. Travel-as-coding task: SYSTEM_PROMPT asks for code that parses booking JSON.",
        gated=False,
        plans=["g6e_spot_single_queue", "p4de_spot_single_queue"],
        default_plan="g6e_spot_single_queue",
        concurrency_default=64,
        primary_instance="g6e.12xlarge",
    ),
}


# Module-global active config (set by build_notebook before cells() is called).
CFG: BatchModelConfig = MODEL_CONFIGS["medgemma_27b"]


def md(src: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": _split_lines(src),
    }


def code(src: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "source": _split_lines(src),
        "execution_count": None,
        "outputs": [],
    }


def _split_lines(src: str) -> list[str]:
    """Notebook JSON uses one string per line WITH trailing \\n."""
    if not src:
        return []
    lines = src.splitlines(keepends=True)
    return lines


def cells() -> list[dict]:
    c = CFG
    hf_warning = (
        f"**Before running:** set `HF_TOKEN` in your environment ({c.display_name} is gated)."
        if c.gated
        else f"_{c.display_name} is ungated; no HF token required._"
    )
    hf_token_init = (
        '''
            # Paste your HuggingFace token here (read access to the gated
            # model). Do NOT commit the notebook with this filled in.
            # The assert cell in section 2.5 will refuse to proceed if it
            # is still the placeholder; that cell then moves the value
            # into Secrets Manager so we never pass it again.
            HF_TOKEN = "PLACEHOLDER_PASTE_YOUR_HF_TOKEN"
            '''
        if c.gated
        else ""
    )
    hf_token_print = (
        '''print(f"HF_TOKEN = {'set' if HF_TOKEN != 'PLACEHOLDER_PASTE_YOUR_HF_TOKEN' else 'NOT SET — paste your token above'}")'''
        if c.gated
        else ""
    )
    return [
        md(dedent(
            f"""\
            # {c.display_name} — Batch Inference on AWS Batch

            End-to-end: deploy CFN stack → push container image → upload inputs to S3 →
            submit batch jobs → wait → collect outputs.

            One container per Batch job runs vLLM locally + drives it with asyncio
            concurrency. Per-job inputs are a manifest of S3 URIs (JSON or JSONL).

            Model: [`{c.hf_repo}`](https://huggingface.co/{c.hf_repo}). {c.weight_note}

            {hf_warning}
            """
        )),

        # --- 0. Imports + AWS config ------------------------------------
        md("## 0. Imports + AWS config"),
        code(dedent(
            """\
            from __future__ import annotations

            import json
            import logging
            import os
            import sys
            from pathlib import Path

            # Make sure the project root is on sys.path (notebooks/ lives one level in)
            NOTEBOOK_DIR = Path.cwd()
            PROJECT_ROOT = (
                NOTEBOOK_DIR
                if (NOTEBOOK_DIR / "models").is_dir()
                else NOTEBOOK_DIR.parents[0]
            )
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            SRC = PROJECT_ROOT / "src"
            if SRC.is_dir() and str(SRC) not in sys.path:
                sys.path.insert(0, str(SRC))

            import boto3
            import pandas as pd

            from llm_batch_deploy import (
                BatchDeploymentPlan,
                JobSubmissionPlan,
                ModelSpec,
            )
            from llm_batch_deploy.deployer import deploy, teardown, build_template
            from llm_batch_deploy.submitter import submit_batch, upsert_hf_token
            from llm_batch_deploy.waiter import (
                wait_for_completion,
                download_outputs,
                sample_outputs,
                poll,
                estimate_cost,
            )

            from models.""" + c.package + """ import (
                """ + c.var_name + """,
                """ + ",\n                ".join(c.plans) + """,
            )

            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
            LOG = logging.getLogger("notebook")

            REGION = "us-west-2"
""" + hf_token_init + """
            # Concurrency = how many in-flight HTTP requests the driver
            # keeps open against vLLM inside ONE container. Default tuned
            # for the """ + c.primary_instance + """ container — short-prompt workloads
            # fit comfortably within the KV cache budget. Tune lower if
            # you hit OOM, higher if you have much longer prompts and
            # want to saturate GPU compute.
            CONCURRENCY = """ + str(c.concurrency_default) + """

            print(f"REGION = {REGION}")
            """ + hf_token_print + """
            print(f"CONCURRENCY = {CONCURRENCY}")
            """
        )),
        code(dedent(
            """\
            sts = boto3.client("sts", region_name=REGION)
            identity = sts.get_caller_identity()
            print(f"Account: {identity['Account']}")
            print(f"ARN:     {identity['Arn']}")
            """
        )),

        # --- 0.5 Persistent run logging + persist() helper --------------
        md(dedent(
            """\
            ## 0.5 Persistent run logging (survives Jupyter freezes)

            All Python `logging` output from this notebook is mirrored to
            `outputs/_runs/<timestamp>/run.log` on disk, and the key results
            (sections 8.5 through 8.9) are saved as JSON + CSV via the
            `persist()` helper — so you can reconstruct the run without needing
            the Jupyter kernel alive. Cell `print()` output still renders
            normally in every cell (we don't redirect stdout).

            Safe to run multiple times — each invocation starts a new
            timestamped run dir; a symlink `outputs/_runs/latest` always
            points to the most recent one.
            """
        )),
        code(dedent(
            """\
            # --- file logger + persist() ---
            import logging
            from datetime import datetime
            _RUN_TS = datetime.now().strftime("%Y%m%dT%H%M%S")
            RUN_DIR = PROJECT_ROOT / "outputs" / "_runs" / _RUN_TS
            (RUN_DIR / "stats").mkdir(parents=True, exist_ok=True)
            _latest = PROJECT_ROOT / "outputs" / "_runs" / "latest"
            try:
                if _latest.is_symlink() or _latest.exists():
                    _latest.unlink()
                _latest.symlink_to(_RUN_TS)
            except OSError:
                pass

            # Logging: one FileHandler (-> run.log, survives a Jupyter freeze)
            # plus one StreamHandler so log lines also render in the cell.
            #
            # NOTE: we deliberately do NOT replace sys.stdout/sys.stderr. ipykernel
            # hands each cell a fresh stdout that routes to that cell via iopub;
            # capturing/replacing it in this setup cell freezes it on THIS cell's
            # stream, so every later cell's print() (e.g. the cost numbers in
            # 8.5-8.9) would vanish from the notebook. Leaving stdout alone means
            # print() renders normally in every cell; run.log + persist() (below)
            # provide the durable copy.
            _root = logging.getLogger()
            _root.setLevel(logging.INFO)
            _log_path = RUN_DIR / "run.log"
            for _h in list(_root.handlers):          # re-run safety
                if getattr(_h, "_persist_run", False):
                    _root.removeHandler(_h)
            _fmt = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
            _fh = logging.FileHandler(_log_path)
            _fh._persist_run = True
            _fh.setFormatter(_fmt)
            _root.addHandler(_fh)
            _sh = logging.StreamHandler()            # live sys.stderr -> renders in-cell
            _sh._persist_run = True
            _sh.setFormatter(_fmt)
            _root.addHandler(_sh)

            # persist() — drop any result into RUN_DIR/stats/<name>.{json,csv}
            def persist(name: str, obj):
                out = RUN_DIR / "stats"
                out.mkdir(exist_ok=True)
                try:
                    if isinstance(obj, pd.DataFrame):
                        p = out / f"{name}.csv"
                        obj.to_csv(p, index=False)
                    else:
                        p = out / f"{name}.json"
                        with p.open("w") as f:
                            json.dump(obj, f, indent=2, default=str)
                    print(f"persist() -> {p.relative_to(PROJECT_ROOT)}")
                    return p
                except Exception as exc:
                    logging.error("persist(%s) failed: %s", name, exc)
                    return None

            print(f"run dir: {RUN_DIR}")
            print(f"log:     {_log_path}")
            print(f"symlink: {_latest}")
            """
        )),

        # --- 1. Pick a plan ------------------------------------------------
        md(
            "## 1. Pick a deployment plan\n\n"
            f"Available plans for **{c.display_name}** (exported by `models.{c.package}`):\n\n"
            + "\n".join(f"* `{p}()`" + (" — **default**" if p == c.default_plan else "") for p in c.plans)
            + "\n\nPlan is a Pydantic object — inspect and override any field you want."
        ),
        code(
            "plan = " + c.default_plan + "()\n"
            + "\n".join(f"# plan = {p}()" for p in c.plans if p != c.default_plan) + "\n" +
            dedent("""\

            print(f"model:    {plan.model_spec.hf_model_id}")
            print(f"region:   {plan.region}")
            print(f"TP/DP/PP: {plan.tensor_parallel}/{plan.data_parallel}/{plan.pipeline_parallel}")
            print(f"in_flight_per_job: {plan.in_flight_per_job}")
            for ce in plan.compute_environments:
                print(f"  CE {ce.name_suffix!s:16s} [{ce.capacity_mode}] "
                      f"instance_types={ce.instance_types} max_vcpus={ce.max_vcpus}")
            print(f"queues: {[(q.name_suffix, q.compute_environment_suffixes) for q in plan.queues]}")
            """
        )),

        # --- 2. Deploy -----------------------------------------------------
        md(dedent(
            """\
            ## 2. Deploy the Batch stack

            `deploy()` creates or updates the CloudFormation stack:
            IAM roles, S3 staging bucket, ECR repo, Batch CE / Queue / JobDefinition.

            Takes ~3–5 minutes on first create. Subsequent deploys are fast (delta).
            """
        )),
        code(dedent(
            """\
            stack = deploy(plan)
            print(f"stack:                 {stack.stack_name}")
            print(f"staging bucket:        s3://{stack.staging_bucket}/")
            print(f"job definition ARN:    {stack.job_definition_arn}")
            print(f"ECR repo URI:          {stack.ecr_repository_uri}")
            print(f"queue ARNs:            {stack.queue_arns_by_suffix}")
            """
        )),

        # --- 2.5 Upsert HF token into Secrets Manager (gated models only) -
        *([
            md(dedent(
                """\
                ## 2.5 Upsert the HuggingFace token into Secrets Manager

                The stack's `HfTokenSecret` was created with a placeholder
                value. The Batch container reads the token from Secrets
                Manager at task-start time via the JobDefinition's `secrets`
                block — it **never** gets passed via `SubmitJob` environment
                overrides, **never** appears in `describe-jobs` output.

                Running this cell:
                1. Asserts the token you pasted above isn't still the
                   placeholder.
                2. Calls `PutSecretValue` on the stack's secret ARN.
                3. Blanks out `HF_TOKEN` in the Python kernel so accidental
                   prints don't leak it.

                After this, you can submit jobs without passing the token
                anywhere — it lives in Secrets Manager.
                """
            )),
            code(dedent(
                """\
                assert HF_TOKEN != "PLACEHOLDER_PASTE_YOUR_HF_TOKEN", \\
                    "Scroll up to section 0.3 and paste your HF token before running this cell."
                assert HF_TOKEN.startswith("hf_"), \\
                    f"{HF_TOKEN[:8]}... doesn't look like an HF token (should start with 'hf_')"

                secret_arn = upsert_hf_token(
                    stack.hf_token_secret_arn,
                    HF_TOKEN,
                    region=REGION,
                )
                print(f"HF token stored in: {secret_arn}")

                # Blank out the in-kernel copy so stray prints don't leak it.
                HF_TOKEN = "(stored in Secrets Manager)"
                """
            )),
        ] if c.gated else []),

        # --- 3. Build + push container image -------------------------------
        md(dedent(
            """\
            ## 3. Build + push the container image (one-off)

            Skip this cell if your ECR repo already has a working image tag.
            The build needs Docker + a working AWS CLI (for ECR login).

            Cost: ~15 GB base image download (vLLM official) + a few MB of our layer.
            Runs ~5–10 min on fast connections.

            > **Heads up** — `run.sh` ships `--enable-prefix-caching`
            > (via the `ENABLE_PREFIX_CACHING=true` env default) and the
            > driver now defaults to `CONCURRENCY=100`. If your last
            > pushed image predates these, **rebuild** — otherwise the
            > flag is silently absent and throughput won't match.
            """
        )),
        code(dedent(
            """\
            # Uncomment and run once; the cell takes ~10 min.
            # import subprocess
            #
            # IMAGE_TAG = "latest"
            # REGISTRY = stack.ecr_repository_uri.split("/")[0]
            #
            # # 1. Log Docker into ECR
            # login = subprocess.run(
            #     ["aws", "ecr", "get-login-password", "--region", REGION],
            #     capture_output=True, text=True, check=True,
            # )
            # subprocess.run(
            #     ["docker", "login", "--username", "AWS", "--password-stdin", REGISTRY],
            #     input=login.stdout, text=True, check=True,
            # )
            #
            # # 2. Build (amd64 — Batch EC2 instances are x86)
            # subprocess.run([
            #     "docker", "build", "--platform", "linux/amd64",
            #     "-t", f"{stack.ecr_repository_uri}:{IMAGE_TAG}",
            #     "-f", str(PROJECT_ROOT / "src/llm_batch_deploy/runtime/Dockerfile"),
            #     str(PROJECT_ROOT),
            # ], check=True)
            #
            # # 3. Push
            # subprocess.run([
            #     "docker", "push", f"{stack.ecr_repository_uri}:{IMAGE_TAG}",
            # ], check=True)
            #
            # # 4. Redeploy so the JobDefinition picks up the real URI
            # stack = deploy(plan, container_image_uri=f"{stack.ecr_repository_uri}:{IMAGE_TAG}")
            # print(f"ContainerImageUri = {stack.ecr_repository_uri}:{IMAGE_TAG}")
            """
        )),

        # --- 4. Upload inputs ----------------------------------------------
        md(dedent(
            f"""\
            ## 4. Prepare + upload inputs

            Reference workload: a single JSONL under
            `sample-data/{c.domain}/{c.sample_data_file}` (per-domain content
            described in `sample-data/{c.domain}/README.md`).
            Each line stores `{{"text": ..., "meta": ...}}` — we convert each to
            an OpenAI ChatCompletions request body on the fly and write
            one processed JSONL into `.scratch/inputs/`.

            Section 5 will ship this single file as one input URI to a
            single Batch job. To fan out, supply a list of input files
            (e.g. all 10 seed files in `sample-data/{c.domain}/`) with
            `max_uris_per_job=1` — same code path, more parallel jobs.
            """
        )),
        code(dedent(
            """\
            # Domain-specific system prompt shared across all records — one fixed
            # system prompt is exactly what prefix caching was designed for.
            # We reuse the per-model SYSTEM_PROMPT from the benchmark tree so
            # smoke, batch and benchmark stay in sync.
            #
            # Section 0 already imported the *batch* `models` package, so
            # `models.<name>` is cached in sys.modules. The benchmark tree has a
            # separate `models` package of the same name, so a plain
            # `from models.<name> import SYSTEM_PROMPT` would resolve to the
            # cached batch package (which has no SYSTEM_PROMPT) and raise
            # ImportError. Load the benchmark prompts.py directly by file path
            # under a unique module name to dodge the sys.modules collision —
            # and to skip the benchmark package's __init__, which imports
            # vllm_ec2_bench (not on this notebook's path).
            import importlib.util as _ilu  # noqa: E402
            _prompts_path = PROJECT_ROOT.parent / "benchmark" / "models" / """ + repr(c.package) + """ / "prompts.py"
            _spec = _ilu.spec_from_file_location(""" + repr(f"_bench_{c.package}_prompts") + """, _prompts_path)
            _bench_prompts = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_bench_prompts)
            SYSTEM_PROMPT = _bench_prompts.SYSTEM_PROMPT

            # Source: one of the synthesized seed files for this model's domain.
            src_file = PROJECT_ROOT.parent / "sample-data" / """ + repr(c.domain) + """ / """ + repr(c.sample_data_file) + """
            assert src_file.exists(), f"expected merged source at {src_file}"

            # Output: one ChatCompletions-shaped JSONL under
            # .scratch/inputs/ with id + messages + max_tokens per line.
            input_dir = PROJECT_ROOT / ".scratch" / "inputs"
            input_dir.mkdir(parents=True, exist_ok=True)
            for f in input_dir.glob("*.jsonl"):
                f.unlink()   # clean stale files from prior runs

            dst = input_dir / src_file.name
            with src_file.open() as fin, dst.open("w") as fout:
                for i, line in enumerate(fin):
                    row = json.loads(line)
                    text = row.get("text") or row.get("content") or ""
                    if not text:
                        continue
                    record = {
                        "id": f"{src_file.stem}-{i:06d}",
                        "model": plan.model_spec.served_model_name,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user",   "content": text},
                        ],
                        "max_tokens": 512,
                        "temperature": 0.2,
                    }
                    fout.write(json.dumps(record) + "\\n")

            shard_paths = [dst]   # one file for now; list-of-paths scales out

            total_records = sum(1 for _ in dst.open())
            total_mib = dst.stat().st_size / 1024 / 1024
            print(f"prepared 1 file, {total_records:,} records, {total_mib:.1f} MiB")
            print(f"  {dst.name}: {total_records:,} records, {total_mib:.2f} MiB")
            """
        )),

        # --- 5. Submit -----------------------------------------------------
        md(dedent(
            """\
            ## 5. Submit the batch

            `submit_batch()`:

            * Uploads each local JSONL to the staging bucket.
            * Packs URIs into per-job **manifests**. A manifest is a
              JSONL where each line is an input-file S3 URI; the
              container reads each URI and processes every record from
              it. One manifest = one Batch job.
            * With 1 input file and `max_uris_per_job=1` → 1 manifest
              with 1 URI → 1 Batch job processing all 100K records.
              To fan out to parallel jobs once you have more input
              files, keep `max_uris_per_job=1` and pass a longer
              `input_sources` list, or raise the knob to pack multiple
              files per job.
            * Tenacity-wrapped against throttling; partial submissions
              are recorded so failed shards don't crash the whole run.
            * Returns a report — see `.to_dataframe()` for a per-shard
              summary.
            """
        )),
        code(dedent(
            """\
            # CONCURRENCY is set in section 0. Re-bind plan.in_flight_per_job
            # so the displayed values + JobDefinition env default agree with
            # the runtime value we're about to submit. submit_batch's own
            # `in_flight_per_job` arg is what actually reaches the container
            # via the SubmitJob containerOverrides, so CONCURRENCY is the
            # single source of truth.
            plan = plan.model_copy(update={"in_flight_per_job": CONCURRENCY})

            report = submit_batch(
                input_sources=shard_paths,    # list of 1 JSONL file here
                stack_outputs=stack,
                plan=plan,
                in_flight_per_job=CONCURRENCY,
                max_uris_per_job=1,           # 1 URI per job (= 1 job here)
                overwrite=False,
            )
            print(f"submission_id: {report.submission_id}")
            print(f"summary:       {report.summary()}")
            persist("5_submission_report", report.summary())
            persist("5_shards", report.to_dataframe())
            report.to_dataframe()
            """
        )),

        # --- 6. Wait -------------------------------------------------------
        md(dedent(
            """\
            ## 6. Wait for jobs to complete

            With the current config (`max_uris_per_job=1`, one input file),
            one container processes the full input shard sequentially.
            For a single 10K-record seed file expect:

            * 0-10 min: instance provisions + vLLM boots +
              """ + c.display_name + """ weights load.
            * Then ~25-30 min of inference at steady-state
              (10K records × ~196 avg output tokens at ~1,200
              output tok/s on a """ + c.primary_instance + """ container). Prefix caching
              (our shared system prompt) shaves most prefill work.

            Typical total wall-clock for the single-seed reference run:
            ~35 min end-to-end.

            When you scale to 10 parallel jobs (one per seed file, total
            100K records), wall-clock stays roughly the same as the
            single-seed run because the 10 containers run in parallel —
            the same math but 10× the total records in the same time.

            `max_wait_s` default below allows 8 hours, plenty of headroom
            for any single-seed or 10-seed parallel run.
            """
        )),
        code(dedent(
            """\
            final = wait_for_completion(
                report, poll_every_s=60, region=REGION, max_wait_s=28800,
            )
            print(final.summary())
            persist("6_final_status_summary", final.summary())
            persist("6_final_status_df", final.to_dataframe())
            final.to_dataframe()
            """
        )),

        # --- 7. Sample -----------------------------------------------------
        md(dedent(
            """\
            ## 7. Sample the outputs

            Cheap peek at a few result records without downloading everything.
            """
        )),
        code(dedent(
            """\
            def _first_message_text(resp):
                # Defensive: any level (response, choices, the first choice,
                # message) may be missing or None on errored records. Some
                # models (e.g. gpt-oss) put text in reasoning_content rather
                # than content, so fall back to that before giving up.
                choices = (resp or {}).get("choices") or []
                msg = (choices[0] or {}).get("message") if choices else None
                msg = msg or {}
                return msg.get("content") or msg.get("reasoning_content") or ""

            samples = sample_outputs(report, n=3, region=REGION)
            for s in samples:
                print("---", s.get("id", "?"))
                print("error:  ", s.get("error"))
                print("output: ", _first_message_text(s.get("response"))[:200])
            """
        )),

        # --- 8. Download ---------------------------------------------------
        md(dedent(
            """\
            ## 8. Download outputs

            ### Where everything lands on disk

            This notebook writes to two separate trees under `outputs/`
            (relative to `PROJECT_ROOT`, i.e. the `batch/` dir — **one level up
            from this `notebooks/` folder**):

            ```
            batch/outputs/
            ├── <submission_id>/              ← THE ACTUAL MODEL OUTPUTS (this cell)
            │   └── shard-0000/
            │       ├── <input-file>.jsonl    ← one line per record: {id, response, usage, error}
            │       │                            (the model's completion for each prompt)
            │       └── _summary.json         ← per-shard token counts + wall-clock
            │
            └── _runs/<timestamp>/            ← this run's LOGS + ANALYSIS (sections 0.5, 8.5-8.9)
                ├── run.log
                └── stats/
                    ├── 8_8_cost_estimate.json        ← actual AWS bill (per-instance)
                    ├── 8_9_project_economics.json    ← $/1M tokens, total cost, real-world tok/s
                    ├── 8_5_aggregate_throughput.json, 8_6_llmeter_stats.json, 8_7_real_world_wall_clock.json
                    └── ... (5_* submission, 6_* final status)
            ```

            `outputs/_runs/latest` is a symlink to the most recent run. The
            **dollar-per-million-token** figures are in
            `stats/8_9_project_economics.json` (key `usd_per_1m_output_tokens`),
            also printed inline by section 8.9 below.

            > Tip: `outputs/` is git-ignored, so JupyterLab may hide it — enable
            > **View → Show Hidden Files** if you don't see it in the file browser.
            """
        )),
        code(dedent(
            """\
            collect = download_outputs(
                report,
                output_dir=PROJECT_ROOT / "outputs" / report.submission_id,
                region=REGION,
            )
            print(f"downloaded {len(collect.files_downloaded)} files")
            print(f"local dir:  {collect.output_dir}")
            print(f"  -> per-record model completions: {collect.output_dir}/shard-*/")
            print(f"  -> cost + $/1M-token stats:      {RUN_DIR / 'stats'}/")
            collect.to_dataframe()
            """
        )),

        # --- 8.5 Throughput (per-shard) -----------------------------------
        md(dedent(
            """\
            ## 8.5 Throughput — per-shard (inference loop only)

            Every shard's `_summary.json` carries token counts + wall-clock
            duration of the inference loop (not including vLLM startup).
            `CollectReport.aggregate_throughput()` rolls these up two ways:

            * **Mean per shard** — average of per-shard token throughput.
              Represents what one container achieved on its own — a
              single-endpoint-style number you can compare against
              published LLMeter or benchmark results for the same model
              + hardware.
            * **Summed across shards** (fleet view) — total tokens / max
              wall-clock. Shows what the whole submission achieved when
              Batch ran multiple containers in parallel.

            Key formulas (applied per shard, then aggregated):

            ```
            shard.output_tokens_per_second = shard.total_output_tokens / shard.wall_clock_s
            fleet.summed_output_tps        = sum(shard.total_output_tokens)
                                             / max(shard.wall_clock_s)
            fleet.mean_per_shard_output_tps = mean(shard.output_tokens_per_second)
            ```

            **These numbers exclude queue wait, container boot, and vLLM
            model-load.** See section 8.7 for the end-to-end view.
            """
        )),
        code(dedent(
            """\
            agg = collect.aggregate_throughput()
            for k, v in agg.items():
                if isinstance(v, float):
                    print(f"  {k:45s} {v:,.2f}")
                elif isinstance(v, int):
                    print(f"  {k:45s} {v:,}")
                else:
                    print(f"  {k:45s} {v}")
            persist("8_5_aggregate_throughput", agg)
            """
        )),
        code(dedent(
            """\
            # One-row summary combining per-container throughput (mean across
            # shards — apples-to-apples with single-endpoint benchmarks)
            # plus the fleet total (what all containers produced in parallel).
            # Useful for dropping alongside rows from other benchmarks that
            # follow the same schema (LLMeter, etc.).
            # With a multi-type CE, we summarise all instance types tried:
            ce0 = plan.compute_environments[0]
            primary_instance_type = "/".join(ce0.instance_types)
            row = collect.comparison_row(
                instance_type=primary_instance_type,
                concurrency=CONCURRENCY,
            )
            persist("8_5_comparison_row", row)
            pd.DataFrame([row])
            """
        )),

        # --- 8.6 Benchmark-compatible stats -------------------------------
        md(dedent(
            """\
            ## 8.6 Benchmark-compatible report (LLMeter stats.json schema)

            Emits a flat dict whose field names match
            [LLMeter](https://github.com/awslabs/llmeter)'s
            `stats.json` output — so this row is directly drop-in-able
            next to any LLMeter-driven benchmark run on the same model
            + hardware for apples-to-apples comparison.

            Under the hood it walks the per-request JSONL files downloaded
            in section 8 and computes the distributions (avg/p50/p90/p99)
            for `time_to_last_token`, `num_tokens_input`,
            `num_tokens_output`. Percentiles use Python's
            `statistics.median` + `statistics.quantiles`, matching
            LLMeter's implementation exactly.
            """
        )),
        code(dedent(
            """\
            bench = collect.llmeter_comparable_stats(
                model_id=plan.model_spec.served_model_name,
                concurrency=CONCURRENCY,
            )
            persist("8_6_llmeter_stats", bench)
            # Format ints with thousand separators, floats with 4 decimals
            def _fmt(v):
                if isinstance(v, float):
                    return f"{v:,.4f}"
                if isinstance(v, int):
                    return f"{v:,}"
                return v
            pd.DataFrame(
                [(k, _fmt(v)) for k, v in bench.items()],
                columns=["metric", "value"],
            )
            """
        )),

        # --- 8.7 Real-world wall-clock ------------------------------------
        md(dedent(
            """\
            ## 8.7 Real-world wall-clock throughput

            "From when I hit Submit to when the last job finished, how
            many tokens per second did I actually process?"

            This uses Batch's own `createdAt` / `startedAt` / `stoppedAt`
            timestamps — **measured server-side**, so it survives your
            Jupyter kernel dying mid-wait. Even if you close the laptop
            and come back a day later, the numbers are exact.

            Formula:

            ```
            duration_s       = (max(job.stoppedAt) - min(job.createdAt)) / 1000
            real_world_tps   = total_output_tokens / duration_s
            real_world_rps   = total_succeeded_requests / duration_s
            ```

            This includes queue wait, container boot, and vLLM
            model-load — the true "what the user observed" number.

            If `final` below is no longer in your kernel (e.g. you
            restarted), re-run `poll(report, region=REGION)` to rebuild
            it — it's a single DescribeJobs call.
            """
        )),
        code(dedent(
            """\
            # If you don't have `final` in your kernel anymore (restart, etc.),
            # uncomment this to rebuild it from Batch:
            # final = poll(report, region=REGION)

            rw = collect.real_world_wall_clock_stats(final)
            persist("8_7_real_world_wall_clock", rw)
            for k, v in rw.items():
                if isinstance(v, float):
                    print(f"  {k:45s} {v:,.2f}")
                elif isinstance(v, int):
                    print(f"  {k:45s} {v:,}")
                else:
                    print(f"  {k:45s} {v}")
            """
        )),

        # --- 8.8 $ cost via Pricing API -----------------------------------
        md(dedent(
            """\
            ## 8.8 Cost estimate — per-EC2-instance actual billing

            Computes the **actual AWS bill** by walking:

            1. Each job's `container_instance_arn` (from Batch
               `DescribeJobs`).
            2. Each container instance's `ec2InstanceId` (from ECS
               `DescribeContainerInstances`).
            3. Each EC2 instance's `LaunchTime`, `TerminationTime`,
               `InstanceType`, `AvailabilityZone`, `Lifecycle`
               (spot vs on-demand).
            4. For spot: `DescribeSpotPriceHistory` for the AZ + time
               window. Handles mid-lifespan price changes via integral.
            5. For on-demand: AWS Pricing API list-price.

            Formula — the actual bill is an integral, not a simple
            rate × time:

            ```
            instance.total_usd = ∫ price(t) dt  over [launch, termination]
            fleet.total_usd    = sum(instance.total_usd for each unique EC2 instance)
            ```

            Summing per-job `stoppedAt - startedAt` would miss 20-40%
            of the bill (instance boot, idle time between jobs, drain).

            ### How AWS spot pricing actually works

            AWS post-2017: spot price **fluctuates** (market-based)
            and is billed **per second at the current market price**.
            A single instance held for 30 minutes may see 0-3 price
            changes within that window — you pay whatever the market
            rate was during each second. If the market price ever rises
            above your max bid (default = on-demand), the instance is
            interrupted and you stop paying.

            So per-instance average $/hour is *not* the acquisition
            price; it's the time-weighted mean across the lifespan:

            ```
            instance.hourly_usd_avg = instance.total_usd
                                      / (instance.billable_seconds / 3600)
            ```

            `n_price_points = 1` means price never changed during the
            lifespan (the typical case for runs <1 hour). `>1` flags
            that spot prices moved — inspect `min_hourly_usd` /
            `max_hourly_usd` for the range, or call
            `cost.price_timeline(instance_id)` for the exact segment-
            level breakdown.

            IAM permissions the caller needs:
            `ecs:DescribeContainerInstances`, `ec2:DescribeInstances`,
            `ec2:DescribeSpotPriceHistory`, `pricing:GetProducts`,
            `ssm:GetParameter` (for region name lookup).
            """
        )),
        code(dedent(
            """\
            cost = estimate_cost(
                collect_report=collect,
                status_snapshot=final,
                region=REGION,
            )
            persist("8_8_cost_estimate", cost.as_dict())
            print(f"instances:     {cost.instance_count:,}")
            print(f"billable hrs:  {cost.total_billable_hours:,.4f}")
            print(f"total $:       ${cost.total_usd:,.4f}")
            if cost.unresolved_job_ids:
                print(f"unresolved:    {len(cost.unresolved_job_ids):,} jobs "
                      f"(see .unresolved_job_ids)")
            """
        )),
        code(dedent(
            """\
            # Per-instance breakdown. Selected columns with thousand delimiters.
            _df = pd.DataFrame([i.to_dict() for i in cost.per_instance])
            # Compact view — drop raw epoch_ms columns and the full job list.
            _show = _df.drop(columns=[
                c for c in ("launch_time_ms", "termination_time_ms", "job_ids")
                if c in _df.columns
            ]).copy()
            # Format numeric columns with thousand delimiters + currency.
            for c in ("usd_total", "hourly_usd_avg",
                       "first_price_usd", "last_price_usd",
                       "min_hourly_usd", "max_hourly_usd"):
                if c in _show:
                    _show[c] = _show[c].map(lambda v: f"${v:,.6f}")
            for c in ("billable_seconds", "billable_hours"):
                if c in _show:
                    _show[c] = _show[c].map(lambda v: f"{v:,.4f}")
            _show
            """
        )),
        code(dedent(
            """\
            # Any instance saw spot price move mid-lifespan? Call
            # cost.price_timeline(instance_id) for the segment breakdown.
            # Each row = one constant-price segment (start_ms, end_ms, hourly_usd,
            # segment_cost_usd). For `n_price_points == 1` instances the list
            # has one row covering the whole lifespan.
            moved = [i for i in cost.per_instance if i.n_price_points > 1]
            if moved:
                print(f"{len(moved):,} instance(s) saw spot price changes during lifespan:")
                for inst in moved[:3]:   # show up to 3
                    print(f"\\n  {inst.instance_id} ({inst.instance_type}, {inst.availability_zone}):")
                    for seg in cost.price_timeline(inst.instance_id):
                        print(
                            f"    [{seg['segment_start_ms']:>14} \\u2192 "
                            f"{seg['segment_end_ms']:>14}] "
                            f"{seg['duration_seconds']:>8,.2f}s @ "
                            f"${seg['hourly_usd']:.6f}/hr "
                            f"= ${seg['segment_cost_usd']:.6f}"
                        )
            else:
                print("All instances saw a flat spot price during their lifespan "
                      "(typical for sub-hour runs).")
            """
        )),

        # --- 8.9 Project economics ----------------------------------------
        md(dedent(
            """\
            ## 8.9 Project economics — the three headline numbers

            * **Total cost** — real AWS bill (from section 8.8).
            * **Total tokens** — input + output across all jobs,
              including tokens from records that errored mid-request
              (where vLLM still reported usage).
            * **Duration** — wall clock from first SubmitJob to last
              job done (`max(stoppedAt) - min(createdAt)`). Includes
              queue wait, boot, vLLM warmup — **this is what the user
              observes end-to-end**.

            Derived:

            ```
            dollars_per_1M_output_tokens = total_cost_usd
                                           / (total_output_tokens / 1_000_000)
            real_world_tokens_per_second = total_output_tokens / duration_seconds
            ```

            `$/1M output tokens` is the economic efficiency headline.
            `real_world_tps` gives planning capacity: "how long will a
            10× bigger job take?"
            """
        )),
        code(dedent(
            """\
            econ = collect.project_economics(final, cost)
            persist("8_9_project_economics", econ)
            for k, v in econ.items():
                if isinstance(v, float):
                    # Show $ fields with currency format, others with comma grouping.
                    if "cost" in k or "usd" in k or "dollar" in k:
                        print(f"  {k:45s} ${v:,.4f}")
                    else:
                        print(f"  {k:45s} {v:,.2f}")
                elif isinstance(v, int):
                    print(f"  {k:45s} {v:,}")
                else:
                    print(f"  {k:45s} {v}")
            """
        )),

        # --- 9. Teardown ---------------------------------------------------
        md(dedent(
            """\
            ## 9. Teardown (commented out — uncomment to destroy the stack)

            * The StagingBucket and ECR repo are **Retain**: they survive stack
              delete (so your outputs + images don't disappear).
            * Batch CE + Queue + JobDefinition + IAM roles are deleted.
            """
        )),
        code(dedent(
            """\
            # teardown(stack.stack_name, region=REGION)
            """
        )),
    ]


def build_notebook(c: BatchModelConfig) -> Path:
    global CFG
    CFG = c
    out_path = REPO_ROOT / "notebooks" / c.nb_filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nb = {
        "cells": cells(),
        "metadata": {
            # Use the universal "python3" kernel that ipykernel ships in every
            # environment, rather than a custom named kernel that would require
            # out-of-band `ipykernel install` registration pointing at an
            # absolute, machine-specific .venv path. Launched from the project
            # venv (per the README), "python3" resolves to that venv's
            # interpreter; on managed envs (SageMaker Studio, Colab) it maps to
            # their default kernel. Keeps the notebooks self-contained.
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
    out_path.write_text(json.dumps(nb, indent=1))
    print(f"Wrote notebook: {out_path} ({out_path.stat().st_size/1024:.1f} KiB, {len(nb['cells'])} cells)")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODEL_CONFIGS), help="single model")
    parser.add_argument("--all", action="store_true", help="build all model notebooks")
    args = parser.parse_args()
    if args.all:
        targets = list(MODEL_CONFIGS.values())
    elif args.model:
        targets = [MODEL_CONFIGS[args.model]]
    else:
        # Backwards-compat: build medgemma_27b by default.
        targets = [MODEL_CONFIGS["medgemma_27b"]]
    for c in targets:
        build_notebook(c)


if __name__ == "__main__":
    main()
