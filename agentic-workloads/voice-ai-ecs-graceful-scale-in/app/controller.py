"""Capacity controller: the only writer of the service's desired count.

Usage: python controller.py scale-in | scale-out | abort | status

`abort` backs out an interrupted removal (DRAINING or UNPROTECTED, count not yet lowered): it
re-protects the task, clears the removal's target fields and returns the task to ACCEPTING.

Scale-in order (each step is persisted before the next one runs, so a restarted controller resumes):
  1. nominate the least-busy ACCEPTING task and record DRAINING
  2. wait until it has stopped dialing and none of its placed calls are still ringing
  3. release its scale-in protection and record UNPROTECTED
  4. confirm every other task is still protected
  5. lower the desired count by one and record COUNT_LOWERED
  6. wait for Amazon ECS to stop it, then remove it from the registry and start a cooldown
"""
import json
import os
import signal
import sys
import threading
import time
import uuid

import boto3
import redis

CLUSTER = os.environ["CLUSTER"]
SERVICE = os.environ["SERVICE"]
VALKEY_HOST = os.environ["VALKEY_HOST"]
DRAIN_READY_TIMEOUT = int(os.environ.get("DRAIN_READY_TIMEOUT_SECONDS", "300"))
STOP_TIMEOUT = int(os.environ.get("STOP_WAIT_TIMEOUT_SECONDS", "1500"))
COOLDOWN = int(os.environ.get("COOLDOWN_SECONDS", "300"))
# A short lease kept alive by a heartbeat thread. A controller that dies without releasing it
# blocks the next run for at most LEASE_SECONDS, well inside the deregistration delay.
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 20
# How many times the UNPROTECTED step re-releases a task that a late renewal re-protected.
RERELEASE_ATTEMPTS = 3
# Protection `abort` puts back on a task; the task's own renewals take over once it is ACCEPTING.
ABORT_PROTECT_MINUTES = int(os.environ.get("PROTECT_MINUTES", "15"))
LEASE_KEY = "controller:lease"
# Controller-owned states that mean a removal is in progress. Calling tasks never write "state"
# after ACCEPTING (they report their own lifecycle in "proc_state"), but tasks running an older
# image write state=STOPPING on SIGTERM, so STOPPING is still treated as in progress.
IN_PROGRESS = ("DRAINING", "UNPROTECTED", "COUNT_LOWERED", "STOPPING")

ecs = boto3.client("ecs")
db = redis.Redis(host=VALKEY_HOST, port=6379, ssl=True, decode_responses=True,
                 socket_timeout=5, socket_connect_timeout=5)  # fail instead of hanging forever
me = str(uuid.uuid4())


def log(event, **fields):
    print(json.dumps({"ts": round(time.time(), 3), "event": event, **fields}), flush=True)


def tasks_in_store():
    return {tid: db.hgetall(f"task:{tid}") for tid in db.smembers("tasks")}


def running_task_ids():
    """Tasks ECS wants running (ListTasks desiredStatus=RUNNING). A task ECS is stopping drops out of
    this list at once but can keep draining for minutes, so this is NOT the test for "gone": use
    task_gone() / describe_status() for that."""
    # Paginated: callers must never see a partial page.
    pages = ecs.get_paginator("list_tasks").paginate(cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING")
    return [a.rsplit("/", 1)[-1] for page in pages for a in page["taskArns"]]


def describe_status(refs):
    """Map task id -> lastStatus from DescribeTasks (up to 100 per call). `refs` are ids or ARNs. A task
    ECS no longer knows (MISSING, for example aged out) maps to None."""
    out = {}
    refs = list(refs)
    for i in range(0, len(refs), 100):
        resp = ecs.describe_tasks(cluster=CLUSTER, tasks=refs[i:i + 100])
        for t in resp.get("tasks", []):
            out[t["taskArn"].rsplit("/", 1)[-1]] = t
    return {r.rsplit("/", 1)[-1]: out.get(r.rsplit("/", 1)[-1]) for r in refs}


def task_gone(t):
    """Gone only when DescribeTasks says STOPPED or no longer knows the task."""
    return t is None or t["lastStatus"] == "STOPPED"


def prune_registry(store, running):
    """Drop registry entries for tasks that have stopped. A task missing from `running` is checked with
    DescribeTasks first, because a task ECS is stopping is still running (and draining) for minutes.
    An in-progress removal is left for scale_in's resume path, which cleans it up itself."""
    live = set(running)
    candidates = {tid: v for tid, v in store.items() if tid not in live and v.get("state") not in IN_PROGRESS}
    # Orphaned hashes: a task:<id> key whose id is not in the registry set (for example recreated by an
    # older task image after the controller removed it). Same rule: delete only once the task is gone.
    orphans = {k.split(":", 1)[1]: db.hgetall(k) for k in db.scan_iter("task:*")
               if k.split(":", 1)[1] not in store}
    candidates.update({tid: v for tid, v in orphans.items() if tid not in live})
    if not candidates:
        return
    status = describe_status(v.get("arn") or tid for tid, v in candidates.items())
    for tid, v in candidates.items():
        if task_gone(status.get(tid)):
            db.srem("tasks", tid)
            db.delete(f"task:{tid}")
            store.pop(tid, None)
            log("pruned", task=tid, state=v.get("state"), orphan=tid in orphans)


def service():
    return ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]


