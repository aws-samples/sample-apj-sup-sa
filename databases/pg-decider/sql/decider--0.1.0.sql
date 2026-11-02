-- decider 0.1.0: plain-language predicates, choices and scores over table rows, judged by a
-- Strands Decider server (https://github.com/strands-labs/strands-decider).
--
--   SELECT * FROM tickets WHERE decide(tickets, 'the customer is angry');
--   SELECT subject, decide_prob(t.body, 'the writer threatens to cancel') AS p FROM tickets t ORDER BY p DESC;
--   SELECT decide_choice(tickets, 'Which team should handle this?', ARRAY['billing','technical','sales']), count(*)
--     FROM tickets GROUP BY 1;
--   SELECT name, decide_score(products, 'How luxurious is this product?', ARRAY['budget','mid-range','luxury'])
--     FROM products;
--
-- How a call runs: the row is serialised with to_json() and sent as the `state` of one
-- POST /v1/systemone request, with the question(s) built by the SQL wrappers below. One row is
-- one request: the decider reads a state once and answers every question in the request over it
-- (decide_ask() sends several at once), and a 2B model is not asked to find rows[i] in a packed
-- array. Answers are cached per (server, model, row, questions) for the backend session.
--
-- Settings (SET decider.<name> = ...):
--   decider.api_url                default 'http://127.0.0.1:8000/v1/systemone'
--   decider.api_key                optional; sent as 'Bearer <key>' (falls back to env DECIDER_API_KEY)
--   decider.model                  default 'strands-decider-latest' (the server answers with whatever it loaded)
--   decider.threshold              default 0.5   probability at which decide() returns true
--   decider.timeout                default 60    seconds per HTTP request (first request per input length can be slow on a Mac)
--   decider.retries                default 2     retries on connection errors, 408, 429, 502, 503, 504
--   decider.cache                  default on    session answer cache
--   decider.max_rows_per_statement default 0 (off)  abort a statement before it sends more rows than this
--   decider.truncation             default 'warn'  'warn' | 'error' | 'off': what to do when a row did not fit the model window
--   decider.notices                default off   NOTICE every 100 rows judged in a statement

\echo Use "CREATE EXTENSION decider" to load this file. \quit

-- ------------------------------------------------------------------ engine (PL/Python)

CREATE FUNCTION _decider(op text, state text, questions text)
RETURNS jsonb
LANGUAGE plpython3u
STRICT
STABLE
AS $py$
import json, os, time, hashlib, threading, random, ssl, http.client
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib.parse import urlsplit

UA = "pg-decider/0.1.0"
STATE_VERSION = 1
SERVE_HINT = "strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8000"

# ---------------------------------------------------------------- session state (lives for the backend session)
if GD.get("decider", {}).get("version") != STATE_VERSION:
    GD["decider"] = {
        "version": STATE_VERSION,
        "cache": {},                      # cache key -> answers object
        "plans": {},                      # prepared SPI plans
        "lock": threading.Lock(),         # guards conns and stats against the worker thread
        "pool": None,                     # single worker thread that does the HTTP
        "conns": {},                      # (scheme, host, port) -> [idle keep-alive connections]
        "ssl": None,
        "window": {},                     # api_url -> model context window from GET /health (None = unknown)
        "stmt": {"ts": None, "rows": 0, "warned": False},
        "stats": {"requests": 0, "rows_evaluated": 0, "cache_hits": 0, "input_tokens": 0,
                  "client_ms": 0.0, "server_ms": 0.0, "errors": 0, "retries": 0, "truncated": 0},
    }
S = GD["decider"]
LOCK = S["lock"]


class Unavailable(Exception):
    pass


class ApiError(Exception):
    pass


def plan(name, sql, types=()):
    p = S["plans"].get(name)
    if p is None:
        p = S["plans"][name] = plpy.prepare(sql, list(types))
    return p

# ---------------------------------------------------------------- settings: one SPI round trip per call
CFG_SQL = """SELECT statement_timestamp()::text AS ts,
  current_setting('decider.api_url', true) AS api_url, current_setting('decider.api_key', true) AS api_key,
  current_setting('decider.model', true) AS model, current_setting('decider.timeout', true) AS timeout,
  current_setting('decider.retries', true) AS retries, current_setting('decider.cache', true) AS cache,
  current_setting('decider.max_rows_per_statement', true) AS max_rows,
  current_setting('decider.truncation', true) AS truncation, current_setting('decider.notices', true) AS notices"""


def truthy(v):
    return v.strip().lower() in ("on", "true", "1", "yes")


def load_cfg():
    r = plpy.execute(plan("cfg", CFG_SQL))[0]

    def g(name, default):
        v = r[name]
        return default if v in (None, "") else v

    url = g("api_url", "http://127.0.0.1:8000/v1/systemone")
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        plpy.error("decider: decider.api_url must be an http(s) URL, got %r" % url)
    try:
        timeout = float(g("timeout", "60"))
        retries = int(g("retries", "2"))
        max_rows = int(g("max_rows", "0"))
    except ValueError as e:
        plpy.error("decider: invalid setting: %s" % e)
    truncation = g("truncation", "warn").strip().lower()
    if truncation not in ("warn", "error", "off"):
        plpy.error("decider: decider.truncation must be 'warn', 'error' or 'off', got %r" % truncation)
    path = u.path or "/"
    health = path[: -len("/v1/systemone")] + "/health" if path.endswith("/v1/systemone") else None
    if u.query:
        path += "?" + u.query
    return {
        "ts": r["ts"], "url": url, "scheme": u.scheme, "host": u.hostname, "port": u.port,
        "path": path, "health_path": health,
        "key": g("api_key", None) or os.environ.get("DECIDER_API_KEY"),
        "model": g("model", "strands-decider-latest"),
        "timeout": max(1.0, timeout), "retries": max(0, retries), "max_rows": max(0, max_rows),
        "cache": truthy(g("cache", "on")), "truncation": truncation,
        "notices": truthy(g("notices", "off")),
    }

# ---------------------------------------------------------------- request validation (mirrors the server's schema)
def no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key %r" % k)
        out[k] = v
    return out


def validate(qs):
    if not isinstance(qs, dict) or not qs:
        plpy.error("decider: questions must be a non-empty JSON object of {name: question}")
    for name, q in qs.items():
        if not isinstance(q, dict):
            plpy.error("decider: question %r must be a JSON object" % name)
        kind, instr, crit = q.get("type"), q.get("instructions"), q.get("criteria")
        if instr is None or (isinstance(instr, str) and not instr.strip()):
            plpy.error("decider: question %r needs non-empty instructions" % name)
        if kind == "noul":
            if crit is not None and (not isinstance(crit, dict) or not set(crit) <= {"true", "false"}):
                plpy.error("decider: noul criteria for %r may only describe 'true' and 'false'" % name)
        elif kind == "choice":
            if not isinstance(crit, dict) or not 2 <= len(crit) <= 255:
                plpy.error("decider: choice %r needs between 2 and 255 distinct options" % name)
            if not all(isinstance(v, str) for v in crit.values()):
                plpy.error("decider: choice %r option descriptions must be strings (use \"\" for none)" % name)
        elif kind == "score":
            if not isinstance(crit, list) or not 2 <= len(crit) <= 10 or \
                    not all(isinstance(x, str) and x.strip() for x in crit):
                plpy.error("decider: score %r needs between 2 and 10 non-empty levels, lowest first" % name)
        else:
            plpy.error("decider: question %r has unknown type %r (expected noul, choice or score)" % (name, kind))

# ---------------------------------------------------------------- HTTP (worker thread only: never touch plpy here)
def borrow(cfg):
    with LOCK:
        idle = S["conns"].setdefault((cfg["scheme"], cfg["host"], cfg["port"]), [])
        c = idle.pop() if idle else None
    if c is not None:
        return c, True
    if cfg["scheme"] == "https":
        if S["ssl"] is None:
            S["ssl"] = ssl.create_default_context()
        return http.client.HTTPSConnection(cfg["host"], cfg["port"], timeout=cfg["timeout"], context=S["ssl"]), False
    return http.client.HTTPConnection(cfg["host"], cfg["port"], timeout=cfg["timeout"]), False


def give_back(cfg, c, reusable):
    if reusable:
        with LOCK:
            idle = S["conns"].setdefault((cfg["scheme"], cfg["host"], cfg["port"]), [])
            if len(idle) < 4:
                idle.append(c)
                return
    c.close()


def do_request(cfg, method, path, body=None):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if cfg["key"]:
        headers["Authorization"] = "Bearer " + cfg["key"]
    attempt, delay, last = 0, 0.5, None
    while True:
        c, reused = borrow(cfg)
        t0 = time.time()
        try:
            c.request(method, path, body=body, headers=headers)
            resp = c.getresponse()
            raw = resp.read()
        except ConnectionRefusedError as e:
            c.close()
            raise Unavailable(str(e))
        except (http.client.HTTPException, OSError) as e:
            c.close()
            last = "%s: %s" % (type(e).__name__, e)
            if reused:
                continue                     # an idle keep-alive connection went stale: retry at once, uncounted
            attempt += 1
            if attempt > cfg["retries"]:
                raise Unavailable(last)
            with LOCK:
                S["stats"]["retries"] += 1
            time.sleep(delay + random.random() * 0.25)
            delay = min(delay * 2, 8)
            continue
        ms = (time.time() - t0) * 1000
        give_back(cfg, c, not resp.will_close)
        if resp.status == 200:
            try:
                return json.loads(raw.decode()), ms
            except ValueError:
                raise ApiError("server returned invalid JSON: %r" % raw[:200])
        last = "HTTP %d: %s" % (resp.status, raw.decode(errors="replace")[:500])
        if resp.status in (408, 429, 502, 503, 504) and attempt < cfg["retries"]:
            attempt += 1
            with LOCK:
                S["stats"]["retries"] += 1
            time.sleep(delay + random.random() * 0.25)
            delay = min(delay * 2, 8)
            continue
        raise ApiError(last)


def call(cfg, method, path, body=None):
    """Run the request on the worker thread and wait in 250 ms slices. The SPI no-op between
    slices lets statement_timeout and query cancel interrupt the wait."""
    if S["pool"] is None:
        S["pool"] = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decider")
    fut = S["pool"].submit(do_request, cfg, method, path, body)
    while True:
        try:
            return fut.result(timeout=0.25)
        except FutureTimeout:
            pass
        try:
            plpy.execute(plan("noop", "SELECT 1"))
        except Exception:
            # Cancelled mid-request: abandon this worker (it finishes on its own) so the next
            # statement does not queue behind it.
            S["pool"] = None
            raise


def fail(e):
    with LOCK:
        S["stats"]["errors"] += 1
    if isinstance(e, Unavailable):
        plpy.error("decider: no Strands Decider server answering at %s (%s). Start one with: %s"
                   % (S.get("last_url", "?"), e, SERVE_HINT))
    plpy.error("decider: server rejected the request: %s" % e)

# ---------------------------------------------------------------- context-window check
def window(cfg):
    """The model's context window, from GET /health, cached per api_url. None when unknown."""
    if cfg["health_path"] is None:
        return None
    if cfg["url"] not in S["window"]:
        try:
            h, _ = call(cfg, "GET", cfg["health_path"])
            S["window"][cfg["url"]] = int(h.get("max_length") or 0) or None
        except (Unavailable, ApiError, ValueError, TypeError):
            S["window"][cfg["url"]] = None
    return S["window"][cfg["url"]]


def check_truncation(cfg, n_questions, input_tokens):
    # With one question the server reports exactly state + question tokens, capped at the window,
    # so reaching the window means the state was cut. With several questions the count includes
    # every question suffix, so it says nothing reliable about truncation; skip it.
    if cfg["truncation"] == "off" or n_questions != 1 or not input_tokens:
        return
    win = window(cfg)
    if not win or input_tokens < win:
        return
    with LOCK:
        S["stats"]["truncated"] += 1
    msg = ("the row did not fit the model's %d-token window and the server cut it (%d input tokens); "
           "the answer may ignore part of the row. Send fewer or shorter columns, e.g. decide(t.body, ...) "
           "or a view with only the columns the question needs" % (win, input_tokens))
    if cfg["truncation"] == "error":
        plpy.error("decider: " + msg)
    if not S["stmt"]["warned"]:
        S["stmt"]["warned"] = True
        plpy.warning("decider: " + msg + " (warned once per statement)")

# ---------------------------------------------------------------- operations
cfg = load_cfg()
S["last_url"] = cfg["url"]
stmt = S["stmt"]
if stmt["ts"] != cfg["ts"]:
    stmt.update(ts=cfg["ts"], rows=0, warned=False)

if op == "health":
    try:
        data, _ = call(cfg, "GET", cfg["health_path"] or "/health")
    except (Unavailable, ApiError) as e:
        fail(e)
    return json.dumps(data)

if op != "ask":
    plpy.error("decider: unknown operation %r" % op)

try:
    qs = json.loads(questions, object_pairs_hook=no_duplicates)
except ValueError as e:
    plpy.error("decider: questions are not valid JSON: %s" % e)
validate(qs)
try:
    st = json.loads(state)
except ValueError as e:
    plpy.error("decider: state is not valid JSON: %s" % e)

key = hashlib.sha1(json.dumps([cfg["url"], cfg["model"], state, questions]).encode()).hexdigest()
if cfg["cache"]:
    hit = S["cache"].get(key)
    if hit is not None:
        S["stats"]["cache_hits"] += 1
        return json.dumps(hit)

if cfg["max_rows"] and stmt["rows"] + 1 > cfg["max_rows"]:
    plpy.error("decider: this statement would send more than %d rows to the decider "
               "(decider.max_rows_per_statement)" % cfg["max_rows"])
stmt["rows"] += 1

body = json.dumps({"model": cfg["model"], "state": st, "questions": qs}, ensure_ascii=False).encode()
try:
    data, ms = call(cfg, "POST", cfg["path"], body)
except (Unavailable, ApiError) as e:
    fail(e)

answers = data.get("answers") if isinstance(data, dict) else None
if not isinstance(answers, dict) or any(name not in answers for name in qs):
    with LOCK:
        S["stats"]["errors"] += 1
    plpy.error("decider: server response is missing answers: %s" % json.dumps(data)[:300])

usage = data.get("usage") or {}
tokens = int(usage.get("input_tokens") or 0)
with LOCK:
    st_ = S["stats"]
    st_["requests"] += 1
    st_["rows_evaluated"] += 1
    st_["input_tokens"] += tokens
    st_["client_ms"] += ms
    st_["server_ms"] += float(data.get("latency_ms") or 0)

check_truncation(cfg, len(qs), tokens)
if cfg["cache"]:
    S["cache"][key] = answers
if cfg["notices"] and stmt["rows"] % 100 == 0:
    plpy.notice("decider: %d rows judged in this statement (%.0f ms per request this session)"
                % (stmt["rows"], S["stats"]["client_ms"] / max(1, S["stats"]["requests"])))
return json.dumps(answers)
$py$;

COMMENT ON FUNCTION _decider(text, text, text) IS
  'Internal: sends one state and its questions to the Strands Decider server (op ''ask'') or reads GET /health (op ''health'').';

-- ------------------------------------------------------------------ question builders (internal)

CREATE FUNCTION _decider_noul(statement text, when_true text, when_false text)
RETURNS json LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN $2 IS NULL AND $3 IS NULL
    THEN json_build_object('type', 'noul', 'instructions', $1)
    ELSE json_build_object('type', 'noul', 'instructions', $1,
                           'criteria', json_strip_nulls(json_build_object('true', $2, 'false', $3)))
  END
$$;

CREATE FUNCTION _decider_question(question text, kind text, options text[], descriptions text[] DEFAULT NULL)
RETURNS json LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF kind = 'noul' THEN
    RETURN _decider_noul(question, NULL, NULL);
  ELSIF kind = 'score' THEN
    RETURN json_build_object('type', 'score', 'instructions', question, 'criteria', to_json(options));
  ELSIF kind = 'choice' THEN
    IF options IS NULL OR cardinality(options) < 2 THEN
      RAISE EXCEPTION 'decider: choice needs at least 2 options';
    END IF;
    IF array_position(options, NULL) IS NOT NULL THEN
      RAISE EXCEPTION 'decider: choice options must not be NULL';
    END IF;
    IF descriptions IS NOT NULL AND cardinality(descriptions) <> cardinality(options) THEN
      RAISE EXCEPTION 'decider: % descriptions given for % options', cardinality(descriptions), cardinality(options);
    END IF;
    -- The server's criteria is dict[str, str]: an option without a description gets '' (as the
    -- upstream CLI does), never null, or the request is rejected with HTTP 422.
    RETURN json_build_object('type', 'choice', 'instructions', question, 'criteria',
      (SELECT json_object_agg(o, coalesce(d, '') ORDER BY i)
       FROM unnest(options, descriptions) WITH ORDINALITY AS u(o, d, i)));
  END IF;
  RAISE EXCEPTION 'decider: unknown kind % (expected noul, choice or score)', quote_literal(kind);
