# Benchmarks

Benchmarks validate platform behavior, network transport, and distributed
training performance. They are kept outside [examples/](../examples/README.md)
because they are measurement workloads rather than beginner workflow examples.

Each benchmark README includes a complete run sequence from the repository root,
including any required GPU or EFA prewarm step and node cleanup command.

| Benchmark | Purpose | Output |
| --- | --- | --- |
| [g6e-efa-nccl](g6e-efa-nccl/README.md) | 2-node G6e EFA NCCL all-reduce benchmark. | [Bandwidth plot](g6e-efa-nccl/artifacts/nccl-efa-2node-bandwidth.svg). |
| [g6e-efa-ddp](g6e-efa-ddp/README.md) | 2-node G6e PyTorch DDP training benchmark comparing EFA against NCCL socket networking. | [Training-time plot](g6e-efa-ddp/artifacts/training-time.svg). |
