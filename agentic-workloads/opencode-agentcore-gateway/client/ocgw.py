#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""ocgw: sign-in helper and local token proxy for opencode on an Amazon Bedrock AgentCore Gateway.

  ocgw login [--no-browser]   OIDC authorization code + PKCE against your IdP (Okta, JumpCloud or Cognito), loopback redirect
  ocgw token                  print a valid token (refreshes silently)
  ocgw claims                 print the decoded claims of the cached token (no signature check)
  ocgw proxy                  run the local proxy opencode talks to: 127.0.0.1 only, a per-machine key, and a fresh IdP
                              token added to every request, so a long opencode session never sends an expired token
  ocgw opencode-config        write the opencode provider block (~/.config/opencode/opencode.json) for this gateway
  ocgw logout                 delete the cached tokens

Config (~/.config/ocgw/config.json, then env, then the MDM file, which wins):
  OCGW_OIDC_ISSUER     issuer URL, for example https://<org>.okta.com/oauth2/default or https://oauth.id.jumpcloud.com/
  OCGW_OIDC_CLIENT_ID  the native (public) app's client ID
  OCGW_TOKEN_KIND      id_token (default) or access_token, whichever the gateway validates
  OCGW_GATEWAY_URL     https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com
  websearch_url        (config file only) the web search MCP gateway URL; the proxy serves it at 127.0.0.1:<port>/mcp
MDM files: /Library/Application Support/OpencodeGateway/ocgw.json (macOS), /etc/opencode-gateway/ocgw.json (Linux),
%ProgramData%\\OpencodeGateway\\ocgw.json (Windows).
Only the Python standard library is used, so MDM can ship this single file.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import http.server
import contextlib
import errno
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8765
LOGIN_WAIT_S = 600  # how long `ocgw login` waits for the browser sign-in to come back
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
HOME_DIR = Path.home() / ".config" / "ocgw"
TOKENS = HOME_DIR / "tokens.json"


class _Exclusive:
    """Bind so no other socket can share the port. http.server sets SO_REUSEADDR, which on Windows lets another
    account's socket bind the same 127.0.0.1 port while this one listens (r196)."""
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            import socket  # noqa: PLC0415
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _LoginServer(_Exclusive, http.server.HTTPServer):
    pass


class _ProxyServer(_Exclusive, http.server.ThreadingHTTPServer):
    pass


def admin_owned(p: Path) -> bool:
    """Windows: True when SYSTEM (S-1-5-18) or BUILTIN\\Administrators (S-1-5-32-544) owns p. Every local user may
    create files under C:\\ProgramData, so a file there that MDM did not write must not choose the issuer (r196)."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415
    owner, sd, sid = ctypes.c_void_p(), ctypes.c_void_p(), wintypes.LPWSTR()
    adv = ctypes.windll.advapi32  # type: ignore[attr-defined]
    if adv.GetNamedSecurityInfoW(str(p), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(sd)) != 0:
        return False  # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION; a failed lookup is not an administrator
    try:
        if not adv.ConvertSidToStringSidW(owner, ctypes.byref(sid)):
            return False
        try:
            return sid.value in ("S-1-5-18", "S-1-5-32-544")
        finally:
            ctypes.windll.kernel32.LocalFree(sid)  # type: ignore[attr-defined]
    finally:
        ctypes.windll.kernel32.LocalFree(sd)  # type: ignore[attr-defined]


def program_data() -> Path:
    """Windows' ProgramData folder from the shell's known-folder API, never from the environment: a repository's
    .env or a user's shell could otherwise set ProgramData and choose which file counts as the managed config."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
        guid = (ctypes.c_byte * 16)(*uuid.UUID("62AB5D82-FDC1-4DC3-A9DD-070D1D495D97").bytes_le)  # FOLDERID_ProgramData
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(out)) == 0:  # type: ignore[attr-defined]
            try:
                return Path(out.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, wintypes.LPVOID))  # type: ignore[attr-defined]
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    return Path(r"C:\ProgramData")


# MDM-installed config per OS (macOS, Linux, Windows), then the per-user file (read first, so MDM values win).
# Only absolute paths, so a checked-out repository can never supply the issuer through a relative one.
CONFIG_PATHS = [p for p in [Path("/Library/Application Support/OpencodeGateway/ocgw.json"), Path("/etc/opencode-gateway/ocgw.json"),
                            *([program_data() / "OpencodeGateway" / "ocgw.json"] if os.name == "nt" else []),
                            HOME_DIR / "config.json"] if p.is_absolute()]


def config() -> dict:
    cfg = {"issuer": "", "client_id": "", "token_kind": "id_token", "scope": "openid email profile offline_access",
           "gateway_url": "", "websearch_url": "", "proxy_port": 8766}
    user_file = HOME_DIR / "config.json"
    if user_file.exists():
        cfg.update(json.loads(user_file.read_text()))
    env = {"OCGW_OIDC_ISSUER": "issuer", "OCGW_OIDC_CLIENT_ID": "client_id", "OCGW_TOKEN_KIND": "token_kind", "OCGW_GATEWAY_URL": "gateway_url"}
    for k, v in env.items():
        if os.environ.get(k):
            cfg[v] = os.environ[k]
    # MDM files last: a value MDM sets cannot be overridden by the environment (a repo's .env or a shell profile), which
    # would send the refresh token to another issuer's token endpoint.
    for p in CONFIG_PATHS:
        if p != user_file and p.exists():
            if os.name == "nt" and not admin_owned(p):
                sys.exit(f"ocgw: {p} is not owned by an administrator; ask IT to reinstall it")
            cfg.update(json.loads(p.read_text()))
    if not cfg["client_id"] or not cfg["issuer"]:
        sys.exit("ocgw: no issuer or client_id configured (see `ocgw --help`)")
    if cfg["token_kind"] not in ("id_token", "access_token"):
        sys.exit("ocgw: token_kind must be id_token or access_token")
    if type(cfg["proxy_port"]) is not int and not (isinstance(cfg["proxy_port"], str) and cfg["proxy_port"].isdigit()):
        sys.exit("ocgw: proxy_port must be a port number")
    cfg["proxy_port"] = int(cfg["proxy_port"])
    if not 0 <= cfg["proxy_port"] <= 65535:  # 0: the OS picks any free port (the tests use it)
        sys.exit("ocgw: proxy_port must be between 0 and 65535 (0 picks any free port)")
    return cfg


