"""CLM System One as a SageMaker /invocations handler inside the AWS vLLM DLC.

vLLM serves the frozen Qwen3-8B encoder (``--runner pooling``) on port 8080. This
handler replaces vLLM's default ``/invocations`` route with the CLM engine from the
``contrastive-lm`` package: it builds the state and candidate texts, gets their
embeddings from vLLM's own ``/v1/embeddings`` on 127.0.0.1, runs the two 75 MB
projection heads and returns the answer distributions. ``/ping`` stays vLLM's.

Request body (JSON):
    {"op": "systemone", "state": ..., "questions": {id: question}, "model"?, "temperature"?}
    {"op": "rank", "context": ..., "question": ..., "answers": [...], "model"?, "temperature"?}

Files next to this one (see the notebook): ``clm/`` (the contrastive-lm package)
and ``CLM_v0.1-8B.pt`` (the heads).
"""
import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # the SageMaker loader imports this file by path, not as a package

import model_hosting_container_standards.sagemaker as sagemaker_standards  # noqa: E402
import torch  # noqa: E402
from fastapi import Request, Response  # noqa: E402

from clm.embedder import Embedder, EmbedderError  # noqa: E402
from clm.engine import DEFAULT_MODEL, Engine, ModelNotFound  # noqa: E402


# The heads are small MLPs, so they run on the host CPU. That keeps the whole GPU for
# vLLM (no second CUDA context in this process, no memory outside vLLM's budget).
DEVICE = os.environ.get("CLM_DEVICE", "cpu")
torch.set_num_threads(int(os.environ.get("CLM_TORCH_THREADS", "2")))

ENGINE = Engine(
    Embedder(url="http://127.0.0.1:8080/v1/embeddings",
             model=os.environ.get("CLM_EMB_MODEL", "qwen3-8b"),
             max_tokens=int(os.environ.get("CLM_EMB_MAX_TOKENS", "2048"))),
    checkpoint=os.path.join(HERE, os.environ.get("CLM_CKPT", "CLM_v0.1-8B.pt")),
    device=DEVICE,
    action_cache=os.environ.get("CLM_ACTION_CACHE"),  # state/action vector cache; "0" disables it
)
# Engine calls are synchronous (an HTTP call to vLLM plus a small torch forward), so they
# run in a thread pool sized to the number of requests we want in flight at once.
POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("CLM_WORKERS", "32")), thread_name_prefix="clm")
print(f"[clm] heads loaded on {DEVICE}: {[m['name'] for m in ENGINE.models()]}", flush=True)


def _json(obj, status: int = 200) -> Response:
    return Response(content=json.dumps(obj), status_code=status, media_type="application/json")


def _run(body: dict):
    op = body.get("op", "systemone")
    model = body.get("model") or DEFAULT_MODEL
    temperature = float(body.get("temperature", 1.0))
    if op == "systemone":
        if not isinstance(body.get("questions"), dict) or "state" not in body:
            raise ValueError("systemone needs {state, questions}")
        return ENGINE.answer(body["state"], body["questions"], model, temperature)
    if op == "rank":
        answers = body.get("answers")
        if not isinstance(answers, list) or not answers or not all(isinstance(a, str) and a for a in answers):
            raise ValueError("rank needs a non-empty list of non-empty strings in 'answers'")
        # Same as Engine.rank (a choice question over the candidates), but keeps the usage block.
        question = {"type": "choice", "instructions": body.get("question"),
                    "criteria": {str(i): a for i, a in enumerate(answers)}}
        out = ENGINE.answer(body.get("context") or "", {"rank": question}, model, temperature)
        probs = sorted(out["answers"]["rank"]["probabilities"].items(), key=lambda kv: -kv[1])
        ranked = [{"rank": r + 1, "candidate": answers[int(i)], "prob": p} for r, (i, p) in enumerate(probs)]
        return {"model": model, "ranked": ranked, "usage": out["usage"]}
    raise ValueError(f"unknown op {op!r}; use 'systemone' or 'rank'")


@sagemaker_standards.custom_invocation_handler
async def invocations(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _json({"error": f"body is not JSON: {e}"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "body must be a JSON object"}, 400)
    try:
        return _json(await asyncio.get_running_loop().run_in_executor(POOL, _run, body))
    except ModelNotFound as e:
        return _json({"error": str(e.args[0])}, 400)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return _json({"error": f"invalid request: {e}"}, 400)
    except EmbedderError as e:
        return _json({"error": str(e)}, 502)
