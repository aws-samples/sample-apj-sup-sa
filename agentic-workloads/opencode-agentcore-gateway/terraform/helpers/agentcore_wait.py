# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Waiting out an asynchronous AgentCore Gateway change (a gateway rule or a rate limit): create and update return at
once and finish in the background, so the helpers poll the resource until it is ACTIVE. Shared by gateway_rule.py and
acgw.py, so what was learned live about the statuses is in one place."""
import concurrent.futures
import os
import re
import time

MIN_BOTO3 = (1, 43, 118)  # the gateway-rule fields (matchModels) and the gateway rate-limit API


def drop_empty_profile(environ=os.environ):
    """An empty AWS_PROFILE or AWS_DEFAULT_PROFILE means unset; botocore would look up a profile named "" and fail.
    Every helper that builds a boto3 Session calls this first (acgw.py, gateway_rule.py)."""
    for v in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        if environ.get(v) == "":
            del environ[v]


def require_boto3(version):
    """Stop with a message when this interpreter's boto3 (version) is older than MIN_BOTO3. Checked inside the helpers,
    so it holds whichever Python runs them: python.sh's choice, var.python or var.rule_python as an operator set it."""
    have = tuple(int(x) for x in re.findall(r"[0-9]+", version)[:3])
    if have < MIN_BOTO3:
        need = ".".join(map(str, MIN_BOTO3))
        raise SystemExit(f"boto3 {version} is older than {need}, which the helpers need: point var.python (and "
                         f"var.rule_python) at a Python with boto3 >= {need}, or leave them empty to use helpers/python.sh")

# Polls during which a read straight after a change can still show the status from before it (the old ACTIVE, or an
# old *_FAILED): seen live in r47. The one figure every wait below uses for "can this read be stale".
STALE_POLLS = 6


def list_all(call, keys, **kw):
    """Every item of a paginated AgentCore list call: call(**kw, nextToken=...) until no nextToken; items under the
    first of keys that is non-empty in the reply (null and [] are empty). The one copy of the loop, for the rule and rate-limit helpers. A nextToken seen
    before (a paging fault) stops it with an error rather than loop for ever."""
    out, token, seen = [], None, set()
    while True:
        r = call(**kw, **({"nextToken": token} if token else {}))
        out += next((r[k] for k in keys if r.get(k)), [])  # the first non-empty list key (null or [] is empty)
        token = r.get("nextToken")
        if not token:
            return out
        if token in seen:
            raise RuntimeError(f"{getattr(call, '__name__', 'list')}: the service repeated nextToken {token!r}")
        seen.add(token)


def shows_change(full, before, seen_in_flight, polls_done):
    """True once a read can be trusted to show a change that started after `before` was read: no earlier read to
    compare with, the change was seen in flight, updatedAt moved, or STALE_POLLS polls passed without either. The one
    copy of this rule, for wait_active (after an update) and wait_changed_many (after a refused delete)."""
    return (before is None or seen_in_flight or (full or {}).get("updatedAt") != before.get("updatedAt")
            or polls_done >= STALE_POLLS)


def wait_active(get, what, grace=0, before=None, polls=60, delay=5, why=lambda full: "", sleep=None, missing_grace=0):
    """Poll get() (the resource in full, or None when it is gone) until it is ACTIVE; raise RuntimeError otherwise.

    grace: polls during which a *_FAILED status is the previous one, not yet replaced (right after an update of a
    failed resource the old UPDATE_FAILED can still be reported). before: the resource as read just before an update:
    straight after it the old ACTIVE can still be returned, so ACTIVE then counts only once the resource shows the
    update (updatedAt moved, or UPDATING was seen), or after STALE_POLLS polls without a change. The new content is no proof: the
    service can store an update before validating it, and still report the old ACTIVE until it fails it (seen live in
    r47), so a resource without updatedAt costs those polls rather than a stale success. A resource that is gone or being
    deleted fails at once, except during missing_grace polls (straight after a create, a read can briefly find
    nothing). why(full): a reason to add to a *_FAILED message.
    Statuses: CREATING, ACTIVE, UPDATING, DELETING, and *_FAILED when a change is rejected."""
    seen_updating = False
    for n in range(polls):
        full = get()
        if full is None:
            if n < missing_grace:
                (sleep or time.sleep)(delay)
                continue
            raise RuntimeError(f"{what} is gone")
        st = full.get("status") or ""
        seen_updating = seen_updating or st == "UPDATING"
        fresh = shows_change(full, before, seen_updating, n)
        if st == "ACTIVE" and fresh:
            return full
        if st == "DELETING":
            raise RuntimeError(f"{what} is being deleted")
        if st.endswith("FAILED") and n >= grace and (fresh or st != (before or {}).get("status")):
            raise RuntimeError(f"{what} is {st}" + (f": {why(full)}" if why(full) else ""))
        (sleep or time.sleep)(delay)  # looked up at call time, so a patched time.sleep applies
    raise RuntimeError(f"{what} not ACTIVE after {polls * delay} s")


