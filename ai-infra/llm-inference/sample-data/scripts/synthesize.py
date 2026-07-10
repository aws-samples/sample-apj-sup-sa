#!/usr/bin/env python3
"""Generate the travel-booking sample data via Amazon Bedrock.

The dataset shipped under ``sample-data/travel/`` consists of synthetic
travel booking confirmation emails (flights, trains, buses, hotels,
packages). Each row is a stand-alone email body. The downstream LLM under
test is asked to extract the booking details into a structured JSON record.

Output layout (default)::

    sample-data/
    └── travel/
        ├── 01-domestic-flight.jsonl
        ├── 02-international-flight.jsonl
        ├── ...
        └── 10-budget-airline.jsonl

Each file has up to ``--per-seed`` JSONL lines, one per record::

    {"text": "Subject: Your booking is confirmed — PNR ABC123\\n\\n...",
     "meta": {"seed": "domestic-flight", "domain": "travel",
              "temperature": 0.8, "top_p": 0.95, "batch_idx": 234}}

Properties
----------
* **Async + bounded concurrency** (``--concurrency``).
* **Throttle-safe** — tenacity exponential backoff on
  ``ThrottlingException`` / ``TooManyRequestsException``.
* **Resume-able** — per-seed progress in
  ``<output-dir>/travel/.journal.json``. Re-running picks up from the
  last completed batch.
* **Diversity sampling** — temperature + top_p rotate per batch.
* **Dry-run + smoke-test flags** for cheap verification.

Usage
-----
::

    # Smoke test (cheap): 2 seeds × 50 records each
    python sample-data/scripts/synthesize.py --smoke

    # Full run (10 seeds × 10K records = 100K rows)
    python sample-data/scripts/synthesize.py --per-seed 10000

    # Resume after interruption (idempotent — same args)
    python sample-data/scripts/synthesize.py --per-seed 10000

    # Cost-only dry run
    python sample-data/scripts/synthesize.py --per-seed 10000 --dry-run

The Bedrock model id is pinned in ``DEFAULT_MODEL_ID`` below; only that
single model is used to generate the sample text.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError
from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

LOG = logging.getLogger("synthesize")

DEFAULT_MODEL_ID = "us.amazon.nova-2-lite-v1:0"
DEFAULT_REGION = "us-east-1"
DEFAULT_BATCH_SIZE = 10
DEFAULT_CONCURRENCY = 20

# The dataset ships exactly one domain. Kept as a constant (rather than a CLI
# flag) so the shape of the output records remains stable.
DOMAIN = "travel"

# Diversity knobs cycled per batch.
TEMPERATURES = [0.6, 0.7, 0.8, 0.9, 1.0]
TOP_PS = [0.85, 0.9, 0.95, 0.99]


# ---------------------------------------------------------------------------
# Seeds (one per file). Each seed is a (name, style) pair Nova uses to
# generate distinct sub-categories within the travel domain. We keep these
# inline so the script is single-file and self-contained.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Seed:
    name: str
    style: str  # short prose telling Nova what kind of variants to produce


TRAVEL_SEEDS: list[Seed] = [
    Seed("domestic-flight", "Confirmation emails for round-trip domestic flights within one "
         "country (US, EU, India, etc.). Vary airline, fare class, baggage, traveler count, "
         "and price."),
    Seed("international-flight", "Confirmation emails for international flights with at least "
         "one connection. Vary origin/destination, airlines, layover duration, and currencies."),
    Seed("train-booking", "Confirmation emails for train tickets (Eurostar, Amtrak, Shinkansen, "
         "Indian Railways, regional EU). Vary class, seat reservation, route, and meal options."),
    Seed("bus-booking", "Confirmation emails for intercity bus tickets (FlixBus, Greyhound, "
         "RedBus, etc.). Vary route, seat type, on-board services, and price."),
    Seed("hotel-only", "Hotel-only booking confirmations (chain hotels, boutique hotels, "
         "hostels). Vary city, check-in/out, room type, breakfast, and cancellation policy."),
    Seed("car-rental", "Car-rental confirmations (Hertz, Avis, Sixt, Enterprise). Vary pickup/"
         "dropoff city, vehicle class, insurance options, mileage policy, and surcharges."),
    Seed("flight-hotel-package", "Bundled flight + hotel package bookings. Vary carrier and "
         "hotel chain, total nights, traveler count, and package savings."),
    Seed("multi-city", "Multi-city itineraries (3+ legs across different transport modes). Vary "
         "number of legs, transport mix (flight/train/bus), and transit cities."),
    Seed("cruise", "Cruise booking confirmations (Caribbean, Mediterranean, Alaska, Asia). "
         "Vary cabin class, ports of call, included excursions, and gratuity policy."),
    Seed("budget-airline", "Low-cost-carrier confirmations (Ryanair, Spirit, AirAsia, IndiGo). "
         "Vary base fare, optional add-ons (seat / bag / meal), and total price."),
]


# ---------------------------------------------------------------------------
# Synthesis prompt
# ---------------------------------------------------------------------------
TRAVEL_META_PROMPT = """\
You are a travel-industry email-template author. Produce {n} DIVERSE synthetic \
booking confirmation emails for the sub-domain described below.

