#!/usr/bin/env python3
"""Synthetic Bedrock workload generator for the dashboard demo.

Run for 24h to populate every widget on Bedrock-FM-Dashboard with realistic data:
multi-provider model mix, streaming + non-streaming, prompt caching, varied
max_tokens for stop-reason mix, intentional 4xx errors, requestMetadata tagging.

Usage:
    cd data-generator && pip install -r requirements.txt
    nohup python3 generate.py > generator.log 2>&1 &
    tail -f generator.log
    # stop:  kill <pid>   (sends SIGTERM, prints final stats)

Cost: ~$15-25 USD over 24h at default settings (us-east-1 list prices).
"""
import asyncio
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

# Model catalog: (modelId, selection_weight, supports_prompt_caching, short_label)
# Cheap models weighted heavily so 24h cost stays bounded; premium tier still
# gets enough volume to populate per-model latency percentiles.
MODELS = [
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", 30, True,  "haiku-4.5"),
    ("us.amazon.nova-micro-v1:0",                   20, False, "nova-micro"),
    ("us.amazon.nova-lite-v1:0",                    10, False, "nova-lite"),
    ("us.anthropic.claude-sonnet-4-6",              15, True,  "sonnet-4.6"),
    ("us.amazon.nova-pro-v1:0",                      8, False, "nova-pro"),
    ("us.meta.llama3-3-70b-instruct-v1:0",           7, False, "llama-3.3-70b"),
    ("us.anthropic.claude-opus-4-7",                10, True,  "opus-4.7"),
]

# Override the catalog without editing source: set BEDROCK_MODELS to a
# semicolon-separated list of "modelId,weight,cache,label" entries. Useful
# outside us-* regions where inference-profile prefixes differ (eu./apac./apne3.),
# or to skew the model mix. Example:
#   BEDROCK_MODELS="eu.anthropic.claude-sonnet-4-6,10,true,sonnet;eu.amazon.nova-lite-v1:0,20,false,nova-lite"
_env_models = os.environ.get("BEDROCK_MODELS", "").strip()
if _env_models:
    _parsed = []
    for _spec in _env_models.split(";"):
        _spec = _spec.strip()
        if not _spec:
            continue
        _parts = [p.strip() for p in _spec.split(",")]
        if len(_parts) != 4:
            raise SystemExit(
                f"BEDROCK_MODELS entry must be 'modelId,weight,cache,label'; got: {_spec!r}"
            )
        _mid, _weight, _cache, _label = _parts
        if not _weight.lstrip("-").isdigit():
            raise SystemExit(
                f"BEDROCK_MODELS weight must be an integer; got: {_weight!r} in {_spec!r}"
            )
        _parsed.append((_mid, int(_weight), _cache.lower() == "true", _label))
    MODELS = _parsed

# The dashboard's per-tenant widgets read requestMetadata.<TenantMetadataKey>
# (default "team"). Match it here so a dashboard deployed with a custom key
# (e.g. TenantMetadataKey=cost_center) still gets populated tenant breakdowns.
TENANT_METADATA_KEY = os.environ.get("TENANT_METADATA_KEY", "team")

TEAMS_APPS = {
    "platform":  ["api-gateway", "auth-service", "cli-helper"],
    "search":    ["query-rewriter", "ranker"],
    "support":   ["chatbot-v2", "ticket-summarizer"],
    "ml-ops":    ["eval-runner", "data-pipeline"],
    "finance":   ["invoice-extractor", "report-builder"],
    "marketing": ["copy-generator", "ab-tester"],
}

PROMPTS = [
    "Summarize the key points of microservice architecture in 3 bullets.",
    "What's the difference between TCP and UDP? Keep it under 100 words.",
    "Write a haiku about cloud computing.",
    "Explain prompt caching in one paragraph.",
    "List 5 benefits of infrastructure as code.",
    "What is the CAP theorem? Be concise.",
    "Translate 'good morning' into Spanish, French, and Japanese.",
    "Suggest a name for a new project management tool.",
    "What are the trade-offs of monolithic vs microservices?",
    "Write a SQL query to find the top 10 customers by revenue.",
    "Explain eventual consistency with a real-world analogy.",
    "What is OAuth 2.0? Give a one-line summary.",
    "Recommend a Python library for HTTP requests and explain why.",
    "What's idempotency in REST APIs?",
    "Describe the OSI model in a single sentence.",
]

# Long stable system prompt — must be identical across calls AND >=1024 tokens
# for Anthropic prompt caching to engage on Bedrock.
LONG_SYSTEM_PROMPT = (
    "You are an AI assistant for the AWS Bedrock observability dashboard demo. "
    "Answer concisely, accurately, and professionally. Do not invent facts. "
    "If unsure, say so. Keep responses under 200 words unless asked for more. "
    "Format code in fenced blocks. Cite AWS documentation when relevant. "
    "Never include credentials, secrets, or PII in your responses. "
    "If a user asks for an opinion on a sensitive topic, decline politely. "
    "Default to plain text — only use markdown when the user is clearly a developer. "
    "When asked about pricing, always remind users that Bedrock prices change; "
    "they should verify against the AWS pricing page. "
    "When asked about latency, distinguish full-request latency from time-to-first-token. "
    "When summarizing, prefer bullet lists over paragraphs. "
    "When generating SQL, default to ANSI-standard syntax unless a dialect is specified. "
    "When generating Python, target version 3.11+ and use type hints. "
    "When generating JavaScript, default to TypeScript with strict mode. "
    "When generating shell, default to bash and quote variables defensively. "
    "When generating IaC, default to AWS CloudFormation YAML, then Terraform, then CDK. "
    "When asked to explain a concept, lead with a one-sentence definition before details. "
    "When asked to compare two things, use a table when there are 3+ axes of comparison. "
    "When asked for a recommendation, provide two options with tradeoffs unless a single best answer exists. "
    "When the user provides a code snippet, identify language and apparent intent before suggesting changes. "
    "When the user reports a bug, ask for the exact error message and runtime version before guessing. "
    "When the user asks about an AWS service, mention the relevant service quotas page if scaling is implied. "
    "When the user asks for a review, prioritize correctness, then security, then performance, then style. "
    "When the user asks for a tutorial, structure the answer as: prerequisites, steps, verification, cleanup. "
) * 8

