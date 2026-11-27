# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Helper that Terraform calls for what hashicorp/aws (v6.69) cannot create yet: Mantle Projects
and gateway rate limits.

Every command is idempotent (looks up by name first), so Terraform can re-run it safely.
Region and credentials come from AWS_PROFILE / AWS_REGION, which Terraform passes in.

  acgw.py projects-ensure  (stdin: {"users": "<json {name: tags}>"}) -> prints {name: '{"id","arn","name"}'}
  acgw.py project-archive <name>   (env OCGW_USER: archives only projects tagged with that User)
  acgw.py projects-retag  (env OCGW_TAGS_<n> = {"<project id>": tags}; keeps the owned tags in step; apply time)
  acgw.py ratelimit-ensure <gateway_id> '<rate limit json>'   (json carries a unique "description")
  acgw.py ratelimit-delete <gateway_id> <description>
  acgw.py seed-groups <table>   (env OCGW_GROUPS_<n> = {user: group}: the group Terraform seeds on each BUDGET item)
"""
import contextlib
import json
import os
import sys
import time
import urllib.parse
import uuid

import boto3
import botocore.config
import botocore.exceptions
import requests
import urllib3.exceptions as u3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentcore_wait  # noqa: E402

agentcore_wait.require_boto3(boto3.__version__)
agentcore_wait.drop_empty_profile()
try:  # a profile that does not exist (var.aws_profile) fails here, at import: a message, not a traceback
    S = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name=os.environ.get("AWS_REGION", "us-east-1"))
    REGION = S.region_name
    CTL = S.client("bedrock-agentcore-control", config=botocore.config.Config(retries={"mode": "adaptive", "max_attempts": 6}))
except botocore.exceptions.BotoCoreError as _e:
    raise SystemExit(f"acgw.py: AWS session not set up: {_e}") from None


def log(*a):
    print(*a, file=sys.stderr)  # stdout is reserved for JSON that Terraform reads


# ---------- Mantle Projects (OpenAI-compatible Projects API, SigV4 service "bedrock-mantle") ----------

def _backoff(attempt):
    """Seconds to wait before retry attempt+1: exponential, capped at 20 s, with jitter so parallel runs spread out."""
    return min(2 ** attempt, 20) * (0.5 + uuid.uuid4().int % 1000 / 1000)


class MantleError(RuntimeError):
    """A Projects API call that failed with this HTTP status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class MantleOutage(MantleError):
    """A call not sent: an earlier one in this run spent its retries on an outage (the circuit breaker in mantle())."""

    def __init__(self, message):
        super().__init__(None, message)


class HelperError(RuntimeError):
    """A failure the helper reports as a message (no credentials, a project still live after its archive). Every
    helper error is a RuntimeError, so per-item loops catch FAILURES and one place (__main__) turns what is left into
    the SystemExit message Terraform shows."""


def _env_chunks(prefix):
    """One dict from the numbered variables Terraform sets (policy.tf, ledger.tf): <prefix>_N, then <prefix>_0..N-1,
    each a JSON object (one per user, so no variable nears the 32 KB a single one may hold). Exactly those, never
    others left in the shell; a missing or malformed chunk is a HelperError naming it."""
    out = {}
    for i in range(int(os.environ.get(f"{prefix}_N", "0"))):
        name = f"{prefix}_{i}"
        try:
            out.update(json.loads(os.environ[name]))
        except (KeyError, ValueError, TypeError) as e:
            raise HelperError(f"{name}: missing or not a JSON object ({type(e).__name__}: {e})") from None
    return out


def _report(what, failed):
    """Raise the one aggregated failure of a per-item loop: what, then one line per failed item (HelperError, which
    __main__ turns into Terraform's message)."""
    raise HelperError(what + ":\n  " + "\n  ".join(failed))


# What a per-item loop collects and reports together, and what __main__ turns into one message: the helper's own
# errors, a network failure past the retries, and a boto3 error (a refusal, no endpoint, no or expired credentials).
FAILURES = (RuntimeError, requests.RequestException, botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError)


class UnclearWrite(RuntimeError):
    """A POST that may have taken effect: a 5xx, or a reply lost after the request was sent."""


