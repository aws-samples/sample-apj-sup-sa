"""
Tiny stdlib proxy that adapts SageMaker's serving contract to vLLM's
OpenAI-compatible transcription endpoint.

SageMaker calls:
    GET  /ping         -> we report healthy once vLLM's /health is up
    POST /invocations  -> we forward audio to vLLM /v1/audio/transcriptions

Accepted /invocations request bodies (kept identical to the HF handler so the
same invoke.py / benchmark.py work unchanged):
    audio/*           raw audio bytes
    application/json  {"audio": "<base64>", "language": "hi", ...}

Returns: {"text": "..."}  (plus "chunks" if timestamps requested)

Uses only the Python standard library so the image needs no extra pip installs
(keeps the cross-platform build pure COPY -- no emulation).
"""

import base64
import json
import urllib.request
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VLLM_BASE = "http://127.0.0.1:8000"
SERVED_MODEL = "whisper"
PORT = 8080


def vllm_healthy():
    try:
        with urllib.request.urlopen(f"{VLLM_BASE}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _multipart(audio_bytes, fields):
    """Build a multipart/form-data body with one file part + text fields."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    body += (f"--{boundary}\r\n"
             'Content-Disposition: form-data; name="file"; filename="audio"\r\n'
             "Content-Type: application/octet-stream\r\n\r\n").encode()
    body += audio_bytes
    body += b"\r\n"
    for name, value in fields.items():
        if value is None:
            continue
        body += (f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                 f"{value}\r\n").encode()
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), boundary


def transcribe(audio_bytes, language=None, timestamps=False):
    fields = {
        "model": SERVED_MODEL,
        "response_format": "verbose_json" if timestamps else "json",
        "temperature": "0",
        "language": language,
    }
    if timestamps:
        fields["timestamp_granularities[]"] = "segment"
    body, boundary = _multipart(audio_bytes, fields)
    req = urllib.request.Request(
        f"{VLLM_BASE}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet access logs
        pass

    def _send(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/ping":
            self._send(200 if vllm_healthy() else 503, {})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invocations":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()

        language, timestamps = None, False
        try:
            if ctype == "application/json":
                payload = json.loads(raw)
                audio = base64.b64decode(payload["audio"])
                language = payload.get("language")
                timestamps = bool(payload.get("return_timestamps"))
            else:
                audio = raw
            result = transcribe(audio, language=language, timestamps=timestamps)
            out = {"text": result.get("text", "")}
            if "segments" in result:
                out["chunks"] = result["segments"]
            self._send(200, out)
        except urllib.error.HTTPError as e:
            self._send(e.code, {"error": e.read().decode("utf-8", "ignore")})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    print(f"SageMaker proxy listening on :{PORT}, forwarding to {VLLM_BASE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
