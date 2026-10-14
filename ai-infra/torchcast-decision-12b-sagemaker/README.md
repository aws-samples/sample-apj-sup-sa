# TorchCast Decision 12B on Amazon SageMaker AI

Deploy [`torchcast-ai/torchcast-decision-12b`](https://huggingface.co/torchcast-ai/torchcast-decision-12b) as an
Amazon SageMaker AI real-time endpoint on one NVIDIA L40S, invoke it, and measure its latency at 1, 10 and 64
requests in flight.

TorchCast Decision 12B answers typed questions about a piece of text (the "state") with probabilities:

| Question type | Returns | Example |
|---|---|---|
| `choice` | a probability per named option, the most probable option, and a confidence | "Which team should handle this ticket?" |
| `noul` | P(yes) | "Should this ticket be answered today?" |
| `score` | an expected level on an ordered scale, with a probability per level | "How badly is the customer affected?" |

It is Gemma 4 12B with a LoRA fine-tune merged into the weights (24 GB in BF16), served by vLLM with a
16,384-token context. Each question is one forward pass over the prompt that reads the probabilities of the option
letters at the answer position, so the model generates no text. Agents use a model like this for routing, triage,
tool selection and checks, where a generated answer would be slower and harder to parse.

> **The model weights are licensed CC BY-NC 4.0: non-commercial use only.** The endpoint downloads them from
> Hugging Face. Do not use this deployment for commercial purposes unless TorchCast AI gives you other terms. Read
> the [model's licence](https://huggingface.co/torchcast-ai/torchcast-decision-12b/blob/main/LICENSE), which also
> lists the training-data sources and their terms, before you deploy. Use of the model is also subject to Google's
> [Gemma Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy). TorchCast's serving code is
> MIT; this sample is MIT-0.

> **Note:** This is sample code for demonstration purposes only and is not intended for production use without
> additional security testing and review.

```
InvokeEndpoint (SigV4) ──► SageMaker ──► :8080 /invocations   container/serve.py + TorchCast decision_server.py
                                              │  one vLLM request per question
                                              ▼
                                        127.0.0.1:8890  vLLM 0.30.0, torchcast-decision-12b BF16 (GPU)
```

The image is the AWS vLLM Deep Learning Container (SageMaker flavour, vLLM 0.30.0) plus TorchCast's own decision
server from the model's [serving repository](https://github.com/Torchcast-AI/torchcast-decision-12b) at the v1.0.0
release. `container/serve.py` starts vLLM on loopback and puts the decision server behind SageMaker's `/ping` and
`/invocations` routes. SageMaker checks every caller with IAM (SigV4), so the decision server's API key is not used.

## Contents

| File | What it does |
|---|---|
| [`deploy-and-invoke.ipynb`](deploy-and-invoke.ipynb) | Image build and push, execution role, endpoint, invocation, concurrency run with latency percentiles, CloudWatch metrics, clean-up |
| [`decisions.py`](decisions.py) | Request bodies, a generator of distinct synthetic support tickets, response checks and the concurrency runner |
| [`container/`](container/) | The serving image: `Dockerfile` on the AWS vLLM DLC, `serve.py` (entry point), `build.sh` (build, push and manifest check) |
| [`notebook-user-policy.json`](notebook-user-policy.json) | IAM policy for the identity that runs the notebook. It grants no IAM write access |
| [`execution-role-policy.json`](execution-role-policy.json), [`execution-role-trust-policy.json`](execution-role-trust-policy.json) | The endpoint's execution role, which an administrator creates once (see Prerequisites) |
| `requirements.txt` | Pinned Python packages for the notebook |

## Prerequisites

* In Service Quotas, SageMaker `ml.g6e.xlarge for endpoint usage` (quota code `L-B0729CB4`) must be
  at least 1 in your Region. New accounts often have 0; request an increase before you start. The endpoint falls
  back to `ml.g6e.2xlarge`, `ml.g6e.4xlarge` and `ml.g6e.8xlarge` when SageMaker AI has no `ml.g6e.xlarge` capacity
  (instance pools), so a quota of 1 for those (`L-F8D7F460`, `L-93531071`, `L-96A28D02`) makes the fallback usable.
* A machine with Docker 23 or later with `buildx`, plus `curl`, the AWS CLI v2, Python 3.12 and about 60 GB of free
  disk for the image build. An x86_64 Linux host, such as an EC2 instance in the same Region, builds fastest and
  measures the endpoint without your internet path in the way. On arm64 (Apple silicon) Docker emulates x86_64 and
  the build is much slower.
* Outbound HTTPS from that machine to `raw.githubusercontent.com` (TorchCast's serving files) and ECR, and from
  the endpoint to `huggingface.co` (the weights).
* An execution role for the endpoint, created once by an administrator (see below). In the notebook's configuration
  cell, set `EXISTING_ROLE_ARN` to its ARN, `arn:aws:iam::<account-id>:role/torchcast-decision-12b-sagemaker-execution`.
* The IAM permissions in [`notebook-user-policy.json`](notebook-user-policy.json) for the identity that runs the
  notebook. Replace `REGION` and `ACCOUNT_ID`. The policy covers creating and deleting the ECR repository, reading the
  execution role and passing it to SageMaker only, the model, endpoint config and endpoint, invoking the endpoint,
  reading its metrics and deleting its log group, each limited to this sample's resource names. It grants no IAM write
  access: whoever can create a role, edit its trust policy or put a policy on it, and also pass or assume it, can
  give that role administrator access and use it.
* A Region that offers `ml.g6e.xlarge` for SageMaker hosting and publishes the AWS DLCs from account
  `763104351884`. The notebook defaults to us-east-2. For an opt-in Region that publishes the DLCs from another
  account, set `DLC_ACCOUNT` for `build.sh`.

### One-time step for an administrator: the execution role

Create the role from the two policy files in this folder, after replacing `REGION` and `ACCOUNT_ID` in both:

```bash
aws iam create-role --role-name torchcast-decision-12b-sagemaker-execution \
  --assume-role-policy-document file://execution-role-trust-policy.json \
  --description "torchcast-decision-12b SageMaker endpoint"
aws iam put-role-policy --role-name torchcast-decision-12b-sagemaker-execution --policy-name endpoint \
  --policy-document file://execution-role-policy.json
```

The role may pull from the sample's ECR repository and write the endpoint's logs and metrics, and only SageMaker in
your account can assume it. If you load the weights from S3 (`WEIGHTS_S3_URI`, below), also allow `s3:GetObject` on
the prefix and `s3:ListBucket` on the bucket with an `s3:prefix` condition. The role carries no
`created-by=torchcast-decision-12b-sagemaker-sample` tag, so the notebook's clean-up leaves it in place for later
runs; delete it with `aws iam delete-role-policy` and `aws iam delete-role` when you no longer need it.

## Cost

`ml.g6e.xlarge` costs $2.6054 per hour in us-east-1, us-east-2 and us-west-2, and more in some other Regions (for
example $3.7786 in ap-northeast-1, Tokyo), on-demand list prices on 13 October 2026. If the endpoint falls back, the
hour costs $2.8026 (`ml.g6e.2xlarge`), $3.7553 (`ml.g6e.4xlarge`) or $5.6607 (`ml.g6e.8xlarge`) in those three Regions;
the notebook prints the type it got. Plan on about an hour of
endpoint time for a full run of the notebook, most of it while the endpoint starts, plus the first image build.
ECR stores the image (several GB) until the clean-up deletes the repository. Check the current
[SageMaker AI pricing](https://aws.amazon.com/sagemaker/ai/pricing/) for your Region.

## Steps

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
jupyter lab deploy-and-invoke.ipynb
```

Run the notebook from this folder, top to bottom:

1. Install the pinned packages.
2. Set `REGION`, `EXISTING_ROLE_ARN` (the execution role from the administrator step) and, if you want them, a
   named `PROFILE` or an existing image.
3. Build and push the image with `container/build.sh`, and check that ECR holds a Docker v2 schema 2 manifest.
4. Use the execution role. With `EXISTING_ROLE_ARN = None` the notebook creates the role itself, which needs IAM
   write access that `notebook-user-policy.json` does not grant; run it that way only as an administrator.
5. Create the model, endpoint config and endpoint, and wait for `InService`. In our run the container started 16
   minutes after `CreateEndpoint`; vLLM then downloads the 24 GB of weights and loads them before `/ping` turns
   healthy.
6. Invoke it with a three-question request, and check that a malformed request gets HTTP 422.
7. After 64 warm-up requests, send 1,880 single-question requests at 1, 10 and 64 in flight, each with a different
   ticket, and print p10, p50, p90 and p99 latency and decisions per second for each level. Every response is
   checked.
8. Read `ModelLatency` and `OverheadLatency` for the same window from CloudWatch.
9. Clean up.

To build the image without the notebook:

```bash
REGION=us-east-2 ACCOUNT=$(aws sts get-caller-identity --query Account --output text) ./container/build.sh
```

## Request format

The body is TorchCast's `/v1/systemone` request, unchanged:

```json
{"state": "I was charged twice for order #4411 and the app still shows it as unpaid.",
 "questions": {
   "team":     {"type": "choice", "instructions": "Which team should handle this support ticket?",
                "criteria": {"billing": "Payments, charges, invoices and refunds",
                             "shipping": "Delivery, tracking and returns",
                             "technical": "App errors, crashes and outages"}},
   "urgent":   {"type": "noul",   "instructions": "Should this ticket be answered today?"},
   "severity": {"type": "score",  "instructions": "How badly is the customer affected?",
                "criteria": ["cosmetic", "annoying", "blocks the customer"]}}}
```

The response has one answer per question, the model name and token usage:

```json
{"model": "torchcast-decision-12b",
 "answers": {"team": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.97, "...": 0.01},
                      "confidence": 0.96},
             "urgent": {"type": "noul", "noul": 0.71},
             "severity": {"type": "score", "score": 1.4, "legend": {"0": "cosmetic", "...": "..."},
                          "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}, "confidence": 0.25}},
 "usage": {"input_tokens": 612, "output_tokens": 3}}
```

The values above show the shape only; section 6 of the notebook prints a real response. A `choice` takes up to
255 options (more than 20 are read in groups, so they cost extra passes) and a `score` up to 10 levels. The decision
server answers HTTP 400 for a body that is not JSON, 413 for a body over 1 MiB, 422 for a request it cannot answer
(unknown type, too many options, a prompt over the context) and 502 when vLLM fails. Through SageMaker,
`InvokeEndpoint` raises `ModelError` for these, with the container's status in `OriginalStatusCode`.

## Measured results

Our benchmark on `ml.g6e.xlarge` in us-east-2, before this sample was written, with the same image and settings. The
client was an EC2 `c7i.2xlarge` in the same Region sending SigV4-signed `InvokeEndpoint` calls over kept-alive
connections, one decision per request. The requests were distinct items from public evaluation splits (BANKING77,
BoolQ, WANLI, CommonsenseQA and SST-5), including BoolQ retrieval states of about 1,000 to 6,000 tokens, so they are
longer on average than the notebook's short tickets.

| In flight | Decisions/s | p50 | p99 | Errors |
|---|---|---|---|---|
| 1 | 17.5 | 55.9 ms | 68.8 ms | 0 |
| 10 | 46.2 | 214.1 ms | 264.8 ms | 0 |
| 64 | 63.4 | 935.2 ms | 1,262.6 ms | 0 |
| 256 | 65.1 | 3,947.9 ms | 4,283.5 ms | 0 |

The rows for 1 and 10 come from the first run and the rows for 64 and 256 from a second run after the listen-backlog
fix described below; the image was otherwise the same. Throughput levels off at about 60 to 65 decisions per second,
the same ceiling we measured on an EC2 `g6e.xlarge` with the same GPU, so past about 32 in flight extra concurrency
adds queueing latency and little throughput. At that rate one instance costs about $11.5 per million decisions in
us-east-2. SageMaker added about 6 ms at one request in flight (55.9 ms against 49.7 ms p50 on EC2, measured in
different Regions).

On the 231 public JevBench items the endpoint answered 203 correctly, the same score as TorchCast's H100 release run,
and picked the same option on 230 of the 231.

Your numbers from the notebook will differ. Its client is boto3 in Python threads, its tickets are short, and if
you run it outside the Region every latency includes your network path.

## Design notes

* Docker v2 manifest. SageMaker accepts only a Docker image manifest v2 schema 2. Recent Docker versions push OCI
  media types and add provenance and SBOM attestations, which turn the push into an OCI image index. `build.sh` builds with `docker buildx build --provenance=false --sbom=false --output
  type=image,oci-mediatypes=false,push=true` and then checks the manifest media type in ECR.
* TCP_NODELAY. TorchCast's server writes the response headers and body in two sends. SageMaker reaches the
  container over kept-alive connections, and on those Nagle's algorithm holds the second send until the delayed ACK
  for the first arrives, which added 40 to 60 ms to every response. `serve.py` sets `TCP_NODELAY` on the server's
  connections (`NODELAY=on`).
* Listen backlog. Python's `socketserver` listens with a backlog of 5. When more new connections arrived at once
  than that, the kernel dropped them, and SageMaker answered HTTP 424 (28 of the requests at 100 in flight, 282 at
  256). `serve.py` raises the backlog to 1024; the second run had no errors from 64 to 256 in flight.
* vLLM requests in flight. The decision server caps the vLLM requests it has open at `TORCHCAST_MAX_PARALLEL`, 8 by
  default. `serve.py` sets 256 so that vLLM can batch them.
* Timeout under SageMaker's limit. SageMaker gives a real-time invocation 60 seconds. `serve.py` sets the decision
  server's upstream timeout to 50 seconds (TorchCast's default is 180), so a stuck vLLM call returns an error before
  SageMaker gives up.
* Non-root. The server runs as an unprivileged user with UID 1001 (the base image's `ubuntu` user has 1000). vLLM
  listens on 127.0.0.1 only. The decision server listens on all interfaces inside the container, because SageMaker's
  front end connects to the container's network interface.
* Pinned inputs. The base image is a versioned AWS DLC tag; TorchCast's serving files come from a fixed commit and are
  checked against SHA-256 sums; the weights are a fixed Hugging Face revision. The image tag is a hash of the
  Dockerfile, `serve.py`, the serving files and the base image, the ECR repository has immutable tags and scans each
  push, and the notebook prints the scan's severity counts. On 13 October 2026 the scan reported 5 critical and 36 high
  findings, all in operating-system and Python packages of the AWS vLLM DLC base image, none in this sample's files.
  Rebuild on the newest DLC tag before you deploy, and review the findings against your own policy.
* Least-privilege execution role. Pull from this one repository, write logs and metrics, and, with
  `WEIGHTS_S3_URI`, read that one S3 prefix. The trust policy names your account in `aws:SourceAccount`.

## Loading the weights from S3

By default the endpoint downloads the weights from Hugging Face when it starts, so it needs internet access and runs
without network isolation. For production, stage them in S3 in the same Region and set `WEIGHTS_S3_URI` in the
notebook. The model then mounts the prefix at `/opt/ml/model`, `serve.py` loads it from there, and the endpoint runs
with network isolation. We benchmarked the Hugging Face path, not this one.

```bash
pip install huggingface_hub==2.2.0
hf download torchcast-ai/torchcast-decision-12b --revision 49107b5589bf2dc4ec3f5acd54316971dd5978b1 --local-dir build/weights
aws s3 sync build/weights s3://<your-bucket>/torchcast-decision-12b/49107b55/ --exclude ".cache/*"
```

Uploading needs `s3:PutObject` on that prefix, which `notebook-user-policy.json` does not include. If the bucket uses
SSE-KMS, the execution role also needs `kms:Decrypt` on the key.

## Your data

The notebook's tickets are synthetic. Real states often contain customer text such as names, account numbers, or
health and payment details, and you are responsible for handling that data under the laws and standards that apply to
you (for example GDPR, HIPAA or PCI DSS). For production, encrypt the endpoint's storage with your own KMS key
(`KmsKeyId` on the endpoint config), keep CloudWatch log retention short, and do not turn on data capture for
regulated data without the controls it needs. The decision server logs one line per request (method, path and status)
and, for HTTP 422, the reason, which can quote a question name or field value from the request. It does not log
states.

## Responsible AI

The model returns probabilities, and they can be wrong: it answered 28 of the 231 public JevBench items incorrectly in
our run. Use its answers to route or triage, keep a person or a stricter check in the loop for decisions that affect
people (account actions, refunds, content moderation), and measure accuracy on your own labelled data before you rely
on a threshold. The model does not filter harmful input or output; if states come from users, put
Amazon Bedrock Guardrails or an equivalent filter in front of it.

## Clean up

The last cell of the notebook deletes the endpoint (which stops the hourly charge), the endpoint config, the model,
the endpoint's log group, the execution role (only one the notebook created) and the ECR repository, each only if it
carries the notebook's tag
`created-by=torchcast-decision-12b-sagemaker-sample`. If the notebook stopped before that cell, run the configuration
cell and then the clean-up cell, or delete the resources yourself:

```bash
aws sagemaker delete-endpoint --endpoint-name torchcast-decision-12b
aws sagemaker delete-endpoint-config --endpoint-config-name torchcast-decision-12b
aws sagemaker delete-model --model-name torchcast-decision-12b
aws ecr delete-repository --repository-name torchcast-decision-12b-sagemaker --force
```

Weights staged in S3 for `WEIGHTS_S3_URI` stay there until you delete them.

## License

This sample is MIT-0; see [LICENSE](LICENSE). TorchCast's serving code, which `build.sh` downloads into the image, is
MIT, with its licence file included in the image. The torchcast-decision-12b weights are CC BY-NC 4.0, for
non-commercial use only, and are a modified version of Google's Gemma 4 12B (Apache 2.0); see the
[model's licence](https://huggingface.co/torchcast-ai/torchcast-decision-12b/blob/main/LICENSE).