Sub-domain:
{style}

Requirements for each email:
- Realistic confirmation email format including a subject line, sender (e.g. \
  "no-reply@<airline>.com"), body, and a closing.
- Body must include all the operational details of the booking: traveler name(s), \
  booking reference / PNR, dates and times, origin/destination(s), service class, \
  fare breakdown, taxes, total price (with currency), payment method last 4 digits, \
  cancellation/change policy summary.
- Vary airlines/operators, currencies (USD, EUR, GBP, INR, JPY, etc.), traveler counts, \
  and number of legs/segments.
- 150-400 words per email.
- No real customer or staff names. Use invented first names + last names. Never use a real \
  credit card number — last 4 digits only, e.g. "**** 4321".
- The seed example below is one illustrative style; your {n} outputs must be clearly \
  different from each other and from the seed.

Seed style note (do NOT copy verbatim):
---
{seed_style}
---

Return ONLY a JSON array of exactly {n} strings, nothing else. No prose, no markdown fences. \
Example of valid output:
[
  "Subject: Your booking is confirmed — PNR ABC123\\n\\nFrom: ...\\n\\nDear ...",
  "Subject: ...",
  ...
]
"""


# ---------------------------------------------------------------------------
# Journal — per-seed completed-batch tracking
# ---------------------------------------------------------------------------
@dataclass
class SeedProgress:
    seed_name: str
    output_path: str
    domain: str
    completed_records: int = 0
    target_records: int = 0
    batches_completed: int = 0
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Journal:
    """Disk-backed per-seed progress at ``<output_dir>/.journal.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, SeedProgress] = self._load()

    def _load(self) -> dict[str, SeedProgress]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("Corrupt journal at %s (%s) — starting fresh.", self.path, exc)
            return {}
        return {k: SeedProgress(**v) for k, v in raw.items()}

    def get(self, seed_name: str) -> SeedProgress | None:
        return self._data.get(seed_name)

    def update(self, progress: SeedProgress) -> None:
        progress.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._data[progress.seed_name] = progress
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {k: v.to_dict() for k, v in self._data.items()}, indent=2,
        ))
        tmp.replace(self.path)


# ---------------------------------------------------------------------------
# Nova I/O
# ---------------------------------------------------------------------------
def _build_bedrock_config(read_timeout: int = 120) -> Config:
    return Config(
        retries={"max_attempts": 1, "mode": "standard"},
        read_timeout=read_timeout,
        connect_timeout=10,
    )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def _extract_json_array(text: str) -> list[str]:
    cleaned = _strip_code_fences(text)
    first = cleaned.find("[")
    last = cleaned.rfind("]")
    if first == -1 or last == -1 or last <= first:
        raise ValueError(f"no JSON array in Nova response (first 200 chars): {text[:200]!r}")
    payload = cleaned[first : last + 1]
    parsed = json.loads(payload)
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise ValueError(f"Nova response not list[str]: {type(parsed).__name__}")
    return [s.strip() for s in parsed if s.strip()]