def https(url: str) -> str:
    """Tokens and the client secret only ever travel over TLS: refuse any other scheme (file:, http:, ...)."""
    if urllib.parse.urlsplit(url).scheme != "https":
        sys.exit(f"ocgw: refusing non-https URL {url!r}")
    return url


_DISCOVERY: dict = {}


def discovery(issuer: str) -> dict:
    """The IdP's OpenID configuration, fetched once per process (a refresh must not wait on it every time)."""
    if issuer in _DISCOVERY:
        return _DISCOVERY[issuer]
    doc = _fetch_discovery(issuer)
    if not isinstance(doc, dict) or not all(isinstance(doc.get(k), str) for k in ("token_endpoint", "authorization_endpoint")):
        # Not cached, so the next refresh fetches again; a ValueError is what the proxy reports as a 502.
        raise ValueError(f"{issuer} discovery document has no token_endpoint / authorization_endpoint")
    _DISCOVERY[issuer] = doc
    return doc


def _fetch_discovery(issuer: str) -> dict:
    with _urlopen(https(issuer.rstrip("/") + "/.well-known/openid-configuration"), timeout=15) as r:  # nosec B310: https enforced  # nosemgrep: dynamic-urllib-use-detected -- URL is an https endpoint from config/Terraform (scheme checked where user-supplied)
        return json.load(r)


class TokenRefused(SystemExit):
    """The token endpoint answered 4xx: a message for the user (as sys.exit would give) plus the OAuth error code
    ("invalid_grant", "invalid_client", ...; "" when the body has none), so a caller can tell a dead refresh token from
    a config fault or a proxy in the way."""

    def __init__(self, message: str, error: str = ""):
        super().__init__(message)
        self.error = error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would resend an Authorization header (and a POST body with a refresh token)
    to the new URL, plain http:// included. A 3xx becomes an HTTPError the caller reports."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urlopen(req, timeout):
    """Every HTTPS call ocgw makes with urllib (discovery, the token endpoint, the gateway's model list) goes through
    this one no-redirect opener, whoever calls it; the process-wide urllib opener is left alone."""
    return _OPENER.open(req, timeout=timeout)


def post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(https(url), data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with _urlopen(req, timeout=30) as r:  # nosec B310: https() checked the URL  # nosemgrep: dynamic-urllib-use-detected -- URL is an https endpoint from config/Terraform (scheme checked where user-supplied)
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code >= 500 or e.code == 429:  # IdP trouble or rate limiting: retryable, so 502 rather than "sign in again"
            raise OSError(f"token endpoint {e.code}") from None
        text = e.read(65536).decode(errors="replace")  # the whole body for the error code (an OAuth body can be long)
        try:
            err = json.loads(text).get("error", "")
        except (ValueError, AttributeError):
            err = ""
        raise TokenRefused(f"ocgw: token endpoint {e.code}: {text[:300]}", err if isinstance(err, str) else "") from None


_THREAD_LOCK = threading.RLock()
_REFRESHING = threading.Event()  # set while a proxy thread asks the IdP for a new token
# [the session as last written, when tokens.json could not store it; tokens.json's mtime_ns when that write failed].
# Only _persist() sets it and only _load() reads it.
_UNSAVED = [None, None]


@contextlib.contextmanager
def token_lock():
    """Exclusive lock around read-refresh-save: two helpers (or two proxy threads) refreshing at once would spend the
    same rotating refresh token, and the second refresh fails with invalid_grant. An in-process lock covers the proxy's
    threads; a file lock (fcntl on Unix, msvcrt on Windows) covers separate processes."""
    HOME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # 0600: another user who can open the lock file could hold a lock on it and stop every refresh (r196).
    fd = os.open(HOME_DIR / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    with _THREAD_LOCK, os.fdopen(fd, "r+b") as f:
        try:
            import fcntl  # noqa: PLC0415 - Unix
        except ImportError:
            import msvcrt  # noqa: PLC0415 - Windows
            f.seek(0)
            # LK_LOCK retries for about 10 s, then raises EDEADLOCK (EACCES on some builds) while another process holds
            # the byte. Try that 6 times (about a minute), then give up; any other error is not contention and is raised
            # at once, so a broken lock file cannot hang every proxy thread behind _THREAD_LOCK.
            busy = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK), errno.EDEADLK}
            for attempt in range(6):
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError as e:
                    if e.errno not in busy or attempt == 5:
                        sys.exit(f"ocgw: cannot lock {HOME_DIR / '.lock'}: {e}")
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            return
        os.fchmod(f.fileno(), 0o600)  # a lock file an older version left 0644
        # Bounded like the Windows branch: about a minute, then a message instead of a hang behind _THREAD_LOCK.
        for attempt in range(1200):
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if attempt == 1199:
                    sys.exit(f"ocgw: {HOME_DIR / '.lock'} has been locked by another process for a minute; "
                             "stop it or delete the file")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def save(tok: dict) -> None:
    """Store a token response just received: obtained_at is now (expiry() times expires_in from it). Raises the
    OSError when tokens.json cannot be written; this process keeps using the tokens either way."""
    err = _persist(tok, stamp=True)
    if err is not None:
        raise err


