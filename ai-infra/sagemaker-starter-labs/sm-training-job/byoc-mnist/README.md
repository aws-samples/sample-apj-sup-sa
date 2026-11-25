English | [한국어](README.ko.md)

# byoc-mnist — SageMaker BYOC (Custom Container) Example

A SageMaker Training Job example that trains the same MNIST model using a Docker image you build yourself.
It is the BYOC (Bring Your Own Container) counterpart to the neighboring `hello-mnist` folder
(managed container + script mode). Instead of using a container provided by AWS, you hand SageMaker
a container you built from scratch and train with it.

---

## hello-mnist (script mode) vs byoc-mnist (BYOC)

| | hello-mnist (script mode) | byoc-mnist (BYOC) |
|---|---|---|
| Container | AWS-managed PyTorch DLC | **Your own image (ECR)** |
| What you provide | Only the `train.py` code | **The entire Docker image** |
| Docker build | Not needed | Build yourself & push to ECR |
| torch installation | Already in the DLC | Installed in your Dockerfile |
| SageMaker execution | A wrapper injected by the SDK runs `train.py` | `docker run <image> train` as-is |
| When to use | When a supported framework is enough (most cases) | When you need a special runtime/OS/dependencies |

In script mode, AWS manages the container and how it runs; with BYOC, you follow the container
contract yourself. Put the other way around, building a BYOC container once shows you what SageMaker
was doing behind the scenes for you in script mode.

In practice, you rarely need to go as far as BYOC. There are three ways to work with containers, and
most cases are covered by the first two, which the next section explains. For learning purposes, this
example builds the lowest-level option, BYOC, by hand.

---

## Three approaches: script mode · extending a DLC · BYOC

There are three ways to work with training containers in SageMaker. The further down the list you go,
the more effort is required and the more room there is for things to break. Before building a BYOC
container, it is better to first check whether one of the first two approaches solves your problem.

| | Script mode | Extending a DLC | BYOC (this example) |
|---|---|---|---|
| What you do | Hand over code only | Add packages to a prebuilt image | Build the image from scratch |
| Dockerfile | None | `FROM <prebuilt DLC>` + a few lines | Everything yourself |
| CUDA · drivers · serving stack | Managed by AWS | Managed by AWS (inherited as-is) | Yourself |
| Difficulty / risk | Lowest | Low | High |