def _not_sent(e):
    """True when a requests error was raised before the request left this host (connect timeout, DNS failure,
    connection refused): nothing reached Mantle, so sending it again is safe for any call."""
    reason = getattr(e.args[0], "reason", None) if e.args else None
    return isinstance(e, requests.ConnectTimeout) or isinstance(reason, (u3.NewConnectionError, u3.ConnectTimeoutError))


def _slow_reply(e):
    """Sent, then no (whole) answer: a read timeout, including one while the body streams (requests raises that as
    ConnectionError wrapping urllib3's ReadTimeoutError), or a reply cut mid-body (ChunkedEncodingError). One such call
    may be one slow project, so the breaker counts it towards two in a row instead of tripping at once. A reset or an
    abort before any reply (ConnectionError wrapping ProtocolError) is a network failure and trips it at once."""
    if isinstance(e, (requests.ReadTimeout, requests.exceptions.ChunkedEncodingError)):
        return True
    inner = e.args[0] if e.args else None
    return isinstance(e, requests.ConnectionError) and isinstance(inner, u3.ReadTimeoutError)


_OUT_OF_REACH = None  # set when Mantle is judged out of reach for the rest of this run; see mantle()
_EXHAUSTED = 0  # calls in a row that spent every retry on a 5xx, a 429 or a read timeout (any answered call resets it)
# Request faults no retry can fix (a bad URL, schema or header, a redirect loop): reported at once, never an outage.
NEVER_SUCCEEDS = (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema,
                  requests.exceptions.InvalidHeader, requests.exceptions.URLRequired, requests.exceptions.TooManyRedirects)


def _spent(reason, at_once=False):
    """The circuit breaker's one rule: a call ran out of retries. Two such calls in a row (or one, with at_once) mean
    the service, not one project, so every later call in the run fails fast with MantleOutage."""
    global _OUT_OF_REACH, _EXHAUSTED
    _EXHAUSTED += 1
    if at_once or _EXHAUSTED >= 2:
        _OUT_OF_REACH = reason or "a network failure with no message"  # never empty: an empty reason must still trip it


def mantle(method, path, body=None, idempotent=True, empty_ok=False):
    """One Projects API call. Terraform evaluates one data source per user in parallel, and on a fresh account the API
    answers 429 "Too many concurrent project requests": a 429 did nothing, and neither did a request that never left
    this host, so both are retried with jittered backoff. A 5xx or a lost reply may come after the work was done, so
    it is retried only for idempotent calls; for a create or an archive (idempotent=False) it raises UnclearWrite.
    A 2xx reply must be a JSON body, except with empty_ok (the archive, whose effect is all that matters): otherwise
    a listing fails, and a write counts as unclear (UnclearWrite), so the caller checks the listing.

    The circuit breaker: once Mantle looks out of reach (a network failure past the retries, or two calls in a row
    that ended on a 5xx, a 429 or a read timeout), every later call in the run raises MantleOutage without being sent,
    so a loop over many projects reports the outage quickly instead of retrying each one."""
    global _EXHAUSTED
    if _OUT_OF_REACH is not None:  # an earlier call in this run already spent its retries on an outage: do not spend them again
        raise MantleOutage(f"{method} {path}: not sent, Mantle was out of reach earlier in this run ({_OUT_OF_REACH})")
    url = f"https://bedrock-mantle.{REGION}.api.aws{path}"
    data = json.dumps(body) if body is not None else None
    for attempt in range(8):
        last = attempt == 7  # the last try: report it now rather than wait before failing
        req = AWSRequest(method=method, url=url, data=data, headers={"Content-Type": "application/json"})
        creds = S.get_credentials()
        if creds is None:
            raise HelperError("no AWS credentials: sign in (for example `aws sso login`) or set AWS_PROFILE, then run again")
        SigV4Auth(creds.get_frozen_credentials(), "bedrock-mantle", REGION).add_auth(req)
        try:
            # No redirects: requests would resend the SigV4 headers (the session token included) to the new host.
            r = requests.request(method, url, headers=dict(req.headers), data=data, timeout=60, allow_redirects=False)
        except NEVER_SUCCEEDS as e:
            raise HelperError(f"{method} {path}: the request is malformed ({e}); check the region and endpoint") from e
        except requests.RequestException as e:
            if not (_not_sent(e) or idempotent):
                raise UnclearWrite(f"{method} {path}: {e}") from e
            if last:
                # The circuit breaker. A network failure (no route, refused, DNS, a reset, TLS, a proxy down) is never
                # one project's fault: the rest of the run fails fast at once. Only a slow or cut reply (_slow_reply)
                # may be one slow project, so it counts like a 5xx: two such calls in a row trip it.
                _spent(str(e), at_once=not _slow_reply(e))
                raise
            log(f"{method} {path}: {e}, retry {attempt + 1}")
            time.sleep(_backoff(attempt))
            continue
        if r.status_code >= 500 and not idempotent:
            raise UnclearWrite(f"{method} {path} -> {r.status_code} {r.text[:300]}")
        if (r.status_code != 429 and r.status_code < 500) or last:
            break
        log(f"{method} {path} -> {r.status_code}, retry {attempt + 1}")
        time.sleep(_backoff(attempt))
    if r.status_code >= 500 or r.status_code == 429:  # retried to the last try
        # One call that keeps failing can be one bad project; two in a row (different calls) is the service. A request
        # never sent trips the breaker at once (above).
        _spent(f"{r.status_code} on {_EXHAUSTED + 1} calls in a row, each after {attempt + 1} tries")  # counts this one
    else:
        _EXHAUSTED = 0
    if r.status_code >= 300:
        raise MantleError(r.status_code, f"{method} {path} -> {r.status_code} {r.text[:300]}")
    try:
        out = r.json() if r.content.strip() else None
    except ValueError:
        out = None
    if isinstance(out, dict):
        return out
    if empty_ok:
        return {}  # the archive may answer 200/204 with no body: it took effect
    msg = f"{method} {path} -> {r.status_code}, the reply is not a JSON object: {r.text[:300]!r}"
    if idempotent:
        raise MantleError(r.status_code, msg)  # a listing read as empty would hide every project
    raise UnclearWrite(msg)


