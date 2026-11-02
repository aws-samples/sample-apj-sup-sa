# pg-decider

> **Sample code — not intended for production use.** This project is a demonstration provided as-is, without warranties or SLAs. It has not undergone the security testing and hardening a production deployment requires. In particular, the extension runs as untrusted PL/Python with the database server's OS privileges, makes outbound HTTP calls, and the decider server has no authentication by default — read the Caveats section and review the security posture before using it anywhere beyond a trusted development environment.

Ask your Postgres rows plain-language questions, answered by a [Strands Decider](https://github.com/strands-labs/strands-decider) server running on your own machine.

```sql
CREATE EXTENSION decider CASCADE;

SELECT * FROM tickets WHERE decide(tickets, 'the customer is angry');

SELECT subject, decide_prob(t.body, 'the writer threatens to cancel') AS p
FROM tickets t ORDER BY p DESC LIMIT 20;

SELECT decide_choice(tickets, 'Which team should handle this?',
                     ARRAY['billing', 'technical', 'sales']) AS team, count(*)
FROM tickets GROUP BY 1;

SELECT name, decide_score(products, 'How luxurious is this product?',
                          ARRAY['budget', 'mid-range', 'premium', 'luxury']) AS luxury
FROM products ORDER BY luxury DESC;
```

Strands Decider is a 2B "decision model". It doesn't generate text. It answers yes/no (`noul`), pick-one (`choice`) and rating (`score`) questions with calibrated probabilities, in about 100–300 ms per question on a laptop GPU (measured with the 2B model on an M3 Pro; see Development for the setup and numbers). It serves `POST /v1/systemone`, the same request shape [pg-jev](https://github.com/realZachi/pg-jev) uses for TypeSafe's Jev. This extension follows pg-jev's structure but is built around how the decider works and what its server can handle.

## Quick start

1. Start a decider server. The model is about 4.5 GB and downloads on first use.
   ```bash
   pip install strands-decider==0.1.0
   strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8000   # add --device mps|cuda|cpu (mps on Apple Silicon)
   ```
2. Install the extension. It needs PostgreSQL 14–17 with `plpython3u` and a superuser.
   ```bash
   make install                          # or: make install PG_CONFIG=/path/to/pg_config
   psql -c "CREATE EXTENSION decider CASCADE"
   ```
   Or use Docker: `docker build -t pg-decider . && docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pw pg-decider`.
   Postgres inside a container reaches the server on your host at `host.docker.internal`, so set `SET decider.api_url = 'http://host.docker.internal:8000/v1/systemone';`. If that can't connect, start the server with `--host 0.0.0.0`. The server has no authentication, so only do this on a trusted network.
3. Check the connection:
   ```sql
   SELECT decider_health();                       -- checkpoint, device, context window
   SELECT decide('Help! My payouts have been failing for 3 days!'::text, 'This conveys urgency');
   ```

## Functions

| Function | Returns | Purpose |
|---|---|---|
| `decide(row, statement [, threshold, when_true, when_false])` | boolean | `WHERE` predicate. Threshold: argument, then `decider.threshold`, then 0.5 |
| `decide_prob(row, statement [, when_true, when_false])` | float8 | P(statement is true of the row) |
| `decide_choice(row, question, options text[] [, descriptions text[]])` | text | Most likely option. Descriptions explain each option |
| `decide_score(row, question, levels text[])` | float8 | Expected level, 0 .. n-1, lowest first (2–10 levels) |
| `decide_score_norm(row, question, levels)` | float8 | Same, scaled to 0..1 |
| `decide_confidence(row, question, kind [, options])` | float8 | Confidence of a choice or score answer. For `noul` it returns \|2p−1\| |
| `decide_eval(row, question [, kind, options])` | jsonb | Full answer: probabilities, legend, confidence |
| `decide_ask(row, questions json)` | jsonb | Several questions about one row in one request |
| `decider_health()` | jsonb | The server's `GET /health` |
| `decider_stats()` | jsonb | Requests, cache hits, tokens, latency, retries, truncations this session |
| `decider_cache_clear()` | void | Forget cached answers |

`row` can be a table alias (`decide(tickets, ...)`), which sends the whole row as JSON. It can also be one column (`decide(t.body, ...)`), which sends just that text. Prefer a single column or a view with only the columns the question needs. The model has a context window of a few thousand tokens.

Phrase `noul` questions as statements. The decider's prompt asks "whether the statement is true of the state". `when_true` / `when_false` describe what each answer means, and the decider's docs say this sharpens the boundary considerably:

```sql
SELECT * FROM tool_calls
WHERE decide(tool_calls, 'The argument values are grounded in what the user said',
             when_true  => 'every argument traces back to something the user said',
             when_false => 'an argument was guessed or invented');
```

Several questions in one request cost much less than one request per question. The decider encodes the row once and adds only each question's tokens:

```sql
SELECT id, a->'urgent'->>'noul' AS urgent, a->'team'->>'choice' AS team
FROM (SELECT id, decide_ask(tickets, '{
        "urgent": {"type": "noul",   "instructions": "This needs a reply today"},
        "team":   {"type": "choice", "instructions": "Which team should handle this?",
                   "criteria": {"billing": "payments, invoices", "technical": "bugs, outages", "sales": "pricing"}}
      }') AS a FROM tickets) s;
```

## Settings

All of these are ordinary settings: `SET decider.<name>`, `ALTER ROLE/DATABASE ... SET`, or `postgresql.conf`.

| Setting | Default | Meaning |
|---|---|---|
| `decider.api_url` | `http://127.0.0.1:8000/v1/systemone` | Server endpoint |
| `decider.api_key` | env `DECIDER_API_KEY` | Optional. Sent as `Bearer <key>` for a server behind an authenticating proxy |
| `decider.model` | `strands-decider-latest` | Sent as `model`. The server answers with whichever checkpoint it loaded |
| `decider.threshold` | `0.5` | Probability at which `decide()` returns true |
| `decider.timeout` | `60` | Seconds per HTTP request. On a Mac, the first request at a new input length can take seconds |
| `decider.retries` | `2` | Retries on connection errors and 408/429/502/503/504 |
| `decider.cache` | `on` | Session answer cache |
| `decider.max_rows_per_statement` | `0` (off) | Abort a statement before it sends more rows than this |
| `decider.truncation` | `warn` | `warn` / `error` / `off`, for when a row didn't fit the model's window |
| `decider.notices` | `off` | NOTICE every 100 rows judged in a statement |

## How it works

All of the extension is in `sql/decider--0.1.0.sql`. One `plpython3u` function, `_decider(op, state, questions)`, does the HTTP, validation and caching. The public functions are SQL wrappers that build the question JSON and pass `to_json(row)` as the decider's `state`.

1. **One row is one request.** The row (or column) is the request's `state`, and the question(s) go in `questions`. pg-jev packs 20 rows into one state and asks about `rows[i]` by position. That was measured to work for Jev. For the decider it would be unmeasured, and the server silently cuts a state that's too long for its window. That would drop later rows from a batch without any error.
2. **Requests go out one at a time.** The decider's server is a single uvicorn worker, and its docs say its "behaviour under concurrent requests is not verified". There's also a known race in its engine (`_fit` keeps per-request offsets on the engine, issue #9). So this extension has no read-ahead and no parallel requests. Throughput is about one row per model latency: roughly 6 rows/s measured on an M3 Pro (see Development), so 1,000 rows take a few minutes. Put cheap SQL predicates before `decide()`, use `LIMIT`, and set `statement_timeout` or `decider.max_rows_per_statement`.
3. **Answers are cached** in PL/Python's session dictionary (`GD`), keyed by server URL, model, row JSON and question JSON. Re-running a query, changing the threshold or sorting by probability doesn't hit the server again. `decide()` and `decide_prob()` share cache entries. The cache grows until `decider_cache_clear()` or the end of the session.
4. **Waiting stays cancellable.** The HTTP call runs on a worker thread. The main thread waits in 250 ms slices with an SPI `SELECT 1` between them, so `statement_timeout` and Ctrl-C work mid-request. Worker threads never call `plpy`, because the Postgres backend isn't thread-safe.
5. **Truncation is detected.** With one question, the server's `usage.input_tokens` reaching the window from `GET /health` means it cut the state. The extension warns once per statement, or raises an error with `decider.truncation = error`. You can also start the server with `--strict-window` to make it reject such rows with HTTP 422.
6. **Validation is client-side.** Option counts (choice 2–255, score 2–10), `noul` criteria keys and duplicate option names are checked before anything is sent, using the same limits as the decider's `schema.py`.

## Caveats

- `plpython3u` is an untrusted language. Only a superuser can `CREATE EXTENSION decider`, and the code runs with the server's OS privileges. That rules out managed hosts: Amazon RDS, Aurora, Supabase and Neon don't offer `plpython3u`.
- The decider server has no authentication and binds to `127.0.0.1` by default. Keep it that way, or put it behind an authenticating proxy and set `decider.api_key`.
- Every row the executor asks about is one model call. This is a full scan by design, with no index.
- A 2B model is wrong sometimes. Upstream reports JevBench accuracy of 0.723, with answers at confidence ≥ 0.9 correct about 95% of the time on short tasks. Look at the `decide_prob()` distribution before choosing a threshold, and use `decide_confidence()` to route uncertain rows to a person.
- A 2B model can also reflect biases in its training data. Answers may differ across demographically marked text — names, dialects, non-English languages — in ways the aggregate accuracy numbers don't show. Evaluate on your own data before relying on outputs, and keep a person in the loop for decisions that affect people (routing, triage, prioritization), using `decide_confidence()` to flag the rows that need review.
- The functions are marked `STABLE` so the planner can use them in `WHERE`. Their answers only stay stable within a session while the cache holds them.

## Development

```bash
make docker-test                 # builds test/Dockerfile and runs pg_regress against the mock (PG_MAJOR=16)
make docker-test PG_MAJOR=17     # tested on 14, 16 and 17
```

Running locally against a server and `pg_config` on PATH: `python3 test/mock_decider.py &` then `make installcheck`. `test/mock_decider.py` speaks the decider's wire format with deterministic answers; its rules are in its docstring. The tests never load a real model.

`test/live/live_test.sql` runs against a real server. It uses 16 labelled support tickets and covers every function, accuracy, multi-question timing, the cache, truncation and `statement_timeout`:

```bash
psql -v url="'http://127.0.0.1:8099/v1/systemone'" -f test/live/live_test.sql
```

Measured on 2026-10-05 with `strands-decider-2B-hobson-v19`, `--device mps`, on an M3 Pro, with Postgres 16 in Docker:

| Check | Result |
|---|---|
| Team routing, `decide_choice` with descriptions | 15/16 correct |
| Urgent, `decide` at 0.5 | 15/16 correct |
| Angry, `decide_score_norm` ≥ 0.5 | 12/16 correct (3 false positives at 0.55–0.64; a threshold of 0.6 would do better) |
| One question over 16 short rows | about 1.3 s, ~80 ms per row |
| 3 questions per row: `decide_ask` vs 3 separate calls | 3.1 s vs 3.9 s (about 20% saved; the saving grows with longer rows) |
| Re-running a cached query | 2 ms |
| A row over the 4096-token window | warned and counted as truncated |
| `statement_timeout = 50ms` during a long request | cancelled cleanly |

A cancelled request still runs to completion on the server, and the server handles one request at a time, so the next query waits behind it on the server side.

## Repository layout

```
decider.control           extension metadata (default_version, requires plpython3u)
sql/decider--0.1.0.sql    the whole extension: PL/Python engine + SQL wrappers
Makefile                  PGXS build: make install / installcheck / docker-test
Dockerfile                postgres + plpython3u + the extension, ready to run
test/sql, test/expected   pg_regress tests and their expected output
test/mock_decider.py      deterministic stand-in for `strands-decider serve`
test/run.sh, Dockerfile   throwaway cluster + mock server, used by `make docker-test`
test/live/live_test.sql   end-to-end check against a real decider server
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to test changes and cut a release, and [CHANGELOG.md](CHANGELOG.md) for what changed in each version.

## License

[MIT-0](LICENSE), the same license as the rest of this repository. The extension's layout and several techniques (keep-alive connection pooling, cancellable waits, the mock-server test setup) are adapted from [pg-jev](https://github.com/realZachi/pg-jev), which is under the PostgreSQL License. It talks to a [Strands Decider](https://github.com/strands-labs/strands-decider) server, which is a separate project under its own license. This project isn't affiliated with the Strands or TypeSafe projects.
