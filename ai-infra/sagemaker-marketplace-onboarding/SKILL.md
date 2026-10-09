---
name: sagemaker-marketplace-onboarding
description: "Interactive walkthrough that onboards a model provider onto SageMaker Marketplace by making their model container spec-compliant. Handles existing projects (inventory + gap analysis + scaffold alongside without editing their files) and greenfield builds. Covers the /ping and /invocations contract (Real-Time, Streaming, Async, Batch Transform), optional bidirectional WebSocket, optional per-inference metering, model weights packaging, local testing, and ECR push. Optionally guides CreateModelPackage with the validation job for users who also want to publish. Use when the user says things like 'onboard my model to SageMaker Marketplace', 'list my model on SageMaker Marketplace', 'help me build a SageMaker Marketplace container', 'package my model for SageMaker Marketplace', 'review my container against the SageMaker Marketplace spec', or attaches the SageMaker Marketplace Model Listing Guide."
---

# SageMaker Marketplace — Container Builder (Interactive Walkthrough)

You are guiding a model provider (the seller) through the process of making their model container compatible with the AWS SageMaker Marketplace container contract. The provider may already have a project or may be starting from scratch, and they may want to stop at the container or continue on to publish a Marketplace listing. Adapt accordingly.

The container contract you are enforcing supports four sync invocation modes with **one identical `/invocations` implementation**: Real-Time (`InvokeEndpoint`), Streaming response (`InvokeEndpointWithResponseStream`), Async Inference (`InvokeEndpointAsync`), and Batch Transform. The container code is mode-agnostic — SageMaker handles routing. Bidirectional WebSocket streaming and per-inference metering are **opt-in** — only generate them if the user says they need them.

## Move phase by phase

Do not dump the whole plan on the user up front. One phase at a time, confirm before moving to the next. When you write files, use the Write tool; don't paste them into chat. Save what you learn about the project to memory (`project` type) so future sessions don't ask twice.

---

## Phase 0 — Goal + project state

Ask two questions up front, in a single AskUserQuestion call. These branch the rest of the walkthrough.

**Question A — Goal (what does "done" look like for you?):**

- **Just build a SageMaker-compatible container** — the provider wants a working container they can deploy however they like. Skip the marketplace-listing phase at the end.
- **Build the container AND list on Marketplace** — after the container is ready, walk them through FDP enrollment, IAM roles, `CreateModelPackage`, pricing, EULA, regions, notebook, and publishing (Limited → Public).

Route the "container only" answer through Phases 1–10 as usual, but skip Phase 11 (Marketplace listing hand-off). Route the "also list" answer through the full sequence including Phase 11.

**Question B — Project state:**

- **Existing project** (they have code they want to make marketplace-ready) → Phase 1 (Inventory)
- **Greenfield** (starting from scratch) → skip to Phase 2 (Discovery interview), then Phase 4 (Scaffold from templates)

If existing, ask for the project path. If no folder is mounted, offer `mcp__cowork__request_cowork_directory`. Do not proceed without seeing the code.

**Save both answers to memory** as `project` type so a later session picks up where you left off. Use these two flags — `goal` (container-only vs also-list) and `project_state` (existing vs greenfield) — to gate the rest of the walkthrough.

---

## Phase 1 — Inventory the existing project (existing projects only)

Do a **shallow** read. You are trying to answer three questions: what framework is this, what HTTP interface (if any) does it already expose, and where do the weights live. Do not read every source file.

Inventory targets (Read/Glob these; skip if not present):

1. **Dependency manifests**: `requirements.txt`, `pyproject.toml`, `Pipfile`, `poetry.lock`, `setup.py`, `environment.yml`. Framework detection: `torch`, `transformers`, `tensorflow`, `onnxruntime`, `tritonclient`, `vllm`, `fastapi`, `flask`, `bentoml`, `torchserve`.
2. **Dockerfile** (any of `Dockerfile`, `Dockerfile.*`, `docker/Dockerfile`). Extract: base image, ENTRYPOINT (exec vs shell form), CMD, exposed ports, `USER` directive, any `LABEL` for SageMaker.
3. **Entry points**: files named `app.py`, `server.py`, `main.py`, `serve.py`, `inference.py`, `handler.py`, `predictor.py`. Read them once. Note: framework (Flask/FastAPI/aiohttp/Starlette/torchserve/BentoML), listen port, existing route paths (`/ping`, `/health`, `/predict`, `/invocations`, `/v1/completions`, etc.), how model weights are loaded.
4. **Weights**: look for `*.pt`, `*.bin`, `*.safetensors`, `*.onnx`, `*.pb`, `model.tar`, `checkpoints/`, `weights/`, `models/`. Are they baked into the repo? Downloaded on startup from HuggingFace Hub or S3?
5. **README / docs**: skim the first ~100 lines of `README.md` to catch stated inputs, outputs, and invocation examples.

