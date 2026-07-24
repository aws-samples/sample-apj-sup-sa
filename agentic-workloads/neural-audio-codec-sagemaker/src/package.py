"""
Package the inference handler into model.tar.gz for SageMaker AI.

SageMaker expects custom inference code under a top-level code/ directory:

  model.tar.gz
  └── code/
      ├── inference.py
      └── requirements.txt

The codec weights are not bundled; they download from the Hugging Face Hub at
container start (pinned to a fixed revision in inference.py). Upload the resulting
model.tar.gz to Amazon S3 and pass its URI as MODEL_S3_URI to deploy.py.

Usage:
  python src/package.py            # writes model.tar.gz in the current directory
"""
import os
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = "model.tar.gz"

with tarfile.open(OUTPUT, "w:gz") as tar:
    for name in ("inference.py", "requirements.txt"):
        tar.add(os.path.join(HERE, name), arcname=os.path.join("code", name))

print(f"Wrote {OUTPUT} (code/inference.py, code/requirements.txt)")
print("Next: upload to S3 and set MODEL_S3_URI, then run src/deploy.py")