def _projects():
    out, after, seen = [], None, set()
    while True:
        page = mantle("GET", "/v1/organization/projects?limit=100" + (f"&after={urllib.parse.quote(str(after), safe='')}" if after else ""))
        out += page.get("data") or []
        if not page.get("has_more"):
            return out
        after = page.get("last_id")
        if not after or after in seen:  # it would ask for a page it already read, forever
            raise MantleError(200, f"the project listing says more pages follow but gives no new last_id ({after!r})")
        seen.add(after)


# ---------- One project per user ----------
# Terraform reads every user's project through ONE data source (policy.tf data.external.projects): one process lists
# the account's projects once, in memory, and creates the missing ones. No cache file, lock or freshness window is
# needed to keep parallel processes from listing users x pages times (the Projects API answers 429 to that).

DEAD = ("archived", "deleted", "failed", "error")  # statuses of a project that no longer is, or never became, a user's


def _complete(p):
    """True for a project entry this helper can use: a dict with an id, a name and an arn."""
    return isinstance(p, dict) and all(p.get(k) for k in ("id", "name", "arn"))


def _live(p):
    """True for a project that is, or is about to be, the user's: any status but a dead one (active, and also creating,
    pending or a status this helper does not know: a project coming up is never a reason to create another), and a
    missing status too. An archived_at timestamp means archived whatever the status says: the one rule for "is this
    project still the user's", used by every lookup and by the archive's own check."""
    if p.get("archived_at"):
        return False
    return str(p.get("status") or "active").lower() not in DEAD


def _created(p):
    """A project's creation time for ordering; one without a usable created_at sorts last, so it is never preferred
    over a project whose age is known."""
    try:
        return float(p.get("created_at"))
    except (TypeError, ValueError):
        return float("inf")


STACK_TAG = "opencode-agentcore-gateway"  # the Project tag of policy.tf local.project_tags