END
$$;

-- ------------------------------------------------------------------ public API

-- Probability (0..1) that the statement is true of the row. when_true / when_false optionally
-- describe what each answer means, which sharpens the boundary.
CREATE FUNCTION decide_prob(rec anyelement, statement text, when_true text DEFAULT NULL, when_false text DEFAULT NULL)
RETURNS float8 LANGUAGE sql STABLE AS $$
  SELECT (_decider('ask', to_json($1)::text,
                   json_build_object('q', _decider_noul($2, $3, $4))::text)->'q'->>'noul')::float8
$$;

-- Boolean predicate for WHERE. Threshold: argument, then decider.threshold, then 0.5.
CREATE FUNCTION decide(rec anyelement, statement text, threshold float8 DEFAULT NULL,
                       when_true text DEFAULT NULL, when_false text DEFAULT NULL)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT decide_prob($1, $2, $4, $5)
         >= coalesce($3, nullif(current_setting('decider.threshold', true), '')::float8, 0.5)
$$;

-- Pick one option. descriptions (same length as options) optionally explain each option.
CREATE FUNCTION decide_choice(rec anyelement, question text, options text[], descriptions text[] DEFAULT NULL)
RETURNS text LANGUAGE sql STABLE AS $$
  SELECT _decider('ask', to_json($1)::text,
                  json_build_object('q', _decider_question($2, 'choice', $3, $4))::text)->'q'->>'choice'
