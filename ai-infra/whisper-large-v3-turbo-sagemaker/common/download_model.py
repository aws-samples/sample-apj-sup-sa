"""
Download the whisper-large-v3-turbo snapshot into <repo>/model_artifacts/snapshot
so it can be baked into either container image (huggingface/ or vllm/).

Only safetensors weights + config/tokenizer/processor files are pulled; duplicate
.bin/.pt weights are skipped to keep the artifact smaller.

Usage:
    python common/download_model.py
"""

from pathlib import Path

from huggingface_hub import snapshot_download

MODEL_ID = "openai/whisper-large-v3-turbo"
# Repo root is the parent of common/ -> keep the snapshot shared at the top level.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEST = REPO_ROOT / "model_artifacts" / "snapshot"

ALLOW_PATTERNS = [
    "*.json", "*.txt", "*.safetensors", "merges.txt", "vocab.json",
    "tokenizer*", "preprocessor_config.json", "generation_config.json",
    "normalizer.json", "added_tokens.json", "special_tokens_map.json",
]
IGNORE_PATTERNS = ["*.bin", "*.pt", "*.pth", "*.h5", "*.onnx", "*.msgpack", "*.tflite"]


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {MODEL_ID} -> {DEST}")
    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=str(DEST),
        allow_patterns=ALLOW_PATTERNS,
        ignore_patterns=IGNORE_PATTERNS,
    )
    if not (DEST / "config.json").exists():
        raise SystemExit("config.json missing after download; aborting.")
    print("Download complete.")


if __name__ == "__main__":
    main()
