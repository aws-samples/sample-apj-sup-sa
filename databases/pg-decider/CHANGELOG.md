# Changelog

## 0.1.0 (2026-10-05)

First release.

- `decide`, `decide_prob`, `decide_choice`, `decide_score`, `decide_score_norm`, `decide_eval`, `decide_confidence` and `decide_ask` over table rows, single columns and subquery records.
- `decider_health`, `decider_stats`, `decider_cache_clear`, `decider_version`.
- One row per request, sent sequentially. This is deliberate, because the Strands Decider server is a single worker whose concurrent behaviour is unverified.
- Session answer cache, per-statement row guard, retries with backoff, keep-alive connections, and waits that `statement_timeout` and cancel can interrupt.
- Client-side validation against the server's schema limits, plus detection of rows the server truncated to fit its context window.
- Regression suite against a mock server on PostgreSQL 14, 15, 16 and 17. A live check against `strands-decider-2B-hobson-v19` is in `test/live/live_test.sql`.
