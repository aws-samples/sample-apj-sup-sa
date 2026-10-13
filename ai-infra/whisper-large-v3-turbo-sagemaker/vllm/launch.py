"""
Python entrypoint for the vLLM-based Whisper SageMaker container.

Uses python3 (guaranteed present in the vLLM image) instead of bash to avoid any
shell-availability issues at container exec time. Starts the SageMaker proxy and
the vLLM OpenAI server, and tracks the vLLM process for the container lifecycle.
"""

import os
import subprocess
import sys

print("[launch] python:", sys.version, flush=True)
print("[launch] argv:", sys.argv, flush=True)
try:
    print("[launch] /opt/ml:", os.listdir("/opt/ml"), flush=True)
    print("[launch] /opt/ml/model/snapshot:", os.listdir("/opt/ml/model/snapshot"), flush=True)
except Exception as e:  # noqa: BLE001
    print("[launch] listing error:", e, flush=True)

# Ensure audio decode deps (no-op if already present in the base image).
try:
    import librosa  # noqa: F401
    import soundfile  # noqa: F401
    print("[launch] audio deps present", flush=True)
except Exception:
    print("[launch] installing audio deps...", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir",
                    "librosa==0.10.2", "soundfile==0.12.1"], check=False)

# Start the SageMaker /ping + /invocations proxy (port 8080).
print("[launch] starting proxy on :8080", flush=True)
proxy = subprocess.Popen([sys.executable, "/opt/ml/proxy.py"])

# Run the vLLM OpenAI server (port 8000) in the foreground.
vllm_cmd = [
    sys.executable, "-m", "vllm.entrypoints.openai.api_server",
    "--model", "/opt/ml/model/snapshot",
    "--served-model-name", "whisper",
    "--host", "127.0.0.1",
    "--port", "8000",
    "--max-model-len", "448",
    "--gpu-memory-utilization", "0.9",
]
print("[launch] starting vLLM:", " ".join(vllm_cmd), flush=True)
rc = subprocess.call(vllm_cmd)
print(f"[launch] vLLM exited with rc={rc}", flush=True)
proxy.terminate()
sys.exit(rc)