Most version mismatch problems are solved by the middle option, extending a DLC. What extending a DLC
means and how to find prebuilt images are covered in [Appendix A](#appendix-a-extending-a-dlc) and [Appendix B](#appendix-b-finding-prebuilt-images),
and the contract a BYOC container must follow is summarized in [Appendix C](#appendix-c-byoc-container-contract).

---

## File structure

```
byoc-mnist/
├─ pyproject.toml        # Local dependencies (sagemaker, boto3) — managed by uv
├─ build_and_push.sh     # Build image (linux/amd64) → push to ECR
├─ launch.py             # Prepare MNIST + submit Training Job with the custom image (CONFIG block)
├─ jobstatus.py          # Check job status/results
├─ data/                 # Where MNIST is downloaded when launch.py runs (git-ignored)
└─ container/            # What goes inside the Docker image
   ├─ Dockerfile         #    python:3.11-slim + torch, train on PATH
   └─ train              #    Training program executed by SageMaker (file name is literally 'train')
```

---

## Prerequisites

- **Docker Desktop** must be running (for building the image)
- **AWS credentials** + default region (verify with `aws configure`, `aws sts get-caller-identity`)
- **IAM execution role** — `launch.py` automatically detects `AmazonSageMaker-ExecutionRole-*` in your account.
  If none exists, create a SageMaker execution role in IAM (`AmazonSageMakerFullAccess` is sufficient)
  and set its ARN in `ROLE` in the CONFIG block of `launch.py`.
- The role must be able to pull images from ECR (included in `AmazonSageMakerFullAccess`).

---

## How to run

Run all commands from this folder (`byoc-mnist/`).

### Step 0. Set up the local environment (first time only)

```bash
uv sync
```

### Step 1. Build the image & push to ECR

```bash
./build_and_push.sh
```

- Builds the image from the Dockerfile in `container/` and pushes it to the ECR repository (`byoc-mnist`).
- SageMaker training instances are x86_64 (amd64), so the image is built with `--platform linux/amd64`
  (already handled in the build script).
- When finished, it prints the image URI:
  `<account>.dkr.ecr.<region>.amazonaws.com/byoc-mnist:latest`

### Step 2. Check the configuration (recommended, no AWS charges)

```bash
uv run launch.py --dry-run
```

Prints only the image URI, instance, and region without submitting anything.

### Step 3. Submit the Training Job

```bash
uv run launch.py
```

It first prepares MNIST in the local `data/` directory and passes it as the `training` channel (the SDK handles the S3 upload).
Because only your image (`training_image`) is passed to `ModelTrainer` and no `source_code` is given,
SageMaker runs `docker run <image> train`, and the container's `train` runs as-is.
By default, it waits until training finishes and streams CloudWatch logs in real time.

### Step 4. Check status / results

```bash
uv run jobstatus.py                    # Check the most recent job
uv run jobstatus.py <job-name>         # Check a specific job
```

Once the job reaches `Completed`, it prints the S3 path of the model artifact (`.../output/model.tar.gz`).

---

## Changing settings

All settings live in one place: the CONFIG block at the top of `launch.py`.

```python
EPOCHS = 3
INSTANCE_TYPE = "ml.c6i.2xlarge"   # The image is a CPU build, so use a CPU instance
MAX_RUN_SECONDS = 3600
WAIT = True                        # If False, return immediately after submission (async)
ECR_REPO = "byoc-mnist"            # Must match the repository name in build_and_push.sh
IMAGE_TAG = "latest"
IMAGE_URI = None                   # Can be set explicitly. If None, built from account/region + ECR_REPO
ROLE = None                        # If None, auto-detected
REGION = None                      # If None, uses the default session region
```

---

## How it works

1. `build_and_push.sh` builds `container/` into a linux/amd64 image and pushes it to ECR.
2. `launch.py` calls `ModelTrainer.train()` with that image URI. Because no `source_code` is given,
   it runs as pure BYOC.
3. `launch.py` prepares MNIST locally and passes it as the `training` channel; the SDK uploads it to S3,
   and SageMaker launches an instance and runs `docker run <image> train`. This is when `container/train`
   runs.
4. `train` reads `/opt/ml/input/config/hyperparameters.json`, trains on the MNIST data mounted at
   `/opt/ml/input/data/training/`, and saves the model to `/opt/ml/model/`.
5. SageMaker packages `/opt/ml/model` into `model.tar.gz` and uploads it to S3, and the instance
   shuts down automatically (you are billed only for the training time).

---

## Data flow (preparation, S3 upload, job submission)

When you run `uv run launch.py`, the steps below happen in order. The first two steps run on your local PC,
and from the third step onward everything runs on AWS.

```
 [Local PC]                                                 [AWS]

 1) Prepare MNIST   2) Upload to S3      3) Submit job      4) Mount channel + train
 ┌──────────────┐   ┌────────────────┐   ┌──────────────┐   ┌────────────────────────┐
 │ data/MNIST/  │   │ s3://<default  │   │ calls        │   │ mounted at             │
 │   raw/*.ubyte│──▶│  bucket>/      │──▶│ create_      │──▶│ /opt/ml/input/data/    │
 │              │   │ byoc-mnist/    │   │ training_job │   │   training/            │
 │              │   │ input/training │   │              │   │ → train reads from here│
 └──────────────┘   └────────────────┘   └──────────────┘   └────────────────────────┘
  My code            Done by the SDK      Done by the SDK    Done by SageMaker
```

### 1) Prepare MNIST (`prepare_mnist_data`)

`launch.py` downloads the four raw MNIST files into the local `data/MNIST/raw/` directory and decompresses them.
This directory structure exactly matches the layout (`<root>/MNIST/raw/`) that torchvision inside the
container expects when reading with `download=False`. The function returns the prepared local path as a string.

### 2)–3) Specifying the channel and submitting

```python
training_channel = InputData(channel_name="training", data_source=data_dir)
trainer.train(input_data_config=[training_channel], ...)
```

The key point here is that `data_source` is given a local path, not an S3 URI. SageMaker runs on
training instances in AWS, so it cannot see the files on your local PC. That is why the SDK
automatically inserts an S3 upload when `train()` is called.

### The SDK handles the S3 upload for you

The SDK branches on the value of `data_source`. If it is an `s3://...` URI, it is used as-is; if it is a
local path, the SDK uploads it to S3 and then configures the channel with that S3 URI. This example uses the latter.

For a local path, the upload location is determined automatically by convention.

```
s3://<account's default SageMaker bucket>/<base_job_name>/input/<channel_name>/
  e.g.) s3://sagemaker-ap-northeast-2-<account>/byoc-mnist/input/training/
```

You do not need to create a bucket. The SDK uses the per-account, per-region default bucket
`sagemaker-<region>-<account>` and creates it if it does not exist. The path is formed by appending `input/`
and the channel name (`training`) to `base_job_name` (`"byoc-mnist"`). The actual upload is just the SDK
uploading files with boto3 internally, so even though your code contains neither a bucket name nor an
`upload` call, simply passing a local path to `data_source` takes care of it.

Once the upload finishes, the SDK replaces the channel's location with the newly created S3 URI and calls
`create_training_job`. This channel is included in the request's `InputDataConfig` (if there are no channels
at all, this value is empty and API validation fails, so even BYOC requires at least one input channel).

### 4) The container receives the channel

SageMaker launches an instance and downloads the `training` channel data from S3 into the container's
`/opt/ml/input/data/training/`, mounting it there. Then `docker run <image> train`
runs, and `train` reads MNIST from that path and trains.

In summary: when you pass a local path to `InputData`, the SDK uploads that folder to
`<job name>/input/<channel name>/` in the default SageMaker bucket, tells the job about that S3 location,
and SageMaker delivers it into the container's `/opt/ml/input/data/<channel name>/` at training time.

---

## Next step: inference container

This example covers training only. To add inference to the same image, add a `serve` executable.
When SageMaker invokes `docker run <image> serve`, it should start a web server on port 8080 and provide
two endpoints. `GET /ping` is a health check that returns HTTP 200 once the model is loaded (2-second
timeout per request), and `POST /invocations` accepts a request body and returns the inference result
(by default, responses must be returned within 60 seconds). SageMaker extracts the model into
`/opt/ml/model` in advance, so you can read it from there (read-only).

For something like MNIST, running a single Flask app on port 8080 that implements just these two endpoints
is enough to satisfy the contract (nginx/gunicorn/MMS are not required). Deploy with
`sagemaker.model.Model(image_uri=..., model_data=<S3 model.tar.gz>).deploy(...)`.
For contract details, see the [inference container documentation](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html).

---

## Common issues

| Symptom | Cause / fix |
|---|---|
| `exec format error` (job fails immediately) | Image architecture mismatch. Build with `--platform linux/amd64` (SageMaker is x86_64) |
| `train: not found` / permission denied | `train` is not on PATH or is not executable. Check `chmod 755` + `ENV PATH` in the Dockerfile |
| Model is not uploaded to S3 | The model was saved somewhere other than `/opt/ml/model/` |
| Hyperparameters have no effect | JSON values are strings, so you must cast them, e.g. `int(hp["epochs"])` |
| Job is `Failed` | Check `FailureReason` with `uv run jobstatus.py`, then check the stack trace in CloudWatch Logs |
| ECR pull permission error | The execution role needs ECR read permissions (included in `AmazonSageMakerFullAccess`) |

---

## References

- BYOC training container contract: https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html
- `/opt/ml` directory structure: https://docs.aws.amazon.com/sagemaker/latest/dg/amazon-sagemaker-toolkits.html
- Inference container contract (`serve`, `/ping`, `/invocations`): https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html
- When to use BYOC: https://docs.aws.amazon.com/sagemaker/latest/dg/docker-containers-create.html

---

## Appendix A. Extending a DLC

A DLC (Deep Learning Container) is a training Docker image that AWS has prebuilt. It already includes a
specific version of PyTorch along with the matching CUDA, cuDNN, and GPU driver configuration, the SageMaker
container contract (input/output path handling, the training launch wrapper), and a serving stack for inference.

Extending a DLC means using this image as the base with `FROM` and adding only the packages you are missing.
You keep the foundation AWS has already validated and add just one thin layer on top.

```dockerfile
# Example: use AWS's PyTorch 2.7.1 GPU image as the base, as-is
FROM 763104351884.dkr.ecr.ap-northeast-2.amazonaws.com/pytorch-training:2.7.1-gpu-py312

# Install only the extra packages you need (CUDA, drivers, and PyTorch are already present)
RUN pip install --no-cache-dir transformers==4.44 accelerate
```

After pushing the resulting image to ECR, you can use it like script mode (the SDK runs the code specified
by `SAGEMAKER_PROGRAM`) or like BYOC. Because you reuse the CUDA/driver combination AWS has already matched,
you avoid problems where things fail to run on GPUs due to version mismatches, and when the base image is
updated, you get security patches just by bumping the `FROM` tag. The only thing you touch directly is a few
lines of pip packages, so there are few places for things to break.

Most version mismatch cases are solved this way. For example, if the latest prebuilt image ships PyTorch
2.7 but you need 2.7.2, you can `FROM` it and then overwrite just that version with `pip install torch==2.7.2`.
If the job starts with `FROM` and ends with `pip install`, it is a DLC extension. Go to BYOC only when that
is not enough — when there is no prebuilt image to use as a base in the first place, when you need to change
CUDA or the OS itself, or when you need to bring in an existing production image as-is.

---

## Appendix B. Finding prebuilt images

There are three ways to find the image URI to use in `FROM`.

To see the full list at a glance, check
[available_images.md](https://github.com/aws/deep-learning-containers/blob/master/available_images.md),
maintained by AWS. It organizes tags into tables by framework, version, CPU/GPU, Python, CUDA, and purpose
(training/inference).

The image account and URI differ by region, so to get the exact value for your region, select your region in
the official documentation's
[Docker Registry Paths list](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-algo-docker-registry-paths.html).

The fastest way is to query it directly with the SDK (already installed in this project).
`image_uris.retrieve` computes the correct URI for your region, so you can immediately tell whether the
version you want is available.

```python
from sagemaker.core import image_uris

# Get the exact URI for a specific version
print(image_uris.retrieve(
    framework="pytorch", region="ap-northeast-2", version="2.7.1",
    image_scope="training", instance_type="ml.g5.xlarge",  # GPU instance → GPU image
))
# → 763104351884.dkr.ecr.ap-northeast-2.amazonaws.com/pytorch-training:2.7.1-gpu-py312

# Full list of PyTorch versions supported for training
cfg = image_uris.config_for_framework("pytorch")
print(sorted(cfg["training"]["versions"]))
```

`763104351884` is the official AWS DLC image account (it is shared by most regions, but some differ, so check
the documentation above). If `instance_type` is a CPU type, a `-cpu-` image is returned; if it is a GPU type,
a `-gpu-` image is returned.

---

## Appendix C. BYOC container contract

When SageMaker starts training, it runs the following
([documentation](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html)):

```
docker run <image> train
```

In other words, an executable named `train` (which must be on PATH and executable) runs. This `train` just
needs to handle the paths below according to the contract. SageMaker creates the files themselves inside the
container. The source documentation for each path is listed in the right-hand column.

| Path | Purpose | Official docs |
|---|---|---|
| `/opt/ml/input/config/hyperparameters.json` | Hyperparameters (all values are strings, so casting is required) | [Input data & hyperparameters](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-running-container.html) |
| `/opt/ml/input/data/training/` | `training` input channel — `launch.py` uploads MNIST to S3 and it is mounted here | [Input data channels](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-running-container.html) · [Directory structure](https://docs.aws.amazon.com/sagemaker/latest/dg/amazon-sagemaker-toolkits.html) |
| `/opt/ml/model/` | Anything saved here is packaged by SageMaker into `model.tar.gz` and uploaded to S3 | [Model & output paths](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-output.html) |
| `/opt/ml/output/failure` | On failure, the reason written here is shown as `FailureReason` in the console (first 1024 characters) | [Failure file](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-output.html) |

Success or failure is communicated through the exit code. `exit 0` means success (Completed); any non-zero
value means failure (Failed)
([success/failure signal documentation](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-signal-success-failure.html)).

There is also a separate contract for inference. The container is run with `docker run <image> serve` and
provides `GET /ping` (health check) and `POST /invocations` (inference) on port 8080; details are summarized
in "Next step: inference container" above.
