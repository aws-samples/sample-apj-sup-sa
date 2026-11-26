#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Deterministic stand-in for `strands-decider serve`, used by the regression tests.

Speaks the same wire format as strands_decider/server.py and schema.py:
  POST /v1/systemone  {"state": ..., "model": ..., "questions": {name: {type, instructions, criteria}}}
  GET  /health        {"status": "ok", "max_length": 4096, ...}

Rules, so expected output is stable:
  state text = the state rendered like the decider does (str as is, JSON with indent=2)
  noul   -> 0.9 if the LAST word of `instructions` appears (case-insensitively) in the state text, else 0.1
  choice -> the first option whose name appears in the state text, else index len(state text) % n;
            probabilities 0.8 on the pick, the rest split evenly; confidence (n*p_max - 1) / (n - 1)
  score  -> level index len(state text) % n, one-hot, confidence 1.0
  instructions containing "trigger422" -> HTTP 422 (the server's caller-error path)
  instructions containing "slowpoke"   -> the response is delayed 3 s
  instructions containing "flaky"      -> HTTP 503 on the first request with that body, 200 after
  state text containing "TOOLONG"      -> usage.input_tokens = max_length (the server's truncation cap)
  usage.input_tokens otherwise = len(state text + instructions) // 4
  Paths under /auth/ require "Authorization: Bearer test-key"; everything else has no auth,
  like the real server.
Run: python3 test/mock_decider.py [port]   (default 8766)
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_LENGTH = 4096
SEEN_FLAKY = set()


def render(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    return json.dumps(content, indent=2, ensure_ascii=False)


def invalid(msg):
    return 422, {"detail": msg}


def answer(state_text, q):
    kind, crit = q.get("type"), q.get("criteria")
    instr = render(q.get("instructions"))
    if kind == "noul":
        if crit is not None and not set(crit) <= {"true", "false"}:
            return None, "noul criteria keys must be a subset of {'true', 'false'}"
        words = instr.split()
        needle = words[-1].strip("?.!").lower() if words else ""
        return {"type": "noul", "noul": 0.9 if needle and needle in state_text.lower() else 0.1}, None
    if kind == "choice":
        if not isinstance(crit, dict) or len(crit) < 2:
            return None, "choice requires at least 2 options"
        if not all(isinstance(v, str) for v in crit.values()):
            return None, "choice criteria values must be strings"
        opts = list(crit)
        k = next((i for i, o in enumerate(opts) if o.lower() in state_text.lower()), len(state_text) % len(opts))
        rest = 0.2 / (len(opts) - 1)
        probs = {o: (0.8 if i == k else rest) for i, o in enumerate(opts)}
        conf = (len(opts) * 0.8 - 1) / (len(opts) - 1)
        return {"type": "choice", "choice": opts[k], "probabilities": probs, "confidence": round(conf, 4)}, None
    if kind == "score":
        if not isinstance(crit, list) or not 2 <= len(crit) <= 10:
            return None, "score requires between 2 and 10 levels"
        k = len(state_text) % len(crit)
        return {"type": "score", "score": float(k), "legend": {str(i): c for i, c in enumerate(crit)},
                "probabilities": {str(i): (1.0 if i == k else 0.0) for i in range(len(crit))},
                "confidence": 1.0}, None
    return None, "unknown question type %r" % kind


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # keep-alive, so connection reuse is exercised

    def log_message(self, *a):
        pass

    def _authorised(self):
        return not self.path.startswith("/auth/") or self.headers.get("Authorization") == "Bearer test-key"

    def do_GET(self):
        if not self._authorised():
            return self._send(401, {"detail": "unauthorised"})
        if self.path.endswith("/health"):
            return self._send(200, {"status": "ok", "model": "decider-mock", "checkpoint": "mock",
                                    "base_model": "mock", "max_length": MAX_LENGTH, "device": "cpu"})
        self._send(404, {"detail": "not found"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not self._authorised():
            return self._send(401, {"detail": "unauthorised"})
        if not self.path.endswith("/v1/systemone"):
            return self._send(404, {"detail": "not found"})
        try:
            req = json.loads(body)
        except ValueError:
            return self._send(*invalid("body is not JSON"))
        questions = req.get("questions")
        if "state" not in req or not isinstance(questions, dict) or not questions:
            return self._send(*invalid("state and at least one question are required"))
        state_text = render(req["state"])
        answers, instr_len = {}, 0
        for name, q in questions.items():
            instr = render(q.get("instructions"))
            instr_len += len(instr)
            if "trigger422" in instr:
                return self._send(*invalid("mock validation failure"))
            if "slowpoke" in instr:
                time.sleep(3)
            if "flaky" in instr and body not in SEEN_FLAKY:
                SEEN_FLAKY.add(body)
                return self._send(503, {"detail": "mock: try again"})
            a, err = answer(state_text, q)
            if err:
                return self._send(*invalid(err))
            answers[name] = a
        tokens = MAX_LENGTH if "TOOLONG" in state_text else (len(state_text) + instr_len) // 4
        self._send(200, {"model": "decider-mock", "answers": answers,
                         "usage": {"input_tokens": tokens, "output_tokens": len(answers)},
                         "latency_ms": 1.0})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