IN_FLIGHT = ("CREATING", "UPDATING")


def wait_settled_many(gets, noun="", polls=60, delay=5, sleep=None):
    """gets: {name: get}. Poll every get() while its change is in flight (CREATING, UPDATING), all in one loop, and
    return {name: the resource once settled, in whatever status (ACTIVE or *_FAILED), or None when it is gone}. The
    caller then decides: a failed change is something to repair, not a reason to stop, so this never raises on
    *_FAILED. noun: put before the names in the timeout message."""
    out, pending = {}, dict(gets)
    for _ in range(polls):
        for name, get in list(pending.items()):
            full = get()
            if full is None or (full.get("status") or "") not in IN_FLIGHT:
                out[name] = full
                del pending[name]
        if not pending:
            return out
        (sleep or time.sleep)(delay)
    raise RuntimeError(f"{noun}{', '.join(map(str, pending))} still changing after {polls * delay} s")


def wait_changed_many(gets, before, noun="", polls=60, delay=5, sleep=None):
    """gets: {name: get}, before: {name: the resource as read before a change started (a refused delete: someone
    else's change is in flight)}. Poll each until a read shows that change (UPDATING or CREATING seen, updatedAt moved,
    or STALE_POLLS polls passed without either, as a read can still show the old status) and it has settled; return
    {name: that read, or None when gone}. This waits as long as the change takes, not a fixed time."""
    out, pending, seen = {}, dict(gets), set()
    for n in range(polls):
        (sleep or time.sleep)(delay)
        for name, get in list(pending.items()):
            full = get()
            st = (full or {}).get("status") or ""
            if st in IN_FLIGHT:
                seen.add(name)
            fresh = shows_change(full, before.get(name) or {}, name in seen, n + 1)
            if full is None or (fresh and st not in IN_FLIGHT):
                out[name] = full
                del pending[name]
        if not pending:
            return out
    raise RuntimeError(f"{noun}{', '.join(map(str, pending))} still changing after {polls * delay} s")


def wait_settled(get, what, polls=60, delay=5, sleep=None):
    """wait_settled_many for one resource: it settled, or None when it is gone."""
    return wait_settled_many({what: get}, polls=polls, delay=delay, sleep=sleep)[what]


def wait_gone_many(gets, noun="", was=None, why=lambda full: "", polls=60, delay=5, sleep=None, on_failed=None):
    """gets: {name: get}. Poll every get() in one loop until each returns None (deleted); raise on DELETE_FAILED or
    after polls. was: {name: status before this delete}; a DELETE_FAILED equal to it is the old status for STALE_POLLS
    polls, not a new failure, so a retried delete of a DELETE_FAILED resource is not failed on its first read (since r90
    the same 6 polls as every other stale read here; a real repeat failure is reported after them).
    on_failed(name): called on a DELETE_FAILED that would raise; True means a new delete was sent, so the wait goes on
    (the old DELETE_FAILED again counting as stale for STALE_POLLS polls). why(full): a reason to add to the message.
    noun: put before the names in messages."""
    was, pending = dict(was or {}), dict(gets)
    since = dict.fromkeys(pending, 0)  # the poll from which each name's current delete counts
    for n in range(polls):
        for name, get in list(pending.items()):
            full = get()
            if full is None:
                del pending[name]
            elif (full.get("status") or "") == "DELETE_FAILED" and (
                    was.get(name) != "DELETE_FAILED" or n - since[name] >= STALE_POLLS):
                if on_failed and on_failed(name):
                    was[name], since[name] = "DELETE_FAILED", n + 1
                    continue
                raise RuntimeError(f"{noun}{name} could not be deleted (DELETE_FAILED)" + (f": {why(full)}" if why(full) else ""))
        if not pending:
            return
        (sleep or time.sleep)(delay)
    raise RuntimeError(f"{noun}{', '.join(map(str, pending))} still there after {polls * delay} s")


def read_many(get, ids, workers=8):
    """(id, get(id)) for each id, `workers` reads at a time, in order. If a read fails, or the caller stops early
    (close the generator), the reads not started yet are cancelled."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        yield from zip(ids, pool.map(get, ids))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)  # waits only for the reads already running (at most workers)


def or_exit(wait, *args, **kw):
    """Run one of the waits above and turn its RuntimeError into SystemExit, the helpers' way of failing an apply with
    a message and no traceback."""
    try:
        return wait(*args, **kw)
    except RuntimeError as e:
        raise SystemExit(str(e)) from None


def error_code(e):
    """The service's error code on e (a botocore ClientError or a client's modelled exception), or None for an
    exception without a usable response."""
    response = getattr(e, "response", None)
    return (response.get("Error") or {}).get("Code") if isinstance(response, dict) else None


def is_not_found(e):
    """True when e is the service's ResourceNotFoundException."""
    return error_code(e) == "ResourceNotFoundException"