def _file_stamp():
    """tokens.json's modification time in ns, or None while there is no file."""
    try:
        return TOKENS.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def _persist(tok: dict, stamp: bool = False):
    """The only way the session is written. On success the in-memory copy is forgotten (the file is current); on
    failure this process keeps the tokens in memory, with tokens.json's stamp at that moment, and gets the OSError back
    rather than raised, so a failed write never hides the answer the caller was about to give."""
    if stamp:
        tok["obtained_at"] = int(time.time())
    try:
        _store(tok)
    except OSError as e:
        _UNSAVED[:] = [dict(tok), _file_stamp()]
        return e
    _UNSAVED[:] = [None, None]
    return None


def _load() -> dict:
    """The only way the session is read: this process's unsaved copy while tokens.json is exactly as it was when that
    copy failed to save, else tokens.json itself - anything another process wrote or removed since is newer."""
    mem, at = _UNSAVED
    if mem is not None:
        if _file_stamp() == at:
            return mem
        _UNSAVED[:] = [None, None]
    return json.loads(TOKENS.read_text())


def _store(tok: dict) -> None:
    """Write tokens.json as given (obtained_at untouched)."""
    HOME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Write a 0600 temp file and rename it over tokens.json, so a concurrent reader never sees a half-written file.
    tmp = TOKENS.with_name(f".tokens.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(tok))
        os.chmod(tmp, 0o600)
        _replace(tmp, TOKENS)
    except BaseException:
        tmp.unlink(missing_ok=True)  # a failed write or rename leaves no second copy of the session on disk
        raise


def _retry_in_use(fn, *args, tries=20):
    """fn(*args), retried for about a second on PermissionError: on Windows a rename or delete fails while another
    thread (the lock-free read in get_token) or program has the file open."""
    for i in range(tries):
        try:
            return fn(*args)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


def _replace(src, dst):
    """os.replace over dst, retried while dst is in use (giving up would drop a rotated refresh token for good)."""
    return _retry_in_use(os.replace, src, dst)


def claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def login(open_browser: bool = True) -> None:
    cfg = config()
    meta = discovery(cfg["issuer"])
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = https(meta["authorization_endpoint"]) + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cfg["client_id"], "redirect_uri": REDIRECT, "scope": cfg["scope"],
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
            if q.get("state") != state or not ("code" in q or "error" in q):
                # Any page in the browser can hit the loopback port; only the IdP's redirect carries our state.
                self.send_response(404)
                self.end_headers()
                return
            result.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and return to the terminal.")

        def log_message(self, *a):
            pass

    # A connection that sends nothing (a browser preconnect) is dropped after 5 s instead of blocking the one-at-a-time
    # server, so the real redirect behind it is still served and the login deadline below is still checked.
    Handler.timeout = 5
    server = _LoginServer(("127.0.0.1", PORT), Handler)
    print("Open this URL to sign in:\n" + url, file=sys.stderr, flush=True)
    if open_browser:
        webbrowser.open(url)
    # Give up after LOGIN_WAIT_S: a closed tab or an IdP error page never redirects back, and the port must not stay
    # held (a second `ocgw login` would fail with "Address already in use").
    server.timeout = 1  # handle_request returns at least once a second, so the deadline is checked
    give_up = time.monotonic() + LOGIN_WAIT_S
    try:
        while "code" not in result and "error" not in result and time.monotonic() < give_up:
            server.handle_request()
    finally:
        server.server_close()
    if "code" not in result and "error" not in result:
        sys.exit(f"ocgw: no sign-in came back within {LOGIN_WAIT_S // 60} minutes; run `ocgw login` again")
    if result.get("state") != state or "code" not in result:
        sys.exit(f"ocgw: sign-in failed: {result}")
    tok = post_form(meta["token_endpoint"], {
        "grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT,
        "client_id": cfg["client_id"], "code_verifier": verifier})
    if not isinstance(tok, dict) or not isinstance(tok.get(cfg["token_kind"]), str):
        sys.exit(f"ocgw: the sign-in provider returned no {cfg['token_kind']}; check that the app grants the openid "
                 "scope, then run `ocgw login` again")
    if not tok.get("refresh_token"):  # absent or null: no offline_access; the session ends with the token
        tok.pop("refresh_token", None)
    with token_lock():  # wait out a refresh in flight (a running proxy), so its old-session tokens cannot overwrite these
        save(tok)
    print(f"Signed in as {_who(tok, cfg['token_kind'])}", file=sys.stderr)


def _claims_or_empty(tok: dict, kind: str) -> dict:
    """The unverified payload of tok[kind] when it is a three-part JWT whose payload is a JSON object, else {} (no
    token, not a string, bad base64 or JSON, or a payload that is not an object). For reading, never for trusting."""
    t = tok.get(kind)
    if not isinstance(t, str) or t.count(".") != 2:
        return {}
    try:
        c = claims(t)
    except (ValueError, TypeError, RecursionError):  # bad base64 or JSON, or JSON nested past the parser's limit
        return {}
    return c if isinstance(c, dict) else {}


def _who(tok: dict, kind: str) -> str:
    """A name for the person who just signed in: the ID token's email or preferred_username when the IdP sent one
    (a Cognito access token has neither), then the handed-out token's email or username, then its subject."""
    idt, own = _claims_or_empty(tok, "id_token"), _claims_or_empty(tok, kind)
    return (idt.get("email") or idt.get("preferred_username") or own.get("email") or own.get("username")
            or own.get("sub") or "unknown user")


# Refresh when less than this much life is left, so a token handed out still outlives a long streamed reply.
MARGIN = 300
# A session left without its token by a refresh is refreshed again at most this often.
MISSING_RETRY_S = 300
REFRESH_RETRY_S = 30  # after an early refresh fails, how long a still-valid token is used before the IdP is asked again
_REFRESH_FAILED_AT = [0.0]  # this process's copy of refresh_failed_at, for when tokens.json cannot be written


def expiry(tok: dict, kind: str) -> tuple:
    """(expires_at, lifetime) of the token handed out: its own exp claim, else obtained_at + the response's expires_in.
    A token whose expiry cannot be read (no exp, no usable obtained_at/expires_in, a payload that is not an object)
    counts as expired, so the caller refreshes or asks for a sign-in instead of failing."""
    c = _claims_or_empty(tok, kind)
    exp, iat = _num(c.get("exp")), _num(c.get("iat"))
    life, at = _num(tok.get("expires_in")), _num(tok.get("obtained_at"))
    if exp is not None:
        if iat is not None:
            # Compared with the local clock as is, so ocgw needs a roughly correct system clock (NTP);
            # one ahead by more than the token's lifetime makes every request refresh (README, Limits).
            return exp, exp - iat
        # No iat: the token was issued when it was saved, so its lifetime is exp - obtained_at (the response's
        # expires_in is the access token's, which can differ from the id_token's).
        if at is not None and exp > at:
            return exp, exp - at
        # exp <= obtained_at: the local clock is ahead of the IdP's, so comparing exp with time.time() would call the
        # token expired at once and refresh on every request. Time it on the local clock instead: obtained_at + life.
        if at is not None and life and life > 0:
            return at + life, life
        return exp, None
    if life is None or at is None:
        return 0.0, None
    return at + life, life or None


def _num(x):
    """x as a float when it is a JSON number (not a bool, which Python counts as an int), else None."""
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _due(tok: dict, kind: str) -> bool:
    """True when the token should be refreshed now (inside MARGIN of its expiry, or its expiry cannot be read)."""
    expires, life = expiry(tok, kind)
    return time.time() > expires - (min(MARGIN, max(life - 60, 0)) if life else MARGIN)


def _usable(tok: dict, kind: str) -> bool:
    """The cached token of this kind is present and not yet expired (it may be inside MARGIN, due for a refresh)."""
    return isinstance(tok.get(kind), str) and time.time() < expiry(tok, kind)[0]


def get_token(cfg: dict) -> str:
    if not TOKENS.exists():
        sys.exit("ocgw: not signed in, run `ocgw login`")
    # Fast path, no lock: a valid token is read and returned at once, so the proxy's other requests never wait behind
    # one that is refreshing. A torn or odd read just falls through to the locked path below.
    try:
        tok = _load()
        if isinstance(tok, dict) and isinstance(tok.get(cfg["token_kind"]), str):
            if not _due(tok, cfg["token_kind"]):
                return tok[cfg["token_kind"]]
            backoff = time.time() - _REFRESH_FAILED_AT[0] < REFRESH_RETRY_S
            if (_REFRESHING.is_set() or backoff) and _usable(tok, cfg["token_kind"]):
                # Another thread is refreshing, or one failed moments ago: the cached token still works meanwhile.
                return tok[cfg["token_kind"]]
    except (OSError, ValueError):
        pass
    with token_lock():
        try:  # re-read under the lock: another helper may have just refreshed
            tok = _load()
        except FileNotFoundError:  # `ocgw logout` in another shell
            sys.exit("ocgw: not signed in, run `ocgw login`")
        except (OSError, ValueError):
            sys.exit("ocgw: the saved session is unreadable, run `ocgw login`")
        if not isinstance(tok, dict):
            sys.exit("ocgw: the saved session is unreadable, run `ocgw login`")
        missing = not isinstance(tok.get(cfg["token_kind"]), str)
        if missing and not tok.get("refresh_token"):
            sys.exit(f"ocgw: the saved session has no {cfg['token_kind']}, run `ocgw login`")
        # No token but a refresh token: an earlier refresh came back without one (OIDC allows that), so try again,
        # but at most once every MISSING_RETRY_S: an IdP that never returns one must not get a refresh per request.
        if missing and time.time() - (_num(tok.get("obtained_at")) or 0) < MISSING_RETRY_S:
            sys.exit(f"ocgw: the sign-in provider's last refresh returned no {cfg['token_kind']}, run `ocgw login`")
        if missing or _due(tok, cfg["token_kind"]):
            kind = cfg["token_kind"]
            # Inside MARGIN the cached token still works: a refresh that fails, for any reason, must not fail the request.
            still_valid = _usable(tok, kind)
            if not tok.get("refresh_token"):  # absent, null, or dropped after invalid_grant
                if still_valid:  # the refresh token was refused earlier; the session lasts until the token expires
                    return tok[kind]
                sys.exit("ocgw: session expired, run `ocgw login`")
            failed_at = max(_num(tok.get("refresh_failed_at")) or 0, _REFRESH_FAILED_AT[0])
            if still_valid and time.time() - failed_at < REFRESH_RETRY_S:
                return tok[kind]  # an early refresh failed moments ago: do not make every request wait on the IdP
            if not still_valid and time.time() - failed_at < REFRESH_RETRY_S:
                # Expired, and the IdP failed moments ago: fail fast (the proxy's 502) rather than have every request
                # queue on the token lock for another ~45 s attempt.
                raise OSError(f"the sign-in provider failed less than {REFRESH_RETRY_S} s ago; try again in a moment")
            try:
                _REFRESHING.set()
                try:
                    new = post_form(discovery(cfg["issuer"])["token_endpoint"], {
                        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cfg["client_id"]})
                finally:
                    _REFRESHING.clear()
                if not isinstance(new, dict):  # the proxy reports a ValueError as a 502
                    raise ValueError("the token endpoint returned something other than a JSON object")
            except (TokenRefused, OSError, ValueError, http.client.HTTPException) as e:
                # One rule for a failed refresh: a refusal, the IdP down or unreachable, or an unreadable answer (a
                # captive portal's HTML, a cut reply). Only invalid_grant means this refresh token is dead: drop it, so
                # once the token expires the user is told to sign in instead of the IdP being asked on every call;
                # invalid_client or a WAF 403 is a fault elsewhere, and the token stays for when it is fixed.
                # obtained_at stays as it was, so an expired token never looks fresh again. Expiry is checked again
                # now (the attempt can take ~45 s), and the failure time is stored in tokens.json so every entry point
                # (the proxy, `ocgw token`, opencode-config) backs off together.
                _REFRESH_FAILED_AT[0] = time.time()
                keep = {**tok, "refresh_failed_at": _REFRESH_FAILED_AT[0]}
                if isinstance(e, TokenRefused) and e.error == "invalid_grant":
                    keep.pop("refresh_token", None)
                _persist(keep)  # a failed write must not hide the refusal; this process keeps `keep` meanwhile
                if _usable(tok, kind):
                    return tok[kind]  # inside MARGIN the cached token still works
                if isinstance(e, TokenRefused) and e.error == "invalid_grant":  # the session is over: say what to do
                    sys.exit(f"ocgw: session expired, run `ocgw login` ({str(e).removeprefix('ocgw: ')[:200]})")
                raise
            if not new.get("refresh_token"):  # absent, or an explicit null: the old one stays
                new["refresh_token"] = tok["refresh_token"]
            if not new.get(kind):
                if _usable(tok, kind):
                    # OIDC makes id_token optional on refresh. Inside MARGIN keep the still-valid token, with the
                    # rotated refresh token, and the old obtained_at so expiry() times it as before.
                    _REFRESH_FAILED_AT[0] = time.time()
                    # Kept in memory if the write fails, so the rotated refresh token is the one used next.
                    _persist({**tok, "refresh_token": new["refresh_token"], "refresh_failed_at": _REFRESH_FAILED_AT[0]})
                    return tok[kind]
                # Expired: keep the rotated refresh token (the next request refreshes with it), and drop the old token
                # rather than save it again under a new obtained_at (expiry() would then time it afresh).
                # Neither old token is kept: under the new obtained_at an old access_token would look fresh too.
                old = {k: v for k, v in tok.items() if k not in ("id_token", "access_token")}
                _persist({k: v for k, v in {**old, **new}.items() if k != kind}, stamp=True)  # never hides the message
                sys.exit(f"ocgw: the sign-in provider's refresh returned no {kind}, run `ocgw login`")
            err = _persist(new, stamp=True)  # kept in memory on failure: later calls use it and its refresh token
            if err is not None:
                print(f"ocgw: could not save the refreshed sign-in to {TOKENS}: {err}", file=sys.stderr)
            tok = new
    return tok[cfg["token_kind"]]


LOCAL_KEY = HOME_DIR / "local.key"
UPSTREAM = None  # connection class for the gateway; None = http.client.HTTPSConnection (tests swap in a fake)
FORWARD = ("/inference/v1/chat/completions", "/inference/v1/responses", "/inference/v1/models", "/inference/websearch")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
       "host", "content-length", "authorization", "accept-encoding"}
