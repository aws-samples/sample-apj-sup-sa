"""
Loads model weights from /opt/ml/model/ (or wherever SM_MODEL_DIR points).

SageMaker mounts model.tar contents at /opt/ml/model/ (read-only) BEFORE the
container starts serving. You cannot hardcode weights into the Docker image —
that path is populated at deploy time.

Cold-start budget: total 8 minutes from `docker run` until /ping returns 200.
That includes S3 download of model.tar, extraction, and this function.
"""

import os
from pathlib import Path


def load_model(model_dir: str):
    """Load and return the model object.

    Called once at container startup. Blocks until the model is ready.
    The FastAPI /ping route returns 503 until this returns.

    Typical layouts inside model_dir:
        /opt/ml/model/model_weights/config.json
        /opt/ml/model/model_weights/model.safetensors
        /opt/ml/model/model_weights/tokenizer.json
        /opt/ml/model/config/inference_config.yaml
    """
    model_dir = Path(model_dir)
    if not model_dir.exists():
        raise RuntimeError(
            f"Model directory {model_dir} does not exist. "
            "SageMaker mounts model.tar contents here. Verify that "
            "ModelDataUrl points at a valid model.tar in S3 and that the "
            "packaging step (package_model.sh) ran successfully."
        )

    # ------------------------------------------------------------------
    # TODO — replace this block with your framework's load logic.
    #
    # !!! NETWORK ISOLATION !!!
    # The container has ZERO outbound network at runtime. This means any code
    # that reaches out to a remote hub or S3 will silently fail during model
    # load — and your endpoint will hit the 8-minute startup timeout.
    #
    # Common footguns:
    #   - transformers `AutoModel.from_pretrained("org/model-name")`
    #     — hub form, DOWNLOADS. Use `from_pretrained(<local-path>)` only.
    #     Set `local_files_only=True` for safety.
    #   - huggingface_hub `snapshot_download`, `hf_hub_download`
    #   - `torch.hub.load(...)` — downloads from github
    #   - Any git-lfs pull, wget, curl in the loader
    #   - Even the DEFAULT tokenizer cache: some tokenizers phone home to
    #     validate. Set `TRANSFORMERS_OFFLINE=1`, `HF_HUB_OFFLINE=1`, and
    #     `HF_DATASETS_OFFLINE=1` in the Dockerfile ENV.
    #
    # HuggingFace Transformers example (LOCAL PATH ONLY):
    #   from transformers import AutoModelForCausalLM, AutoTokenizer
    #   weights = model_dir / "model_weights"
    #   tokenizer = AutoTokenizer.from_pretrained(weights, local_files_only=True)
    #   model = AutoModelForCausalLM.from_pretrained(
    #       weights, torch_dtype="auto", device_map="auto", local_files_only=True,
    #   )
    #   return {"model": model, "tokenizer": tokenizer}
    #
    # Plain PyTorch example:
    #   import torch
    #   model = torch.load(model_dir / "model_weights" / "model.pt", map_location="cuda")
    #   model.eval()
    #   return model
    #
    # ONNX Runtime example:
    #   import onnxruntime as ort
    #   sess = ort.InferenceSession(str(model_dir / "model_weights" / "model.onnx"),
    #                               providers=["CUDAExecutionProvider"])
    #   return sess
    # ------------------------------------------------------------------

    raise NotImplementedError(
        "Replace load_model() with your framework's loading code."
    )
