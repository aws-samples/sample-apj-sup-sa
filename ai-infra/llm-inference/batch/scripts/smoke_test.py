"""End-to-end smoke test — deploy → submit → wait → collect → teardown.

Runs the whole flow against real AWS for a chosen model. Uses 3 input prompts
to keep cost low. The deploy+teardown overhead still means this takes ~15 min
and creates real AWS resources (Compute Environment, Queue, S3 bucket, ECR
repo, IAM roles). Expect ~$0.50 (qwen3-8b on g6e spot) up to ~$2 (llama-4-scout
on p4d spot) per run.

The container image MUST already be in ECR before invoking this script. Build
and push it first with::

    scripts/build_and_push.sh <ecr-repo-uri> [tag]

Then run the smoke (gated behind an opt-in env var so it can't run by accident)::

    LLM_BATCH_SMOKE=YES python scripts/smoke_test.py --model qwen3_8b
    LLM_BATCH_SMOKE=YES python scripts/smoke_test.py --model medgemma_27b
    LLM_BATCH_SMOKE=YES python scripts/smoke_test.py --model llama_4_scout_17b

Requirements:
* AWS creds for an account with the chosen instance family on spot in us-west-2
* HF_TOKEN env var (only required for gated models — medgemma_27b, llama_4_scout_17b)
* The Docker image already pushed to the model's ECR repo (run
  ``scripts/build_and_push.sh`` once first)
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from time import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

LOG = logging.getLogger("smoke")


# Model registry: each entry maps a CLI --model value to (package, plan_factory,
# domain). Domain selects which sample-data dir feeds smoke prompts.
MODEL_REGISTRY: dict[str, tuple[str, str, str]] = {
    "qwen3_8b":              ("models.qwen3_8b",              "g6e_spot_single_queue", "travel"),
    "mistral_small_3_2_24b": ("models.mistral_small_3_2_24b", "g7e_spot_single_queue", "travel"),
    "qwen3_30b_a3b":         ("models.qwen3_30b_a3b",         "g7e_spot_single_queue", "travel"),
    "gemma_4_31b":           ("models.gemma_4_31b",           "g7e_spot_single_queue", "travel"),
    "medgemma_27b":          ("models.medgemma_27b",          "g7e_spot_single_queue", "travel"),
    "llama_4_scout_17b":     ("models.llama_4_scout_17b",     "p4d_spot_single_queue", "travel"),
    "gpt_oss_20b":           ("models.gpt_oss_20b",           "g7e_spot_single_queue", "travel"),
    "qwen3_coder_next":      ("models.qwen3_coder_next",      "p4d_spot_single_queue", "travel"),
}


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if os.environ.get("LLM_BATCH_SMOKE") != "YES":
        print("Opt-in required: LLM_BATCH_SMOKE=YES", file=sys.stderr)
        print("(This spins up real AWS resources and costs $$$.)", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="medgemma_27b",
                        choices=sorted(MODEL_REGISTRY.keys()),
                        help="Model package to smoke-test.")
    parser.add_argument("--tag", default=None,
                        help="Stack-tag suffix; defaults to <model>-smoke-<epoch>.")
    parser.add_argument("--skip-build", action="store_true",
                        help="Deprecated no-op. The smoke driver no longer "
                             "builds images; run scripts/build_and_push.sh "
                             "first. Accepted for backwards compatibility.")
    parser.add_argument("--skip-teardown", action="store_true",
                        help="Leave the stack in place after completion.")
    parser.add_argument("--n-prompts", type=int, default=3)
    args = parser.parse_args()

    pkg_name, factory_name, domain = MODEL_REGISTRY[args.model]

    from llm_batch_deploy.deployer import deploy, teardown
    from llm_batch_deploy.submitter import submit_batch, upsert_hf_token
    from llm_batch_deploy.waiter import (
        download_outputs, sample_outputs, wait_for_completion,
    )

    pkg = importlib.import_module(pkg_name)
    plan_factory = getattr(pkg, factory_name)
    plan = plan_factory()
    gated = getattr(plan.model_spec, "gated", False)
    hf_token = os.environ.get("HF_TOKEN", "")
    if gated and not hf_token:
        print(f"HF_TOKEN env var required for gated model {args.model}.",
              file=sys.stderr)
        return 1
    sid = args.tag or f"{args.model}-smoke-{int(time())}"

    LOG.info("Smoke test tag: %s", sid)
    LOG.info("Plan: %s (%s)",
             plan.model_spec.hf_model_id,
             plan.compute_environments[0].instance_types)

    # -------------------------------------------------------------------
    # 1. Deploy (initial — image URI placeholder)
    # -------------------------------------------------------------------
    LOG.info("=== Step 1: deploy stack ===")
    t0 = time()
    # IAM roles, ECR repo, and other resources inside the stack carry
    # fixed names derived from `resource_prefix` (not the stack name), so
    # only one stack per model can exist at a time. Use the plan's
    # canonical stack_name and let the deployer's `_stack_in_unrecoverable_state`
    # path clean up any prior failed stack on retry.
    stack = deploy(plan, stack_name=plan.model_spec.stack_name)
    LOG.info("Stack created in %ds. ECR: %s",
             int(time() - t0), stack.ecr_repository_uri)

    try:
        # -------------------------------------------------------------------
        # 2. Update stack with real image URI
        #
        # The container image must already be in ECR (build it first with
        # ``scripts/build_and_push.sh <ecr-repo-uri>``). The deploy step
        # 1 created the stack with a placeholder image; here we point it at
        # the real ``:latest`` tag.
        # -------------------------------------------------------------------
        LOG.info("=== Step 2: update stack with image URI ===")
        stack = deploy(
            plan,
            stack_name=stack.stack_name,
            container_image_uri=f"{stack.ecr_repository_uri}:latest",
        )

        # -------------------------------------------------------------------
        # 3. Prep inputs
        # -------------------------------------------------------------------
        LOG.info("=== Step 3: prep inputs (%d prompts, domain=%s) ===",
                 args.n_prompts, domain)
        shards_dir = PROJECT_ROOT.parent / "sample-data" / domain
        shard_files = sorted(shards_dir.glob("*.jsonl"))
        if not shard_files:
            raise FileNotFoundError(
                f"No shard files under {shards_dir}. Generate sample data first: "
                f"python sample-data/scripts/synthesize.py --smoke"
            )
        # Two record shapes are supported:
        #  text:   {"text": "..."} or {"content": "..."}
        #  vision: {"image_url": "...", "prompt": "..."}
        # We collect the raw records (not just the prompt text) so the vision
        # path can attach the image URL to the chat message.
        records: list[dict] = []
        for f in shard_files:
            if len(records) >= args.n_prompts:
                break
            with f.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if domain == "vision":
                        if rec.get("image_url") and rec.get("prompt"):
                            records.append(rec)
                    else:
                        text = rec.get("text") or rec.get("content")
                        if text:
                            records.append({"text": text})
                    if len(records) >= args.n_prompts:
                        break
        if len(records) < args.n_prompts:
            raise RuntimeError(
                f"Only found {len(records)} prompts in shards; asked for {args.n_prompts}"
            )
        # Reuse the per-model SYSTEM_PROMPT from the benchmark module so
        # smoke and benchmark stay in sync. The batch package was already
        # loaded as `models.<name>`; load the prompts module directly from
        # the benchmark tree so we don't conflict.
        prompts_path = (PROJECT_ROOT.parent / "benchmark"
                        / pkg_name.replace(".", "/") / "prompts.py")
        spec = importlib.util.spec_from_file_location(
            f"_bench_{args.model}_prompts", prompts_path)
        bench_prompts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bench_prompts)

        inputs_jsonl = PROJECT_ROOT / ".scratch" / "inputs" / f"inputs-{sid}.jsonl"
        inputs_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with inputs_jsonl.open("w") as f:
            for i, rec in enumerate(records[: args.n_prompts]):
                if domain == "vision":
                    user_content = [
                        {"type": "image_url",
                         "image_url": {"url": rec["image_url"]}},
                        {"type": "text", "text": rec["prompt"]},
                    ]
                else:
                    user_content = rec["text"]
                f.write(json.dumps({
                    "id": f"rec-{i:03d}",
                    "model": plan.model_spec.served_model_name,
                    "messages": [
                        {"role": "system", "content": bench_prompts.SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": 256,
                }) + "\n")

        # -------------------------------------------------------------------
        # 4a. Upsert HF token (gated models only)
        # -------------------------------------------------------------------
        if gated and hf_token and getattr(stack, "hf_token_secret_arn", None):
            LOG.info("=== Step 4a: upsert HF token to Secrets Manager ===")
            upsert_hf_token(
                stack.hf_token_secret_arn, hf_token, region=plan.region,
            )

        # -------------------------------------------------------------------
        # 4. Submit
        # -------------------------------------------------------------------
        LOG.info("=== Step 4: submit ===")
        report = submit_batch(
            input_sources=[inputs_jsonl],
            stack_outputs=stack,
            plan=plan,
            in_flight_per_job=args.n_prompts,
            max_uris_per_job=1,
        )
        LOG.info("Submitted: %s", report.summary())

        # -------------------------------------------------------------------
        # 5. Wait
        # -------------------------------------------------------------------
        # Wait budget per attempt =
        #   AWS Batch dispatch (~5 min) + image pull (~5 min) + vLLM cold-
        #   start (plan.vllm_startup_timeout_seconds — sized per model to
        #   cover HF weight download; e.g. 5400s for Llama-4-Scout's 218
        #   GiB) + actual inference (n_prompts * a few seconds).
        #
        # AWS Batch JobDefinitions are created with RetryStrategy.Attempts=2
        # (see deployer/cfn_batch.py) so an infrastructure failure (spot
        # interruption, host EC2 fault, docker timeout) restarts the job
        # from scratch — including a fresh ECR pull and HF weight
        # re-download. The smoke wait must cover (1 + max_attempts_after_
        # first) attempts in the worst case, otherwise a single spot
        # interrupt during attempt 1 of Llama-4-Scout would TimeoutError
        # the smoke driver before attempt 2 finishes warmup.
        #
        # Bug #17 fixed sizing one attempt off the plan; bug #19 fixes the
        # multiplicative shape — same retry-amplified pattern as bug #18.
        LOG.info("=== Step 5: wait for completion ===")
        _DISPATCH_BUDGET_S = 600          # AWS Batch SUBMITTED -> RUNNING
        _IMAGE_PULL_BUDGET_S = 600        # ECR -> instance
        _SMOKE_INFERENCE_BUDGET_S = 600   # n_prompts decode (small)
        _per_attempt_s = (
            _DISPATCH_BUDGET_S
            + _IMAGE_PULL_BUDGET_S
            + plan.vllm_startup_timeout_seconds
            + _SMOKE_INFERENCE_BUDGET_S
        )
        # Mirror the JobDef RetryStrategy.Attempts (2 = first try + 1
        # retry). Keep this in sync with cfn_batch.py — the regression
        # test pins the relationship.
        _BATCH_ATTEMPTS = 2
        max_wait_s = _per_attempt_s * _BATCH_ATTEMPTS
        LOG.info(
            "Wait budget: %ds = per-attempt %ds × %d Batch attempts "
            "(dispatch %d + image %d + vLLM startup %d + decode %d)",
            max_wait_s, _per_attempt_s, _BATCH_ATTEMPTS,
            _DISPATCH_BUDGET_S, _IMAGE_PULL_BUDGET_S,
            plan.vllm_startup_timeout_seconds, _SMOKE_INFERENCE_BUDGET_S,
        )
        final = wait_for_completion(
            report, poll_every_s=30, region=plan.region, max_wait_s=max_wait_s,
        )
        LOG.info("Final: %s", final.summary())
        if final.failed > 0:
            LOG.error("FAILED: %d jobs did not complete successfully.", final.failed)
            # Capture statusReason + last container log lines BEFORE teardown
            # deletes the CloudWatch log group (DeletionPolicy: Delete on
            # JobLogGroup means logs are gone after teardown).
            try:
                import boto3 as _boto3
                _batch = _boto3.client("batch", region_name=plan.region)
                _logs = _boto3.client("logs", region_name=plan.region)
                _job_ids = [s.job_id for s in report.shards]
                _resp = _batch.describe_jobs(jobs=_job_ids)
                for _j in _resp.get("jobs", []):
                    LOG.error(
                        "  Job %s status=%s reason=%s",
                        _j["jobId"], _j.get("status"), _j.get("statusReason"),
                    )
                    for _att in _j.get("attempts", []):
                        _ls = _att.get("container", {}).get("logStreamName")
                        _ec = _att.get("container", {}).get("exitCode")
                        _r = _att.get("statusReason")
                        LOG.error(
                            "    attempt: exitCode=%s statusReason=%s logStream=%s",
                            _ec, _r, _ls,
                        )
                        if _ls:
                            try:
                                _ev = _logs.get_log_events(
                                    logGroupName=f"/aws/batch/{plan.model_spec.resource_prefix}",
                                    logStreamName=_ls,
                                    startFromHead=False,
                                    limit=50,
                                )
                                for _e in _ev.get("events", [])[-30:]:
                                    LOG.error("      [cw] %s", _e.get("message", ""))
                            except Exception as _exc:
                                LOG.error("    (could not fetch log stream %s: %s)", _ls, _exc)
            except Exception as _exc:
                LOG.error("Failure-capture step itself failed: %s", _exc)
            return 2

        # -------------------------------------------------------------------
        # 6. Sample
        # -------------------------------------------------------------------
        LOG.info("=== Step 6: sample ===")
        for s in sample_outputs(report, n=args.n_prompts, region=plan.region):
            content = (s.get("response") or {}).get("choices", [{}])[0] \
                      .get("message", {}).get("content", "")
            LOG.info("Sample id=%s: %s", s.get("id"), content[:120])

        # -------------------------------------------------------------------
        # 7. Download
        # -------------------------------------------------------------------
        LOG.info("=== Step 7: download ===")
        collect = download_outputs(
            report,
            output_dir=PROJECT_ROOT / "outputs" / sid,
            region=plan.region,
        )
        LOG.info("Downloaded %d files to %s",
                 len(collect.files_downloaded), collect.output_dir)

    finally:
        # -------------------------------------------------------------------
        # 8. Teardown (unless skipped)
        # -------------------------------------------------------------------
        if not args.skip_teardown:
            LOG.info("=== Step 8: teardown ===")
            teardown(stack.stack_name, region=plan.region)

    LOG.info("Smoke test PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
