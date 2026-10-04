# CLM-v0.1-8B on Amazon SageMaker AI

Deploy [`Contrastive-LM/CLM-v0.1-8B`](https://huggingface.co/Contrastive-LM/CLM-v0.1-8B), a contrastive
"System One" scoring model, as a real-time Amazon SageMaker AI endpoint in **one container with no custom
image**, then test and benchmark it.

CLM does not generate text. It **scores** candidates against a state, which makes it useful inside agent
loops: pick the next tool, verify a step, rank best-of-N answers, route a ticket. It has two parts:

| Part | What it is | Where it runs |
|---|---|---|
| Encoder | frozen **Qwen3-8B**, 16.4 GB bf16, last-token pooled embeddings | vLLM 0.30 in pooling mode, on the GPU |
| Heads | two MLP projection heads (4096 → 1536 → 1536 → 512 with LayerNorm, **75 MB** together) from the `contrastive-lm` package | PyTorch on the CPU, in the same container |

Locally the project runs these as two servers (`vllm serve` and `clm-serve`). On SageMaker they share one
container built from the **AWS vLLM Deep Learning Container**, because that image can load a `model.py`
from the model artifact and let it replace the default `/invocations` route:

```
InvokeEndpoint ──► /invocations  (code/model.py: CLM engine, heads on CPU)
                       │  POST 127.0.0.1:8080/v1/embeddings
                       ▼
                   vLLM 0.30  --runner pooling  Qwen3-8B bf16  (GPU)
```

`/ping` stays vLLM's own health check. Nothing is forked or rebuilt: the handler imports the upstream `clm`
package at a pinned commit and calls the engine the project ships. One small runtime patch fixes a cache bug
that shows up under concurrent traffic (see `_claim_once` in `code/model.py`).

> **Note:** This is sample code for demonstration purposes only and is not intended for production use without
> additional security testing and review.

## Contents

| File | What it does |
|---|---|
| [`01-generate-dataset.ipynb`](01-generate-dataset.ipynb) | Builds a small **labelled** benchmark set with Amazon Nova 2 Lite on Amazon Bedrock: about 120 support tickets and 100 ranking items (120 and 101 in the committed file), about 50 KB |
| [`02-deploy-and-benchmark.ipynb`](02-deploy-and-benchmark.ipynb) | Execution role, S3 artifact, endpoint, correctness checks, benchmark, CloudWatch metrics, cost, clean-up |
| [`code/model.py`](code/model.py) | The `/invocations` handler: the whole SageMaker-specific part, in one file |
| `data/clm_bench.jsonl` | The generated dataset, committed so notebook 2 runs on its own |

Run notebook 2 on its own if you just want the endpoint. Notebook 1 is only needed to regenerate the data.

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # pinned; boto3 1.43.1+ is needed for instance pools
# then open 02-deploy-and-benchmark.ipynb and run it top to bottom
```

You need: SageMaker (including `sagemaker:AddTags` and `sagemaker:ListTags`: the notebook tags what it creates, so it
never resumes or deletes a same-named resource it did not create), S3, ECR, IAM and Bedrock access in a US Region; GPU endpoint quota for at least one of
the instance types below; and about 17 GB of local disk for the weights on the first run.

**Cost:** **$1.13 to $2.61 per endpoint hour**, depending on which instance pool has capacity, plus S3 storage. A full run takes roughly 45 minutes,
most of it the first 16 GB artifact upload and an endpoint start of 10 to 17 minutes. The clean-up section is
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
and `temperature`. A request scores at most 1,025 texts (`CLM_MAX_TEXTS`), so a `rank` call takes at most 1,024 answers, and each
text (the state with a question's instructions, or one option) is at most 32 KB (`CLM_MAX_TEXT_BYTES`, 16 times the
encoder's 2,048-token cut-off). It may
also need at most 200,000 encoder tokens (`CLM_MAX_TOKENS`, counted over the texts not already cached, as each
text's UTF-8 length in bytes up to the 2,048-token cut-off), so it can finish within the deadline; a larger action
set can be cached in parts, after which it re-scores in one call. Malformed requests get HTTP 400, a
request that cannot finish within 55 s gets 503 so the caller can retry, and an encoder fault gets 502.

## Instance choice

Qwen3-8B in bf16 needs 24 GB of GPU memory. Quantizing is not an option, because the heads are trained on
exact bf16 Qwen3-8B embeddings. The endpoint therefore asks for five single-GPU types, **cheapest first**,
using SageMaker [capacity-aware instance pools](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints-heterogeneous.html):

| Priority | Instance | GPU | GPU memory | us-east-1 $/h |
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

`ml.g5.2xlarge` (A10G 24 GB) in us-east-1, the fourth pool (the three before it had no capacity this time), with the client on
EC2 in the same Region. vLLM 0.30.0, `max-model-len 2048`, one instance, no autoscaling. These are the
outputs committed in `02-deploy-and-benchmark.ipynb`.

CLM returns its scores in one response and generates no tokens, so **TTFT is time to first byte**; with a
~1 KB response it lands within a fraction of a millisecond of end-to-end latency. `ticket` is one `systemone` call with 3 typed
questions; `rank` is one `rank` call over 8 candidates. Every state and every `rank` candidate is new, so it is
embedded; only `ticket`'s 10 fixed option texts come from the cache after the first request.

| Workload | Concurrency | req/s | TTFT p50 | e2e p50 | e2e p99 | encoder tok/s |
|---|---|---|---|---|---|---|
| ticket | 1 | 11.2 | 97 ms | 97 ms | 100 ms | 1,360 |
| ticket | 4 | 35.1 | 113 ms | 113 ms | 151 ms | 4,276 |
| ticket | 16 | 51.7 | 298 ms | 298 ms | 416 ms | 6,465 |
| ticket | 32 | **58.4** | 510 ms | 510 ms | 731 ms | 7,435 |
| ticket | 64 | 57.7 | 974 ms | 974 ms | 1,689 ms | 7,320 |
| rank | 1 | 6.9 | 145 ms | 145 ms | 156 ms | 1,362 |
| rank | 4 | 22.2 | 176 ms | 176 ms | 213 ms | 4,387 |
| rank | 16 | 32.7 | 458 ms | 458 ms | 644 ms | 6,778 |
| rank | 32 | 35.8 | 822 ms | 822 ms | 1,230 ms | 7,528 |
| rank | 64 | **37.2** | 1,660 ms | 1,660 ms | 2,149 ms | 7,919 |

Throughput flattens past 32 concurrent requests. Beyond that knee, extra concurrency only buys
queueing latency (from 32 to 64 in flight `ticket` lost 1% and `rank` gained 4%, at twice the latency). Pick the
concurrency that fits your latency budget; section 11 of the notebook computes an autoscaling target at 70% of the measured peak (its scaling calls are
commented out, so the endpoint stays at one instance unless you run them). Server-side CloudWatch metrics over the benchmark window:

| Metric | Value |
|---|---|
| `GPUUtilization`, highest minute | 93% |
| `GPUMemoryUtilization`, highest minute | 86% |
| `CPUUtilization`, highest minute | 76% of 800 (8 vCPU) |
| `MemoryUtilization`, highest minute | 14% |
| `ModelLatency`, mean over the benchmark | 647 ms |
| `OverheadLatency`, mean over the benchmark | 3.7 ms |
| `Invocation5XXErrors`, total | 0 |

At the concurrency the notebook picks (the lowest within 2% of the peak, 32 for `ticket` and 64 for `rank` here), on one instance, that is
about **$7.20 per million `systemone` calls** and **$11.31 per million 8-candidate `rank` calls**.
The first pool is cheaper when it has capacity: an earlier run on `ml.g6.xlarge` (L4, $1.127/h) measured 57.8 `ticket`
and 36.9 `rank` requests per second, **$5.42 and $8.47 per million**. Other pools in earlier runs: `ml.g5.xlarge` (A10G,
$1.408/h) 54.9 and 38.3 ($7.13 and $10.21), and `ml.g6e.xlarge` (L40S 48 GB, $2.605/h) 110.6 and 52.9 ($6.54 and
$13.68). One run on `ml.g6.2xlarge` measured `rank` at half its usual rate with the same code, so treat any single
run's figures as indicative.

### The CLM embedding cache earns its keep

CLM caches the embedding of every text it has seen, so an agent that reuses a fixed action set pays only
for the new state. One request of each size, sent cold and then warm (single measurements, so expect some
spread between runs):

| Candidates | Request size | Cold | Warm | Speed-up |
|---|---|---|---|---|
| 8 | 0.4 KB | 153 ms | 53 ms | 3x |
| 64 | 2.9 KB | 445 ms | 54 ms | 8x |
| 256 | 12 KB | 1,557 ms | 55 ms | 28x |
| 1024 | 50 KB | 4,514 ms | 66 ms | 68x |

Warm latency stays between 53 and 66 ms no matter how many candidates, because only the state is new. That is
the shape an agent loop wants: a fixed tool or action set costs almost nothing to re-score.

### Keep vLLM's default batch size

Raising `max_num_batched_tokens` above vLLM's 2048 default **hurts**. This was a separate experiment: three
otherwise identical `ml.g5.xlarge` endpoints, an earlier version of the handler, and a client in another Region,
so compare the rows with each other rather than with the tables above. 64 concurrent requests:

| `max_num_batched_tokens` | ticket throughput | rank throughput |
|---|---|---|
| 2048 (default) | **100%** (60.5 req/s) | **100%** (35.6 req/s) |
| 8192 | 96% | 71% |
| 16384 | 94% | 62% |

The KV cache on a 24 GB GPU holds about 24,000 tokens (vLLM logs
`GPU KV cache size: 24,672 tokens`), so bigger prefill batches make each step longer without running more
requests at once. `rank` loses the most because each of its requests carries 9 texts.

### Answer quality

Notebook 2 scores the endpoint against the Nova 2 Lite labels. This checks the *deployment*, since wrong
pooling, a wrong revision or broken heads would all land near chance. It is not a benchmark of CLM:

| Metric | Result | Baseline |
|---|---|---|
| rank top-1 (8 candidates) | 40.6% | 12.5% chance |
| urgent AUROC | 0.77 | 0.50 random |
| department accuracy (5-way) | 40.0% | 20% chance; 25.0% always the most common team |

Zero-shot `noul` probabilities are not calibrated, so urgency is scored with AUROC rather than a 0.5
cut-off. The model card is explicit that CLM's strong agentic numbers come from **fine-tuned** heads;
those are another `*.pt` file you can drop into `code/` and select with `"model": "<file stem>"`.

Notebook 2 also asserts the model-card examples against an independent reference run of the same
checkpoint (Hugging Face Transformers, Qwen3-8B bf16 on CPU). bf16 on a GPU, and how vLLM batches the request,
move these values a little (urgency came out between 0.827 and 0.851 across our endpoint runs, against 0.822 on CPU), so the check allows 0.05. Reference, then this run: `billing` 0.988 and 0.989,
urgency 0.822 and 0.834, the Moon 0.994 and 0.993.

## Design notes

Things that are easy to get wrong here.

- **Pooling must match training.** `--runner pooling` on `Qwen3ForCausalLM` resolves to last-token pooling
  with L2 normalisation, which is what the heads were trained on. Serving the Qwen3-*Embedding* checkpoint
  instead, or another engine with different pooling, silently changes the embeddings the heads expect.
- **Heads on the CPU.** They are small MLPs, and the handler runs in vLLM's API-server process, not the
  engine process. Putting them on the GPU would add a second CUDA context and memory outside vLLM's budget.
  `CLM_DEVICE=cuda` is still available if you prefer.
- **Never block the event loop.** The engine call is synchronous and makes an HTTP call back into the same
  server, so the handler runs it in a thread pool (`CLM_WORKERS`, default 32). Each request has a 55 s
  deadline that includes its time in the queue; past it the handler answers 503 instead of starting work
  the caller has already given up on.
- **Network isolation is on.** Weights, heads and the `clm` package all ship in the artifact, so the
  container needs no internet and never calls the Hugging Face Hub. That also means `requirements.txt`
  auto-install cannot reach PyPI, so bake anything extra into a derived image.
- **Pin everything.** The notebook pins the Qwen3-8B and CLM revisions, the `contrastive-lm` source commit
  and the DLC image tag. The S3 artifact prefix includes a hash of `code/`, so changing the handler creates a
  new artifact rather than editing one a running endpoint (or its next scale-out) reads from. Note that PyPI `contrastive-lm` 0.1.0
  is behind GitHub `main`, so the sample vendors `src/clm` from the pinned commit instead.
- **Uncompressed artifact.** `CompressionType: None` with an `S3Prefix` means no 16 GB tarball to extract
  at start-up.
- **CUDA 13 needs a newer host AMI.** The 0.30.0 images require
  `InferenceAmiVersion: al2023-ami-sagemaker-inference-gpu-4-1`; the default host AMI has an older driver.
- **Raise the start-up timeouts.** 16 GB of weights plus a 22 GB image do not fit the 8-minute default;
  the notebook uses 1200 s for both `ContainerStartupHealthCheckTimeoutInSeconds` and
  `ModelDataDownloadTimeoutInSeconds`.
- **Least-privilege role.** The notebook creates a role that can read only its own S3 prefix, pull only the
  vLLM DLC repository, and write only SageMaker logs and metrics. If the bucket uses SSE-KMS with your own key,
  the notebook adds `kms:Decrypt` on that key, limited to calls through S3. Its trust policy is bound to this account with
  `aws:SourceAccount`. The role is tagged when created; the notebook will not modify, and the clean-up will not
  delete, a role of the same name that it did not create.
- **The 60 s and 6 MB invocation limits apply.** Both are comfortable here: a 1024-candidate request is
  about 50 KB and answered in 4.5 s cold in the run above.

## Your data

The benchmark data is synthetic. Real states and candidates often contain customer text such as names, account
numbers, or health and payment details, and you are responsible for handling that data under the laws and
standards that apply to you (for example GDPR, HIPAA or PCI DSS). For production, encrypt the S3 artifact and the
CloudWatch logs with your own KMS key (the instance store on these GPU types is already encrypted by hardware, so
SageMaker does not accept `KmsKeyId` on their endpoint config), keep CloudWatch log retention short, and do not enable data
capture for regulated data without the controls it needs. The handler does not log request bodies, only errors.

## Responsible AI

CLM's scores can be wrong, and zero-shot they are not calibrated: on the synthetic set above it picks the wrong
department for more than half of the tickets. Use the scores to rank, route or triage, keep a person or a stricter check in the
loop for decisions that affect people (account actions, refunds, content moderation), and measure accuracy on your
own labelled data, ideally with a fine-tuned head, before you rely on a threshold. The model does not filter
harmful input: if states come from users, put Amazon Bedrock Guardrails or an equivalent filter in front of it.

## Production next steps

Notebook 2 ends with a commented-out autoscaling snippet whose target comes from the benchmark; the other
items are pointers.

- **Autoscaling** on `SageMakerVariantInvocationsPerInstance`, with the target at 70% of the measured
  peak throughput (section 11 of notebook 2 computes it). On a mixed-instance fleet, use a CloudWatch metric-math weighted utilisation
  metric instead, since pool members differ in throughput.
- **Scale to zero** for spiky or dev traffic, by deploying the same model as an inference component with
  `MinInstanceCount=0`.
- **Fine-tuned heads** served side by side on the same encoder: add more `*.pt` files under `code/` and send
  `"model": "<file stem>"`.
- **VPC** via `VpcConfig` plus an S3 gateway endpoint; network isolation already blocks egress.

## License

CLM-v0.1-8B weights and Qwen3-8B are both Apache 2.0. This sample is MIT-0; see [LICENSE](LICENSE).
