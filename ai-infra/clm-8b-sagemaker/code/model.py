"""CLM System One as a SageMaker /invocations handler inside the AWS vLLM DLC.

vLLM serves the frozen Qwen3-8B encoder (``--runner pooling``) on port 8080. This
handler replaces vLLM's default ``/invocations`` route with the CLM engine from the
``contrastive-lm`` package: it builds the state and candidate texts, gets their
embeddings from vLLM's own ``/v1/embeddings`` on 127.0.0.1, runs the two projection
heads (75 MB together) and returns the answer distributions. ``/ping`` stays vLLM's.

Request body (JSON):
    {"op": "systemone", "state": ..., "questions": {id: question}, "model"?, "temperature"?}
    {"op": "rank", "context": ..., "question": ..., "answers": [...], "model"?, "temperature"?}

Malformed requests get HTTP 400. Failures inside the engine get 500 (or 502 when vLLM
itself fails), so they count as server errors and clients can retry them. A request that
cannot finish within its deadline (queue time included) gets 503 and is not run.

Extra ``*.pt`` head checkpoints placed next to this file are served too, each under its
file stem: send ``"model": "<stem>"``. A ``.pt`` file is a pickle; this handler loads every one with
``torch.load(..., weights_only=True)``, which reads tensors and plain containers only and runs no code.

Files next to this one (see the notebook): ``clm/`` (the contrastive-lm package)
and ``CLM_v0.1-8B.pt`` (the heads).
"""
import asyncio
import hashlib
import inspect
import json
import math
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # the SageMaker loader imports this file by path, not as a package

import model_hosting_container_standards.sagemaker as sagemaker_standards  # noqa: E402
import requests  # noqa: E402
import torch  # noqa: E402
from fastapi import Request, Response  # noqa: E402

import clm.cache  # noqa: E402
import clm.heads  # noqa: E402
from clm.embedder import Embedder, EmbedderError  # noqa: E402
from clm.engine import DEFAULT_MODEL, RAW_MODEL, Engine  # noqa: E402
from clm.heads import HIDDEN  # noqa: E402
from clm.schema import build_pairs, to_text  # noqa: E402

# SageMaker gives a real-time invocation 60 s. Each request gets DEADLINE_S from arrival, counting
# its wait for a worker. A request is refused (503) unless enough of that remains when a worker picks it
# up: MIN_RUN_S, or the estimated encoder time of its own uncached texts plus those of the requests running
# now, if that is longer. Its calls to vLLM never wait
# past the deadline, so a worker does not keep computing an answer the caller has already given up on.
DEADLINE_S = float(os.environ.get("CLM_REQUEST_DEADLINE", "55"))
MIN_RUN_S = float(os.environ.get("CLM_MIN_RUN", "10"))
# Encoder tokens/s with nothing cached, by GPU: the lowest a benchmark sweep measured on each GPU of the
# notebook's instance pools (L4 on ml.g6, A10G on ml.g5, L40S on ml.g6e), rounded down. CLM_ENC_RATE overrides it.
ENC_RATES = {"NVIDIA L4": 5500, "NVIDIA A10G": 6500, "NVIDIA L40S": 10000}
DEFAULT_ENC_RATE = 5500                                      # another GPU: assume the slowest measured


def _gpu_name() -> str:
    """The GPU's name from NVML (no CUDA context in this process, so vLLM keeps the whole GPU)."""
    try:
        import pynvml                                       # nvidia-ml-py, a vLLM dependency
        pynvml.nvmlInit()
        name = pynvml.nvmlDeviceGetName(pynvml.nvmlDeviceGetHandleByIndex(0))
        return name.decode() if isinstance(name, bytes) else name
    except Exception:  # noqa: BLE001  no GPU, no driver or no NVML: fall back to the default rate
        return ""


