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

```
InvokeEndpoint ──► /invocations  (container/serve.py: SageMaker contract, warm-up, worker processes)
                       │
                       ▼
                   strands-decider 0.1.0 engine: Qwen3.5-2B torso + LoRA + pointer head   (GPU)
```

## Contents

| File | What it does |
|---|---|
| [`01-generate-dataset.ipynb`](01-generate-dataset.ipynb) | Builds a small labelled benchmark set with Amazon Nova 2 Lite on Amazon Bedrock |
| [`02-deploy-and-benchmark.ipynb`](02-deploy-and-benchmark.ipynb) | Image build and push, S3 artifact, two endpoints, correctness checks, benchmark, CloudWatch metrics, cost, clean-up |
| [`container/`](container/) | The serving image: `Dockerfile` on the AWS PyTorch DLC, `serve.py`, pinned `requirements.txt` |
| `data/decider_bench.jsonl` | The generated dataset, committed so notebook 2 runs on its own |

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # pinned; boto3 1.43.1+ is needed for instance pools
# then open 02-deploy-and-benchmark.ipynb and run it top to bottom
```

You need Docker (the notebook builds the serving image through the Docker SDK), SageMaker, S3, ECR, IAM and
Bedrock access in a US Region, endpoint quota for `ml.g4dn.xlarge` and one of `ml.g6.xlarge` / `ml.g5.xlarge` /
`ml.g6e.xlarge`, and about 25 GB of local disk for the image and the model files.

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

The response carries the answers, token usage and the engine's own latency. Malformed requests get HTTP 400;
a request that could not start within 50 s, because the model was busy, gets 503 so the caller can retry.

## Measured results

Both endpoints in us-east-1, client on EC2 in the same Region, one instance each, every request a new state.
The pools gave `ml.g4dn.xlarge` (T4) and `ml.g6.xlarge` (L4). These are the outputs committed in
`02-deploy-and-benchmark.ipynb`. The decider generates no tokens, so **TTFT is time to first byte**, which
equals end-to-end latency here; "engine" is the model's own time per request.

`ticket` asks 3 questions about one support message (a 5-way `choice`, a `noul` and a 3-level `score`);
`rank` asks one 8-way `choice`.

| Endpoint | Workload | Concurrency | req/s | TTFT p50 | e2e p99 | Engine p50 |
|---|---|---|---|---|---|---|
| T4, fp32, 1 worker | ticket | 1 | 2.6 | 376 ms | 423 ms | 368 ms |
| T4, fp32, 1 worker | ticket | 32 | 2.7 | 11,958 ms | 12,319 ms | 374 ms |
| T4, fp32, 1 worker | rank | 1 | 5.7 | 188 ms | 199 ms | 181 ms |
| T4, fp32, 1 worker | rank | 32 | 5.8 | 5,358 ms | 5,857 ms | 183 ms |
| L4, bf16, 3 workers | ticket | 1 | 7.0 | 141 ms | 171 ms | 133 ms |
| L4, bf16, 3 workers | ticket | 4 | **12.6** | 266 ms | 521 ms | 232 ms |
| L4, bf16, 3 workers | rank | 1 | 13.6 | 73 ms | 79 ms | 67 ms |
| L4, bf16, 3 workers | rank | 32 | **27.3** | 941 ms | 2,037 ms | 104 ms |

The T4 runs one request at a time, so its throughput is flat and extra concurrency only queues. On the L4,
three workers overlap requests; at low concurrency SageMaker keeps reusing the same connection, so the extra
workers only engage as concurrency rises.

### Which instance is cheaper

| Endpoint | $/h | ticket: $ per million | rank: $ per million |
|---|---|---|---|
| `ml.g4dn.xlarge` (T4) | 0.736 | 75.80 | 34.70 |
| `ml.g6.xlarge` (L4) | 1.127 | **24.80** | **11.50** |

The T4 is the cheaper instance but the L4 is about **3x cheaper per request**, because it answers 4.6x more
requests per second for 1.5x the price. Choose the T4 only when traffic is too low to keep an L4 busy, when
the hourly bill matters more than cost per request.

### Answers are correct, and identical on both GPUs

Against an independent CPU run of the authors' engine on the same revisions (urgency 0.8287, `billing` 0.8442,
score 1.102), the T4 in fp32 returns 0.829 / 0.844 / 1.102 and the L4 in bf16 0.828 / 0.845 / 1.100. On the
labelled Nova 2 Lite dataset both endpoints score the same:

| Metric | Result | Baseline |
|---|---|---|
| rank top-1 (8 options) | 98.0% | 12.5% chance |
| urgent AUROC | 0.915 | 0.50 random |
| department accuracy (5-way) | 82.4% | 20% chance |

### More questions per request cost little

The engine encodes the state once and shares that work across every question about it:

| Questions in one request | Engine time | Per question |
|---|---|---|
| 1 | 67 ms | 67 ms |
| 5 | 142 ms | 28 ms |
| 10 | 207 ms | 21 ms |
| 20 | 383 ms | 19 ms |

Ask an agent's related decisions together in one request rather than one request each.


## Design notes

- **No vLLM, so a small image of our own.** The authors' engine needs torch 2.7 or later, and the AWS PyTorch
  *inference* images stop at 2.6, so the image builds on the AWS PyTorch 2.9 training DLC (CUDA 13) and adds
  four pinned packages. The build fails if they introduce a dependency conflict the base image did not
  already have.
- **Precision per GPU.** The checkpoint is bf16. The T4 has no native bf16, and fp16 is not safe for this
  architecture's Gated DeltaNet layers, so the T4 runs fp32 (exact, 7.1 GiB). The check uses
  `torch.cuda.is_bf16_supported(including_emulation=False)`: recent torch otherwise reports bf16 as available
  on a T4 through slow emulation.
- **Fast kernels, warmed up.** `flash-linear-attention` provides Triton kernels for the Gated DeltaNet layers
  and works on the T4 too. Triton compiles them on first use, which took 106 s on a T4 and 36 s on an L4, past
  SageMaker's 60 s invocation limit, so the container compiles them before `/ping` reports healthy.
- **Worker processes.** The engine answers one request at a time. Three processes on a 24 GB GPU let requests
  overlap; a second process on the T4 added nothing in our tests.
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
  upgrades `urllib3` and `tornado`. What remains is in the base image's own CPython build; a newer AWS DLC
  release fixes it, so bump `BASE_IMAGE` when one is available.
- **Least-privilege role.** Read-only S3 on the artifact prefix in this account's bucket, image pull from this
  one repository, logs and metrics; the trust policy is bound to this account with `aws:SourceAccount`.

## Your data

The benchmark data is synthetic. Real states often contain customer text such as names, account numbers, or
health and payment details, and you are responsible for handling that data under the laws and standards that
apply to you (for example GDPR, HIPAA or PCI DSS). For production, encrypt the endpoint storage with your own
KMS key (`KmsKeyId` on the endpoint config), keep CloudWatch log retention short, and do not enable data capture
for regulated data without the controls it needs. The server logs only errors, never request bodies.

## License

Strands Decider 2B and Qwen3.5-2B-Base are Apache 2.0. This sample is MIT-0; see [LICENSE](LICENSE).