def protection(task_ids):
    out = {}
    for i in range(0, len(task_ids), 10):
        resp = ecs.get_task_protection(cluster=CLUSTER, tasks=task_ids[i:i + 10])
        for t in resp["protectedTasks"]:
            out[t["taskArn"].rsplit("/", 1)[-1]] = t["protectionEnabled"]
    return out


def set_state(tid, state):
    db.hset(f"task:{tid}", "state", state)
    log("state_recorded", task=tid, state=state)


def release_protection(tid):
    """Turn off scale-in protection for one task. UpdateTaskProtection returns HTTP 200 even when it
    fails for a task, listing it in `failures`, so that list must be checked."""
    resp = ecs.update_task_protection(cluster=CLUSTER, tasks=[tid], protectionEnabled=False)
    if resp.get("failures"):
        sys.exit(f"could not release protection for task {tid}: {resp['failures']}; not lowering the count")
    return resp


def acquire_lease():
    if not db.set(LEASE_KEY, me, nx=True, ex=LEASE_SECONDS):
        sys.exit("another controller holds the lease")


def renew_lease(attempts=3):
    """Extend the lease only if this controller still owns it. WATCH makes check-and-extend atomic:
    if the key changes between the read and the EXPIRE, the transaction is discarded. A discarded
    transaction alone doesn't mean the lease was lost (ElastiCache Serverless can abort one during slot
    migration), so retry, and report a loss only when the key names another owner (or none)."""
    for _ in range(attempts):
        with db.pipeline() as p:
            try:
                p.watch(LEASE_KEY)
                if p.get(LEASE_KEY) != me:
                    return False
                p.multi()
                p.expire(LEASE_KEY, LEASE_SECONDS)
                p.execute()
                return True
            except redis.WatchError:
                continue
    # Still contended after every attempt: the outcome is unknown, not lost. The lease stays valid
    # until it expires, so the next heartbeat tries again.
    log("lease_renew_contended", attempts=attempts)
    return None


class LeaseHeartbeat:
    """Renews the lease every HEARTBEAT_SECONDS on a daemon thread. If the lease is ever found to
    belong to someone else, `lost` is set and the main thread stops at its next check_lease()."""

    def __init__(self):
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lease-heartbeat", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                if renew_lease() is False:
                    self.lost.set()
                    log("lease_lost")
                    return
            except redis.RedisError as e:  # transient; the lease is still valid for up to LEASE_SECONDS
                log("lease_heartbeat_failed", error=repr(e))


heartbeat = LeaseHeartbeat()


def check_lease():
    """Call before each step that changes ECS or the store."""
    if heartbeat.lost.is_set():
        sys.exit("lost the controller lease; stopping before another controller acts")


def release_lease():
    heartbeat.stop()
    if db.get(LEASE_KEY) == me:
        db.delete(LEASE_KEY)