GPU_NAME = _gpu_name()
ENC_RATE = float(os.environ.get("CLM_ENC_RATE") or ENC_RATES.get(GPU_NAME, DEFAULT_ENC_RATE))
# The token bound counts UTF-8 bytes; admission instead estimates each text's tokens at this many bytes per token
# (about right for code and CJK text, cautious for English, which is nearer 4), capped at the encoder's cut-off.
BYTES_PER_TOKEN = float(os.environ.get("CLM_BYTES_PER_TOKEN", "3"))
WORKERS = int(os.environ.get("CLM_WORKERS", "32"))
# Texts one request may score (states plus options, over all its questions): a 1024-candidate rank. The cache holds
# far more, so a request never evicts its own vectors, and the upstream cache's duplicate check (a list scan under
# the lock every request shares) stays a few milliseconds.
MAX_TEXTS = int(os.environ.get("CLM_MAX_TEXTS", "1025"))
# Encoder tokens one request may need, so it can finish inside the deadline. Counted over the texts not already
# in the vector cache, as an upper bound that needs no tokenizer: each text as its UTF-8 length in bytes (a
# byte-level BPE never makes more tokens than bytes), capped at the encoder's 2,048-token cut-off. 200,000 is
# under 30 s at ENC_RATE.
MAX_TOKENS = int(os.environ.get("CLM_MAX_TOKENS", "200000"))
EMB_MAX_TOKENS = int(os.environ.get("CLM_EMB_MAX_TOKENS", "2048"))
# Bytes one text (a rendered state plus instructions, or one option) may have. The encoder reads at most
# EMB_MAX_TOKENS of it, so anything far past that is only cost: rendering, sending and tokenizing it.
MAX_TEXT_BYTES = int(os.environ.get("CLM_MAX_TEXT_BYTES", str(16 * EMB_MAX_TOKENS)))
MAX_RESPONSE_BYTES = 6_000_000               # under SageMaker's 6 MB real-time response limit
_REQUEST = threading.local()                 # per-thread deadline of the request being served
ADMIT_LOCK = threading.Lock()
_ADMITTED = [0]                              # estimated encoder tokens (an int) of the requests running now
_RUNNING: dict = {}                          # deadline of each request running now


class DeadlineSession(requests.Session):
    """Caps each HTTP timeout at the time left before the current request's deadline."""

    def request(self, *args, timeout=None, **kwargs):
        for attempt in (0, 1):
            limit = timeout
            deadline = getattr(_REQUEST, "deadline", None)
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:                # the caller already has its 503: send nothing more to vLLM
                    raise DeadlineExceeded()
                limit = left if timeout is None else min(timeout, left)
            try:
                return super().request(*args, timeout=limit, **kwargs)
            except requests.ConnectionError as e:
                # A pooled keep-alive connection that vLLM closed just as it was reused: retry once on a new one.
                # Not a timeout, so the retry gets only the time that is left.
                if attempt or isinstance(e, requests.Timeout):
                    raise

# The heads are small MLPs, so they run on the host CPU. That keeps the whole GPU for
# vLLM (no second CUDA context in this process, no memory outside vLLM's budget).
DEVICE = os.environ.get("CLM_DEVICE", "cpu")
torch.set_num_threads(int(os.environ.get("CLM_TORCH_THREADS", "2")))

_claim = clm.cache.Pool.claim
_load = clm.heads.HeadPair._load


def _load_weights_only(self) -> None:
    """HeadPair._load at clm bb42c6c, with weights_only=True: a .pt is a pickle, and this one reads only tensors and
    plain containers, so a head file dropped next to this handler cannot run code when it loads."""
    ck = torch.load(self.path, map_location="cpu", weights_only=True)
    cfg = dict(ck["cfg"])
    kw = dict(width=cfg["width"], depth=cfg["depth"],
              proj=ck.get("projection_dim", cfg.get("projection_dim", clm.heads.PROJ_DIM)),
              activation=cfg.get("activation", "gelu"), layernorm=cfg.get("layernorm", False),
              residual=cfg.get("residual", False), hidden=cfg.get("hidden_size", HIDDEN))
    sh, ah = clm.heads.make_head(**kw), clm.heads.make_head(**kw)
    sh.load_state_dict(ck["state_head"]); ah.load_state_dict(ck["action_head"])
    sh.eval().to(self.device); ah.eval().to(self.device)
    self.state_head, self.action_head, self.cfg = sh, ah, cfg
    self.generation += 1
    self.proj_dim = kw["proj"]
    self.scale = float(torch.as_tensor(ck["logit_scale"]).float().exp().clamp(max=100.0))


def _claim_once(pool, key):
    """Pool.claim, but a key two concurrent requests both missed keeps its first slot.

    Upstream (clm @ bb42c6c) claims a second slot for the same key and loses the first one for good, so a
    long-running endpoint's cache shrinks under concurrent traffic. It is always called under the arena lock.
    """
    if key in pool.slots:
        pool.slots.move_to_end(key)
        return pool.slots[key]
    return _claim(pool, key)


def _source_sha(fn) -> str:
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