# What relay_reply never copies from the upstream: the hop-by-hop set, plus the framing and identity headers it writes
# itself (send_response writes Server and Date; the framing follows the client's HTTP version).
_REPLY_SKIP = frozenset(HOP | {"server", "date"})
# The request headers the proxy passes on (Authorization is its own): what the chat and Responses APIs and an MCP
# Streamable HTTP session use. Everything else the client sends stays on this machine.
FORWARD_HEADERS = ("Content-Type", "Accept", "Mcp-Session-Id", "Mcp-Protocol-Version", "Last-Event-ID", "User-Agent")


def connection_named(msg) -> set:
    """The header names a message's own Connection header lists: hop-by-hop for that message too (RFC 9110 7.6.1)."""
    return {t.strip().lower() for v in (msg.get_all("Connection") or []) for t in v.split(",") if t.strip()}


class FinalResponse(http.client.HTTPResponse):
    """An upstream response that skips every interim 1xx (102 Processing, 103 Early Hints), as http.client already
    does for 100 Continue, so the reply relayed is the final one and a model call that ran is never lost behind one."""

    def _read_status(self):
        while True:
            version, status, reason = super()._read_status()
            if not 100 < status < 200 or status == 101:  # 100 is begin()'s own; 101 switches protocol, never skipped
                return version, status, reason
            http.client.parse_headers(self.fp)  # the interim reply's headers, read and dropped


