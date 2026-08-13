# Bridge internals: connection pooling and caching

`resolve_image_urls()` accepts two optional keyword arguments that make
repeated calls cheaper without changing default behavior at all when
they're not used.

## Connection pooling (`session`)

Every HTTP download in `bridge/core.py` goes through a
[`requests.Session`](https://requests.readthedocs.io/en/latest/user/advanced/#session-objects)
instead of the module-level `requests.get()`. A `Session` keeps a
per-host connection pool (`HTTPAdapter`, default 10 pooled connections
per host) and reuses TCP+TLS handshakes across calls instead of paying
that setup cost on every single image fetch.

You don't have to do anything to get this: `resolve_image_urls()` lazily
creates one module-level `Session` the first time it's called and reuses
it for the lifetime of the process (thread-safe, created once behind a
lock). Pass your own `session=requests.Session()` only if you need custom
retry policy, a proxy, or mTLS -- otherwise the default is correct.

```python
import requests
from bridge.core import resolve_image_urls

# Optional: bring your own session (e.g. with retries configured)
custom_session = requests.Session()
payload = resolve_image_urls(payload, session=custom_session)
```

**Live-verified** (region us-east-1): the default session is created on
first call and the identical `Session` object is confirmed reused on a
second call; its HTTPS adapter reports a pool size of 10 connections per
host.

## Caching (`cache`)

If the same image URL appears across multiple requests -- a recurring
prompt template, a retry, a batch of requests referencing a shared asset
-- there's no reason to re-download and re-verify it every time. Pass any
object that behaves like a `MutableMapping[str, str]` (a plain `dict` is
enough) as `cache=`, and `resolve_image_urls()` will:

1. Check `url in cache` before downloading. On a hit, return the cached
   `data:` URI immediately -- no network call, no re-verification.
2. On a miss, download and verify as normal, then write
   `cache[url] = data_uri` before returning.

```python
cache: dict[str, str] = {}  # or any dict-like store you already have

payload = resolve_image_urls(payload, cache=cache)          # downloads
payload_again = resolve_image_urls(other_payload, cache=cache)  # reuses if same URL
```

**No caching happens unless you pass one in** -- this is the exact same
behavior as before caching was added if you don't opt in. The bridge does
not create, own, size-limit, or expire anything on your behalf. What you
pass in is what gets used, verbatim:

- A plain `dict()` -- unbounded, process-lifetime, no eviction. Fine for
  a single Lambda invocation reusing images within a request, or a short
  batch job.
- `functools.lru_cache`-style wrapping, or any bounded LRU dict
  implementation -- if you need a size cap.
- A Redis- or DynamoDB-backed object implementing
  `__contains__`/`__getitem__`/`__setitem__` -- if you need the cache to
  survive process restarts or be shared across concurrent
  Lambda/container instances. The bridge has no idea what's backing the
  mapping; any object satisfying that minimal interface works.

**Live-verified** (region us-east-1): first `resolve_image_urls()` call
against a real https image URL took ~0.07s (network download + Pillow
verification); an identical second call with the same `cache` dict took
0.000s (skip). The resolved payload was byte-identical between the two
calls, and the cached `data:` URI was confirmed to work in an actual
`bedrock-mantle` Chat Completions call.

## What this does *not* do

- No TTL, no expiration, no invalidation -- if the underlying image
  changes at the same URL, a caller-owned cache will keep serving the
  stale version until the caller clears it.
- No cross-process sharing by default -- a plain `dict` is scoped to one
  process. Share a Redis/DynamoDB-backed mapping instead if that matters.
- No concurrency control -- see [SCALING.md](./SCALING.md) for bounding
  concurrent fetches; caching and concurrency limits are independent
  concerns and compose fine together.