$$;

-- Probability-weighted position on ordered levels, lowest first (0 .. n-1).
CREATE FUNCTION decide_score(rec anyelement, question text, levels text[])
RETURNS float8 LANGUAGE sql STABLE AS $$
  SELECT (_decider('ask', to_json($1)::text,
                   json_build_object('q', _decider_question($2, 'score', $3))::text)->'q'->>'score')::float8
$$;

-- Same, normalised to 0..1.
CREATE FUNCTION decide_score_norm(rec anyelement, question text, levels text[])
RETURNS float8 LANGUAGE sql STABLE AS $$
  SELECT decide_score($1, $2, $3) / greatest(cardinality($3) - 1, 1)
$$;

-- Full answer object for one question: probabilities, legend, confidence.
CREATE FUNCTION decide_eval(rec anyelement, question text, kind text DEFAULT 'noul', options text[] DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT _decider('ask', to_json($1)::text,
                  json_build_object('q', _decider_question($2, $3, $4))::text)->'q'
$$;

-- Confidence (0..1) of a choice or score answer. A noul has no separate confidence; this
-- returns |2p - 1| for it, which is how far the probability sits from a coin flip.
CREATE FUNCTION decide_confidence(rec anyelement, question text, kind text, options text[] DEFAULT NULL)
RETURNS float8 LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN a ? 'confidence' THEN (a->>'confidence')::float8
              ELSE abs(2 * (a->>'noul')::float8 - 1) END
  FROM (SELECT decide_eval($1, $2, $3, $4) AS a) s
$$;

-- Several questions about one row in one request: the decider reads the row once and each
-- extra question costs only its own tokens. questions is {name: {type, instructions, criteria}}
-- in the server's format; answers come back under the same names.
CREATE FUNCTION decide_ask(rec anyelement, questions json)
RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT _decider('ask', to_json($1)::text, $2::text)
$$;

-- The server's GET /health: loaded checkpoint, device, context window.
CREATE FUNCTION decider_health()
RETURNS jsonb LANGUAGE sql VOLATILE AS $$ SELECT _decider('health', '', '') $$;

-- Session statistics.
CREATE FUNCTION decider_stats()
RETURNS jsonb LANGUAGE plpython3u VOLATILE AS $py$
import json
d = GD.get("decider", {})
s = {"requests": 0, "rows_evaluated": 0, "cache_hits": 0, "input_tokens": 0,
     "client_ms": 0.0, "server_ms": 0.0, "errors": 0, "retries": 0, "truncated": 0}
s.update(d.get("stats", {}))
s["client_ms"] = round(s["client_ms"], 1)
s["server_ms"] = round(s["server_ms"], 1)
s["cached_answers"] = len(d.get("cache", {}))
s["connections"] = sum(len(v) for v in d.get("conns", {}).values())
return json.dumps(s)
$py$;

-- Forget cached answers (and the cached context window) for this session.
CREATE FUNCTION decider_cache_clear()
RETURNS void LANGUAGE plpython3u VOLATILE AS $py$
if "decider" in GD:
    GD["decider"]["cache"].clear()
    GD["decider"]["window"].clear()
$py$;

CREATE FUNCTION decider_version() RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT '0.1.0' $$;

COMMENT ON FUNCTION decide(anyelement, text, float8, text, text) IS
  'True when the statement holds for the row (Strands Decider). Usage: WHERE decide(tbl, ''statement'')';
COMMENT ON FUNCTION decide_prob(anyelement, text, text, text) IS 'Probability that the statement holds for the row.';
COMMENT ON FUNCTION decide_choice(anyelement, text, text[], text[]) IS 'Classifies the row into one of the options.';
COMMENT ON FUNCTION decide_score(anyelement, text, text[]) IS 'Probability-weighted level of the row on an ordered scale.';
COMMENT ON FUNCTION decide_ask(anyelement, json) IS 'Several decider questions about one row in one request.';
