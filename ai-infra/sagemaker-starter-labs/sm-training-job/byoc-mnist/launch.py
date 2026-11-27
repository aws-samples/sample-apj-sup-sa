"""Submit a BYOC (custom container) Training Job (run this on your local PC).

There is exactly one difference from hello-mnist (script mode):
  - hello-mnist: managed PyTorch DLC; the SDK injects source_code (train.py) into the container
  - byoc-mnist : runs your own ECR image (training_image) as-is. source_code is NOT passed.
    → The SDK does not override the entrypoint, so SageMaker runs 'docker run <image> train',
      which executes the 'train' executable inside the container (= true BYOC).

First, push the image to ECR:  ./build_and_push.sh
Then:
  uv run launch.py --dry-run   # check the image URI/settings without submitting to AWS
  uv run launch.py             # submit and wait for completion
"""

import gzip
import json
import os
import pathlib
import sys
import urllib.request

import boto3
from sagemaker.core.helper.session_helper import Session
from sagemaker.core.shapes.shapes import MetricDefinition, StoppingCondition
from sagemaker.core.training.configs import Compute, InputData
from sagemaker.train import ModelTrainer

HERE = pathlib.Path(__file__).parent

# ============================================================
# CONFIG — these are the only values you need to change.
# ============================================================
EPOCHS = 3
INSTANCE_TYPE = "ml.c6i.2xlarge"   # CPU instance, since the BYOC image is a CPU build
MAX_RUN_SECONDS = 3600             # Max run time (safety limit)
WAIT = True                        # If False, return right after submitting (async)

ECR_REPO = "byoc-mnist"            # Repository name created by build_and_push.sh
IMAGE_TAG = "latest"
IMAGE_URI = None                   # Set explicitly, or None = account/Region + ECR_REPO

ROLE = None                        # Required: set your SageMaker execution role ARN
REGION = None                      # AWS Region. None = environment/default session Region
# ============================================================


# torchvision's MNIST(download=False) expects the layout <root>/MNIST/raw/.
# Prepare the raw files locally in that structure; the SDK uploads them to S3 and SageMaker
# mounts them as-is at /opt/ml/input/data/training/ in the container.
MNIST_MIRROR = "https://ossci-datasets.s3.amazonaws.com/mnist"
MNIST_FILES = [
    "train-images-idx3-ubyte.gz",
    "train-labels-idx1-ubyte.gz",
    "t10k-images-idx3-ubyte.gz",
    "t10k-labels-idx1-ubyte.gz",
]


def prepare_mnist_data():
    """Prepare MNIST (decompressed) under local data/MNIST/raw/ and return that path.

    Skips files that already exist. Uses only the stdlib (no torchvision),
    which keeps the local environment light.
    """
    raw = HERE / "data" / "MNIST" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for fname in MNIST_FILES:
        gz_path = raw / fname
        out_path = raw / fname[:-3]  # decompressed file without .gz (this is what torchvision reads)
        if out_path.exists():
            continue
        print(f"  Downloading: {fname}")
        urllib.request.urlretrieve(f"{MNIST_MIRROR}/{fname}", gz_path)
        with gzip.open(gz_path, "rb") as fin, open(out_path, "wb") as fout:
            fout.write(fin.read())
        gz_path.unlink()  # delete the archive (torchvision only needs the decompressed file)
    return str(HERE / "data")


def resolve_role():
    """Require an explicitly selected execution role; never search account roles."""
    if not ROLE:
        raise SystemExit(
            "Set ROLE in launch.py CONFIG to your SageMaker execution role ARN. "
            "See the IAM execution role instructions in the README."
        )
    return ROLE


def main():
    dry_run = "--dry-run" in sys.argv[1:]

    # Validate before downloading data, uploading code, or submitting a job.
    role = resolve_role() if not dry_run else ROLE
    region = (
        REGION
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or boto3.session.Session().region_name
    )
    if not region:
        raise SystemExit("Could not find a region. Set REGION in CONFIG or run 'aws configure'.")

    account = boto3.client("sts").get_caller_identity()["Account"]
    image_uri = IMAGE_URI or f"{account}.dkr.ecr.{region}.amazonaws.com/{ECR_REPO}:{IMAGE_TAG}"

    print(f"region   : {region}")
    print(f"image    : {image_uri}   (your custom ECR image)")
    print(f"instance : {INSTANCE_TYPE}")
    print(f"epochs   : {EPOCHS}")

    if dry_run:
        print("[dry-run] Not submitted. Configuration check complete.")
        print("If the image is not in ECR yet, run first: ./build_and_push.sh")
        return

    session = Session(boto_session=boto3.Session(region_name=region))

    # Data prep: build MNIST locally and pass it as the 'training' channel.
    # Given a local path, the SDK uploads it to S3 and wires it up as the channel.
    print("\nPreparing MNIST data...")
    data_dir = prepare_mnist_data()
    training_channel = InputData(channel_name="training", data_source=data_dir)

    # Key point: training_image is your own image, and no source_code → true BYOC
    trainer = ModelTrainer(
        training_image=image_uri,
        compute=Compute(instance_type=INSTANCE_TYPE, instance_count=1),
        stopping_condition=StoppingCondition(max_runtime_in_seconds=MAX_RUN_SECONDS),
        hyperparameters={"epochs": EPOCHS},  # delivered as /opt/ml/input/config/hyperparameters.json
        base_job_name="byoc-mnist",
        role=role,
        sagemaker_session=session,
    ).with_metric_definitions(
        [MetricDefinition(name="test:accuracy", regex=r"test_accuracy=([0-9\.]+)")]
    )

    # Inject the 'training' channel → mounted at /opt/ml/input/data/training/ in the container
    print(f"Submitting... (wait={WAIT})")
    trainer.train(input_data_config=[training_channel], wait=WAIT, logs=WAIT)

    # sagemaker-train is pinned to 1.18.0: train() returns no job and exposes no
    # public job accessor. Recheck this private access when upgrading the SDK.
    job = trainer._latest_training_job
    print(f"\n✅ Training Job: {job.training_job_name}")

    (HERE / ".last_job.json").write_text(
        json.dumps({"job_name": job.training_job_name, "region": region})
    )

    if WAIT:
        job.refresh()
        if job.model_artifacts:
            print(f"Model: {job.model_artifacts.s3_model_artifacts}")
    else:
        print("Check status: uv run jobstatus.py")


if __name__ == "__main__":
    main()