class _ThrottleError(Exception):
    """Sentinel wrapping ThrottlingException so tenacity matches it cleanly."""


async def _invoke_nova_batch_async(
    *,
    client,
    model_id: str,
    seed: Seed,
    batch_size: int,
    temperature: float,
    top_p: float,
) -> list[str]:
    prompt = TRAVEL_META_PROMPT.format(
        n=batch_size, style=seed.style, seed_style=seed.style,
    )

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(6),
        wait=wait_exponential_jitter(initial=2.0, max=60.0, exp_base=2.0),
        retry=retry_if_exception_type((_ThrottleError, ValueError)),
        before_sleep=before_sleep_log(LOG, logging.WARNING),
        reraise=True,
    ):
        with attempt:
            try:
                response = await client.converse(
                    modelId=model_id,
                    messages=[{"role": "user", "content": [{"text": prompt}]}],
                    inferenceConfig={
                        "maxTokens": 4096,
                        "temperature": temperature,
                        "topP": top_p,
                    },
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in (
                    "ThrottlingException",
                    "TooManyRequestsException",
                    "ServiceQuotaExceededException",
                    "ModelNotReadyException",
                ):
                    raise _ThrottleError(f"{code}: {exc}") from exc
                raise

            text = response["output"]["message"]["content"][0]["text"]
            variants = _extract_json_array(text)
            if len(variants) < max(1, batch_size // 2):
                raise ValueError(
                    f"Nova returned {len(variants)}/{batch_size} variants (too few)"
                )
            return variants[:batch_size]

    raise RetryError(f"unreachable after 6 attempts for {seed.name}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Per-seed generation loop
# ---------------------------------------------------------------------------
async def generate_seed(
    *,
    client,
    seed: Seed,
    target_records: int,
    batch_size: int,
    output_path: Path,
    journal: Journal,
    model_id: str,
    concurrency: int,
) -> int:
    existing = 0
    if output_path.exists():
        with output_path.open() as fh:
            existing = sum(1 for _ in fh)
        LOG.info("[%s/%s] resuming with %d existing records",
                 DOMAIN, seed.name, existing)

    remaining = target_records - existing
    if remaining <= 0:
        LOG.info("[%s/%s] already complete (%d records)",
                 DOMAIN, seed.name, existing)
        return existing

    total_batches = (remaining + batch_size - 1) // batch_size
    LOG.info(
        "[%s/%s] %d remaining across ~%d batches (concurrency=%d)",
        DOMAIN, seed.name, remaining, total_batches, concurrency,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    written = existing

    progress = SeedProgress(
        seed_name=seed.name,
        output_path=str(output_path),
        domain=DOMAIN,
        completed_records=existing,
        target_records=target_records,
        batches_completed=existing // batch_size,
    )
    journal.update(progress)

    async def _one_batch(batch_idx: int, this_size: int) -> None:
        nonlocal written
        temp = TEMPERATURES[batch_idx % len(TEMPERATURES)]
        top_p = TOP_PS[batch_idx % len(TOP_PS)]
        async with sem:
            try:
                variants = await _invoke_nova_batch_async(
                    client=client, model_id=model_id,
                    seed=seed, batch_size=this_size,
                    temperature=temp, top_p=top_p,
                )
            except Exception as exc:  # noqa: BLE001
                LOG.error("[%s/%s] batch %d failed permanently: %s",
                          DOMAIN, seed.name, batch_idx, exc)
                return

            async with lock:
                with output_path.open("a") as fh:
                    for note in variants:
                        fh.write(json.dumps({
                            "text": note,
                            "meta": {
                                "seed": seed.name,
                                "domain": DOMAIN,
                                "temperature": temp,
                                "top_p": top_p,
                                "batch_idx": batch_idx,
                            },
                        }, ensure_ascii=False) + "\n")
                        written += 1
                progress.completed_records = written
                progress.batches_completed = batch_idx + 1
                journal.update(progress)

            if total_batches and batch_idx % max(1, total_batches // 20) == 0:
                LOG.info(
                    "[%s/%s] progress: %d/%d (%.1f%%)",
                    DOMAIN, seed.name, written, target_records,
                    100.0 * written / target_records,
                )

    tasks: list[asyncio.Task] = []
    idx = existing // batch_size
    todo = remaining
    while todo > 0:
        n = min(batch_size, todo)
        tasks.append(asyncio.create_task(_one_batch(idx, n)))
        idx += 1
        todo -= n

    await asyncio.gather(*tasks)
    LOG.info("[%s/%s] done: %d records in %s",
             DOMAIN, seed.name, written, output_path)
    return written


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
async def main_async(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve() / DOMAIN
    output_dir.mkdir(parents=True, exist_ok=True)

    seeds = TRAVEL_SEEDS
    if args.seeds is not None:
        seeds = seeds[args.skip_seeds : args.skip_seeds + args.seeds]
    else:
        seeds = seeds[args.skip_seeds:]
    start_idx = args.skip_seeds

    if args.dry_run:
        total = len(seeds) * args.per_seed
        calls = ((args.per_seed + args.batch_size - 1) // args.batch_size) * len(seeds)
        # Order-of-magnitude cost estimate using 2026 Nova Lite list price:
        # input ~ $0.06 / 1M tok, output ~ $0.24 / 1M tok.
        in_tokens = calls * 700  # ~700 input tokens per Nova call (longer prompts here)
        out_tokens = total * 250  # ~250 output tokens per record (long-form text)
        cost = in_tokens / 1e6 * 0.06 + out_tokens / 1e6 * 0.24
        LOG.info("DRY RUN — would generate %d records across %d seeds (%s)",
                 total, len(seeds), DOMAIN)
        LOG.info("  output dir:        %s", output_dir)
        LOG.info("  ~Nova calls:       %s", f"{calls:,}")
        LOG.info("  ~est. cost (USD):  %.2f", cost)
        return 0

    journal = Journal(output_dir / ".journal.json")

    session = aioboto3.Session()
    async with session.client(
        "bedrock-runtime",
        region_name=args.region,
        config=_build_bedrock_config(),
    ) as client:
        for i, seed in enumerate(seeds):
            filename = f"{start_idx + i + 1:02d}-{seed.name}.jsonl"
            output_path = output_dir / filename
            t0 = datetime.now()
            written = await generate_seed(
                client=client, seed=seed,
                target_records=args.per_seed, batch_size=args.batch_size,
                output_path=output_path, journal=journal,
                model_id=args.model_id, concurrency=args.concurrency,
            )
            elapsed = (datetime.now() - t0).total_seconds()
            LOG.info("[%s/%s] FINAL: wrote %d records in %.1fs (%.1f records/s)",
                     DOMAIN, seed.name, written, elapsed,
                     written / max(elapsed, 0.001))

    LOG.info("All seeds complete for domain=%s. Files in %s", DOMAIN, output_dir)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="synthesize",
        description="Generate the travel-booking sample-data via Amazon Bedrock.",
    )
    p.add_argument("--per-seed", type=int, default=10_000,
                   help="records per seed file (default 10000 = 10×10K = 100K total)")
    p.add_argument("--seeds", type=int, default=None,
                   help="limit to first N seeds (default all 10)")
    p.add_argument("--skip-seeds", type=int, default=0,
                   help="skip the first N seeds (useful for resume / split)")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--model-id", default=DEFAULT_MODEL_ID,
                   help="Bedrock model id; the default is the only model supported for this code sample")
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--output-dir",
                   default=str(Path(__file__).resolve().parents[1]),
                   help="root of the sample-data tree (default: sample-data/)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--smoke", action="store_true",
                   help="shortcut: 2 seeds × 50 records, concurrency=5")
    p.add_argument("-v", "--verbose", action="count", default=0)
    args = p.parse_args()
    if args.smoke:
        args.seeds = 2
        args.per_seed = 50
        args.concurrency = 5
    return args


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.WARNING - min(args.verbose + 1, 2) * 10,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