# Patch only the exact code this was written against (Pool.claim at clm bb42c6c), so a newer clm commit is
# never changed silently. The source hashes are not secrets.
if _source_sha(_claim) == "29fbefd4990b6892":  # pragma: allowlist secret
    clm.cache.Pool.claim = _claim_once
else:
    print("[clm] cache patch not applied: clm.cache.Pool has changed upstream", flush=True)
if _source_sha(_load) == "ae352693da390bcc":  # pragma: allowlist secret
    clm.heads.HeadPair._load = _load_weights_only
# Upstream loads with torch.load's default. Without the patch above, that is safe only where the default is
# weights_only=True (torch 2.6 and later) and nothing has switched it off, so anything else refuses to start.
elif torch.__version__ < "2.6" or os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "0") not in ("", "0"):
    raise RuntimeError("clm.heads.HeadPair._load has changed upstream and torch.load would unpickle head files "
                       "without weights_only; use torch 2.6 or later with TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD unset")
else:
    print("[clm] head-loading patch not applied: clm.heads.HeadPair._load has changed upstream; relying on "
          "torch.load's weights_only=True default", flush=True)
# The token bound below skips texts already cached by looking up the engine's own cache keys. That works only
# with the key layout of Engine._cached and VectorArena.get at bb42c6c; on any other code it counts every text.
# Engine.answer and HeadPair.namespace build the key namespaces; Engine._cached and VectorArena.get build the keys.
CACHE_PEEK = (_source_sha(Engine._cached), _source_sha(clm.cache.VectorArena.get), _source_sha(Engine.answer),
              _source_sha(clm.heads.HeadPair.namespace.fget)) == (
    "426c35eb0aa7990a", "e77d9b125b743c62", "e0c81d455d734ee2", "a46b450cf3b6f33b")  # pragma: allowlist secret
if not CACHE_PEEK:
    print("[clm] cached texts count toward the token bound: the clm cache keys have changed upstream", flush=True)

EMBEDDER = Embedder(url="http://127.0.0.1:8080/v1/embeddings",
             model=os.environ.get("CLM_EMB_MODEL", "qwen3-8b"),
             max_tokens=EMB_MAX_TOKENS,
             # A stalled vLLM must free the worker before SageMaker's 60 s limit.
             timeout=float(os.environ.get("CLM_EMB_TIMEOUT", "50")),
             # Up to 1024 texts per HTTP call: vLLM schedules them itself, so a 1024-candidate rank
             # is one call for its candidates (the engine embeds states and candidates separately).
             batch=int(os.environ.get("CLM_EMB_BATCH", "1024")),
             # The engine's vector arena below already caches projected vectors, so the
             # embedder's own cache of raw 4096-d vectors is off by default to save host RAM.
             cache_size=int(os.environ.get("CLM_EMB_CACHE_SIZE", "0")))
EMBEDDER.session = DeadlineSession()
# One pooled connection per worker thread (WORKERS, plus the clm-raw thread), so requests reuse connections to vLLM.
EMBEDDER.session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=WORKERS + 1))
ENGINE = Engine(
    EMBEDDER,
    checkpoint=os.path.join(HERE, os.environ.get("CLM_CKPT", "CLM_v0.1-8B.pt")),
    checkpoint_dir=HERE,                     # also serve any other *.pt heads shipped in code/
    device=DEVICE,
    # The projected-vector arena is sized by CLM_ACTION_CACHE, which the engine reads itself ("0" disables it).
)
# Engine calls are synchronous (an HTTP call to vLLM plus a small torch forward), so they
# run in a thread pool sized to the number of requests we want in flight at once.
POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="clm")
# Decoding and validation get their own threads, so a request does not wait in POOL's queue twice and a
# malformed one gets its 400 at once even when every worker is busy.
PARSE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="clm-parse")
# With the cache on, clm-raw requests run one at a time on their own thread, and each fits the raw-vector pool, so no request can evict
# another's rows (which would make the upstream cache re-embed under its lock and stall every request). Waiting
# clm-raw requests queue here, not on POOL, so a burst of them never holds the threads the other heads use.
RAW_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clm-raw")
print(f"[clm] heads loaded on {DEVICE}: {[m['name'] for m in ENGINE.models()]}; admission at {ENC_RATE:,.0f} "
      f"encoder tokens/s ({GPU_NAME or 'GPU not identified'})", flush=True)


class BadRequest(ValueError):
    """The request itself is wrong; reported as HTTP 400."""


class DeadlineExceeded(RuntimeError):
    """The request would not finish inside SageMaker's invocation limit; reported as 503."""