def _all_live(projects, name, user):
    """The live projects with this name that are tagged as this user's project of this repo (User and Project tags),
    oldest first. Names alone are not enough: stack "ocgw" with user "dev-alice" and stack "ocgw-dev" with user
    "alice" both name a project ocgw-dev-alice (r196, threat model T-070). A same-named project of another user, or
    one with neither tag, is logged and never used. user None matches on the name alone: only an archive whose
    Terraform state predates the User input (see project_archive)."""
    same = [p for p in projects if p["name"] == name and _live(p)]
    if user is not None:
        mine = [p for p in same if isinstance(p.get("tags"), dict) and p["tags"].get("User") == user
                and p["tags"].get("Project") == STACK_TAG]
        if len(mine) < len(same):
            log(f"{name}: skipping {len(same) - len(mine)} live Mantle project(s) with this name not tagged "
                f"User={user}, Project={STACK_TAG}: {', '.join(p['id'] for p in same if p not in mine)}")
        same = mine
    return sorted(same, key=lambda p: (_created(p), p["id"]))


def _oldest(projects, name, user):
    """The user's project: the oldest live one with this name and the user's tags. Mantle has no idempotent create,
    so a create whose reply was lost (or two checkouts planning at once) can still leave two projects with one name.
    Choosing the oldest makes every run, on any machine, agree on the same one; the others are logged to archive."""
    same = _all_live(projects, name, user)
    if len(same) > 1:
        log(f"{name}: {len(same)} live Mantle projects with this name; using the oldest, {same[0]['id']}. Archive "
            f"the others in the Bedrock console: {', '.join(p['id'] for p in same[1:])}")
    return same[0] if same else None


def _listed():
    return [p for p in _projects() if _complete(p)]


CREATE_TRIES = 3  # creates sent for one project, each after fresh listings showed the one before did not take effect
UNCLEAR_LOOKS = 4  # listings made after an unclear create, LOOK_SECONDS apart
LOOK_SECONDS = 15


def projects_ensure(users_json):
    """The data source: users_json is {project name: tags}; prints {project name: '{"id","arn","name"}'} (data
    "external" results are strings). The listing is read once and a missing project is created (tags on an existing
    one are set at apply time: projects_retag). A create with an unclear outcome (a 5xx or a lost reply) is not sent again until fresh listings over the next minute
    have not shown the project; if a duplicate still happens, the oldest-first rule keeps every run on the same one.
    One user's failed create does not stop the others: every user is tried, then the failures are reported together."""
    users = json.loads(users_json)
    projects = _listed()
    out, failed = {}, []
    for name, tags in sorted(users.items()):
        try:
            p = _oldest(projects, name, tags.get("User")) or _create(name, tags, projects)
        except FAILURES as e:
            failed.append(f"{name}: {e}")
            continue
        out[name] = json.dumps({"id": p["id"], "arn": p["arn"], "name": p["name"]})
    if failed:
        _report("Mantle projects not ready (the others were made)", failed)
    print(json.dumps(out))


# The tags this helper owns on a project: the keys of policy.tf local.project_tags (a unit test keeps the two equal).
# Kept here, not derived from what Terraform sends, so an owned key that a later version drops is removed (null).
TAG_KEYS = ("Project", "Component", "User", "Group")


def _get_project(pid):
    """Project pid read by its own GET, or None when Mantle answers 404."""
    try:
        return mantle("GET", f"/v1/organization/projects/{pid}")
    except MantleError as e:
        if e.status == 404:
            return None
        raise


def projects_retag(tags_json=None):
    """The apply step that keeps projects' tags (the Group above all) in step with var.users: tags are only set on
    create otherwise. tags_json is {project id: tags}; with none, it is read from the OCGW_TAGS_<n> environment
    variables (policy.tf: one per project, so no single variable grows past Linux's 128 KiB limit).

    Verified live (13 Nov 2026): POST /projects/{id} with tags merges them into the project's tags, a null value
    removes a key, and an aws: key is refused (400). So only the owned keys (TAG_KEYS) that differ are sent: a changed
    value, or null for an owned key Terraform no longer sets; tags anyone else set are never touched. One listing
    gives every project's tags; a project it does not show (made at plan time moments ago) is read directly. A
    project that does not exist fails the step (so the next apply retries it); one that is not live (archived) is
    skipped. Every project is tried; failures are reported together."""
    want = json.loads(tags_json) if tags_json is not None else _env_chunks("OCGW_TAGS")
    try:
        listed = {p["id"]: p for p in _listed()}
    except FAILURES as e:  # every project can still be read directly (and on an outage the breaker makes that quick)
        log(f"project listing failed ({e}); reading each project directly")
        listed = {}
    failed = []
    for pid, tags in sorted(want.items()):
        try:
            p = listed.get(pid) or _get_project(pid)  # not listed yet (made moments ago at plan time): read it directly
        except FAILURES as e:
            failed.append(f"{pid}: {e}")
            continue
        if p is None:
            failed.append(f"{pid}: no such project; run terraform apply again")
            continue
        if not _live(p):
            log("not retagged (not a live project):", pid, p.get("status"))
            continue
        have = p.get("tags") if isinstance(p.get("tags"), dict) else {}
        change = {k: v for k, v in tags.items() if k in TAG_KEYS and have.get(k) != v}  # only the keys this helper owns
        change.update({k: None for k in TAG_KEYS if k in have and k not in tags})
        if not change:
            continue
        try:
            mantle("POST", f"/v1/organization/projects/{pid}", {"tags": change}, empty_ok=True)
        except FAILURES as e:
            failed.append(f"{pid}: {e}")
            continue
        log("retagged", pid, sorted(change))
    if failed:
        _report("project tags not updated (the others were)", failed)