def on_sigterm(signum, frame):
    """Amazon ECS stops a task with SIGTERM. Exiting through SystemExit runs `finally`, which
    releases the lease, so the next run can resume at once from the persisted state."""
    raise SystemExit("controller stopped by SIGTERM; the next run resumes from the persisted state")


def scale_in():
    if db.exists("controller:cooldown"):
        sys.exit("in cooldown after the last removal")
    store = tasks_in_store()
    running = running_task_ids()
    prune_registry(store, running)
    # Resume an interrupted removal before starting a new one.
    in_progress = [t for t, v in store.items() if v.get("state") in IN_PROGRESS]
    svc = service()
    if in_progress:
        d = in_progress[0]
        log("resuming", task=d, state=store[d]["state"])
        if d not in running:
            st = describe_status([store[d].get("arn") or d])[d]
            if task_gone(st):
                # D already stopped (or died and was replaced). Whatever count change this removal made
                # has taken effect or never happened; changing the count now could remove a second task.
                db.srem("tasks", d)
                db.delete(f"task:{d}")
                if store[d]["state"] != "DRAINING":
                    db.set("controller:cooldown", "1", ex=COOLDOWN)
                log("removed_not_running", task=d, state=store[d]["state"], desired=svc["desiredCount"])
                return
            # ECS is already stopping D (desiredStatus STOPPED) but it is still draining. Never change
            # the count here: wait for it to stop, then clean up.
            log("resume_draining", task=d, state=store[d]["state"], last_status=st["lastStatus"])
            finish_removal(d, cooldown=store[d]["state"] != "DRAINING")
            return
    else:
        accepting = [t for t in running if store.get(t, {}).get("state") == "ACCEPTING"]
        # A task that is still starting is unprotected until its first protection call.
        if svc["pendingCount"] or svc["runningCount"] != svc["desiredCount"] or len(accepting) != len(running):
            sys.exit("a task is starting or the service is not steady; not scaling in")
        if len(accepting) < 2:
            sys.exit("need at least two accepting tasks to remove one")
        d = min(accepting, key=lambda t: int(store[t].get("active_calls", 0)))
        log("nominated", task=d, active_calls=store[d].get("active_calls"))
        check_lease()
        set_state(d, "DRAINING")

    state = db.hget(f"task:{d}", "state")
    if state == "STOPPING":
        # Written by an older calling task on SIGTERM, which hides how far the removal got. ECS is
        # already stopping D, so never change the count here: wait for it to stop and clean up.
        log("resume_stopping", task=d)
        state = "COUNT_LOWERED"
    if state == "DRAINING":
        deadline = time.time() + DRAIN_READY_TIMEOUT
        polls = 0
        while True:
            check_lease()
            t = db.hgetall(f"task:{d}")
            gate = {"dialer_stopped": t.get("dialer_stopped"), "pending_calls": t.get("pending_calls"),
                    "renewal_stopped": t.get("renewal_stopped"), "renewal_in_flight": t.get("renewal_in_flight")}
            # renewal_stopped=1: the task will not renew again and no renewal PUT is still in flight,
            # so nothing the task sent can re-protect it after the release below.
            if (t.get("dialer_stopped") == "1" and int(t.get("pending_calls", 1)) == 0
                    and t.get("renewal_stopped") == "1"):
                log("drain_ready", task=d, active_calls=t.get("active_calls"), **gate)
                break
            if time.time() > deadline:
                sys.exit(f"task {d} did not stop dialing in time; it stays DRAINING, and because it has "
                         "stopped renewing, its protection lapses at its current expiry")
            if polls % 15 == 0:  # about every 30 s
                log("drain_waiting", task=d, **gate)
            polls += 1
            time.sleep(2)
        # Persist the target count, and the count it was derived from, before acting. A resumed run
        # sets the same absolute value only if nobody changed the count in between.
        before = service()["desiredCount"]
        target = before - 1
        db.hset(f"task:{d}", mapping={"target_desired": target, "desired_before": before})
        check_lease()
        release_protection(d)
        log("protection_released", task=d)
        set_state(d, "UNPROTECTED")
        state = "UNPROTECTED"

    if state == "UNPROTECTED":
        # Re-check, and re-release D if a late renewal re-protected it (bounded), before lowering.
        for attempt in range(RERELEASE_ATTEMPTS + 1):
            others = [t for t in running_task_ids() if t != d]
            prot = protection(others + [d])
            if d not in prot:
                # GetTaskProtection listed D under failures: its protection is unknown, so fail closed.
                sys.exit(f"protection of task {d} is unknown; not lowering the count")
            if prot[d] is False:
                break
            if attempt == RERELEASE_ATTEMPTS:
                sys.exit(f"task {d} is still protected after {RERELEASE_ATTEMPTS} re-releases; "
                         "not lowering the count")
            log("protected_again", task=d, attempt=attempt + 1)
            check_lease()
            release_protection(d)
            time.sleep(2)
        unprotected = [t for t in others if not prot.get(t)]
        if unprotected:
            sys.exit(f"other tasks are unprotected {unprotected}; not lowering the count")
        log("others_protected", tasks=others)
        rec = db.hgetall(f"task:{d}")
        target = int(rec["target_desired"])
        before = int(rec["desired_before"]) if "desired_before" in rec else None
        current = service()["desiredCount"]
        if current == target:
            # An earlier run lowered the count but died before recording COUNT_LOWERED.
            log("count_already_lowered", desired=current)
        elif current == before:
            check_lease()
            ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=target)
            log("count_lowered", desired_to=target)
        else:
            sys.exit(f"desired count is {current}, but this removal planned {before} -> {target}; the count "
                     f"changed since task {d} was nominated, so nothing was changed. Check the service, "
                     "then re-protect the task or finish its removal by hand")
        set_state(d, "COUNT_LOWERED")

    finish_removal(d, cooldown=True)