**Report back** to the user in a short block:

```
Framework:           <detected>
Existing HTTP server: <file>:<line> on port <N>, routes: <list>
Model weights:        <where they are, whether baked-in>
Dockerfile:           <present + summary, or absent>
```

Then confirm your read with the user before moving to Phase 2. Ask them to correct anything you got wrong.

---

## Phase 2 — Discovery interview

Use **AskUserQuestion** — not free-text — grouped into at most two calls to avoid overwhelming the user. Cover:

**Group 1 — Invocation modes (multiSelect):**
- Real-Time `/invocations` (baseline — required for any listing)
- Streaming response (`InvokeEndpointWithResponseStream` — chunked HTTP response, for LLMs/generation)
- **Async Inference** (`InvokeEndpointAsync` — auto-supported by the same `/invocations` with **no container code change**. But the container-side implications matter: **1 GB payload** (via S3) and **60-minute** processing budget vs. the 60-second sync timeout. Ask if the model has invocations that can run >60s or process >25 MB — if yes, note this for the customer-facing notebook so buyers know Async is an option. This is informational for the container walkthrough; no scaffold changes.)
- Batch Transform (auto-supported by the same `/invocations`, but ask if they'll actually use it — affects whether we scaffold `/execution-parameters`)
- **Bidirectional WebSocket** (`InvokeEndpointWithBidirectionalStream` — full-duplex real-time streaming; typical use cases include STT/TTS/voice, live translation, agent-to-agent flows. Requires `/invocations-bidirectional-stream` endpoint + Docker label + Ping/Pong handling. Skip unless the model genuinely needs simultaneous input and output.)

**Group 2 — Model shape:**
- **Modality** — LLM (text-in / text-out) / STT (audio-in / text-out) / TTS (text-in / audio-out) / Image classification or detection / Image generation / Text embedding / Vision-language (multimodal) / Other. This drives which modality-specific patterns (if any) apply — for example, pronunciation overrides only apply to TTS; audio streaming protocols only apply to STT/TTS. Do not ask about pronunciation, audio streaming, or other modality-specific patterns unless the modality warrants it.
- Framework (confirm from inventory or ask fresh for greenfield): PyTorch / HuggingFace Transformers / TensorFlow / ONNX Runtime / Triton / vLLM / Other
- Model size on disk: <5 GB / 5–20 GB / 20–50 GB / >50 GB (drives cold-start budget conversation)
- Target instance family: ml.g5.* / ml.g6.* / ml.g6e.* / ml.p4d.* / CPU (ml.m5/ml.c5)
- Primary input `Content-Type`: application/json / audio (wav/mp3) / image / text/plain / multipart / other
- Response shape: single JSON / single binary / multi-file (needs zip) / streaming chunks

**Group 3 — Billing:**
- Billing model: **Hourly** (customer pays $/hour per instance) or **Per-inference / usage-based** (customer pays per unit consumed — you must emit metering; harder to change later, 90-day freeze after any price update)
- If per-inference: what's the billable dimension? The right choice depends on modality — pick from the list below or supply a custom name. This is a naming decision — it must match between the container's metering emission and the Marketplace listing configuration.

  | Modality | Typical dimensions |
  |---|---|
  | LLM | `tokens`, `inference.count` |
  | STT | `audio_seconds`, `inference.count` |
  | TTS | `characters_synthesized`, `audio_seconds`, `inference.count` |
  | Image classification / detection | `images`, `inference.count` |
  | Image generation | `images`, `pixels`, `inference.count` |
  | Text embedding | `characters_processed`, `tokens`, `inference.count` |
  | Vision-language | `tokens`, `images`, `inference.count` |
  | Other / catch-all | `inference.count` |

**Modality-specific follow-ups** — only ask if the answer above triggers them:

- **TTS only**: do customers need per-request pronunciation overrides? Because the container runs in network isolation and cannot load pronunciation dictionaries from S3 at runtime, the standard pattern is to accept `pronunciations` in the request body. If yes, document this convention in the customer-facing notebook and have the container's TTS handler read the field and apply overrides during synthesis. Skip this entirely for non-TTS models.
- **STT / TTS only** (and only if bidirectional WebSocket was picked in Group 1): confirm the audio streaming protocol shape. Skip for other modalities.
- **LLM only** (and only if streaming response or WebSocket was picked in Group 1): confirm token-level streaming vs sentence-level chunks.

Also confirm the **target directory** for scaffolding. For existing projects, propose a sibling dir like `<project>/sagemaker/` so nothing overwrites their code.

Record everything to memory (`project` type). If the user has already answered a subset in prior conversations, don't ask again.

---

## Phase 3 — Gap analysis (existing projects only)

Before writing any code, produce a punch list comparing the existing project against the container contract. For each gap, name the specific file/line, the spec constraint, and the fix. Do **not** edit the existing files — just report.

Reference `reference/gap-checks.md` for the full list of things to check. The common gaps are:

1. **Wrong port** — anything not listening on 8080 must be moved.
2. **Wrong health-check path** — SageMaker only pings `/ping`. Rename or add.
3. **`/ping` always returns 200** — must return 503 while loading; spec warns this causes routing to dead instances.
4. **Non-standard inference path** — `/predict`, `/v1/chat/completions`, etc. must become `/invocations`. Add a shim, don't just rename (keeps the original working for other clients).
5. **Weights baked into the image** — must be extracted into `model.tar` and loaded from `/opt/ml/model/`.
6. **Weights downloaded at startup** — HuggingFace Hub, S3, git-lfs pulls: all fail. Container runs with zero outbound network.
7. **Shell-form ENTRYPOINT** — breaks SIGTERM. Convert to exec form.
8. **Missing `CMD ["serve"]`** — SageMaker runs `docker run <image> serve`.
9. **Non-root `USER`** — causes permission issues with the `/opt/ml/model/` mount.
10. **NVIDIA drivers bundled** — remove; SageMaker provides them.
11. **`tini` used as init** — remove; it gets confused by the `serve` arg.
12. **Startup > 8 minutes** — spec hard limit. If load is close, recommend uncompressed tar + safetensors.
13. **Reliance on custom HTTP headers** — SageMaker strips everything except the five documented headers.
14. **Custom pricing hooks needed** — if per-inference billing was picked in Phase 2, metering emission is currently missing.
15. **WebSocket missing** — if bidirectional was picked in Phase 2, `/invocations-bidirectional-stream` + Docker label + Ping/Pong all need to be added.

Present the punch list, get user confirmation, then move to Phase 4.

---

## Phase 4 — Scaffold alongside (both paths)

Create the following layout in the target directory. For existing projects this is a sibling directory (e.g., `<project>/sagemaker/`) — do not touch the existing project's files.

```
<target>/
├── Dockerfile
├── requirements.txt
├── app.py                      # FastAPI server — /ping, /invocations, /execution-parameters
├── model_loader.py             # Loads weights from /opt/ml/model/
├── inference.py                # predict() and (if streaming) predict_stream() — provider fills in
├── package_model.sh            # Builds model.tar from a weights dir + uploads to S3
├── test/
│   ├── test_input.json
│   ├── test_local.sh           # docker run + curl smoke tests + SIGTERM + --network none
│   └── test_streaming.py       # Streaming response chunk-arrival check
├── PRE_SUBMISSION_CHECKLIST.md
└── (if WebSocket opted in:)
    ├── websocket_handler.py    # /invocations-bidirectional-stream + Ping/Pong + framing + metadata channel
    └── test/test_websocket.py  # Local WebSocket smoke test
└── (if per-inference billing opted in:)
    └── metering.py             # emits X-Amzn-Inference-Metering JSON header on 2XX responses
```

Populate from the templates in this skill's `templates/` directory. Read the templates via Read and pass their contents to Write:

- Always: `Dockerfile`, `requirements.txt`, `app.py`, `model_loader.py`, `inference.py`, `package_model.sh`, `test/test_input.json`, `test/test_local.sh`, `PRE_SUBMISSION_CHECKLIST.md`
- If streaming response was chosen: also `test/test_streaming.py`, and uncomment the streaming block in the copy of `app.py`
- If Bidirectional WebSocket was chosen: also `websocket_handler.py`, `test/test_websocket.py`, uncomment the Docker `LABEL` in the copy of `Dockerfile`, wire the WebSocket route into the copy of `app.py`
- If per-inference billing was chosen: also `metering.py`, wire it into the invocations handler and (if WebSocket) the metadata channel

For `inference.py`, generate a framework-specific stub based on Phase 2 answers. HuggingFace users get a `transformers.pipeline` skeleton; plain PyTorch gets `torch.load`; ONNX gets `onnxruntime.InferenceSession`; etc.

After scaffolding, show the user the file tree and confirm before Phase 5.

---

## Phase 5 — Container code walkthrough

Walk the user through each endpoint's contract. For each one, show the relevant snippet from `app.py`, explain the constraint from the spec, and offer to customize. Do not skip the "why" — the spec has non-obvious gotchas.

**`/ping` — GET, port 8080**
- Must respond within 2 seconds; socket accept within 250 ms.
- Return 200 only when the model is actually loaded and a lightweight inference path works. Static 200 during model failure keeps SageMaker routing to the dead instance.
- Return 503 while loading and on failure.
- 8-minute total budget from `docker run` to `/ping` = 200 (includes S3 download of `model.tar` + extraction + weight load).

**`/invocations` — POST, port 8080**
- The **same** endpoint serves Real-Time, Streaming response, **Async**, and Batch Transform. The container gets zero indication of the mode. Do not branch on invocation mode.
- Max payload: 25 MB Real-Time / streaming; **1 GB** Async (SageMaker downloads from S3 and hands the container a normal POST); 100 MB per record Batch Transform.
- Timeout: 60 s sync / 8 min streaming / **60 min Async** / unlimited Batch Transform.
- Async is fully transparent to the container — customer uploads to S3, SageMaker downloads and calls `/invocations`, container returns as normal, SageMaker uploads response to a customer S3 path. No code changes needed to support it beyond keeping your sync `/invocations` correct.
- SageMaker forwards only these headers: `Content-Type`, `Accept`, `X-Amzn-SageMaker-Custom-Attributes`, `X-Amzn-SageMaker-Inference-Id`, `X-Amzn-SageMaker-Session-Id`.
- If multiple output files, zip inside the container and return `Content-Type: application/zip`.
- **If per-inference billing**: every 2XX response must include the `X-Amzn-Inference-Metering` response header. Its value is a JSON string: `{"Dimension": "inference.count", "ConsumedUnits": N}`. For mini-batch invocations (multiple inferences in one request), set `ConsumedUnits` to the number processed. Metering is ignored on non-2XX responses — do not return 200 with a silent error body. See `metering.py`.

**Streaming response** (only if selected in Phase 2)
- Return `Transfer-Encoding: chunked`. Each chunk becomes a `PayloadPart` event client-side.
- Same `/invocations` endpoint. No new route.

**`/execution-parameters` — GET, port 8080** (recommended for Batch Transform)
- Returns `{"MaxConcurrentTransforms": N, "BatchStrategy": "SINGLE_RECORD"|"MULTI_RECORD", "MaxPayloadInMB": M}`. SageMaker calls this before starting a batch job.

**`/invocations-bidirectional-stream` — WebSocket** (only if selected in Phase 2)
- Requires Docker label `com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true`. Without it, SageMaker will not route WebSocket traffic.
- Handshake, then WebSocket frames both ways. Client PayloadParts map to Text/Binary frames.
- Must respond to WebSocket Ping frames (60s interval; 5 missed Pongs → connection closed).
- Max connection duration: 30 minutes. Client must reconnect for longer sessions. Design the container stateless per-connection.
- Errors: send Text frame with `{"ModelStreamError": {"ErrorCode": "...", "Message": "..."}}` or a Close frame.
- **If per-inference billing on WebSocket**: metering goes on a **companion metadata WebSocket** at `/invocations-bidirectional-stream-metadata`, not on the main data stream. Signal support with `X-Amzn-SageMaker-Metadata-Stream-Supported: true` in the upgrade response. See `websocket_handler.py` and `reference/websocket.md`.
- **If modality is STT/TTS**: if the user wants this container easily consumable from a voice-orchestration framework like Pipecat, mention the control-message-vocabulary pattern (Speak/Flush/Clear/KeepAlive/Finalize) in `reference/pipecat-integration.md` while designing the container's WebSocket protocol — cheaper to build in now than retrofit later. Do not implement it unasked; just flag that the option exists at this point in the walkthrough.

**Optional: mirroring metering into your own CloudWatch (EMF)** (only if per-inference billing was selected in Phase 2)
- The `X-Amzn-Inference-Metering` header / metadata-channel frame is what actually gets the seller paid — nothing else is required for billing to work. Separately, mention that `reference/observability.md` documents an **optional** pattern (CloudWatch Embedded Metric Format) for mirroring the same `ConsumedUnits` value into the seller's own CloudWatch, as a self-serve way to reconcile the Marketplace bill against actual traffic without needing buyer-side CloudWatch access.
- This is a nice-to-have, not a spec requirement. Ask before adding anything: if the user wants it, add the `emit_emf_metric()` call (from `reference/observability.md`) to their `metering.py`, generated at that point for their specific file — do not scaffold it into `templates/metering.py` by default. If they don't want it, don't mention it again.

**Model loading — `model_loader.py`**
- Weights at `/opt/ml/model/` (read-only), read via `SM_MODEL_DIR` env var.
- `/tmp` is the only writable path. Zero outbound network.

**Environment variables in Batch mode** (informational — don't branch on them):
`SAGEMAKER_BATCH=true`, `SAGEMAKER_MAX_PAYLOAD_IN_MB`, `SAGEMAKER_BATCH_STRATEGY`, `SAGEMAKER_MAX_CONCURRENT_TRANSFORMS`. Real-Time invocations do not set these.

Ask the user to review the generated `app.py` (and `websocket_handler.py` / `metering.py` if applicable) before moving on. Offer to fill in `inference.py` with a working stub for their framework.

---

## Phase 6 — Dockerfile

**One image, one Dockerfile, one endpoint.** Marketplace expects exactly one Docker image per model listing. Multi-stage builds are fine (and often smaller final images), but only the final stage gets pushed to ECR. There is no "loader image + server image" split — everything runs in the same container.

**How SageMaker starts your container.** It literally runs `docker run <image> serve`. That maps to `ENTRYPOINT + "serve"` at runtime. Two supported patterns:

- **Single-process** (default):
  ```
  ENTRYPOINT ["python3", "app.py"]
  CMD ["serve"]
  ```
  Result: `python3 app.py serve`. "serve" lands in `sys.argv[1]`. Local `docker run <image>` (no args) also works — CMD supplies the default.

- **Multi-process** (only if you have >1 internal process — see `templates/supervisord.conf`):
  ```
  ENTRYPOINT ["/usr/bin/supervisord"]
  CMD ["-c", "/etc/supervisord.conf"]
  ```
  Supervisord ignores the "serve" arg, but the `[program:api-gateway]` block in the conf runs `python3 app.py serve` explicitly, so the effect is identical.

**Exec form is non-negotiable.** `ENTRYPOINT ["python3", "app.py"]` — JSON array, no shell. If you write `ENTRYPOINT python3 app.py` (shell form), Docker wraps it in `/bin/sh -c "..."` and SIGTERM goes to sh instead of Python. Your model never gets 30s to shut down cleanly, gets SIGKILL'd, and Marketplace validation fails. This is the single most common failure mode — check your ENTRYPOINT form before anything else.

**Other rules the spec enforces** (each of these fails Marketplace validation if violated):
- **Base image** matching target instance's CUDA version. Default `nvidia/cuda:12.1.0-runtime-ubuntu22.04` for g5/g6. Do **not** bundle NVIDIA drivers.
- **Run as root** — no `USER` directive. Non-root causes permission issues with the `/opt/ml/model/` mount.
- **No tini** — gets confused by the `serve` argument.
- **No baked weights** — never `COPY weights/` into the image.
- **Port 8080 only.** If multi-process, use supervisord; other processes on localhost.
- **Docker label** `com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true` **only** if the WebSocket endpoint is implemented — otherwise omit.
- **No outbound network at runtime.** Bundle every dependency; no `pip install`, no HuggingFace Hub, no external license servers.

---

## Phase 7 — Model weights packaging

Guide the user through `package_model.sh`.

- Organize weights in a directory (`model_weights/`, `config/`, etc.).
- Use **uncompressed** `tar -cf` (not `tar -czf`). Saves 1–3 min cold start for large models.
- Prefer `.safetensors` over `.pt`/`.bin` (~2× faster load).
- Upload to a seller S3 bucket. After `CreateModelPackage`, weights transfer to a Marketplace-managed bucket with a Marketplace KMS key — **seller loses direct access to that version**. Keep a source backup.
- To ship an update: new `model.tar` → new `ModelPackageVersion`. Existing customer endpoints stay on the old version until they redeploy. Docker image does not change unless code changes.

8-minute cold-start budget: S3 download ~30–120 s, extraction ~10–30 s, weight load 60–180 s.

---

## Phase 8 — Local testing (the validation gate)

**This is the primary validation. If it passes locally, it will work on SageMaker.** Do not let the user push to ECR until every test below is green.

1. **Container starts** — `docker run --gpus all --rm -p 8080:8080/tcp -v $(pwd)/model_artifacts:/opt/ml/model:ro --name test-model <image>:latest serve`
2. **/ping returns 200 within 2 s** — after the model has actually loaded. Watch logs to confirm.
3. **/invocations** — POST with `test/test_input.json`; valid response with correct `Content-Type`.
4. **/execution-parameters** — returns valid batch config.
5. **Streaming** (if enabled) — `test/test_streaming.py` shows chunks arriving progressively.
6. **WebSocket** (if enabled) — `test/test_websocket.py`: connect, exchange frames, verify Ping/Pong, verify graceful close.
7. **Metering** (if per-inference) — inspect `/invocations` response headers; confirm `X-Amzn-Inference-Metering: {"Dimension":"...","ConsumedUnits":N}` present on every 2XX response and absent on error responses.
8. **Network isolation** — `docker run --network none ...`. Container must still start and respond to `/ping`. If it fails, there's a runtime dependency on external services.
9. **SIGTERM** — `docker stop test-model` completes in <30 s. If it doesn't, ENTRYPOINT is probably shell form.

Only after all applicable tests pass, proceed to Phase 9.

---

## Phase 9 — ECR push and vulnerability scan

Walk the user through pushing to ECR **in each region they plan to list in**.

```bash
aws ecr get-login-password --region <region> | \
  docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
aws ecr create-repository --repository-name <model-name> --region <region>   # first time
docker tag <model-name>:latest <account>.dkr.ecr.<region>.amazonaws.com/<model-name>:v1
docker push <account>.dkr.ecr.<region>.amazonaws.com/<model-name>:v1
aws ecr describe-image-scan-findings --repository-name <model-name> \
  --image-id imageTag=v1 --region <region>
```

Recommend Trivy locally first: `trivy image --severity CRITICAL,HIGH <image>:latest`. Common fixes: base-image upgrade, `apt-get upgrade` in Dockerfile, pin `requirements.txt` to non-vulnerable versions.

---

## Phase 10 — Pre-submission checklist

Read `PRE_SUBMISSION_CHECKLIST.md` back to the user, item by item. Confirm each. Do not declare "marketplace-ready" until every applicable item is checked. Items conditional on Phase 2 answers:
- WebSocket-related items apply only if WebSocket was implemented.
- Metering items apply only if per-inference billing was chosen.

---

## Phase 11 — CreateModelPackage hand-off (only if `goal = also-list`)

**Skip this phase entirely if the user chose "container only" in Phase 0.**

For "also list" users, the skill helps with exactly two things: they already packaged `model.tar` in Phase 7, and here you help them run `CreateModelPackage` with a validation job. Everything else in the listing workflow — FDP enrollment, IAM roles, pricing, EULA, regions, the customer-facing notebook, publishing — the user handles manually via their AWS account team and the Marketplace Management Portal. Do not walk them through those steps.

### Step 1 — Prep sample validation input in S3

`CreateModelPackage` with `CertifyForMarketplace=True` triggers a SageMaker validation transform job that runs the container against sample input. If validation fails, the listing request fails. Before calling the API, the user must:

1. Choose a small, realistic sample of what customers will send to `/invocations`. A handful of records is fine.
2. Upload them to a seller-owned S3 bucket under a prefix like `s3://<bucket>/validation-input/`. The format must match one of the `SupportedContentTypes` declared in the API call.
3. Have a separate S3 prefix ready for validation output: `s3://<bucket>/validation-output/`.

### Step 2 — CreateModelPackage API call

Walk through the API structure with the user. See `reference/marketplace-listing.md` for the full skeleton. The fields that matter:

- `InferenceSpecification.Containers[0].Image` → the ECR image URI pushed in Phase 9.
- `InferenceSpecification.Containers[0].ModelDataUrl` → `s3://…/model.tar` from Phase 7.
- `SupportedRealtimeInferenceInstanceTypes` / `SupportedTransformInstanceTypes` → the instance families tested during Phase 8. Match what the container actually runs on.
- `SupportedContentTypes` / `SupportedResponseMIMETypes` → what `/invocations` accepts and returns. Must match reality.
- `ValidationSpecification.ValidationRole` → an IAM role with permission to run the transform job (typically the user's existing SageMaker execution role).
- `ValidationSpecification.ValidationProfiles[0].TransformJobDefinition` → points at the S3 input from Step 1 and the S3 output prefix.
- `CertifyForMarketplace=True` → triggers the Marketplace review workflow. Without it, you get a private ModelPackage instead.

What SageMaker validates: container starts, `/ping` returns 200 within 8 minutes, `/invocations` processes the sample input, valid output is produced, no security vulnerabilities. This is a **contract check**, not an accuracy evaluation.

### Step 3 — After CreateModelPackage passes

Everything from here is manual work the user handles via AWS account team + Marketplace Management Portal, not this skill. That includes: FDP enrollment (start it early — takes 1–2 weeks), the second IAM role for `assets.marketplace.amazonaws.com`, pricing (hourly vs per-inference — big commitment, hard to change), EULA, supported regions, the customer-facing notebook, and publishing (Limited → Public).

Point them at:

- **AWS docs for the Marketplace listing steps:** https://docs.aws.amazon.com/marketplace/latest/userguide/machine-learning-products.html
- **ML publishing prerequisites (including FDP):** https://docs.aws.amazon.com/marketplace/latest/userguide/ml-publishing-prerequisites.html
- **Marketplace Management Portal (where all UI configuration happens):** https://aws.amazon.com/marketplace/management/

The walkthrough ends here.

---

## Phase 12 — Operating a live listing (optional, only if asked)

Only relevant after Phase 11 is complete and the listing is published. Do not raise this unprompted — surface it if the user asks about monitoring a live endpoint, reconciling Marketplace billing, or giving customer support teams account access.

**Observability.** SageMaker publishes request/latency/error metrics and instance resource metrics to CloudWatch automatically — no container code needed for those. For streaming/WebSocket models, point the user at `ConcurrentRequestsPerModel` (10s-resolution, includes queued requests — the real load signal for long-lived connections) and `FirstChunkLatency`, not `InvocationsPerInstance`. For GPU capacity planning, SageMaker's detailed observability (on by default) exposes per-GPU Prometheus metrics via PromQL with zero container changes. If the seller wants their own business/billing metrics in CloudWatch (e.g. to reconcile Marketplace metered billing against actual traffic), recommend CloudWatch Embedded Metric Format (EMF) — a stdout JSON line CloudWatch auto-extracts into a real metric, no outbound call, works under network isolation. See `reference/observability.md` for the full metric catalog and an EMF emission snippet that mirrors the existing `metering.py` `ConsumedUnits` value.

**Customer support access.** If the seller asks how to support a buyer's endpoint without a standing cross-account IAM role, point them at AWS IAM Temporary Delegation — buyer-approved, time-limited (≤12h) STS credentials, CloudTrail-logged, no wildcards. This requires separate AWS Partner qualification to initiate requests; it is account/product-level enablement, not something this skill's container or `CreateModelPackage` call sets up. See `reference/iam-temporary-delegation.md`.

Both of these are pointers, not implementation phases — this skill does not build the CloudWatch dashboards, alarms, or delegation integration for the user.

---

## Phase 13 — Pipecat ecosystem hand-off (optional, only if applicable)

**Trigger condition** — independent of Phase 0's `goal` flag (relevant whether or not the user is listing on Marketplace): modality is STT or TTS, Bidirectional WebSocket was implemented (Phase 2), and Phase 8's local testing gate has passed. If any of those don't hold, skip this phase silently — don't mention it.

If the trigger condition holds, ask once whether the user wants help getting the model consumable from [Pipecat](https://github.com/pipecat-ai/pipecat), an open-source voice-agent orchestration framework with first-class support for SageMaker bidirectional-streaming endpoints. Use AskUserQuestion, not free text, with these options:

- **Skip** — no Pipecat work. Default if the user hasn't heard of Pipecat or doesn't have a voice-agent use case in mind.
- **Community-maintained integration** — the user hosts and maintains the integration in their own separate repository; Pipecat lists it for discoverability. Lower barrier, the realistic default for most providers.
- **Contribute to Pipecat core** — a PR into `pipecat-ai/pipecat` itself, only appropriate if the model is broadly relevant enough that Pipecat's maintainers would want to own and maintain it. Flag this as the Pipecat maintainers' call, not something to promise will be accepted.

Read `reference/pipecat-integration.md` before proceeding — it has the full architecture explanation, the control-message-vocabulary table, both paths' concrete requirements, and an illustrative (not scaffolded) service-wrapper code appendix.

If **Community-maintained integration**:
1. Confirm with the user: which GitHub account/org will host the repo, what license (BSD-2 like Pipecat, or another permissive OSS license), and whether they want **company attribution** in the README (Pipecat's own guide recommends this when the provider works for the company behind the model — it builds confidence the integration will be maintained). Do not default or assume any of these — ask explicitly, the same way Phase 0 asks rather than assumes the user's goal.
2. Walk through the requirements checklist from `reference/pipecat-integration.md`'s "Path A" section: source implementation (subclass `STTService`/`TTSService`, using the illustrative appendix as a starting point adapted to the container's own WebSocket control-message vocabulary from Phase 5/6 — not copy-pasted verbatim), a foundational single-file usage example, README (intro, install, usage, tested Pipecat version, attribution per step 1), LICENSE, docstrings, changelog.
3. Mention the separate docs-site submission (`pipecat-ai/docs`, Supported Services page + a dedicated service page with a demo video) as a follow-up step once the integration repo itself is working — don't try to do both in one pass.
4. Do not scaffold a `templates/pipecat_service_stub.py` file automatically. The service wrapper needs to be adapted to whatever control-message vocabulary this specific container's WebSocket protocol implements (see Phase 5's note) — there is no generic version that would be more than the illustrative appendix already provides. Help the user adapt the appendix's code to their container's actual protocol, in their own repository.

If **Contribute to Pipecat core**:
1. Confirm the user understands this is a judgment call for Pipecat's maintainers, not guaranteed acceptance.
2. Walk through the mechanics from `reference/pipecat-integration.md`'s "Path B" section: fork `pipecat-ai/pipecat`, branch, implement the service wrapper under `src/pipecat/services/<provider>/sagemaker/` following the real `deepgram/sagemaker/{stt,tts}.py` files as structural reference, add a changelog fragment (`changelog/<PR_number>.added.md`), commit, push to fork, open the PR against `main` with a clear description.
3. Same caution as above on not auto-generating the service wrapper — help adapt the illustrative appendix, in the user's fork, to their container's real protocol.

This phase never writes to the user's model container project — any files it produces belong in a separate Pipecat-integration repository or fork, matching this skill's existing rule of never editing the user's own project outside the scaffolded sibling directory.

---

## Reference material (in this skill's directory)

Read these when you need to quote a specific constraint:

- `reference/contract.md` — one-page endpoint/header/timing cheat sheet
- `reference/timing.md` — every hard timing constraint
- `reference/checklist.md` — full pre-submission checklist template
- `reference/gap-checks.md` — the gap-analysis checklist for Phase 3
- `reference/websocket.md` — bidirectional streaming protocol, framing, Ping/Pong, metadata channel
- `reference/billing.md` — hourly vs per-inference, dimensions, freeze rule
- `reference/logging.md` — CloudWatch log groups, structured JSON logging, key events to log, multi-process pitfalls
- `reference/marketplace-listing.md` — CreateModelPackage API skeleton + validation job (Phase 11 only)
- `reference/observability.md` — CloudWatch metrics catalog (invocation/instance/detailed-observability) + EMF business-metric emission (Phase 12 only)
- `reference/iam-temporary-delegation.md` — buyer-approved temporary support access for a live listing (Phase 12 only)
- `reference/pipecat-integration.md` — Pipecat voice-agent orchestration architecture, control-message-vocabulary design guidance, and both ecosystem contribution paths (Phase 5 pointer + Phase 13 only)

## Interaction style

- One phase at a time. Confirm before advancing.
- Use AskUserQuestion for decisions that change what gets generated. Never free-text those.
- Never edit the user's existing project files. Always scaffold alongside.
- When you write, use the Write tool — don't paste files into chat.
- Show the file tree after scaffolding.
- When a test fails, cite the spec constraint, propose a specific fix, and offer to apply it (to the scaffolded copy — still not the original).
- Don't recite the whole spec. Cite the relevant constraint only when the user hits it or asks.