def _create(name, tags, projects):
    """Create the project and add it to projects (the listing this run works from); see projects_ensure."""
    for attempt in range(CREATE_TRIES):
        try:
            p = mantle("POST", "/v1/organization/projects", {"name": name, "tags": tags}, idempotent=False)
            if not _complete(p):
                raise UnclearWrite(f"create {name}: the reply lacks the project's id, name or arn")
            projects.append(p)
            return p
        except UnclearWrite as e:
            if attempt == CREATE_TRIES - 1:
                raise RuntimeError(f"creating Mantle project {name}: {e}; it may exist: run terraform plan again "
                                   f"(it uses the project once Mantle lists it)") from e
            log(f"create {name}: {e}; checking whether it was made")
            for _look in range(UNCLEAR_LOOKS):
                time.sleep(LOOK_SECONDS)
                projects[:] = _listed()
                if p := _oldest(projects, name, tags.get("User")):
                    return p
    raise AssertionError("unreachable: the last try returns or raises")


def project_archive(name):
    """The destroy step of one user: archive every live project with this name and the user's tags (OCGW_USER, from
    the resource's input; a same-named project of another stack or user is left live), duplicates included (projects cannot
    be deleted: DELETE -> 405; archiving stops them taking traffic), so a later re-add of the user never reuses a
    leftover. Verified live (13 Nov 2026): the archive answers 200 with status "archived" at once, a second archive
    answers 200 again (it is idempotent), and the listing leaves an archived project out at once, so a re-add straight
    after cannot pick it up. One listing per user being removed; a 429 is retried with backoff (mantle)."""
    failed = []
    user = os.environ.get("OCGW_USER") or None
    if user is None:  # a resource whose state was written before r196 carries no user until the next apply updates it
        log(f"{name}: OCGW_USER not set; archiving by name alone, as before r196")
    for p in _all_live(_listed(), name, user):  # every duplicate is tried, whatever happens to the one before it
        try:
            _archive(p["id"])
        except FAILURES as e:
            failed.append(f"{p['id']}: {e}")
            continue
        log("archived", name, p["id"])
    if failed:
        _report(f"{name}: projects not archived (the others were)", failed)


def _archive(pid):
    """POST the archive. It is idempotent (see project_archive), so a 5xx or a lost reply is simply sent again
    (mantle(idempotent=True)); a 404 means the project is gone. The reply carries the new status; a reply without it
    (an empty 200) is checked with one GET of the project."""
    try:
        r = mantle("POST", f"/v1/organization/projects/{pid}/archive", empty_ok=True)
    except MantleError as e:
        if e.status == 404:
            return
        raise
    if r.get("status") is None:
        r = _get_project(pid)
        if r is None:
            return
    if _live(r):  # the same rule as every lookup: a re-add must not find this project live
        raise HelperError(f"archive {pid}: Mantle still reports it live ({r.get('status')}); archive it in the Bedrock "
                         "console, then run terraform apply again")


# ---------- Inference targets ----------

RL_GETS = 8  # concurrent GetGatewayRateLimit calls when list summaries carry no description (as the dashboard)


