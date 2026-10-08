# Strands Decider 2B on Amazon SageMaker AI

Deploy [`StrandsAgents/strands-decider-2B-hobson-v19`](https://huggingface.co/StrandsAgents/strands-decider-2B-hobson-v19)
as real-time Amazon SageMaker AI endpoints on two GPU price points, check its answers, and benchmark it.

Strands Decider answers typed questions about a piece of text (the "state") with calibrated probabilities:

| Question type | Returns | Example |
|---|---|---|
| `noul` | P(true) | "Is this urgent?" |
| `choice` | one of N options, a probability per option, and a confidence | "Which team should handle this?" |
| `score` | an expected level on an ordered scale | "How frustrated is the writer?" |

Agents use it for routing, tool selection, argument checks, triage and guardrails, where a full LLM call is
slower and more expensive than the decision needs. It is `Qwen/Qwen3.5-2B-Base` with a rank-16 LoRA adapter and
a small pointer head, served by the authors' own engine
([strands-labs/strands-decider](https://github.com/strands-labs/strands-decider), Apache-2.0). vLLM cannot serve
it: the pointer head reads each option's hidden state, which is not an operation vLLM has.

> **Note:** This is sample code for demonstration purposes only and is not intended for production use without
> additional security testing and review.

```
InvokeEndpoint ──► /invocations  (container/serve.py front → app.py workers: SageMaker contract, warm-up)
                       │
                       ▼
                   strands-decider 0.1.0 engine: Qwen3.5-2B torso + LoRA + pointer head   (GPU)
```

## Contents

| File | What it does |
|---|---|
| [`01-generate-dataset.ipynb`](01-generate-dataset.ipynb) | Builds a small labelled benchmark set with Amazon Nova 2 Lite on Amazon Bedrock |
| [`02-deploy-and-benchmark.ipynb`](02-deploy-and-benchmark.ipynb) | Image build and push, S3 artifact, two endpoints, correctness checks, benchmark, CloudWatch metrics, cost, clean-up |
| [`container/`](container/) | The serving image: `Dockerfile` on the AWS PyTorch DLC, `serve.py` (launcher), `app.py` (server), pinned `requirements.txt` |
| `data/decider_bench.jsonl` | The generated dataset, committed so notebook 2 runs on its own |

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # pinned; boto3 1.43.1+ is needed for instance pools
# then open 02-deploy-and-benchmark.ipynb and run it top to bottom
```

You need Docker (the notebook builds the serving image through the Docker SDK), SageMaker, S3, ECR, IAM and
Bedrock access in a US Region (including `sagemaker:AddTags` and `sagemaker:ListTags`: the notebook tags
what it creates, so clean-up never touches resources it did not create), plus `cloudwatch:GetMetricStatistics` for the
server metrics, `kms:DescribeKey` if the bucket uses SSE-KMS, and `logs:DeleteLogGroup` and
`application-autoscaling:DeregisterScalableTarget` for the clean-up, endpoint quota for `ml.g4dn.xlarge` and one of `ml.g6.xlarge` / `ml.g6.2xlarge` /
`ml.g5.xlarge` / `ml.g5.2xlarge` / `ml.g6e.xlarge` (the second endpoint's pool, cheapest first), and about 25 GB of local disk for the image and the model files.

**Cost:** **$1.86 to $3.55 per hour while both endpoints run**, depending on which pools have capacity, plus
ECR and S3 storage. A full run takes about 50 minutes, plus about 20 minutes for the first image build. The clean-up cell is commented out by default:
uncomment and run it, or the endpoints keep billing.

## Request format

The body is the decider's own System One request, unchanged:

```json
{"state": "Help! My payouts have been failing for 3 days!",
 "questions": {
   "team":        {"type": "choice", "instructions": "Which team should handle this?",
                   "criteria": {"billing": "", "sales": "", "retail": ""}},
   "urgent":      {"type": "noul",   "instructions": "Does this convey urgency?"},
   "frustration": {"type": "score",  "instructions": "How frustrated is the writer?",
                   "criteria": ["calm", "frustrated", "depressed"]}}}
```

The response carries the answers, token usage and the engine's own latency. Malformed requests, and requests with
more than 64 questions (`DECIDER_MAX_QUESTIONS`) or 64 options in one question (`DECIDER_MAX_OPTIONS`), get HTTP
400; a body over 32 KB (`DECIDER_MAX_BODY_BYTES`) gets 413, so one request cannot hold a worker for long;
under overload a request gets 503 at once, so callers fail fast and can retry: when a worker already holds 64
requests (`DECIDER_MAX_QUEUE`), or its oldest queued request has waited 20 s, or the queue would take 20 s at the
median recent engine time (`DECIDER_MAX_QUEUE_WAIT`). With several workers the front tries another worker first. A
request that still could not start within 50 s (`DECIDER_REQUEST_DEADLINE`) gets 503 rather than running for a caller
that has gone.

## Measured results

Both endpoints in us-east-1, client on EC2 in the same Region, one instance each. The engine keeps no state between requests, so repeating the dataset does not inflate the numbers.
The pools gave `ml.g4dn.xlarge` (T4, its first choice) and `ml.g5.2xlarge` (A10G; `ml.g6.xlarge`, `ml.g6.2xlarge` and `ml.g5.xlarge` had no capacity). These are the outputs committed in
`02-deploy-and-benchmark.ipynb`. The decider generates no tokens, so **TTFT is time to first byte**, which
equals end-to-end latency here; "engine" is the model's own time per request.

`ticket` asks 3 questions about one support message (a 5-way `choice`, a `noul` and a 3-level `score`);
`rank` asks one 8-way `choice`.

| Endpoint | Workload | Concurrency | req/s | TTFT p50 | e2e p99 | Engine p50 |
|---|---|---|---|---|---|---|
| T4, fp32, 1 worker | ticket | 1 | 2.6 | 388 ms | 395 ms | 379 ms |
| T4, fp32, 1 worker | ticket | 32 | 2.6 | 12,238 ms | 12,276 ms | 382 ms |
| T4, fp32, 1 worker | rank | 1 | 6.1 | 149 ms | 204 ms | 142 ms |
| T4, fp32, 1 worker | rank | 32 | 6.3 | 5,055 ms | 5,508 ms | 144 ms |
| A10G, bf16, 3 workers | ticket | 1 | 6.3 | 158 ms | 174 ms | 148 ms |
| A10G, bf16, 3 workers | ticket | 32 | **12.7** | 2,470 ms | 2,611 ms | 234 ms |
| A10G, bf16, 3 workers | rank | 1 | 12.5 | 80 ms | 85 ms | 72 ms |
| A10G, bf16, 3 workers | rank | 2 | 23.9 | 83 ms | 94 ms | 75 ms |
| A10G, bf16, 3 workers | rank | 32 | **32.2** | 949 ms | 1,066 ms | 88 ms |

The T4 runs one request at a time, so its throughput is flat and extra concurrency only queues. On the
24 GB GPU, three workers overlap requests: two `rank` requests at once take 83 ms each end to end, against 80 ms for one.

### Which instance is cheaper

| Endpoint | $/h | ticket: $ per million | rank: $ per million |
|---|---|---|---|
| `ml.g4dn.xlarge` (T4) | 0.736 | 77.73 | 32.64 |
| `ml.g5.2xlarge` (A10G) | 1.515 | **33.14** | **13.06** |

The T4 is the cheaper instance but costs **2.3x to 2.5x more per request**: the A10G answers 4.8x to 5.1x more
requests per second for 2.1x the price. The GPU pool's first choice is cheaper still when it has capacity: an earlier
run on `ml.g6.xlarge` (L4, $1.127/h) measured 13.0 ticket and 29.6 rank requests per second ($24.16 and $10.58 per
million), and an earlier run on the T4 pool's fallback `ml.g4dn.2xlarge` ($0.940/h) measured $99.71 and $37.25. Choose the T4 only when traffic is too low to keep a 24 GB GPU busy.

### Answers are correct, and nearly identical on both GPUs

Against an independent CPU run of the authors' engine on the same revisions (urgency 0.8287, `billing` 0.8442,
score 1.102), the T4 in fp32 returns the same values to three decimals and the A10G in bf16 agrees within
0.003. On the labelled Nova 2 Lite dataset the two endpoints score the same (urgent AUROC 0.91 on both):

| Metric | T4 (fp32) | A10G (bf16) | Baseline |
|---|---|---|---|
| rank top-1 (8 options) | 92.1% | 92.1% | 12.5% chance |
| urgent AUROC | 0.91 | 0.91 | 0.50 random |
| department accuracy (5-way) | 85.0% | 85.0% | 20% chance |
| frustration MAE (0-2 `score`) | 0.51 | 0.51 | 0.64 always guessing 1 |

### More questions per request cost little

The engine encodes the state once and shares that work across every question about it:

| Questions in one request | Engine time | Per question |
|---|---|---|
| 1 | 72 ms | 72 ms |
| 5 | 153 ms | 31 ms |
| 10 | 214 ms | 21 ms |
| 20 | 338 ms | 17 ms |

Ask an agent's related decisions together in one request rather than one request each.

## Design notes

- **No vLLM, so a small image of our own.** The authors' engine needs torch 2.7 or later, and the AWS PyTorch
  *inference* images stop at 2.6, so the image builds on the AWS PyTorch 2.9 training DLC (CUDA 13) and adds
  nine pinned packages (the engine, transformers, peft, the two flash-linear-attention packages, regex,
  tokenizers, and upgraded urllib3 and tornado). The build fails if they introduce a dependency conflict the base image did not
  already have.
- **Precision per GPU.** The checkpoint is bf16. The T4 has no native bf16, and fp16 is not safe for this
  architecture's Gated DeltaNet layers, so the T4 runs fp32 (exact, 7.1 GiB). The check uses
  `torch.cuda.is_bf16_supported(including_emulation=False)`: recent torch otherwise reports bf16 as available
  on a T4 through slow emulation.
- **Fast kernels, warmed up.** `flash-linear-attention` provides Triton kernels for the Gated DeltaNet layers
  and works on the T4 too. Triton compiles them on first use, which took 106 s on a T4 and 36 s on an L4, past
  SageMaker's 60 s invocation limit, so the container compiles them before `/ping` reports healthy.
- **Worker processes.** The engine answers one request at a time. Three processes on a 24-48 GB GPU let
  requests overlap; a second process on the T4 added nothing in our tests. A small front process in `serve.py`
  sends each request to the worker with the fewest in flight. Balancing by connection is not enough: SageMaker
  reaches a container over a few kept-alive connections, so one worker would take most of the traffic. The front
  restarts a worker that exits after a fatal GPU error and routes around it while it loads again, sending a
  request that reached it while it was exiting to another worker.
- **Offline model files, pinned.** The decider's loader fetches the base model from the Hugging Face Hub, so
  the artifact carries a Hugging Face cache with `Qwen/Qwen3.5-2B-Base` at revision `b1485b2` (the one the
  decider's `provenance.json` records) and the image sets `HF_HUB_OFFLINE=1`. The endpoints run with network
  isolation.
- **Capacity.** Both endpoints use instance pools; SageMaker requires at least two entries, so the T4 endpoint
  falls back from `ml.g4dn.xlarge` to `ml.g4dn.2xlarge` (same GPU). `ml.g7` and `ml.g7e` are left out: they
  cost more, and new accounts have a quota of 0 for them.
- **Supply chain.** The image tag is a hash of `container/` and the base image, the ECR repository has
  immutable tags and scans every push, and the notebook prints the scan's severity counts. The build applies
  Ubuntu's security updates to the base image and removes packages a serving container does not need but that
  carried HIGH or CRITICAL CVEs (ffmpeg inside `opencv-python`, `flash-attn`, the SageMaker Python SDK), and
  upgrades `urllib3` and `tornado`. The Ubuntu updates are whatever is current at build time, so they are the one
  build input that is not pinned; rebuild to pick up new ones. What remains is in the base image's own CPython build; a newer AWS DLC
  release fixes it, so bump `BASE_IMAGE` when one is available.
- **Least-privilege role.** Read-only S3 on the artifact prefix in this account's bucket, image pull from this
  one repository, logs and metrics; the trust policy is bound to this account with `aws:SourceAccount`.

## Your data

The benchmark data is synthetic. Real states often contain customer text such as names, account numbers, or
health and payment details, and you are responsible for handling that data under the laws and standards that
apply to you (for example GDPR, HIPAA or PCI DSS). For production, encrypt the endpoint storage with your own
KMS key (`KmsKeyId` on the endpoint config), keep CloudWatch log retention short, and do not enable data capture
for regulated data without the controls it needs. The server logs only errors, never request bodies.

## Responsible AI

The decider returns probabilities, and they can be wrong. On the synthetic set above it picks the wrong department
for about 1 ticket in 5 (15% to 23% across our runs). Use its answers to route or to triage, keep a person or a stricter check in the loop for decisions
that affect people (account actions, refunds, content moderation), and measure accuracy on your own labelled data
before you rely on a threshold. The model is trained on English text; test other languages before you use them.
It does not filter harmful input or output: if states come from users, put Amazon Bedrock Guardrails or an
equivalent filter in front of it.

## License

Strands Decider 2B and Qwen3.5-2B-Base are Apache 2.0. This sample is MIT-0; see [LICENSE](LICENSE).
