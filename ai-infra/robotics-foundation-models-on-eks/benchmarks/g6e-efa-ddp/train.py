#!/usr/bin/env python3
import argparse
import json
import os
import statistics
import time

import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP


class CommunicationHeavyModel(torch.nn.Module):
    def __init__(self, param_mib: int) -> None:
        super().__init__()
        numel = (param_mib * 1024 * 1024) // 4
        self.weight = torch.nn.Parameter(torch.ones(numel, device="cuda"))

    def forward(self, scale: torch.Tensor) -> torch.Tensor:
        return (self.weight * scale).sum()


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def run_step(model: DDP, optimizer: torch.optim.Optimizer, scale: torch.Tensor) -> None:
    optimizer.zero_grad(set_to_none=True)
    loss = model(scale)
    loss.backward()
    optimizer.step()
    torch.cuda.synchronize()


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic 2-node DDP training benchmark.")
    parser.add_argument("--mode", required=True, choices=["efa", "socket"])
    parser.add_argument("--param-mib", type=int, default=256)
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--bucket-cap-mb", type=int, default=64)
    args = parser.parse_args()

    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend="nccl")

    rank = dist.get_rank()
    world_size = dist.get_world_size()
    model = CommunicationHeavyModel(args.param_mib)
    ddp_model = DDP(
        model,
        device_ids=[local_rank],
        bucket_cap_mb=args.bucket_cap_mb,
        gradient_as_bucket_view=True,
    )
    optimizer = torch.optim.SGD(ddp_model.parameters(), lr=0.0)
    scale = torch.tensor(0.000001, device="cuda")

    for _ in range(args.warmup_steps):
        run_step(ddp_model, optimizer, scale)

    dist.barrier()
    torch.cuda.synchronize()
    start = time.perf_counter()
    step_seconds: list[float] = []
    for _ in range(args.steps):
        step_start = time.perf_counter()
        run_step(ddp_model, optimizer, scale)
        step_seconds.append(time.perf_counter() - step_start)
    dist.barrier()
    total_seconds = time.perf_counter() - start

    local_summary = torch.tensor(
        [total_seconds, statistics.mean(step_seconds), percentile(step_seconds, 50), percentile(step_seconds, 95)],
        device="cuda",
    )
    gathered = [torch.zeros_like(local_summary) for _ in range(world_size)]
    dist.all_gather(gathered, local_summary)

    if rank == 0:
        rank_summaries = [item.cpu().tolist() for item in gathered]
        slowest_total = max(item[0] for item in rank_summaries)
        avg_step = max(item[1] for item in rank_summaries)
        payload_mib = args.param_mib
        result = {
            "mode": args.mode,
            "world_size": world_size,
            "gpus_per_node": 1,
            "param_mib": args.param_mib,
            "warmup_steps": args.warmup_steps,
            "steps": args.steps,
            "total_seconds": slowest_total,
            "avg_step_seconds": avg_step,
            "p50_step_seconds": max(item[2] for item in rank_summaries),
            "p95_step_seconds": max(item[3] for item in rank_summaries),
            "gradient_payload_mib_per_rank": payload_mib,
            "gradient_payload_mib_per_step": payload_mib * world_size,
            "gradient_payload_gib_per_second": (payload_mib * world_size / 1024.0) / avg_step,
        }
        print("RESULT_JSON: " + json.dumps(result, sort_keys=True), flush=True)

    dist.destroy_process_group()


if __name__ == "__main__":
    main()