# The other heads use their own, much larger vector pool.
_raw_pool = ENGINE.arena.pools.get(HIDDEN) if getattr(ENGINE, "arena", None) else None
RAW_ROWS = _raw_pool.capacity if _raw_pool else None
# Every worker may hold one request of up to MAX_TEXTS vectors in the head pool at once. If a small
# CLM_ACTION_CACHE leaves less room than that, lower the per-request cap so the requests in flight at one time fit
# (the upstream cache re-embeds under the lock every request shares when a request's rows are evicted). With a
# small cache, churn from short requests can still evict a long one's rows, so keep the default size in production.
_head_rows = ([p.capacity for dim, p in ENGINE.arena.pools.items() if dim != HIDDEN]
              if getattr(ENGINE, "arena", None) else [])
if _head_rows and min(_head_rows) // WORKERS < 2:
    raise SystemExit(f"CLM_ACTION_CACHE is too small for {WORKERS} workers ({min(_head_rows)} rows): raise it, "
                     "lower CLM_WORKERS, or set CLM_ACTION_CACHE=0 to turn the cache off")
if _head_rows and min(_head_rows) // WORKERS < MAX_TEXTS:
    MAX_TEXTS = min(_head_rows) // WORKERS
    print(f"[clm] CLM_ACTION_CACHE is small for {WORKERS} workers: at most {MAX_TEXTS} texts per request", flush=True)


def _cache_view(model: str):
    """The vector pool and key namespace the engine caches this model's vectors under (None without a cache)."""
    arena = getattr(ENGINE, "arena", None)
    if arena is None or not CACHE_PEEK:
        return None, None
    if model == RAW_MODEL:
        return arena.pools.get(HIDDEN), "raw"
    head = ENGINE.heads[model].ensure()
    return arena.pools.get(head.proj_dim), head.namespace


def _check_texts(state, questions: dict, model: str) -> int:
    """Reject requests whose state or option texts are empty: vLLM refuses empty prompts.

    This renders the texts the way the engine will, which the engine then does again. Rendering is
    microseconds per text, against milliseconds to embed it, so the second pass is worth a clear 400.
    Returns the estimated encoder tokens of the texts that are not cached now (a peek: the engine looks again).
    """
    if len(questions) > MAX_TEXTS // 2:        # each question has a state text and at least one option
        raise BadRequest(f"the request has {len(questions)} questions; at most {MAX_TEXTS // 2} are allowed")
    if len(state.encode("utf-8")) > MAX_TEXT_BYTES:
        raise BadRequest(f"the state is longer than {MAX_TEXT_BYTES:,} bytes; the encoder reads at most "
                         f"{EMB_MAX_TOKENS:,} tokens of it")
    try:
        pairs = build_pairs(state, questions)          # same rendering the engine uses
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        raise BadRequest(f"invalid question: {str(e)[:200]}") from None
    except RecursionError:                     # a state or criteria value nested too deeply to render
        raise BadRequest("the request is nested too deeply") from None
    # The engine copies one vector per option, repeats included, so MAX_TEXTS caps them all; it embeds each
    # distinct text only once, so repeats count once against the token bound and the admission estimate.
    every = sum(len(texts) + 1 for _state, _keys, texts in pairs.values())
    if every > MAX_TEXTS:
        raise BadRequest(f"the request has {every} texts to score; at most {MAX_TEXTS} are allowed")
    texts_by_kind = list(dict.fromkeys(
        (kind, t) for state, _keys, texts in pairs.values()
        for kind, t in (("state", state), *(("action", x) for x in texts))))
    total = len(texts_by_kind)
    if model == RAW_MODEL and RAW_ROWS is not None and total > RAW_ROWS:
        raise BadRequest(f"clm-raw takes at most {RAW_ROWS} texts per request with this cache size")
    pool, ns = _cache_view(model)
    encoded = [t.encode("utf-8") for _kind, t in texts_by_kind]
    if any(len(b) > MAX_TEXT_BYTES for b in encoded):
        raise BadRequest(f"a text (state plus instructions, or an option) is longer than {MAX_TEXT_BYTES:,} bytes")
    uncached = [b for (kind, t), b in zip(texts_by_kind, encoded)
                if pool is None or f"{ns}/{kind}\x00{t}" not in pool.slots]
    bound = sum(min(len(b), EMB_MAX_TOKENS) for b in uncached)   # an upper bound: never more tokens than bytes
    if bound > MAX_TOKENS:
        raise BadRequest(f"the request may need up to {bound:,} encoder tokens; at most {MAX_TOKENS:,} are allowed "
                         "(send fewer or shorter options per request)")
    for qid, (state_text, _keys, texts) in pairs.items():
        if not state_text.strip():
            raise BadRequest(f"question {_clip(qid)}: the state and instructions are both empty")
        if any(not t.strip() for t in texts):
            raise BadRequest(f"question {_clip(qid)}: every option needs a non-empty text")
    return math.ceil(sum(min(_est_tokens(b), EMB_MAX_TOKENS) for b in uncached))   # whole tokens keep the books exact


