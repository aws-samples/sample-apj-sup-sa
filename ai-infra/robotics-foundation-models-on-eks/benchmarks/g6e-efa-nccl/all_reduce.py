import argparse
import csv
import json
import os
import socket
import time
from pathlib import Path

import torch
import torch.distributed as dist


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["efa", "socket"])
    parser.add_argument("--min-mib", type=int, default=8)
    parser.add_argument("--max-mib", type=int, default=64)
    parser.add_argument("--factor", type=int, default=2)
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-csv", default="")
    args = parser.parse_args()
    if args.min_mib <= 0:
        parser.error("--min-mib must be positive")
    if args.max_mib < args.min_mib:
        parser.error("--max-mib must be greater than or equal to --min-mib")
    if args.factor < 2:
        parser.error("--factor must be at least 2")
    if args.warmup_steps < 0:
        parser.error("--warmup-steps must be non-negative")
    if args.steps <= 0:
        parser.error("--steps must be positive")
    return args


def message_sizes(min_mib, max_mib, factor):
    size = min_mib
    while size <= max_mib:
        yield size
        size *= factor


def main():
    args = parse_args()
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    rank = int(os.environ["RANK"])
    world_size = int(os.environ["WORLD_SIZE"])

    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend="nccl")

    rows = []
    for mib in message_sizes(args.min_mib, args.max_mib, args.factor):
        element_count = mib * 1024 * 1024 // torch.empty((), dtype=torch.float32).element_size()
        tensor = torch.ones(element_count, device="cuda", dtype=torch.float32)

        for _ in range(args.warmup_steps):
            dist.all_reduce(tensor)
        torch.cuda.synchronize()
        dist.barrier()

        start = time.perf_counter()
        for _ in range(args.steps):
            dist.all_reduce(tensor)
        torch.cuda.synchronize()
        dist.barrier()
        elapsed = time.perf_counter() - start

        avg_seconds = elapsed / args.steps
        payload_gib = (mib * world_size) / 1024
        algbw_gib_s = payload_gib / avg_seconds
        busbw_gib_s = algbw_gib_s * (2 * (world_size - 1) / world_size)
        rows.append(
            {
                "message_mib_per_rank": mib,
                "world_size": world_size,
                "avg_seconds": avg_seconds,
                "algbw_gib_per_second": algbw_gib_s,
                "busbw_gib_per_second": busbw_gib_s,
            }
        )

    if rank == 0:
        result = {
            "mode": args.mode,
            "hostname": socket.gethostname(),
            "world_size": world_size,
            "warmup_steps": args.warmup_steps,
            "steps": args.steps,
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "nccl": torch.cuda.nccl.version(),
            "rows": rows,
        }
        print("RESULT_JSON: " + json.dumps(result, sort_keys=True), flush=True)

        if args.output_json:
            Path(args.output_json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        if args.output_csv:
            with Path(args.output_csv).open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

    dist.destroy_process_group()


if __name__ == "__main__":
    main()