def fail_reply(handler, code, msg):
    """The proxy's own JSON error, and the end of the connection: the request body may still be unread on the socket,
    so it is closed rather than parsed as the next request."""
    body = json.dumps({"error": {"message": msg, "type": "ocgw_proxy_error", "code": code}}).encode()
    handler.close_connection = True
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Connection", "close")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":  # a response to HEAD carries the headers only
        handler.wfile.write(body)


def relay_reply(handler, r, tee=None):
    """Send the upstream reply r (an http.client response) to handler's client, streaming. The one set of framing rules
    the proxy keeps (the E2E reply recorder reuses it): an interim 1xx that got this far (FinalResponse skips them)
    is refused (502); no body for 204 and 304; chunked for HTTP/1.1; for an
    HTTP/1.0 client the upstream Content-Length when there is one, else a body ended by the close; and a reply cut
    short upstream is never finished as if whole (chunked: no terminator; HTTP/1.0: fewer bytes than its length).
    tee(chunk) sees each chunk before it is sent; an error it raises reaches the caller. Returns "ok", "client gone" (it hung up: nothing to tell it) or
    "upstream failed: <error>"."""
    def send(data):
        try:
            handler.wfile.write(data)
            handler.wfile.flush()
            return True
        except OSError:
            handler.close_connection = True
            return False
    length = r.length  # what a Content-Length reply owes before reading; None for a chunked or close-delimited one
    if 100 <= r.status < 200:
        # A safety net: the proxy's FinalResponse skips 102 and 103, so only a caller with a plain http.client
        # response (or a 101) gets here, and an interim reply relayed as the answer would end the client's request.
        try:
            fail_reply(handler, 502, f"the gateway sent an interim reply ({r.status}) instead of an answer")
        except OSError:
            return "client gone"
        return f"upstream failed: interim reply {r.status}"
    try:
        handler.send_response(r.status)
        skip = _REPLY_SKIP | connection_named(r.msg)  # and whatever the upstream's own Connection header names
        for k, v in r.getheaders():
            if k.lower() not in skip:
                handler.send_header(k, v)
        bodyless = r.status in (204, 304)  # RFC 9110: a terminator here would be misread as the next reply
        chunked = not bodyless and handler.request_version not in ("HTTP/1.0", "HTTP/0.9")
        if chunked:
            handler.send_header("Transfer-Encoding", "chunked")
        elif not bodyless:
            handler.close_connection = True
            if length is not None:
                handler.send_header("Content-Length", str(length))
        if handler.close_connection:  # a body the close ends, an HTTP/1.0 client, or one that asked to close: say so
            handler.send_header("Connection", "close")
        handler.end_headers()
    except OSError:
        handler.close_connection = True
        return "client gone"
    if bodyless:
        return "ok"
    read = r.read1 if hasattr(r, "read1") else r.read
    while True:
        try:  # only the upstream read is an upstream failure; a tee that raises is its caller's error, not the gateway's
            chunk = read(65536)
            if not chunk and r.length:  # a Content-Length body cut short: read1 returns b"" at early EOF, no raise
                raise http.client.IncompleteRead(b"", r.length)
        except (OSError, http.client.HTTPException) as e:
            handler.close_connection = True  # no error body is possible once headers are out: close, unterminated
            return f"upstream failed: {type(e).__name__}"
        if not chunk:
            break
        if tee:
            tee(chunk)
        if not send(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n" if chunked else chunk):
            return "client gone"
    if chunked and not send(b"0\r\n\r\n"):
        return "client gone"
    return "ok"


_KEY_FORMAT = re.compile(r"ocgw-[A-Za-z0-9_-]{43,256}")  # "ocgw-" + secrets.token_urlsafe(32), or a longer key


def local_key() -> str:
    """The per-machine key opencode sends to the proxy (0600 file). A page in the browser or another local user cannot
    use the proxy without it, so the proxy never spends the signed-in user's token for someone else."""
    HOME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not LOCAL_KEY.exists():
        # Write the whole key to a private temp file, then link it into place: the link either creates local.key with
        # its full content or fails because another process (a second proxy, opencode-config) got there first, in
        # which case that key wins. No caller ever sees an empty or half-written key file.
        tmp = HOME_DIR / f".local.key.{os.getpid()}.{secrets.token_hex(4)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write("ocgw-" + secrets.token_urlsafe(32))
            try:
                os.link(tmp, LOCAL_KEY)
            except FileExistsError:
                pass
            except OSError:  # no hard links on this filesystem (some network, FUSE or exFAT homes): create it directly
                with contextlib.suppress(FileExistsError):
                    kfd = os.open(LOCAL_KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        with os.fdopen(kfd, "w") as f:
                            f.write(tmp.read_text())
                    except OSError:  # a failed write (a full disk) must not leave an empty key behind
                        LOCAL_KEY.unlink(missing_ok=True)
                        raise
        finally:
            tmp.unlink(missing_ok=True)
    # Without hard links another process may have created the file a moment ago and still be writing it: wait briefly.
    # An empty key is never used: the proxy would then accept an empty bearer.
    for _ in range(40):
        try:
            with LOCAL_KEY.open() as f:
                key = f.read(512).strip()  # bounded: a stray huge file is refused, not loaded
        except FileNotFoundError:  # the other process's write failed and it removed the file
            key = ""
        if _KEY_FORMAT.fullmatch(key):  # complete: a half-written key is never used
            return key
        time.sleep(0.05)
    sys.exit(f"ocgw: {LOCAL_KEY} is empty, incomplete or missing; delete it if present and run again")


def proxy() -> None:
    """Forward opencode's requests to the gateway with a fresh IdP token. Binds 127.0.0.1 only."""
    import hmac  # noqa: PLC0415
    cfg = config()
    gw = urllib.parse.urlsplit(https(cfg["gateway_url"] or sys.exit("ocgw: gateway_url is not configured")))
    # Web search MCP gateway (optional): opencode's remote MCP server "web-search" points at 127.0.0.1:<port>/mcp.
    ws = urllib.parse.urlsplit(https(cfg["websearch_url"])) if cfg.get("websearch_url") else None
    for name, u in (("gateway_url", gw), ("websearch_url", ws)):
        try:
            if u:
                u.port  # noqa: B018 - urlsplit only checks the port when it is read
        except ValueError:
            sys.exit(f"ocgw: {name} has an invalid port")
    key = local_key()

    class Proxy(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 120  # reading a request: a client that stalls mid-request (or idles on keep-alive) frees its thread

        def handle_one_request(self):
            self.connection.settimeout(self.timeout)  # each request is read under the timeout again
            # Cleared per request: a malformed or timed-out request line is logged before the stdlib sets them, and
            # on keep-alive they would still name the previous request.
            self.command = self.path = None
            super().handle_one_request()

        def handle_expect_100(self):
            # The stdlib would answer 100 Continue before the local key or the length is checked. A model client
            # sends its body at once; refuse the expectation instead (RFC 9110 10.1.1), and close.
            fail_reply(self, 417, "send the request without Expect: 100-continue")
            return False

        def _forward(self):
            if self.request_version == "HTTP/0.9":  # no status line or headers exist to answer with: just close
                self.close_connection = True
                return
            if "close" in connection_named(self.headers):  # the stdlib honours only a bare "Connection: close"
                self.close_connection = True
            got = self.headers.get("Authorization", "")
            if not hmac.compare_digest(got.encode(), f"Bearer {key}".encode()):
                return fail_reply(self, 401, "missing or wrong local key (run `ocgw opencode-config`)")
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            if parts.query:
                # No gateway route takes a query string; refuse it in the open rather than drop it silently.
                return fail_reply(self, 400, "ocgw proxy does not forward query strings")
            if self.command not in ("GET", "POST") and not (self.command == "DELETE" and path == "/mcp" and ws):
                # Only MCP session termination uses DELETE; answer anything else in JSON rather than stdlib's HTML 501.
                return fail_reply(self, 405, f"ocgw proxy does not forward {self.command} {path}")
            up, up_path = gw, path
            if path == "/mcp" and ws:
                up, up_path = ws, (ws.path or "/mcp") + (f"?{ws.query}" if ws.query else "")  # as configured
            elif path not in FORWARD:
                return fail_reply(self, 404, f"ocgw proxy forwards only {', '.join(FORWARD)}" + (" and /mcp" if ws else ""))
            # Framing first: a request that will be refused must not spend a sign-in refresh (or get a 401 or 502).
            if self.headers.get_all("Transfer-Encoding"):  # on any line, chunked or not: only a Content-Length frames it
                return fail_reply(self, 411, "send the request body with a Content-Length")
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,12}", lengths[0].strip())):
                return fail_reply(self, 400, "bad Content-Length")  # RFC 9110: one value, ASCII digits only
            n = int(lengths[0]) if lengths else 0
            if n > 32 * 1024 * 1024:
                return fail_reply(self, 413, "request body larger than 32 MiB")
            # Only the headers a model call or an MCP session needs go on, each sent once: nothing else the client
            # sent (cookies, Expect, hop-by-hop headers, a spoofed identity) reaches the gateway, nor an allowed one
            # this request's own Connection header names (RFC 9110 7.6.1).
            named = connection_named(self.headers)
            headers = {}
            for k in FORWARD_HEADERS:
                vals = self.headers.get_all(k) or []
                if len(vals) > 1:
                    return fail_reply(self, 400, f"send the {k} header once")
                if vals and k.lower() not in named:
                    headers[k] = vals[0]
            try:
                tok = get_token(cfg)
            except SystemExit as e:
                return fail_reply(self, 401, str(e))
            except (OSError, ValueError, http.client.HTTPException) as e:  # IdP unreachable, a bad or cut-off answer
                return fail_reply(self, 502, f"could not refresh the sign-in token: {type(e).__name__}: {str(e)[:200]}")
            try:
                body = self.rfile.read(n) if n else None
            except OSError:  # reset, or timed out mid-body: nobody is left to read a reply
                self.close_connection = True
                return
            if n and len(body) < n:  # the client closed (or timed out) before sending the whole body
                return fail_reply(self, 400, "request body shorter than its Content-Length")
            # The response may be a long event stream: the 120 s read timeout would cut off a slow reader, but a
            # client that stops reading for 10 minutes has gone, and its thread is freed.
            self.connection.settimeout(600)
            headers["Authorization"] = f"Bearer {tok}"
            # A fresh connection per request, on purpose: a pooled one needs a retry when the gateway has closed it,
            # and a POST retried after a reset may already have run (a model call billed twice). One TLS handshake per
            # call is the cheaper cost.
            # The MCP GET event stream may sit idle between server notifications: allow an hour of silence there.
            idle = 3600 if (path == "/mcp" and self.command == "GET") else 900
            conn = (UPSTREAM or http.client.HTTPSConnection)(up.hostname, up.port or 443, timeout=idle)
            conn.response_class = FinalResponse  # an interim 1xx is skipped, never relayed as the answer
            try:
                conn.request(self.command, up_path, body=body, headers=headers)
                r = conn.getresponse()
            except (OSError, http.client.HTTPException) as e:  # gateway unreachable, TLS reset, timeout
                conn.close()
                return fail_reply(self, 502, f"could not reach the gateway: {type(e).__name__}")
            try:
                outcome = relay_reply(self, r)
                if outcome.startswith("upstream failed"):
                    print(f"ocgw proxy: {outcome}", file=sys.stderr)
            finally:
                conn.close()

        # HEAD and OPTIONS too, so every method gets the proxy's JSON answer (405 if not forwarded), never stdlib's HTML 501.
        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_HEAD = do_OPTIONS = _forward  # noqa: N815

        def log_message(self, fmt, *a):
            # command and path are None for a request line the stdlib could not parse (handle_one_request clears them).
            path = urllib.parse.urlsplit(getattr(self, "path", None) or "").path
            print(f"ocgw proxy: {getattr(self, 'command', None) or '-'} {path} {a[1] if len(a) > 1 else ''}", file=sys.stderr)

    server = _ProxyServer(("127.0.0.1", int(cfg["proxy_port"])), Proxy)
    # The port it bound, not the one configured: with proxy_port 0 the OS picks one, and 0 is not where to connect.
    print(f"ocgw proxy on http://127.0.0.1:{server.server_address[1]} -> {gw.hostname}", file=sys.stderr, flush=True)
    server.serve_forever()


def opencode_config(path: Path | None = None) -> Path:
    """Add (or replace) the `ocgw` provider in opencode's global config, pointing at the local proxy. Other providers
    and settings in the file are kept. The model list comes from the gateway (GET /v1/models, the caller's groups)."""
    cfg = config()
    if not cfg["proxy_port"]:  # 0 means "any free port", which no baseURL can name; opencode needs a fixed one
        sys.exit("ocgw: set proxy_port to a fixed port (not 0) before writing the opencode config")
    path = path or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "opencode.json"
    try:
        token = get_token(cfg)
    except (OSError, http.client.HTTPException, ValueError, KeyError) as e:  # the IdP, not the gateway
        sys.exit(f"ocgw: could not refresh the sign-in (run `ocgw login`): {e}")
    try:
        req = urllib.request.Request(https(cfg["gateway_url"].rstrip("/") + "/inference/v1/models"),
                                     headers={"Authorization": f"Bearer {token}"})
        with _urlopen(req, timeout=20) as r:  # nosec B310: https() checked the URL  # nosemgrep: dynamic-urllib-use-detected -- https gateway URL from MDM/user config
            reply = json.load(r)
        data = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(data, list) or not all(isinstance(m, dict) and isinstance(m.get("id"), str) for m in data):
            raise ValueError("the reply is not an OpenAI model list")
        models = {m["id"]: {"name": m.get("name") or m["id"]} for m in data}
    except urllib.error.HTTPError as e:  # the gateway answered: show its explanation (an identity_map hint, say)
        try:
            detail = json.loads(e.read() or b"{}").get("error", {}).get("message", "")
        except (OSError, ValueError, AttributeError):
            detail = ""
        detail = detail if isinstance(detail, str) else ""  # a number or an object is no explanation
        sys.exit(f"ocgw: could not list models from the gateway: HTTP {e.code}{': ' + detail if detail else ''}")
    except (OSError, http.client.HTTPException, ValueError, KeyError) as e:  # URLError, a stalled or cut reply too
        sys.exit(f"ocgw: could not list models from the gateway: {e}")
    try:
        doc = json.loads(path.read_text()) if path.exists() else {"$schema": "https://opencode.ai/config.json"}
    except ValueError as e:
        sys.exit(f"ocgw: {path} is not plain JSON ({e}); if it has comments or trailing commas (JSONC), remove them "
                 "or move the file aside, then run again")
    if not isinstance(doc, dict) or not isinstance(doc.setdefault("provider", {}), dict):
        sys.exit(f"ocgw: {path} must be a JSON object whose \"provider\" is an object; fix or move it, then run again")
    doc["provider"]["ocgw"] = {
        "npm": "@ai-sdk/openai-compatible", "name": "AgentCore Gateway (Amazon Bedrock)",
        "options": {"baseURL": f"http://127.0.0.1:{cfg['proxy_port']}/inference/v1", "apiKey": "{file:" + str(LOCAL_KEY) + "}"},
        "models": models}
    local_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    real = path.resolve()  # through a symlink (a dotfiles repo): rewrite the file it points at, keep the link
    # A temp file and a rename: never a half-written config. A random name and O_EXCL (which refuses any existing path,
    # a planted link included): the target's directory may be shared (a dotfiles repo).
    tmp = real.with_name(f".{real.name}.{secrets.token_hex(8)}.tmp")
    try:
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as e:
            if e.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise
            sys.exit(f"ocgw: cannot write next to {real} (read-only, e.g. a Nix or home-manager link); "
                     f"add the provider there, or replace {path} with a plain file, then run again")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(doc, indent=2) + "\n")
        # The file's own permissions stay (0600 when it holds other providers' keys); a new file gets 0600.
        os.chmod(tmp, (real.stat().st_mode & 0o777) if real.exists() else 0o600)
        _replace(tmp, real)
    finally:
        with contextlib.suppress(OSError):  # on a read-only mount this fails too; it must not hide the message above
            tmp.unlink(missing_ok=True)  # a failed write or rename leaves no copy of the config behind
    print(f"wrote provider 'ocgw' with {len(models)} models to {path}", file=sys.stderr)
    return path


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "token"
    if cmd == "login":
        login("--no-browser" not in sys.argv)
    elif cmd == "token":
        try:
            print(get_token(config()))
        except (OSError, ValueError, http.client.HTTPException) as e:  # the IdP unreachable or a bad answer
            sys.exit(f"ocgw: could not refresh the sign-in token: {e}")
    elif cmd == "claims":
        try:
            tok = _load()
        except FileNotFoundError:
            sys.exit("ocgw: not signed in, run `ocgw login`")
        except (OSError, ValueError):
            tok = None
        if not isinstance(tok, dict):  # a list or a string after a bad edit: the same message as every other command
            sys.exit("ocgw: the saved session is unreadable, run `ocgw login`")
        print(json.dumps({k: c for k in ("id_token", "access_token") if (c := _claims_or_empty(tok, k))}, indent=2))
    elif cmd == "proxy":
        proxy()
    elif cmd == "opencode-config":
        opencode_config()
    elif cmd == "logout":
        with token_lock():  # a refresh in flight (a running proxy) finishes first, so it cannot write the session back
            _retry_in_use(TOKENS.unlink, True)  # missing_ok=True; retried while Windows has the file open
            for stray in HOME_DIR.glob(".tokens.*.tmp"):  # a temp file an interrupted write left behind holds it too
                _retry_in_use(stray.unlink, True)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, http.client.HTTPException) as e:  # IdP unreachable, a bad or cut-off answer: a message, not a traceback
        sys.exit(f"ocgw: {type(e).__name__}: {e}")