def finish_removal(d, cooldown):
    """Wait until DescribeTasks reports D STOPPED (or MISSING), then remove it from the registry."""
    deadline = time.time() + STOP_TIMEOUT
    ref = db.hget(f"task:{d}", "arn") or d
    while True:
        check_lease()
        task = describe_status([ref])[d]
        if task_gone(task):
            log("task_stopped", task=d, stopped_reason=task.get("stoppedReason") if task else "MISSING")
            break
        # Backstop: if D was re-protected after the check, every running task is protected and
        # ECS can't stop any of them (DEPLOYMENT_BLOCKED). Release D again so ECS can stop it.
        if protection([d]).get(d):
            log("protected_after_lowering", task=d)
            release_protection(d)
        if time.time() > deadline:
            sys.exit(f"task {d} did not stop within {STOP_TIMEOUT}s")
        time.sleep(10)
    db.srem("tasks", d)
    db.delete(f"task:{d}")
    if cooldown:
        db.set("controller:cooldown", "1", ex=COOLDOWN)
    log("removed", task=d, cooldown_seconds=COOLDOWN if cooldown else 0)


def abort():
    """Back out an interrupted removal: re-protect the task, clear its removal fields and return it to
    ACCEPTING. Only before the count was lowered, and only while the task is still running."""
    store = tasks_in_store()
    busy = [t for t, v in store.items() if v.get("state") in IN_PROGRESS]
    if not busy:
        sys.exit("no removal in progress; nothing to abort")
    d = busy[0]
    rec = store[d]
    if rec["state"] not in ("DRAINING", "UNPROTECTED"):
        sys.exit(f"task {d} is {rec['state']}: the count was already lowered, so the removal can't be "
                 "aborted; run scale-in to finish it")
    if "target_desired" in rec and service()["desiredCount"] == int(rec["target_desired"]):
        sys.exit(f"the desired count already equals this removal's target {rec['target_desired']}; the count "
                 "was lowered, so the removal can't be aborted; run scale-in to finish it")
    st = describe_status([rec.get("arn") or d])[d]
    if task_gone(st) or d not in running_task_ids():
        sys.exit(f"task {d} is not running (or ECS is stopping it); nothing to re-protect. Run scale-in to "
                 "clean up its record")
    check_lease()
    resp = ecs.update_task_protection(cluster=CLUSTER, tasks=[d], protectionEnabled=True,
                                      expiresInMinutes=ABORT_PROTECT_MINUTES)
    if resp.get("failures"):
        sys.exit(f"could not re-protect task {d}: {resp['failures']}; nothing else was changed")
    if protection([d]).get(d) is not True:
        sys.exit(f"task {d} is not reported as protected after re-protecting it; nothing else was changed")
    log("protection_restored", task=d, expires_in_minutes=ABORT_PROTECT_MINUTES)
    # Clear the removal fields before handing the task back, so a later run can't reuse a stale target.
    db.hdel(f"task:{d}", "target_desired", "desired_before")
    set_state(d, "ACCEPTING")
    log("aborted", task=d, previous_state=rec["state"])


