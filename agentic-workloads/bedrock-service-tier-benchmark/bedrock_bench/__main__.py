"""Command-line entry point: ``python -m bedrock_bench [options]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .benchmark import Benchmark
from .config import BenchmarkConfig, Tier, Transport
from .registry import families as all_families


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bedrock_bench",
        description="Benchmark Bedrock default/flex/priority service tiers (TTFT + total latency).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--profile",
        default=os.environ.get("BEDROCK_BENCH_PROFILE"),
        help="AWS named profile. Defaults to $BEDROCK_BENCH_PROFILE, else the "
        "standard boto3 credential chain ($AWS_PROFILE / default profile / role).",
    )
    p.add_argument(
        "--regions",
        default="us-west-2,us-east-1",
        help="Comma-separated region preference order.",
    )
    p.add_argument(
        "-n", "--n-requests", type=int, default=30, help="Samples per cell (>=30 advised)."
    )
    p.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Seconds between requests within one (transport,model) domain.",
    )
    p.add_argument("--max-tokens", type=int, default=200, help="Output token cap per request.")
    p.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout (s).")
    p.add_argument("--output-dir", default="results", help="Base directory for outputs.")
    p.add_argument(
        "--families",
        default=None,
        help=f"Comma-separated subset of families. Available: {', '.join(all_families())}",
    )
    p.add_argument(
        "--transports",
        default="invoke,mantle",
        help="Comma-separated subset of: invoke, mantle.",
    )
    p.add_argument(
        "--tiers",
        default="default,flex,priority",
        help="Comma-separated subset of: default, flex, priority. "
        "(default is always added as the baseline for any non-default tier.)",
    )
    p.add_argument(
        "--keys",
        default=None,
        help="Comma-separated subset of exact model keys (see --dry-run for the list).",
    )
    p.add_argument(
        "--preflight-only",
        action="store_true",
        help="Probe every cell once and print the go/no-go matrix, then exit.",
    )
    p.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip the preflight probe (not recommended).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the matrix + time estimate and exit without calling Bedrock.",
    )
    p.add_argument(
        "--public",
        action="store_true",
        help="Redact account-identifying metadata (account id, profile) from reports "
        "so they are safe to share externally.",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Debug logging.")
    return p.parse_args(argv)


def _build_config(args: argparse.Namespace) -> BenchmarkConfig:
    transports = tuple(Transport(t.strip()) for t in args.transports.split(",") if t.strip())
    tiers = tuple(Tier(t.strip()) for t in args.tiers.split(",") if t.strip())
    families = tuple(f.strip() for f in args.families.split(",")) if args.families else None
    return BenchmarkConfig(
        profile=args.profile,
        regions=tuple(r.strip() for r in args.regions.split(",") if r.strip()),
        n_requests=args.n_requests,
        interval_seconds=args.interval,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
        output_dir=args.output_dir,
        families=families,
        transports=transports,
        tiers=tiers,
        redact=args.public,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config = _build_config(args)
    keys = tuple(k.strip() for k in args.keys.split(",")) if args.keys else None
    bench = Benchmark(config, keys=keys, interval_override=args.interval)

    est = bench.estimate()
    print("Benchmark matrix:")
    print(f"  models={est['models']}  cells={est['cells']}  domains={est['domains']}")
    print(
        f"  n/cell={est['n_per_cell']}  interval={est['interval_s']}s  "
        f"total_requests={est['total_requests']}"
    )
    mins = est["est_seconds_if_fully_parallel"] / 60.0
    print(f"  est. wall-clock (parallel across models): ~{mins:.1f} min")
    print("  cells:")
    for c in bench.cells:
        print(f"    - {c.label}")

    if args.dry_run:
        return 0

    if args.preflight_only:
        asyncio.run(bench.run_preflight())
        return 0

    summaries = asyncio.run(bench.run(skip_preflight=args.skip_preflight))
    ran = sum(s.succeeded for s in summaries)
    print(f"\nDone. {ran} successful samples across {len(summaries)} cells.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