def _get_many(gw, ids):
    """(id, the limit in full or None when it is gone) for each id, read RL_GETS at a time (agentcore_wait.read_many)."""
    return agentcore_wait.read_many(lambda rid: _get_limit(gw, rid), ids, RL_GETS)


def _ratelimits(gw, *descriptions):
    """{rate limit id: the limit in full} for the gateway's limits carrying one of these descriptions, in listing
    order, in one listing pass. The list's description can lag a rename, but only within this limit's own versions
    (same base, another hash or tag), so the summaries read in full are those with that base or with no description
    (absent, null or empty);
    each is kept only when the Get shows one of the descriptions. One deleted since the List is skipped."""
    bases = tuple({_base(d) for d in descriptions})
    ids = list(dict.fromkeys(i["rateLimitId"] for i in _summaries(gw)
                             if not i.get("description") or i["description"].startswith(bases)))
    return {rid: d for rid, d in _get_many(gw, ids) if d and d.get("description") in descriptions}


def _base(description):
    """A limit description without its spec hash and instance tag: what every version of it shares."""
    return (description or "").split(" #")[0].split(" ~")[0]


def _summaries(gw):
    """Every rate-limit summary on the gateway, across pages ("rateLimits", or "items" as the dashboard also accepts)."""
    return agentcore_wait.list_all(CTL.list_gateway_rate_limits, ("rateLimits", "items"), gatewayIdentifier=gw)


def _get_limit(gw, rid):
    """A rate limit in full, or None when it was deleted since it was listed."""
    return agentcore_wait.get_or_none(CTL.get_gateway_rate_limit, gatewayIdentifier=gw, rateLimitId=rid)


def _limit_with_keys(gw, keys, base=None):
    """The gateway's rate limit on exactly these dimension keys, in full (Get), or None. The gateway allows one limit
    per set of dimension keys ("must be unique per gateway"), so this is the one the "*" default and every per-user
    entry live in. Summaries that name other keys are skipped; the rest are read RL_GETS at a time. A limit whose keys
    cannot be read at all (dimensionKeys null and no entries, as one that never came up) is taken by its description
    base, so it is repaired instead of blocking a create."""
    # A summary without dimensionKeys (absent or null) may still be the one: it is read in full like the matches.
    ids = [i["rateLimitId"] for i in _summaries(gw) if sorted(i.get("dimensionKeys") or keys) == sorted(keys)]
    with contextlib.closing(_get_many(gw, ids)) as got:
        return next((d for _rid, d in got if d and (_keys(d) == sorted(keys) or (
            not _keys(d) and base and _base(d.get("description")) == base))), None)


def _keys(limit):
    """A limit's dimension keys, sorted: its dimensionKeys, or (when the service returns them null) the keys its
    entries' dimensions use, so an existing limit is still found and never created a second time."""
    return sorted(limit.get("dimensionKeys") or {k for e in limit.get("entries") or [] for k in (e.get("dimensions") or {})})


def _owned(description):
    """The description with this Terraform instance's tag (its terraform_data id, OCGW_RL_OWNER): a replacement with an
    unchanged spec (terraform apply -replace) retags the limit, so the old instance's destroy step, which deletes only
    a limit carrying its own tag, leaves it alone."""
    owner = os.environ.get("OCGW_RL_OWNER", "")[:8]
    return f"{description} ~{owner}" if owner else description


