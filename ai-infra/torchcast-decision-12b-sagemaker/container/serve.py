"""SageMaker AI entry point for torchcast-decision-12b.

Starts vLLM on 127.0.0.1:8890 with the settings TorchCast publishes for the model (BF16, 16,384-token context,
prefix caching on), then serves TorchCast's decision server on SageMaker's port:

    GET  /ping         200 once vLLM answers /health, 503 before that
    POST /invocations  the /v1/systemone request body, answered exactly as decision_server.py answers it

SageMaker checks every caller with IAM (SigV4) before a request reaches the container, so the decision server's own
API key is not used.

Two changes to how decision_server.py runs, both on the server object and neither in TorchCast's files:

* TCP_NODELAY on accepted connections (NODELAY=on, the default). The handler writes the headers and the body in
  two sends. On a kept-alive connection, which is how SageMaker reaches the container, Nagle's algorithm holds the
  second send until the peer's delayed ACK arrives, adding 40-60 ms to every response. NODELAY=off runs it as
  shipped.
* A listen backlog of 1024 instead of socketserver's default of 5. With more new connections arriving at once than
  the backlog holds, the kernel drops them, and SageMaker returned HTTP 424 at 100 or more concurrent requests.

Weights come from /opt/ml/model when the model is created with ModelDataSource (staged in S3 in the same Region),
otherwise from Hugging Face at the pinned revision.

Environment (defaults in brackets): TP [1]  MAX_MODEL_LEN [16384]  GPU_UTIL [0.90]  MAX_PARALLEL [256]
    PREFIX_CACHE [on]  NODELAY [on]  HF_MODEL_ID [torchcast-ai/torchcast-decision-12b]  HF_REVISION [49107b55...]
"""
import os
# vLLM is started with a fixed argument list, never through a shell.
import subprocess  # nosec B404
import sys
import threading
import time
import urllib.request

VLLM_PORT = 8890
MODEL_DIR = "/opt/ml/model"
SERVED_NAME = "torchcast-decision-12b"
HF_MODEL_ID = "torchcast-ai/torchcast-decision-12b"
HF_REVISION = "49107b5589bf2dc4ec3f5acd54316971dd5978b1"   # the revision TorchCast's v1.0.0 release pins  # pragma: allowlist secret (public git commit / file checksum, not a secret)

# decision_server.py and torchcast_shim.py read these at import time.
os.environ.setdefault("SHIM_VLLM", f"http://127.0.0.1:{VLLM_PORT}/v1/chat/completions")
os.environ.setdefault("SHIM_MODEL", SERVED_NAME)
# SageMaker gives a real-time invocation 60 s. The shim's default upstream timeout is 180 s; 50 s returns an error
# to the caller before SageMaker gives up on the request.
os.environ.setdefault("SHIM_TIMEOUT", "50")
# SageMaker's front end connects to the container on its network interface, so the server must listen on all
# interfaces inside the container. Only SageMaker can reach that port; vLLM itself stays on loopback.
os.environ["TORCHCAST_HOST"] = "0.0.0.0"  # nosec B104  # noqa: S104
os.environ["TORCHCAST_PORT"] = os.environ.get("SAGEMAKER_BIND_TO_PORT", "8080")
os.environ["TORCHCAST_MAX_PARALLEL"] = os.environ.get("MAX_PARALLEL", "256")
os.environ.setdefault("TORCHCAST_MAX_BODY", str(1024 * 1024))   # 16,384 tokens of text fit well inside 1 MiB
os.environ["TORCHCAST_API_KEY"] = ""
os.environ["TORCHCAST_ALLOW_NO_KEY"] = "1"   # SageMaker authenticates callers with IAM before they reach us
sys.path.insert(0, "/opt/program/serving")


def vllm_command():
    if os.path.isfile(os.path.join(MODEL_DIR, "config.json")):
        source = [MODEL_DIR]
    else:
        rev = os.environ.get("HF_REVISION", HF_REVISION)
        source = [os.environ.get("HF_MODEL_ID", HF_MODEL_ID), "--revision", rev, "--tokenizer-revision", rev]
    cmd = ["vllm", "serve", *source, "--served-model-name", SERVED_NAME, "--host", "127.0.0.1",
           "--port", str(VLLM_PORT), "--max-model-len", os.environ.get("MAX_MODEL_LEN", "16384"),
           "--gpu-memory-utilization", os.environ.get("GPU_UTIL", "0.90"),
           "--tensor-parallel-size", os.environ.get("TP", "1")]
    if os.environ.get("PREFIX_CACHE", "on") == "off":
        cmd.append("--no-enable-prefix-caching")
    return cmd


def vllm_healthy():
    try:
        # A fixed loopback URL.
        with urllib.request.urlopen(f"http://127.0.0.1:{VLLM_PORT}/health", timeout=2) as r:  # nosec B310 nosemgrep
            return r.status == 200
    except OSError:
        return False


def main():
    vllm = subprocess.Popen(vllm_command())  # nosec B603  # noqa: S603
    import decision_server as ds  # reads the TORCHCAST_* and SHIM_* settings above

    class Handler(ds.Handler):
        disable_nagle_algorithm = os.environ.get("NODELAY", "on") != "off"

        def do_GET(self):
            if self.path.startswith("/ping"):
                ok = vllm.poll() is None and vllm_healthy()
                return self._send(200 if ok else 503, {"status": "ok" if ok else "loading"})
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/invocations"):
                self.path = "/v1/systemone"
            return super().do_POST()

    ds.ThreadingHTTPServer.request_queue_size = 1024
    srv = ds.ThreadingHTTPServer((ds.HOST, ds.PORT), Handler)
    srv.daemon_threads = True
    print(f"decision server on {ds.HOST}:{ds.PORT}, vLLM pid {vllm.pid}, "
          f"readout temperatures {ds.shim.describe_temperatures()}", flush=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # The container lives as long as vLLM does: if vLLM exits, so does the container, and SageMaker replaces it.
    while vllm.poll() is None:
        time.sleep(5)
    srv.shutdown()
    sys.exit(f"vLLM exited with code {vllm.returncode}")


if __name__ == "__main__":
    main()