STREAMING_RATIO = 0.5
CACHING_RATIO = 0.4
ERROR_RATIO = 0.05

# (start_hour_utc, end_hour_utc, calls_per_minute, label)
SCHEDULE = [
    (0,  6,   2,  "light-overnight"),
    (6,  8,   6,  "morning-ramp"),
    (8,  9,  60,  "morning-burst"),
    (9, 12,  10,  "morning-steady"),
    (12, 13, 60,  "lunch-burst"),
    (13, 17, 10,  "afternoon-steady"),
    (17, 18, 60,  "evening-burst"),
    (18, 22,  8,  "evening-winddown"),
    (22, 24,  2,  "night"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("genmgr")

# max_attempts=1: surface real throttles instead of letting the SDK retry them away.
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=REGION,
    config=Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=120),
)

stop_event = asyncio.Event()
stats = {"ok": 0, "stream_ok": 0, "err4xx": 0, "err5xx": 0, "throttle": 0,
         "cache_used": 0, "by_model": {}, "by_phase": {}}


def pick_model():
    return random.choices(MODELS, weights=[m[1] for m in MODELS], k=1)[0]

def pick_team_app():
    team = random.choice(list(TEAMS_APPS.keys()))
    app = random.choice(TEAMS_APPS[team])
    env = "prod" if random.random() < 0.8 else "staging"
    return team, app, env

def pick_max_tokens():
    return random.choice([50, 100, 200, 500, 1500])

def current_phase():
    hour = datetime.utcnow().hour
    for start, end, cpm, label in SCHEDULE:
        if start <= hour < end:
            return cpm, label
    return 2, "fallback"


async def invoke_one():
    model_id, _, supports_caching, label = pick_model()
    team, app, env = pick_team_app()
    streaming = random.random() < STREAMING_RATIO
    use_cache = supports_caching and random.random() < CACHING_RATIO
    inject_error = random.random() < ERROR_RATIO
    max_tokens = pick_max_tokens()
    prompt = random.choice(PROMPTS)

    request_metadata = {TENANT_METADATA_KEY: team, "app": app, "env": env}

    if inject_error:
        if random.random() < 0.5:
            model_id = "anthropic.this-model-does-not-exist-v99:0"
        else:
            max_tokens = -1

    messages = [{"role": "user", "content": [{"text": prompt}]}]
    kwargs = dict(
        modelId=model_id,
        messages=messages,
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.7},
        requestMetadata=request_metadata,
    )
    if use_cache:
        kwargs["system"] = [
            {"text": LONG_SYSTEM_PROMPT},
            {"cachePoint": {"type": "default"}},
        ]

    try:
        if streaming:
            resp = await asyncio.to_thread(bedrock.converse_stream, **kwargs)
            await asyncio.to_thread(lambda: [_ for _ in resp["stream"]])
            stats["stream_ok"] += 1
        else:
            await asyncio.to_thread(bedrock.converse, **kwargs)
        stats["ok"] += 1
        stats["by_model"][label] = stats["by_model"].get(label, 0) + 1
        if use_cache:
            stats["cache_used"] += 1
    except ParamValidationError:
        stats["err4xx"] += 1
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ThrottlingException", "TooManyRequestsException"):
            stats["throttle"] += 1
        elif code in ("ValidationException", "AccessDeniedException",
                      "ResourceNotFoundException", "ModelNotReadyException"):
            stats["err4xx"] += 1
        else:
            stats["err5xx"] += 1
    except Exception as e:
        log.warning(f"unexpected: {type(e).__name__}: {e}")
        stats["err5xx"] += 1


async def progress_logger():
    snap = {k: stats[k] for k in ["ok", "stream_ok", "err4xx", "err5xx",
                                  "throttle", "cache_used"]}
    while not stop_event.is_set():
        await asyncio.sleep(60)
        delta = {k: stats[k] - snap[k] for k in snap}
        cpm, phase = current_phase()
        log.info(
            f"phase={phase} target_cpm={cpm} | last_60s {delta} | "
            f"totals ok={stats['ok']} stream={stats['stream_ok']} "
            f"4xx={stats['err4xx']} 5xx={stats['err5xx']} "
            f"throttle={stats['throttle']} cache_used={stats['cache_used']}"
        )
        snap = {k: stats[k] for k in snap}


async def main():
    log.info(f"starting generator | region={REGION} | "
             f"models={len(MODELS)} | teams={len(TEAMS_APPS)}")
    asyncio.create_task(progress_logger())
    while not stop_event.is_set():
        cpm, _ = current_phase()
        interval = 60.0 / cpm
        asyncio.create_task(invoke_one())
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def _shutdown(*_):
    log.info("shutdown signal received")
    stop_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    try:
        asyncio.run(main())
    finally:
        log.info(f"FINAL stats: {stats}")