def ratelimit_ensure(gw, spec_json):
    """Make the gateway's limit on spec's dimension keys carry spec's entries (the "*" default and rpm_overrides). The
    gateway allows one limit per set of dimension keys, so it is updated in place (UpdateGatewayRateLimit), never
    replaced, and the users are never without a limit. Its description (a hash of the spec, and this instance's tag)
    marks the Terraform instance that set it, so another instance's destroy step leaves it alone. Create and update
    finish asynchronously, so it waits for ACTIVE (agentcore_wait) and fails the apply on a *_FAILED status; a limit
    left UPDATE_FAILED is updated again even when it already shows the spec, one that never came up (CREATE_FAILED)
    is deleted and created again, and one being deleted is waited out first. A change still in flight is waited out
    to whatever status it settles in, so these repairs also apply when it then fails."""
    spec = json.loads(spec_json)
    spec["description"] = _owned(spec["description"])
    cur = _limit_with_keys(gw, spec["dimensionKeys"], _base(spec["description"]))
    if cur and cur.get("status") in agentcore_wait.IN_FLIGHT:
        cur = _settle(gw, cur["rateLimitId"])
    if cur and cur.get("status") in ("CREATE_FAILED", "DELETE_FAILED", "DELETING"):
        # CREATE_FAILED serves nothing and cannot be updated, a DELETE_FAILED limit refuses updates: delete either
        # (again); the keys stay taken until it is gone.
        rid, st = cur["rateLimitId"], cur["status"]
        if _delete_and_wait(gw, {rid: cur}):
            log(f"rate limit deleted ({st})", gw, rid)
        cur = None
    if cur is None:
        rid = agentcore_wait.send(lambda: CTL.create_gateway_rate_limit(gatewayIdentifier=gw, clientToken=str(uuid.uuid4()), **spec),
                                  "the rate limit could not be created", need=lambda r: r.get("rateLimitId"))["rateLimitId"]
        log("rate limit created", gw, spec["description"], rid)
        before, grace, missing = None, 0, 3
    else:
        rid = cur["rateLimitId"]
        have, want = _normal(cur.get("entries")), _normal(spec["entries"])
        failed = (cur.get("status") or "").endswith("FAILED")
        if cur.get("description") == spec["description"] and have == want and not failed:
            before, grace, missing = None, 0, 0  # unchanged
        else:
            # Entries on the limit that Terraform does not set (added by hand, or by an earlier version's dashboard)
            # are replaced by the update: name them, so their users can be moved to rpm_overrides or the interceptor.
            extra = sorted({d for d, _ in have} - {d for d, _ in want})
            if extra:
                log(f"rate limit: replacing entries Terraform does not set: {', '.join(extra)}")
            agentcore_wait.send(lambda: CTL.update_gateway_rate_limit(gatewayIdentifier=gw, rateLimitId=rid,
                                                                      description=spec["description"], entries=spec["entries"]),
                                f"rate limit {rid} could not be updated")
            log("rate limit updated", gw, spec["description"], rid)
            before, grace, missing = cur, (3 if failed else 0), 0
    _wait(gw, rid, before, grace, missing)
    _drop_other_keys(gw, spec, rid)


def _drop_other_keys(gw, spec, keep):
    """Delete this stack's limits (same description base) on other dimension keys, once the new one is ACTIVE. A
    user_claim change (switching the IdP: Cognito's username to sub) moves the limit to new keys, so the old limit is
    a different one and the gateway would keep serving it; its own destroy step is not enough, because Terraform does
    not run it for a create_before_destroy replacement in every case (r178: the Cognito limit was left behind)."""
    base, keys = _base(spec["description"]), sorted(spec["dimensionKeys"])
    ids = [i["rateLimitId"] for i in _summaries(gw) if i["rateLimitId"] != keep
           and (not i.get("description") or i["description"].startswith(base))]
    stale = {rid: d for rid, d in _get_many(gw, ids) if d and _base(d.get("description")) == base and _keys(d) != keys}
    for rid in _delete_and_wait(gw, stale):
        log("rate limit deleted (a limit on other dimension keys)", gw, rid)


def _wait(gw, rid, before, grace, missing):
    return agentcore_wait.or_exit(agentcore_wait.wait_active, lambda: _get_limit(gw, rid), f"rate limit {rid}",
                                  grace=grace, before=before, delay=3, missing_grace=missing)


def _settle(gw, rid):
    return agentcore_wait.or_exit(agentcore_wait.wait_settled, lambda: _get_limit(gw, rid), f"rate limit {rid}", delay=3)


def _normal(entries):
    """Entries as a sorted list of (dimensions, rates), the rates as numbers (Terraform renders 60, the service returns
    60.0) with the period in lower case, and only the fields Terraform sets compared: neither the order nor a number's
    spelling counts as a change, and two entries for one value stay two."""
    def rates(e, kind):
        return sorted((float(x["rate"]), str(x.get("period") or "").lower()) for x in e.get(kind) or [])
    return sorted((json.dumps(e.get("dimensions"), sort_keys=True), json.dumps([rates(e, "requests"), rates(e, "tokens")]))
                  for e in entries or [])


