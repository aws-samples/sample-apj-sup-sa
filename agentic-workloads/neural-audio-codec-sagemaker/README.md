  # Neural audio codecs on Amazon SageMaker AI

  Deploy two neural audio codecs — [Mimi](https://huggingface.co/kyutai/mimi) and
  [EnCodec](https://huggingface.co/facebook/encodec_24khz) — behind a single Amazon
  SageMaker AI endpoint, and benchmark them on frame rate, bitrate, and latency.

  A neural audio codec encodes a waveform into discrete tokens and decodes them back —
  the tokenization layer that speech language models such as Kyutai Moshi are built on.
  Both codecs here operate at 24 kHz and download from the Hugging Face Hub at container
  start, pinned to a fixed commit.

  ## Table of contents
  
  - [How it works](#how-it-works)
  - [Prerequisites](#prerequisites)
  - [Setup](#setup)
  - [Deploy](#deploy)
  - [Usage](#usage)
  - [Cost](#cost)
  - [Cleanup](#cleanup)
  - [Security](#security)
  - [Project structure](#project-structure)
  - [Limitations](#limitations)
  - [References](#references)
  
  ## How it works

  One inference handler (`src/inference.py`) loads both codecs once at container start
  and routes each request by a `codec` field, so Mimi and EnCodec run on the same
  instance and container and any measured difference comes from the codec, not the setup.
  Each request selects the codec (`mimi` or `encodec`) and a task (`encode`, `decode`, or
  `roundtrip`).

  The endpoint runs on the AWS Deep Learning Container for Hugging Face, which already
  includes PyTorch and the Transformers library; only `inference.py` and a short
  `requirements.txt` are added on top.

  ## Prerequisites

  - An AWS account with permission to create Amazon SageMaker AI endpoints
  - A SageMaker AI execution role and an Amazon S3 bucket in your Region
  - Python 3.12, and the client dependencies: `pip install -r requirements.txt`
    (the SageMaker Python SDK v2 and Boto3)

  Both codecs are open models: Mimi (`kyutai/mimi`, CC BY 4.0) and EnCodec
  (`facebook/encodec_24khz`, MIT). Review each license before production use.

  ### IAM permissions (least privilege)

  The client that invokes the endpoint needs only `sagemaker:InvokeEndpoint` (or
  `sagemaker:InvokeEndpointAsync`) on your endpoint ARN. The SageMaker AI execution role
  attached to the endpoint needs `s3:GetObject`/`s3:PutObject` on your bucket, CloudWatch
  Logs write permissions, and the four Amazon ECR pull actions
  (`ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`,
  `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`) to pull the container image.

  ## Setup

  Package the handler into `model.tar.gz` (the `code/` layout SageMaker expects) and
  upload it to your bucket:
  
  ```bash
  pip install -r requirements.txt
  python src/package.py
  aws s3 cp model.tar.gz s3://<your-bucket>/neural-codec/model.tar.gz

  Deploy

  Configuration is read from environment variables; no account-specific values are
  committed to this repository.
  
  export AWS_REGION=us-east-1
  export SAGEMAKER_ROLE_ARN=arn:aws:iam::<account-id>:role/<your-execution-role>
  export MODEL_S3_URI=s3://<your-bucket>/neural-codec/model.tar.gz

  python src/deploy.py                 # real-time endpoint
  # or, for batch / large payloads:
  export ASYNC_OUTPUT_S3_URI=s3://<your-bucket>/neural-codec/async-output/
  python src/deploy_async.py           # asynchronous endpoint

  Usage

  Encode a clip to tokens and decode it back:
  
  ENDPOINT_NAME=neural-codec-rt python examples/invoke.py samples/source_poem.wav

  Benchmark both codecs at a matched bitrate band (Mimi 1.1 kbps / 8 codebooks vs
  EnCodec 1.5 kbps / 2 codebooks), reporting frame rate and warm encode/decode latency:

  ENDPOINT_NAME=neural-codec-rt python examples/benchmark.py samples/source_poem.wav

  Cost

  ml.c5.2xlarge is $0.408/hour in US East (N. Virginia) while the endpoint is                                                                  
  InService (real-time) or processing (asynchronous). An asynchronous endpoint paired
  with an Application Auto Scaling policy at minimum capacity 0 scales to zero when idle.
  Amazon S3 and CloudWatch usage for this walkthrough is negligible. Delete the endpoint
  when finished to stop charges.

  Cleanup
  
  Delete both endpoints, their endpoint configurations, and the models when finished;
  endpoints bill for as long as they are InService. Also remove any CloudWatch
  dashboard you created and the audio and model artifacts from Amazon S3 if no longer
  needed.

  Security
  
  The handler pins each model to a specific Hugging Face commit so an upstream re-push
  cannot change the served weights, and validates every request (allowlisted codec and
  task, bounded num_quantizers, checked audio and token payloads) before running a
  model, returning a clear error instead of a server-side failure. For production, scope
  IAM as described above, enable S3 default encryption (SSE-S3 or SSE-KMS) with Block
  Public Access, reach the endpoint through an AWS PrivateLink interface endpoint for
  SageMaker AI Runtime, and keep role ARNs and bucket URIs in environment variables or
  AWS Systems Manager Parameter Store.

  Project structure

  src/
    inference.py       Multi-codec handler (model_fn / input_fn / predict_fn / output_fn)
    requirements.txt   Container dependencies (soundfile, librosa)
    package.py         Build model.tar.gz with the code/ layout SageMaker expects
    deploy.py          Deploy a real-time endpoint
    deploy_async.py    Deploy an asynchronous endpoint (S3 in/out, larger payloads)
  examples/
    invoke.py          Encode a clip to tokens and decode it back
    benchmark.py       Compare Mimi and EnCodec at a matched bitrate band
  samples/             Mimi reconstructions at 1/2/4/8 codebooks (see samples/README.md)

  Limitations
  
  - The benchmark reports deterministic axes only (frame rate, bitrate, latency). It does
  not compute a cross-codec audio-quality score, as objective metrics such as PESQ and
  VisQOL are not valid for comparing differently-trained neural codecs. Judge quality by
  listening to the samples.
  - Codecs are compared at their closest available bitrate band, not an identical bitrate.
  - The Hugging Face DLC runs on Multi Model Server (MMS), whose default maximum request
  and response size is 6,553,500 bytes; deploy_async.py raises this for large payloads.

  References

  - Mimi (kyutai/mimi) (https://huggingface.co/kyutai/mimi)                                                                                    
  - EnCodec (facebook/encodec_24khz) (https://huggingface.co/facebook/encodec_24khz)
  - Amazon SageMaker AI asynchronous inference (https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html)
  - Hugging Face on AWS Deep Learning Containers (https://docs.aws.amazon.com/sagemaker/latest/dg/hugging-face.html)

  License

  This sample is licensed under the repository root LICENSE (MIT-0). Audio samples are                                                         
  synthesized from a public-domain source; see samples/README.md.