_DIGITS = b"0123456789"


def _est_tokens(b: bytes) -> float:
    """Encoder tokens for one text: Qwen3 gives every digit its own token, other text about BYTES_PER_TOKEN bytes each."""
    digits = len(b) - len(b.translate(None, _DIGITS))
    return digits + (len(b) - digits) / BYTES_PER_TOKEN


_ANY_SURROGATE = re.compile(rb"\\u[dD][89a-fA-F]")    # any \\uD800-\\uDFFF escape at all


def _clip(value, limit: int = 80) -> str:
    """repr() of a request value for an error message, cut short so a 400 stays small."""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _reject_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _finite_float(text):
    value = float(text)
    if math.isinf(value):                    # such as 1e400: valid JSON syntax, but no float can hold it
        raise ValueError(f"the number {text[:20]} is out of range")
    return value


def _render_state(state) -> str:
    """The state as the text the engine embeds, rendered once (upstream renders it again for every question)."""
    try:
        return to_text(state)
    except RecursionError:                     # nested too deeply to render
        raise BadRequest("the request is nested too deeply") from None


def _dumps(obj) -> bytes:
    # UTF-8 as is: escaping it as \\uXXXX would double the size of a response that echoes non-Latin text.
    return json.dumps(obj, allow_nan=False, ensure_ascii=False).encode("utf-8")


def _json(obj, status: int = 200) -> Response:
    content = obj if isinstance(obj, bytes) else _dumps(obj)
    return Response(content=content, status_code=status, media_type="application/json")


def _parse(body: dict):
    """Validate the request; return a no-argument function that runs it, the model it uses, and its estimated
    encoder tokens."""
    op = body.get("op", "systemone")
    model = body.get("model")
    if model is None:                        # only a missing or null model means the default
        model = DEFAULT_MODEL
    if not isinstance(model, str) or not ENGINE.has(model):
        raise BadRequest(f"unknown model {_clip(model)}; available: {[m['name'] for m in ENGINE.models()]}")
    temperature = body.get("temperature")
    if temperature is None:                  # missing or null, like model
        temperature = 1.0
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise BadRequest("temperature must be a number")
    if not (0.01 <= temperature <= 100):     # also rejects NaN; a sanity bound, not a numerical limit
        raise BadRequest("temperature must be between 0.01 and 100")
    temperature = float(temperature)

    if op == "systemone":
        questions = body.get("questions")
        if "state" not in body or not isinstance(questions, dict) or not questions:
            raise BadRequest("systemone needs 'state' and a non-empty 'questions' object")
        if not all(isinstance(q, dict) for q in questions.values()):
            raise BadRequest("every question must be an object")
        state = _render_state(body["state"])
        work = _check_texts(state, questions, model)
        return (lambda: ENGINE.answer(state, questions, model, temperature)), model, work

    if op == "rank":
        answers = body.get("answers")
        if not isinstance(answers, list) or not answers or not all(isinstance(a, str) and a for a in answers):
            raise BadRequest("rank needs a non-empty list of non-empty strings in 'answers'")
        # Same as Engine.rank (a choice question over the candidates), but keeps the usage block.
        question = {"type": "choice", "instructions": body.get("question"),
                    "criteria": {str(i): a for i, a in enumerate(answers)}}
        context = _render_state(body.get("context"))   # None renders as "", like state
        work = _check_texts(context, {"rank": question}, model)
        # The response echoes every candidate, so a request near the payload limit could answer past it.
        if sum(len(_dumps(a)) + 64 for a in answers) > MAX_RESPONSE_BYTES:   # as escaped in the response
            raise BadRequest(f"the ranked response would exceed {MAX_RESPONSE_BYTES:,} bytes; send fewer or shorter answers")

        def run():
            out = ENGINE.answer(context, {"rank": question}, model, temperature)
            probs = sorted(out["answers"]["rank"]["probabilities"].items(), key=lambda kv: -kv[1])
            ranked = [{"rank": r + 1, "candidate": answers[int(i)], "prob": p} for r, (i, p) in enumerate(probs)]
            return {"model": model, "ranked": ranked, "usage": out["usage"]}
        return run, model, work

    raise BadRequest(f"unknown op {_clip(op)}; use 'systemone' or 'rank'")