def ratelimit_delete(gw, description):
    """The destroy step: delete only a limit still carrying this instance's description (spec hash and its own tag):
    after an update by a newer instance, a replacement included, the description has changed and the limit is left
    alone. Every match is deleted, so none is left behind, and it waits until each is gone."""
    # The bare description too: a limit an older helper set, before descriptions carried the tag.
    mine = {_owned(description), description}
    try:
        found = _ratelimits(gw, *mine)
    except botocore.exceptions.ClientError as e:
        if agentcore_wait.is_not_found(e):  # the gateway itself is gone (replaced or destroyed), and its limits with it
            return
        raise
    def owned(cur):
        """cur, or None when it is gone or carries another instance's description (renamed while it settled)."""
        if cur is not None and cur.get("description") not in mine:
            log("rate limit left alone: another instance's now", gw, cur.get("rateLimitId"))
            return None
        return cur

    # Each already read in full and owned by its Get description; one already deleting (an earlier destroy timed out) is
    # waited out, not deleted again.
    for rid in _delete_and_wait(gw, found, owned):
        log("rate limit deleted", gw, description, rid)


def _delete_and_wait(gw, limits, owned=lambda cur: cur):
    """agentcore_wait.delete_and_wait for rate limits: the ids a delete was sent for, once all of them are gone."""
    return agentcore_wait.delete_and_wait(limits, lambda rid: CTL.delete_gateway_rate_limit(gatewayIdentifier=gw, rateLimitId=rid),
                                          lambda rid: _get_limit(gw, rid), owned, noun="rate limit ", delay=3)


def seed_groups(table, groups_json=None):
    """SET the seeded group on each USER#<user>/BUDGET, leaving every other attribute (overrides, boost,
    token_groups that the dashboard and interceptor write) as it is. groups_json is {user: group}; with none, it is
    read from the OCGW_GROUPS_0..N-1 variables (ledger.tf: one per user, as with the project tags). One process for
    every user; each item must exist (this never creates a user). Failures are reported together."""
    groups = json.loads(groups_json) if groups_json is not None else _env_chunks("OCGW_GROUPS")
    ddb, failed = S.client("dynamodb"), []
    for user, group in sorted(groups.items()):
        try:
            ddb.update_item(
                TableName=table, Key={"pk": {"S": f"USER#{user}"}, "sk": {"S": "BUDGET"}},
                UpdateExpression="SET #g = :g", ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#g": "group"}, ExpressionAttributeValues={":g": {"S": group}})
        except FAILURES as e:
            failed.append(f"{user}: {e}")
            continue
        log("seeded group", user, group)
    if failed:
        _report("groups not seeded (the others were)", failed)


def main():
    """The command line Terraform runs: acgw.py <command> [args]. Commands are looked up when main() runs."""
    COMMANDS = {"projects-ensure": projects_ensure, "project-archive": project_archive, "projects-retag": projects_retag,
                "ratelimit-ensure": ratelimit_ensure, "ratelimit-delete": ratelimit_delete, "seed-groups": seed_groups}
    cmd, *args = sys.argv[1:] or [""]
    if cmd not in COMMANDS:
        raise SystemExit(f"acgw.py: unknown command {cmd!r}; one of: {', '.join(COMMANDS)}")
    # "@env:NAME" reads the argument from the environment (Terraform passes JSON specs that way, not via shell quoting)
    missing = [a[5:] for a in args if a.startswith("@env:") and a[5:] not in os.environ]
    if missing:
        raise SystemExit(f"acgw.py {cmd}: environment variable(s) not set: {', '.join(missing)}")
    args = [os.environ[a[5:]] if a.startswith("@env:") else a for a in args]
    run = COMMANDS[cmd]
    try:
        if cmd == "projects-ensure":  # data "external": the query arrives as JSON on stdin
            args = [json.load(sys.stdin)["users"]]
        run(*args)
    except (*FAILURES, KeyError, ValueError, TypeError) as e:  # a message for Terraform, not a traceback (a missing
        # OCGW_TAGS_i, malformed JSON in a spec, or a wrong argument count included)
        raise SystemExit(f"acgw.py {cmd}: {type(e).__name__}: {e}" if not isinstance(e, FAILURES) else str(e)) from None


if __name__ == "__main__":
    main()
