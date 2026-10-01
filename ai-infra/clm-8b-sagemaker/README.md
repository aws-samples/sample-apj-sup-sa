# CLM-v0.1-8B on Amazon SageMaker AI

Deploy [`Contrastive-LM/CLM-v0.1-8B`](https://huggingface.co/Contrastive-LM/CLM-v0.1-8B), a contrastive
"System One" scoring model, as a real-time Amazon SageMaker AI endpoint in **one container with no custom
image**, then test and benchmark it.

CLM does not generate text. It **scores** candidates against a state, which makes it useful inside agent
loops: pick the next tool, verify a step, rank best-of-N answers, route a ticket. It has two parts:

| Part | What it is | Where it runs |
|---|---|---|
| Encoder | frozen **Qwen3-8B**, 16.4 GB bf16, last-token pooled embeddings | vLLM 0.30 in pooling mode, on the GPU |
| Heads | two **75 MB** MLP projection heads (4096 → 1536 → 512) from the `contrastive-lm` package | PyTorch on the CPU, in the same container |

Locally the project runs these as two servers (`vllm serve` and `clm-serve`). On SageMaker they share one
container built from the **AWS vLLM Deep Learning Container**, because that image can load a `model.py`
from the model artifact and let it replace the default `/invocations` route:

```
InvokeEndpoint ──► /invocations  (code/model.py: CLM engine, heads on CPU)
                       │  POST 127.0.0.1:8080/v1/embeddings
                       ▼
                   vLLM 0.30  --runner pooling  Qwen3-8B bf16  (GPU)
```

`/ping` stays vLLM's own health check. Nothing is forked, patched or rebuilt: the handler imports the
upstream `clm` package and calls the engine the project ships.

## Contents

| File | What it does |
|---|---|
| [`01-generate-dataset.ipynb`](01-generate-dataset.ipynb) | Builds a small **labelled** benchmark set with Amazon Nova 2 Lite on Amazon Bedrock: 120 support tickets and 101 ranking items, 51 KB |
| [`02-deploy-and-benchmark.ipynb`](02-deploy-and-benchmark.ipynb) | Execution role, S3 artifact, endpoint, correctness checks, benchmark, CloudWatch metrics, cost, clean-up |
| [`code/model.py`](code/model.py) | The `/invocations` handler: the whole SageMaker-specific part, about 90 lines |
| `data/clm_bench.jsonl` | The generated dataset, committed so notebook 2 runs on its own |

Run notebook 2 on its own if you just want the endpoint. Notebook 1 is only needed to regenerate the data.

## Quick start

```bash
pip install boto3 huggingface_hub pandas numpy matplotlib jupyter
# then open 02-deploy-and-benchmark.ipynb and run it top to bottom
```

You need: SageMaker, S3, ECR, IAM and Bedrock access in a US Region; GPU endpoint quota for at least one of
the instance types below; and about 17 GB of local disk for the weights on the first run.

**Cost:** about **$1.13 to $1.52 per endpoint hour** plus S3 storage. A full run takes roughly 45 minutes,
most of it the first 16 GB artifact upload and a ~12 minute endpoint start. The clean-up section is
commented out by default: uncomment and run it, or the endpoint keeps billing.

## Request format

A SageMaker endpoint exposes only `/invocations`, so the body's `op` field selects the CLM call.

```python
# typed questions about a state
{"op": "systemone", "state": "my invoice was charged twice and nobody answers the phone!",
 "questions": {
   "urgency":    {"type": "noul",   "instructions": "Is this urgent?"},
   "department": {"type": "choice", "instructions": "Which team should handle this?",
                  "criteria": {"billing": "Charges, invoices, refunds", "technical": "Bugs and outages"}},
   "frustration":{"type": "score",  "instructions": "How frustrated is the customer?",
                  "criteria": ["Calm", "Frustrated", "Very angry"]}}}
# -> {"answers": {"department": {"choice": "billing", "probabilities": {...}}, ...}, "usage": {...}}

# rank free-form candidates
{"op": "rank", "question": "What causes tides on Earth?",
 "answers": ["The Moon's gravitational pull.", "Photosynthesis in plants.", "Because the Earth is round."]}
# -> {"ranked": [{"rank": 1, "candidate": "The Moon's gravitational pull.", "prob": 0.994}, ...], "usage": {...}}
```

Both accept an optional `model` (any extra head checkpoint you add, or `clm-raw` for the no-head ablation)
and `temperature`.

## Instance choice

Qwen3-8B in bf16 needs 24 GB of GPU memory. Quantizing is not an option, because the heads are trained on
exact bf16 Qwen3-8B embeddings. The endpoint therefore asks for five single-GPU types, **cheapest first**,
using SageMaker [capacity-aware instance pools](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-heterogeneous.html):

| Priority | Instance | GPU | GPU memory | us-west-2 $/h |
|---|---|---|---|---|
| 1 | `ml.g6.xlarge` | L4 | 24 GB | **1.127** |
| 2 | `ml.g6.2xlarge` | L4 | 24 GB | 1.222 |
| 3 | `ml.g5.xlarge` | A10G | 24 GB | 1.408 |
| 4 | `ml.g5.2xlarge` | A10G | 24 GB | 1.515 |
| 5 | `ml.g6e.xlarge` | L40S | 48 GB | 2.605 |

`ml.g4dn.xlarge` ($0.736) is cheaper but has 16 GB and no bf16 support, so it cannot run this model.
Bigger instances pay for memory and tensor parallelism an 8B encoder does not need.

Small GPU instances are genuinely scarce: while writing this sample, `ml.g6.xlarge` and `ml.g5.xlarge`
each failed with `InsufficientInstanceCapacity` in both us-west-2 and us-east-1 at different times. With
one instance type that is a failed deployment; with five pools SageMaker just takes the next one.

## Measured results

`ml.g5.xlarge` (A10G 24 GB) in us-east-1, the cheapest pool with capacity at the time, with the client on
EC2 in the same Region. vLLM 0.30.0, `max-model-len 2048`, one instance, no autoscaling. These are the
outputs committed in `02-deploy-and-benchmark.ipynb`.

CLM returns its scores in one response and generates no tokens, so **TTFT is time to first byte**; with a
~1 KB response it lands within 0.1 ms of end-to-end latency. `ticket` is one `systemone` call with 3 typed
questions; `rank` is one `rank` call over 8 candidates. Every request is a cache miss by construction.

| Workload | Concurrency | req/s | TTFT p50 | e2e p50 | e2e p99 | encoder tok/s |
|---|---|---|---|---|---|---|
| ticket | 1 | 10.6 | 97 ms | 97 ms | 104 ms | 1,060 |
| ticket | 4 | 33.9 | 108 ms | 108 ms | 156 ms | 3,381 |
| ticket | 16 | 51.6 | 291 ms | 291 ms | 414 ms | 5,298 |
| ticket | 32 | **56.5** | 531 ms | 531 ms | 744 ms | 5,952 |
| ticket | 64 | 56.0 | 1,061 ms | 1,061 ms | 1,615 ms | 5,873 |
| rank | 1 | 6.5 | 151 ms | 151 ms | 163 ms | 776 |
| rank | 4 | 19.4 | 198 ms | 198 ms | 261 ms | 2,298 |
| rank | 16 | 22.2 | 702 ms | 702 ms | 854 ms | 2,830 |
| rank | 32 | 23.9 | 1,262 ms | 1,262 ms | 1,748 ms | 3,136 |
| rank | 64 | **26.5** | 2,387 ms | 2,387 ms | 2,739 ms | 3,542 |

Throughput flattens past 16–32 concurrent requests, where the GPU saturates. Beyond the knee, extra
concurrency only buys queueing latency, so that is where to set an autoscaling target. Server-side
CloudWatch metrics over the whole benchmark window confirm the GPU is the limit:

| Metric | Value |
|---|---|
| `GPUUtilization` max | 100% |
| `GPUMemoryUtilization` max | 86% |
| `CPUUtilization` max | 96% of 400 (4 vCPU) |
| `MemoryUtilization` max | 27% |
| `OverheadLatency` average | 6.0 ms |
| `Invocation5XXErrors` | 0 |

At the best measured point, on one instance, that is about **$6.90 per million `systemone` calls** and
**$14.80 per million 8-candidate `rank` calls**.

### The CLM embedding cache earns its keep

CLM caches the embedding of every text it has seen, so an agent that reuses a fixed action set pays only
for the new state. Same request, sent cold and then warm:

| Candidates | Request size | Cold | Warm | Speed-up |
|---|---|---|---|---|
| 8 | 0.3 KB | 190 ms | 53 ms | 4x |
| 64 | 2.1 KB | 484 ms | 55 ms | 9x |
| 256 | 10 KB | 1,776 ms | 55 ms | 32x |
| 1024 | 40 KB | 6,937 ms | 62 ms | 111x |

Warm latency is flat at about 55 ms no matter how many candidates, because only the state is new. That is
the shape an agent loop wants: a fixed tool or action set costs almost nothing to re-score.

### Keep vLLM's default batch size

Raising `max_num_batched_tokens` above vLLM's 2048 default **hurts**. Three otherwise identical
`ml.g5.xlarge` endpoints, same client, same workload, 64 concurrent requests:

| `max_num_batched_tokens` | ticket req/s | rank req/s |
|---|---|---|
| 2048 (default) | **60.5** | **35.6** |
| 8192 | 58.1 | 25.4 |
| 16384 | 56.8 | 21.9 |

The KV cache on a 24 GB GPU holds about 24,000 tokens (vLLM logs
`GPU KV cache size: 24,672 tokens`), so bigger prefill batches make each step longer without running more
requests at once. `rank` loses the most because each of its requests carries 9 texts.

### Answer quality

Notebook 2 scores the endpoint against the Nova 2 Lite labels. This checks the *deployment*, since wrong
pooling, a wrong revision or broken heads would all land near chance. It is not a benchmark of CLM:

| Metric | Result | Baseline |
|---|---|---|
| rank top-1 (8 candidates) | 59.4% | 12.5% chance |
| urgent AUROC | 0.75 | 0.50 random |
| department accuracy (5-way) | 32.5% | 20% chance |

Zero-shot `noul` probabilities are not calibrated, so urgency is scored with AUROC rather than a 0.5
cut-off. The model card is explicit that CLM's strong agentic numbers come from **fine-tuned** heads;
those are another `*.pt` file you can drop into `code/` and select with `"model": "<file stem>"`.

Notebook 2 also asserts the model-card examples against an independent reference run of the same
checkpoint (Hugging Face Transformers, Qwen3-8B bf16 on CPU), which agrees with the endpoint to within
0.02: `billing` 0.988 vs 0.988, urgency 0.822 vs 0.839, the Moon 0.994 vs 0.993.

## Design notes

Things that are easy to get wrong here.

- **Pooling must match training.** `--runner pooling` on `Qwen3ForCausalLM` resolves to last-token pooling
  with L2 normalisation, which is what the heads were trained on. Serving the Qwen3-*Embedding* checkpoint
  instead, or another engine with different pooling, silently changes the embeddings the heads expect.
- **Heads on the CPU.** They are small MLPs, and the handler runs in vLLM's API-server process, not the
  engine process. Putting them on the GPU would add a second CUDA context and memory outside vLLM's budget.
  `CLM_DEVICE=cuda` is still available if you prefer.
- **Never block the event loop.** The engine call is synchronous and makes an HTTP call back into the same
  server, so the handler runs it in a thread pool (`CLM_WORKERS`, default 32).
- **Network isolation is on.** Weights, heads and the `clm` package all ship in the artifact, so the
  container needs no internet and never calls the Hugging Face Hub. That also means `requirements.txt`
  auto-install cannot reach PyPI, so bake anything extra into a derived image.
- **Pin everything.** The notebook pins the Qwen3-8B and CLM revisions, the `contrastive-lm` source commit
  and the DLC image tag, and the S3 prefix is derived from all three. Note that PyPI `contrastive-lm` 0.1.0
  is behind GitHub `main`, so the sample vendors `src/clm` from the pinned commit instead.
- **Uncompressed artifact.** `CompressionType: None` with an `S3Prefix` means no 16 GB tarball to extract
  at start-up.
- **CUDA 13 needs a newer host AMI.** The 0.30.0 images require
  `InferenceAmiVersion: al2023-ami-sagemaker-inference-gpu-4-1`; the default host AMI has an older driver.
- **Raise the start-up timeouts.** 16 GB of weights plus a 22 GB image do not fit the 8-minute default;
  the notebook uses 1200 s for both `ContainerStartupHealthCheckTimeoutInSeconds` and
  `ModelDataDownloadTimeoutInSeconds`.
- **Least-privilege role.** The notebook creates a role that can read only its own S3 prefix, pull only the
  vLLM DLC repository, and write only SageMaker logs and metrics.
- **The 60 s and 6 MB invocation limits apply.** Both are comfortable here: a 1024-candidate request is
  40 KB and answers in about 7 seconds cold.

## Production next steps

Notebook 2 ends with runnable snippets for these.

- **Autoscaling** on `SageMakerVariantInvocationsPerInstance`, with the target taken from the knee of the
  measured throughput curve. On a mixed-instance fleet, use a CloudWatch metric-math weighted utilisation
  metric instead, since pool members differ in throughput.
- **Scale to zero** for spiky or dev traffic, by deploying the same model as an inference component with
  `MinInstanceCount=0`.
- **Fine-tuned heads** served side by side on the same encoder: add more `*.pt` files under `code/`.
- **VPC** via `VpcConfig` plus an S3 gateway endpoint; network isolation already blocks egress.

## License

CLM-v0.1-8B weights and Qwen3-8B are both Apache 2.0. This sample is MIT-0; see [LICENSE](LICENSE).