def scale_out():
    store = tasks_in_store()
    running = running_task_ids()
    # Before the count is lowered, raising it would make the removal's stored target stale. Backing the
    # removal out (abort) is the faster way to add capacity: it hands the warm task back at once.
    before_lowering = [t for t, v in store.items() if v.get("state") in ("DRAINING", "UNPROTECTED")]
    if before_lowering:
        sys.exit(f"a removal of {before_lowering} hasn't lowered the count yet; run abort to hand the task "
                 "back (the fastest way to add capacity), or scale-in to finish the removal first")
    # After the count is lowered, scaling out is safe once ECS has started stopping the task (it is no
    # longer desired RUNNING). Before that, raising the count could leave it running, unprotected.
    not_yet_stopping = [t for t, v in store.items()
                        if v.get("state") in ("COUNT_LOWERED", "STOPPING") and t in running]
    if not_yet_stopping:
        sys.exit(f"Amazon ECS hasn't started stopping {not_yet_stopping} yet; retry in a minute")
    svc = service()
    desired = svc["desiredCount"]
    prot = protection(running) if running else {}
    protected = sum(1 for t in running if prot.get(t))
    # running counts only tasks ECS wants running, so a removed task that is still draining isn't a surplus.
    if protected > desired or len(running) > desired:
        # Every surplus task is protected, so ECS can't stop one, refuses all renewals
        # (DEPLOYMENT_BLOCKED) and won't start a new task for a +1: waiting would only time out.
        sys.exit(f"service is blocked: {protected} protected and {len(running)} running tasks for desired "
                 f"count {desired}; not changing the count. Adopt the surplus by setting the desired count to "
                 f"{max(protected, len(running))}, or let protection lapse on one task")
    before = set(running)
    check_lease()
    ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=desired + 1)
    log("count_raised", desired_from=desired, desired_to=desired + 1)
    deadline = time.time() + 600
    while time.time() < deadline:
        check_lease()
        new = [t for t in running_task_ids() if t not in before]
        if new and db.hget(f"task:{new[0]}", "state") == "ACCEPTING":
            log("new_task_accepting", task=new[0])
            return
        time.sleep(5)
    sys.exit("new task did not reach ACCEPTING in time")


def status():
    svc = service()
    log("service", desired=svc["desiredCount"], running=svc["runningCount"], pending=svc["pendingCount"])
    if svc["runningCount"] > svc["desiredCount"]:
        # Every task is protected, so Amazon ECS can't remove the surplus, and it refuses all
        # protection renewals (DEPLOYMENT_BLOCKED) until protection expires somewhere. Drain one
        # task deliberately with scale-in logic before that happens.
        log("surplus_tasks", count=svc["runningCount"] - svc["desiredCount"])
    store = tasks_in_store()
    ids = running_task_ids()
    prune_registry(store, ids)
    prot = protection(ids) if ids else {}
    for tid in ids:
        v = store.get(tid, {})
        log("task", task=tid, state=v.get("state"), proc_state=v.get("proc_state"),
            active_calls=v.get("active_calls"),
            pending_calls=v.get("pending_calls"), placed_total=v.get("placed_total"),
            protected=prot.get(tid))


def main(argv):
    # Without a handler, SIGTERM (how Amazon ECS stops a task) kills Python without running
    # `finally`, which used to leave the lease behind.
    signal.signal(signal.SIGTERM, on_sigterm)
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        status()
        return
    acquire_lease()
    heartbeat.start()
    try:
        {"scale-in": scale_in, "scale-out": scale_out, "abort": abort}[cmd]()
    finally:
        release_lease()


if __name__ == "__main__":
    main(sys.argv)
