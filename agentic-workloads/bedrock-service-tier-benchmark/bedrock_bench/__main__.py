"""Command-line entry point: ``bedrock-bench`` (or ``python -m bedrock_bench``)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import Api, BenchmarkConfig, CacheMode, Endpoint, PromptSize, Scope, Tier

#: Named presets (see docs/DESIGN.md section 6). Explicit flags override them.
PROFILES: dict[str, dict[str, str | int]] = {
    "quick": {
        "apis": "converse_stream,chat_completions",
        "endpoints": "runtime",
        "prompt_sizes": "small",
        "cache_modes": "cold",
        "tiers": "default,flex",
        "n": 5,
        "interval": 5,
    },
    "standard": {
        "apis": "converse_stream,invoke_stream,chat_completions,responses,messages",
        "endpoints": "runtime,mantle",
        "prompt_sizes": "small,medium",
        "cache_modes": "cold,warm_implicit",
        "tiers": "default,flex,priority",
        "n": 30,
        "interval": 60,
    },
    "full": {
        "apis": ",".join(a.value for a in Api),
        "endpoints": "runtime,mantle",
        "prompt_sizes": "small,medium,large",
        "cache_modes": "cold,warm_implicit,warm_explicit",
        "tiers": "default,flex,priority",
        "n": 30,
        "interval": 60,
    },
}


def _csv(v: str | None) -> tuple[str, ...] | None:
    return tuple(x.strip() for x in v.split(",") if x.strip()) if v else None


def _choices(enum_cls) -> str:
    return ", ".join(e.value for e in enum_cls)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bedrock-bench",
        description="Benchmark Amazon Bedrock service tiers across endpoints, APIs, scopes, "
        "prompt sizes and cache modes.",
    )
    p.add_argument(
        "--preset",
        dest="run_profile",
        default="quick",
        choices=sorted(PROFILES),
        help="Preset matrix (default: quick).",
    )
    p.add_argument(
        "--aws-profile",
        "--profile",
        dest="aws_profile",
        default=os.environ.get("BEDROCK_BENCH_PROFILE"),
        help="AWS named profile (default: $BEDROCK_BENCH_PROFILE or the boto3 chain).",
    )
    p.add_argument("--regions", default="us-east-1", help="Comma-separated source regions.")
    p.add_argument("--keys", help="Comma-separated model keys (see --list-models).")
    p.add_argument("--families", help="Comma-separated family substrings.")
    p.add_argument("--endpoints", help=f"Subset of: {_choices(Endpoint)}.")
    p.add_argument("--apis", help=f"Subset of: {_choices(Api)}.")
    p.add_argument("--scopes", help=f"Subset of: {_choices(Scope)}.")
    p.add_argument("--prompt-sizes", help=f"Subset of: {_choices(PromptSize)}.")
    p.add_argument("--cache-modes", help=f"Subset of: {_choices(CacheMode)}.")
    p.add_argument("--tiers", help=f"Subset of: {_choices(Tier)} (default is always added).")
    p.add_argument("-n", "--n-requests", type=int, help="Measured samples per cell.")
    p.add_argument("--interval", type=float, help="Seconds between request starts per domain.")
    p.add_argument("--warmup", type=int, default=1, help="Discarded requests per cell.")
    p.add_argument("--max-output-tokens", type=int, help="Override the per-size output cap.")
    p.add_argument(
        "--reasoning-effort",
        default="none",
        help="Reasoning effort for reasoning models ('none', 'low', ...; "
        "'model-default' sends nothing).",
    )
    p.add_argument("--timeout", type=float, default=180.0, help="Per-request timeout (s).")
    p.add_argument("--flex-timeout", type=float, default=600.0, help="Flex-tier timeout (s).")
    p.add_argument("--seed", type=int, default=20261008, help="RNG seed.")
    p.add_argument("--registry", type=Path, help="Path to a models.json (schema v2).")
    p.add_argument("--output-dir", default="results", help="Base directory for outputs.")
    p.add_argument("--public", action="store_true", help="Mask account metadata in reports.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the matrix and estimates, then exit (no AWS calls).",
    )
    p.add_argument(
        "--preflight-only",
        action="store_true",
        help="Probe every cell once and print the go/no-go list.",
    )
    p.add_argument("--skip-preflight", action="store_true", help="Skip the preflight probe.")
    p.add_argument("--list-models", action="store_true", help="List registry models and exit.")
    p.add_argument(
        "--render",
        type=Path,
        metavar="SUMMARY_JSON",
        help="Re-render report.html from an existing summary.json and exit.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def build_config(a: argparse.Namespace) -> BenchmarkConfig:
    preset = PROFILES[a.run_profile]
    tiers = list(_csv(a.tiers) or _csv(str(preset["tiers"])) or ())
    if "default" not in tiers:
        tiers.insert(0, "default")
    return BenchmarkConfig(
        profile=a.aws_profile,
        regions=_csv(a.regions) or ("us-east-1",),
        n_requests=a.n_requests or int(preset["n"]),
        interval_seconds=a.interval if a.interval is not None else float(preset["interval"]),
        timeout_seconds=a.timeout,
        flex_timeout_seconds=a.flex_timeout,
        warmup_requests=a.warmup,
        seed=a.seed,
        output_dir=a.output_dir,
        redact=a.public,
        families=_csv(a.families),
        endpoints=_csv(a.endpoints) or _csv(str(preset["endpoints"])),  # type: ignore[arg-type]
        apis=_csv(a.apis) or _csv(str(preset["apis"])),  # type: ignore[arg-type]
        scopes=_csv(a.scopes) or tuple(s.value for s in Scope),  # type: ignore[arg-type]
        prompt_sizes=_csv(a.prompt_sizes) or _csv(str(preset["prompt_sizes"])),  # type: ignore[arg-type]
        cache_modes=_csv(a.cache_modes) or _csv(str(preset["cache_modes"])),  # type: ignore[arg-type]
        tiers=tuple(tiers),  # type: ignore[arg-type]
        max_output_tokens=a.max_output_tokens,
    )


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if a.render:
        from .html_report import write_html

        print(write_html(a.render))
        return 0

    from .benchmark import Benchmark
    from .registry import select

    if a.list_models:
        for s in select(_csv(a.families), _csv(a.keys), a.registry):
            tiers = sorted({t.value for o in s.offerings for t in o.tiers})
            apis = sorted({f"{o.endpoint.value}:{o.api.value}" for o in s.offerings})
            print(f"{s.key:40s} {s.display_name:30s} tiers={','.join(tiers)} apis={len(apis)}")
        return 0

    try:
        config = build_config(a)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    effort = None if a.reasoning_effort == "model-default" else a.reasoning_effort
    bench = Benchmark(config, keys=_csv(a.keys), registry_path=a.registry, reasoning_effort=effort)
    est = bench.estimate()
    print("Benchmark matrix:")
    print(json.dumps(est, indent=2))
    if a.verbose or a.dry_run:
        for c in bench.cells:
            print("  -", c.label)
    if not bench.cells:
        print("No cells match the filters and registry.", file=sys.stderr)
        return 1
    if a.dry_run:
        return 0
    if a.preflight_only:
        kept, report = bench.preflight()
        for r in report:
            status = "OK  " if r["ok"] else "DROP"
            detail = r["served_tier"] if r["ok"] else f"{r['error_kind']}: {r['error']}"
            print(f"{status} {r['cell']}  {detail}")
        print(f"{len(kept)}/{len(bench.cells)} cells usable.")
        return 0
    summaries = bench.run(skip_preflight=a.skip_preflight)
    print(f"\nDone: {sum(s.succeeded for s in summaries)} samples across {len(summaries)} cells.")
    print(f"Reports in {bench.out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