def get_or_none(get, **kw):
    """get(**kw), or None when the service answers ResourceNotFoundException: the 'gone' every wait above expects."""
    try:
        return get(**kw)
    except Exception as e:  # noqa: BLE001 - only the not-found error is swallowed, anything else re-raised
        if is_not_found(e):
            return None
        raise



def send(call, what, need=None):
    """call() (an update or create) and return its result. A service refusal stops the step with "<what>: <error>"
    (SystemExit); for a ConflictException (another change to it started during this run) the message asks for the
    run to be repeated: a resend here would be judged against a read taken before that change. need(result): the id a
    create reply must carry; a reply without it stops the step too (the resource is found again on the next run)."""
    try:
        result = call()
    except Exception as e:  # noqa: BLE001 - a service error is reported; anything else is re-raised
        code = error_code(e)
        if code is None:
            raise
        hint = " (another change to it started during this run: run terraform again)" if code == "ConflictException" else ""
        raise SystemExit(f"{what}: {e}{hint}") from None
    if need is not None and not need(result or {}):
        raise SystemExit(f"{what}: the reply carries no id; run terraform apply again (it finds what was made)")
    return result


def _getter(get, i):
    return lambda: get(i)


def settle_in_place(full, get, owned=lambda f: f, noun="", delay=5, sleep=None):
    """full ({id: resource as read}) with every in-flight entry read again until settled, in one poll loop over
    get(id), and entries gone meanwhile or no longer owned (owned(resource) is None, e.g. renamed by a replacement)
    dropped. Returns full."""
    flying = {i: _getter(get, i) for i, f in full.items() if (f.get("status") or "") in IN_FLIGHT}
    for i, f in (or_exit(wait_settled_many, flying, noun=noun, delay=delay, sleep=sleep) if flying else {}).items():
        f = owned(f) if f is not None else None
        if f is None:
            del full[i]
        else:
            full[i] = f
    return full


CONFLICT_TRIES = 6  # deletes sent for one entry refused with ConflictException, each after a pause and a fresh settle


def delete_and_wait(full, delete, get, owned=lambda f: f, noun="", why=lambda f: "", delay=5, sleep=None):
    """Delete every entry of full ({id: resource as read}) and wait until all are gone; return the ids a delete was
    sent for. Entries in flight settle first (a delete is refused meanwhile), and one already DELETING is only waited
    for. A delete refused with ConflictException (a change started since the read) is not fatal: those entries settle
    together in one loop, are checked with owned() again, and are sent again, up to CONFLICT_TRIES times (a read
    straight after the refusal can still show the old ACTIVE, so each resend waits until a read shows the change that
    caused the refusal, then its end: wait_changed_many). Any other refusal, or the last one, stops the step with a
    message. NotFound counts as deleted. sleep: as in the other waits, used by every wait here."""
    full = settle_in_place(full, get, owned, noun, delay, sleep=sleep)
    sent, refused, gone = [], [], set()

    def one(i, last):
        try:
            delete(i)
            sent.append(i)
        except Exception as e:  # noqa: BLE001 - a service error is handled or reported; anything else is re-raised
            code = error_code(e)
            if code is None:
                raise
            if code == "ResourceNotFoundException":
                gone.add(i)  # already gone (a retried delete whose first try took effect): no read is waited for
                return
            if code != "ConflictException" or last:
                raise SystemExit(f"{noun}{i} could not be deleted: {e}") from None
            refused.append(i)

    for i, f in full.items():
        if f.get("status") != "DELETING":
            one(i, last=False)

    def failed(i):
        """An entry this step only waited for (it was DELETING already) whose delete failed: delete it once more."""
        if i in sent or i in gone:
            return False
        one(i, last=True)
        return True
    for attempt in range(1, CONFLICT_TRIES):
        if not refused:
            break
        ids, refused[:] = list(refused), []
        again = or_exit(wait_changed_many, {i: _getter(get, i) for i in ids}, {i: full[i] for i in ids}, noun=noun,
                        delay=delay, sleep=sleep)
        for i in ids:
            f = owned(again[i]) if again[i] is not None else None
            if f is None:
                del full[i]  # gone meanwhile, or no longer ours
                continue
            full[i] = f
            if f.get("status") != "DELETING":
                one(i, last=attempt == CONFLICT_TRIES - 1)
    for i in gone:
        full.pop(i, None)
    or_exit(wait_gone_many, {i: _getter(get, i) for i in full}, noun=noun,
            was={i: f.get("status") for i, f in full.items()}, why=why, delay=delay, on_failed=failed, sleep=sleep)
    return sent