def _timeout():
    return _json({"error": f"request did not finish within {DEADLINE_S:.0f} s; retry later"}, 503)


@sagemaker_standards.custom_invocation_handler
async def invocations(request: Request) -> Response:
    deadline = time.monotonic() + DEADLINE_S
    raw = await request.body()                 # only the read happens on vLLM's event loop

    def parse():
        """Decode and validate in a thread rather than on vLLM's event loop. (The C JSON decoder still holds the
        GIL while it runs, so a body of several MB pauses the loop for some milliseconds; small ones do not.)"""
        try:
            # Strict UTF-8 first: json.loads on bytes would also accept UTF-16/32 and raw surrogate bytes.
            body = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant, parse_float=_finite_float)
        except ValueError as e:                # includes UnicodeDecodeError and NaN / Infinity
            raise BadRequest(f"body is not JSON: {e}") from None
        except RecursionError:
            raise BadRequest("the request is nested too deeply") from None
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        # A lone surrogate anywhere (texts, question IDs, choice keys) would make the UTF-8 response fail. In JSON
        # it can only arrive as an unpaired \\uD800-\\uDFFF escape, so the full check runs only when one is present.
        try:
            if _ANY_SURROGATE.search(raw):        # a cheap pre-check; encoding the parsed body is the exact test
                _dumps(body)
            return _parse(body)
        except UnicodeEncodeError:             # the check above, or _check_texts encoding a text
            raise BadRequest("the body must be valid Unicode (a lone surrogate such as \\ud800 is not)") from None

    def execute(run, work):
        # Start only if the encoder can get through this request and those already running before the deadline,
        # both this one's and the earliest of those already running, so new work never pushes those past theirs.
        token = object()
        with ADMIT_LOCK:
            now = time.monotonic()
            # A request with nothing to encode (all cached) does not wait for the encoder's backlog.
            backlog = _ADMITTED[0] + work if work else 0
            need_s = max(MIN_RUN_S, backlog / ENC_RATE)
            # Encoder users only; one already past its deadline has had its 503 and no longer constrains new work.
            earliest = min((d for d in _RUNNING.values() if d > now), default=deadline) if work else deadline
            if deadline - now < need_s or earliest - now < backlog / ENC_RATE:
                raise DeadlineExceeded()
            _ADMITTED[0] += work
            if work:                         # a fully cached request never waits on the encoder
                _RUNNING[token] = deadline
        _REQUEST.deadline = deadline
        try:
            out = _dumps(run())                          # serialised in this thread, not on vLLM's event loop
            if len(out) > MAX_RESPONSE_BYTES:            # e.g. a score legend echoing many large cached levels
                raise BadRequest(f"the response would exceed {MAX_RESPONSE_BYTES:,} bytes; ask fewer questions "
                                 "or use shorter options")
            return out
        finally:
            _REQUEST.deadline = None
            with ADMIT_LOCK:
                _ADMITTED[0] -= work
                _RUNNING.pop(token, None)

    def left():
        return max(deadline - time.monotonic(), 0)

    try:
        loop = asyncio.get_running_loop()
        run, model, work = await asyncio.wait_for(loop.run_in_executor(PARSE_POOL, parse), timeout=left())
        pool = RAW_POOL if model == RAW_MODEL and RAW_ROWS is not None else POOL
        return _json(await asyncio.wait_for(loop.run_in_executor(pool, execute, run, work), timeout=left()))
    except BadRequest as e:
        return _json({"error": str(e)}, 400)
    except (DeadlineExceeded, asyncio.TimeoutError):
        return _timeout()
    except EmbedderError as e:
        if time.monotonic() >= deadline:     # the vLLM call ran out of the request's time: a deadline, not a fault
            return _timeout()
        return _json({"error": f"encoder error: {str(e)[:200]}"}, 502)
    except Exception as e:  # noqa: BLE001  (a server-side fault: log it, report 500)
        # The exception type and where it happened, not its message: messages can quote request text.
        print(f"[clm] {type(e).__name__}\n" + "".join(traceback.format_tb(e.__traceback__)), flush=True)
        return _json({"error": f"internal error: {type(e).__name__}"}, 500)
