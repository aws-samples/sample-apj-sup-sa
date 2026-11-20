"""Controller tests. Every assertion states the SAFE / INTENDED behaviour; a failing test is a finding.

Letters refer to the review checklist (a..o)."""
import fakeredis
import pytest

import controller
from fake_ecs import Crash, FakeClock, FakeECS, World

D = "taskdddd"
OTHERS = ["taskaaaa", "taskbbbb", "taskcccc"]


def idx(events, kind, name, pred=lambda f: True):
    for i, e in enumerate(events):
        if e[0] == kind and e[1] == name and pred(e[2]):
            return i
    raise AssertionError(f"{kind}:{name} not found in timeline")


def once(fn):
    state = {"done": False}

    def wrapper(*a):
        if not state["done"]:
            state["done"] = True
            return fn(*a)
    return wrapper


def raise_crash(*_):
    raise Crash("injected")


def ecs_ops(w, op):
    return [kw for o, kw in w.ecs.calls if o == op]


def add_new_task_on_scale_out(w):
    """Simulated ECS + task: when desired > running, launch a protected task that reaches ACCEPTING."""
    n = {"i": 0}

    def hook():
        while len(w.ecs.running()) < w.ecs.desired:
            n["i"] += 1
            tid = f"tasknew{n['i']:02d}"
            w.ecs.tasks[tid] = {"status": "RUNNING", "protected": True, "reason": None}
            w.db.sadd("tasks", tid)
            w.db.hset(f"task:{tid}", mapping={"arn": w.ecs.arn(tid), "state": "ACCEPTING", "active_calls": 0,
                                              "pending_calls": 0, "dialer_stopped": 0})
    w.tick_hooks.append(hook)


# ---------------------------------------------------------------- (a) happy path
def test_a_happy_path_order(world):
    w = world
    snap = {}
    # Calls still ringing for 3 ticks after the dialer stops: release must wait for pending_calls == 0.
    w.auto_drain = False
    ticks = {"n": 0}

    def drain():
        if w.db.hget(f"task:{D}", "state") == "DRAINING":
            ticks["n"] += 1
            w.db.hset(f"task:{D}", mapping={"dialer_stopped": 1, "pending_calls": max(0, 3 - ticks["n"]),
                                            "renewal_stopped": 1})  # renewal_stopped: new field (H1)
    w.tick_hooks.append(drain)

    def before_release(kw):
        t = w.db.hgetall(f"task:{D}")
        snap.update(target=t.get("target_desired"), state=t.get("state"),
                    pending=t.get("pending_calls"), dialer=t.get("dialer_stopped"))
    w.ecs.before["update_task_protection"] = before_release

    def after_get(kw, resp):
        # (Updated for H1.) Keep the first call (the pre-lowering check); the fixed controller also
        # polls D alone while waiting for it to stop.
        snap.setdefault("prot", {FakeECS.tid(p["taskArn"]): p["protectionEnabled"] for p in resp["protectedTasks"]})
    w.ecs.after["get_task_protection"] = after_get

    controller.scale_in()
    ev = w.events
    nominated = [e for e in ev if e[1] == "nominated"]
    assert nominated[0][2]["task"] == D, "least-busy ACCEPTING task must be chosen"
    order = [
        idx(ev, "log", "state_recorded", lambda f: f["state"] == "DRAINING"),
        idx(ev, "log", "drain_ready"),
        idx(ev, "ecs", "update_task_protection"),
        idx(ev, "log", "state_recorded", lambda f: f["state"] == "UNPROTECTED"),
        idx(ev, "ecs", "get_task_protection"),
        idx(ev, "log", "others_protected"),
        idx(ev, "ecs", "update_service"),
        idx(ev, "log", "state_recorded", lambda f: f["state"] == "COUNT_LOWERED"),
        idx(ev, "log", "task_stopped"),
        idx(ev, "log", "removed"),
    ]
    assert order == sorted(order), f"steps out of order: {order}"
    assert snap["target"] == "3" and snap["state"] == "DRAINING"
    assert snap["dialer"] == "1" and snap["pending"] == "0", "released before ringing calls connected"
    utp = ecs_ops(w, "update_task_protection")
    assert utp == [{"cluster": "voice-cluster", "tasks": [D], "protectionEnabled": False}], "release D only"
    assert snap["prot"][D] is False and all(snap["prot"][t] for t in OTHERS)
    assert [kw["desiredCount"] for kw in ecs_ops(w, "update_service")] == [3]
    assert w.ecs.tasks[D]["status"] == "STOPPED"
    assert all(w.ecs.tasks[t]["status"] == "RUNNING" and w.ecs.tasks[t]["protected"] for t in OTHERS)
    assert D not in w.db.smembers("tasks") and not w.db.exists(f"task:{D}")
    assert w.db.exists("controller:cooldown")


# ---------------------------------------------------------------- (b) crash / resume
def _crash_log(event, pred=lambda f: True):
    return lambda w: setattr(w, "crash_on_log", (event, pred))


CRASH_POINTS = {
    "b1 after nominate, before DRAINING persisted": _crash_log("nominated"),
    "b2 after DRAINING persisted": _crash_log("state_recorded", lambda f: f["state"] == "DRAINING"),
    "b3 after drain_ready": _crash_log("drain_ready"),
    "b4 target persisted, before UpdateTaskProtection": lambda w: w.ecs.before.__setitem__(
        "update_task_protection", once(raise_crash)),
    "b5 after release, before UNPROTECTED persisted": _crash_log("protection_released"),
    "b6 after UNPROTECTED persisted": _crash_log("state_recorded", lambda f: f["state"] == "UNPROTECTED"),
    "b7 after GetTaskProtection check": _crash_log("others_protected"),
    "b8 after UpdateService, before COUNT_LOWERED persisted": lambda w: w.ecs.after.__setitem__(
        "update_service", once(raise_crash)),
    "b9 after COUNT_LOWERED persisted": _crash_log("state_recorded", lambda f: f["state"] == "COUNT_LOWERED"),
    "b10 after task_stopped, before cleanup": _crash_log("task_stopped"),
}


@pytest.mark.parametrize("point", list(CRASH_POINTS))
def test_b_crash_and_resume_never_lowers_twice(world, point):
    w = world
    CRASH_POINTS[point](w)
    with pytest.raises(Crash):
        controller.scale_in()
    w.tick()  # time passes before the restart (ECS may stop D meanwhile)
    controller.scale_in()
    counts = [kw["desiredCount"] for kw in ecs_ops(w, "update_service")]
    assert counts and set(counts) == {3}, f"desired counts sent: {counts}"
    assert w.ecs.desired == 3
    assert [t for t, v in w.ecs.tasks.items() if v["status"] == "STOPPED"] == [D]
    assert not w.db.exists(f"task:{D}") and w.db.exists("controller:cooldown")


def test_b11_resume_after_task_wrote_STOPPING(world):
    """Controller dies after lowering the count; while it is down, ECS stops D and D's SIGTERM handler
    writes state=STOPPING (calling_task.py shutdown()). The restarted controller must finish D's removal,
    not start a second one."""
    w = world
    w.crash_on_log = ("state_recorded", lambda f: f["state"] == "COUNT_LOWERED")
    with pytest.raises(Crash):
        controller.scale_in()
    w.tick()
    assert w.ecs.tasks[D]["status"] == "STOPPED"
    w.db.hset(f"task:{D}", "state", "STOPPING")  # what calling_task.shutdown() writes
    try:
        controller.scale_in()
    except SystemExit:
        pass
    assert w.ecs.desired == 3, f"a second task was removed; desired is now {w.ecs.desired}"
    assert not w.db.exists(f"task:{D}"), "D's record was never cleaned up"


# ---------------------------------------------------------------- (c) late renewal race
def test_c1_renewal_before_check_blocks_count_lowering(world):
    """(Updated for M4.) A renewal re-protects D before every check: the controller re-releases a
    bounded number of times, then exits without lowering the count. Original: one exit, no retry."""
    w = world
    w.ecs.before["get_task_protection"] = lambda kw: w.ecs.tasks[D].__setitem__("protected", True)
    with pytest.raises(SystemExit, match="still protected"):
        controller.scale_in()
    assert w.ecs.desired == 4 and not ecs_ops(w, "update_service")
    assert len(ecs_ops(w, "update_task_protection")) == 1 + controller.RERELEASE_ATTEMPTS


def test_c2_resume_after_late_renewal_rereleases(world):
    """(Updated for M4.) One late renewal re-protects D before the check: the same run re-releases D
    and finishes the removal. Original test expected an exit first, then a resume that re-releases;
    the fix no longer exits, so the first run is the one that must finish."""
    w = world
    w.ecs.before["get_task_protection"] = once(lambda kw: w.ecs.tasks[D].__setitem__("protected", True))
    d_protected_at_lowering = []
    w.ecs.before["update_service"] = lambda kw: d_protected_at_lowering.append(w.ecs.tasks[D]["protected"])
    try:
        controller.scale_in()
    except SystemExit as e:
        pytest.fail(f"late renewal not re-released; stuck until protection expires: {e}")
    assert d_protected_at_lowering == [False], "count lowered while D was protected"
    assert w.ecs.desired == 3 and w.ecs.tasks[D]["status"] == "STOPPED"
    assert [kw["protectionEnabled"] for kw in ecs_ops(w, "update_task_protection")] == [False, False]


def test_c3_renewal_after_check_before_update_service(world):
    """Renewal lands between GetTaskProtection and UpdateService. Safe outcome: ECS is never left with
    running > desired and every task protected."""
    w = world
    w.ecs.before["update_service"] = once(lambda kw: w.ecs.tasks[D].__setitem__("protected", True))
    msg = None
    try:
        controller.scale_in()
    except SystemExit as e:
        msg = str(e)
    blocked = w.ecs.schedule() == "blocked"
    assert not blocked, (f"count lowered to {w.ecs.desired} with all {len(w.ecs.running())} tasks protected "
                         f"(DEPLOYMENT_BLOCKED until protection expires); controller exit: {msg!r}; "
                         f"re-release attempts after lowering: "
                         f"{len(ecs_ops(w, 'update_task_protection')) - 1}")


# ---------------------------------------------------------------- (d) UpdateTaskProtection failures[]
def test_d1_release_failures_are_checked(world):
    w = world
    w.ecs.update_failures[D] = "TASK_NOT_VALID"
    with pytest.raises(SystemExit):
        controller.scale_in()
    released = [e for e in w.events if e[1] == "protection_released"]
    assert not released and w.db.hget(f"task:{D}", "state") != "UNPROTECTED", \
        "HTTP 200 with failures[] was treated as success: logged protection_released and recorded UNPROTECTED"


def test_d2_backstop_keeps_count_when_release_failed(world):
    # (Updated for H1/M1.) The exit message changed from "protected again" to a re-release / release
    # failure message; the asserted safety property (count stays 4) is unchanged.
    w = world
    w.ecs.update_failures[D] = "TASK_NOT_VALID"
    with pytest.raises(SystemExit, match="protect"):
        controller.scale_in()
    assert w.ecs.desired == 4


def test_d3_release_failure_plus_get_failure(world):
    """Release silently failed AND GetTaskProtection reports D under failures[]: must not lower."""
    w = world
    w.ecs.update_failures[D] = "TASK_NOT_VALID"
    w.ecs.get_failures.add(D)
    try:
        controller.scale_in()
    except SystemExit:
        pass
    assert w.ecs.desired == 4, "count lowered while D was still protected"


# ---------------------------------------------------------------- (e) GetTaskProtection missing D
def test_e1_missing_D_no_keyerror(world):
    w = world
    w.ecs.get_failures.add(D)
    try:
        controller.scale_in()
    except SystemExit:
        pass
    except KeyError as e:
        pytest.fail(f"KeyError {e}")


def test_e2_missing_D_fails_closed(world):
    w = world
    w.ecs.get_failures.add(D)
    try:
        controller.scale_in()
    except SystemExit:
        pass
    assert not ecs_ops(w, "update_service"), "D's protection was unverified (failures[]) yet count was lowered"


def test_e3_missing_other_task_fails_closed(world):
    w = world
    w.ecs.get_failures.add("taskaaaa")
    with pytest.raises(SystemExit, match="other tasks are unprotected"):
        controller.scale_in()
    assert w.ecs.desired == 4


# ---------------------------------------------------------------- (f) IDs vs ARNs
def test_f_id_arn_consistency(world):
    w = world
    controller.scale_in()
    for op in ("get_task_protection", "update_task_protection"):
        for kw in ecs_ops(w, op):
            assert all("/" not in t for t in kw["tasks"]), f"{op} mixed ARNs and IDs: {kw['tasks']}"
    for kw in ecs_ops(w, "describe_tasks"):
        assert kw["tasks"] == [w.ecs.arn(D)]
    assert all("/" not in m for m in w.db.smembers("tasks"))
    assert controller.protection(["taskaaaa"]) == {"taskaaaa": True}


# ---------------------------------------------------------------- (g) batch limits / pagination
@pytest.fixture
def big_world(monkeypatch):
    def make(n):
        ids = [f"task{i:04d}" for i in range(n)]
        ecs = FakeECS(ids=ids)
        db = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        w = World(ecs, db, [])
        for i, tid in enumerate(ids):
            db.sadd("tasks", tid)
            db.hset(f"task:{tid}", mapping={"arn": ecs.arn(tid), "state": "ACCEPTING", "active_calls": i + 1,
                                            "pending_calls": 0, "dialer_stopped": 0})
        monkeypatch.setattr(controller, "ecs", ecs)
        monkeypatch.setattr(controller, "db", db)
        monkeypatch.setattr(controller, "time", FakeClock(w))
        monkeypatch.setattr(controller, "log", lambda e, **f: w.events.append(("log", e, f)))
        return w
    return make


def test_g1_get_protection_chunks_within_limit(big_world):
    w = big_world(250)
    out = controller.protection(w.ecs.ids)
    sizes = [len(kw["tasks"]) for kw in ecs_ops(w, "get_task_protection")]
    assert len(out) == 250 and max(sizes) <= 100
    w.events.append(("note", "chunks", {"sizes": sizes}))


def test_g2_update_protection_within_limit(world):
    controller.scale_in()
    assert all(len(kw["tasks"]) <= 10 for kw in ecs_ops(world, "update_task_protection"))


def test_g3_list_tasks_paginates(big_world):
    w = big_world(120)
    ids = controller.running_task_ids()
    assert len(ids) == 120, f"ListTasks nextToken ignored: saw {len(ids)} of 120 running tasks"


def test_g4_status_with_many_tasks(big_world):
    w = big_world(25)
    controller.status()
    assert sum(1 for e in w.events if e[1] == "task") == 25


# ---------------------------------------------------------------- (k) lease / one-at-a-time / cooldown
def test_k1_lease_is_exclusive(world, monkeypatch):
    controller.acquire_lease()
    monkeypatch.setattr(controller, "me", "other-controller")
    with pytest.raises(SystemExit, match="another controller"):
        controller.acquire_lease()
    controller.release_lease()  # non-owner must not delete it
    assert world.db.exists("controller:lease")


def test_k2_hard_killed_controller_can_be_restarted_promptly(world, monkeypatch):
    """ECS stops the controller task with SIGTERM -> Python dies without running `finally`."""
    controller.acquire_lease()
    ttl = world.db.ttl("controller:lease")
    assert ttl <= 900, (f"lease TTL {ttl}s: a killed controller blocks resume for ~1h, longer than the "
                        f"900 s deregistration delay, which makes b11 (STOPPING overwrite) the likely path")


def test_k3_cooldown_blocks_next_removal(world):
    controller.scale_in()
    with pytest.raises(SystemExit, match="cooldown"):
        controller.scale_in()


def test_k4_in_progress_removal_resumed_before_new_one(world):
    w = world
    w.db.hset("task:taskbbbb", "state", "DRAINING")
    controller.scale_in()
    assert [e[2]["task"] for e in w.events if e[1] == "resuming"] == ["taskbbbb"]
    assert w.ecs.tasks["taskbbbb"]["status"] == "STOPPED" and w.ecs.tasks[D]["status"] == "RUNNING"


def test_k5_scale_out_during_interrupted_scale_in(world):
    """(Updated for M2.) scale-in stops after UNPROTECTED (target_desired=3 persisted). The original test
    expected scale-out to raise 4->5 and the resume to end at 4; the fix makes scale-out refuse while a
    removal hasn't lowered the count yet, so the stale target can never be applied. Resume then ends at 3."""
    w = world
    add_new_task_on_scale_out(w)
    w.crash_on_log = ("state_recorded", lambda f: f["state"] == "UNPROTECTED")
    with pytest.raises(Crash):
        controller.scale_in()
    with pytest.raises(SystemExit, match="hasn't lowered the count yet"):
        controller.scale_out()
    assert w.ecs.desired == 4 and not ecs_ops(w, "update_service")
    controller.scale_in()
    assert w.ecs.desired == 3, f"stale absolute target applied: desired {w.ecs.desired}"


def test_k6_count_changed_elsewhere_during_interrupted_scale_in(world):
    """(New, M2.) Something other than this controller raises the count 4->5 mid-removal. Resume must
    not apply the stale target 3; it exits and changes nothing."""
    w = world
    add_new_task_on_scale_out(w)
    w.crash_on_log = ("state_recorded", lambda f: f["state"] == "UNPROTECTED")
    with pytest.raises(Crash):
        controller.scale_in()
    w.ecs.desired = 5
    w.tick()
    with pytest.raises(SystemExit, match="count changed"):
        controller.scale_in()
    assert w.ecs.desired == 5 and not ecs_ops(w, "update_service")


# ---------------------------------------------------------------- (l) starting tasks
def test_l1_task_without_state_blocks_scale_in(world):
    world.db.hdel("task:taskaaaa", "state")
    with pytest.raises(SystemExit, match="starting"):
        controller.scale_in()


def test_l2_pending_task_blocks_scale_in(world):
    world.ecs.pending = 1
    with pytest.raises(SystemExit, match="starting"):
        controller.scale_in()


def test_l3_needs_two_accepting(monkeypatch, big_world):
    big_world(1)
    with pytest.raises(SystemExit, match="at least two"):
        controller.scale_in()


def test_l4_new_unprotected_task_during_removal_blocks_lowering(world):
    w = world

    def add_starting(kw):
        w.ecs.tasks["taskstart"] = {"status": "RUNNING", "protected": False, "reason": None}
    w.ecs.before["update_task_protection"] = once(add_starting)
    with pytest.raises(SystemExit, match="other tasks are unprotected"):
        controller.scale_in()
    assert w.ecs.desired == 4


# ---------------------------------------------------------------- (m) reconcile vs ListTasks
def test_m1_ghost_entries_pruned(world):
    w = world
    w.db.sadd("tasks", "taskghost")
    w.db.hset("task:taskghost", mapping={"arn": w.ecs.arn("taskghost"), "state": "ACCEPTING"})
    controller.scale_in()
    controller.status()
    assert "taskghost" not in w.db.smembers("tasks"), "no reconcile: dead task stays in the registry forever"


def test_m2_dead_draining_task_is_not_resumed_into_a_count_drop(world):
    """A task died while DRAINING (e.g. health check failure) and ECS already replaced it. Resuming its
    removal must not lower the desired count while every running task is protected."""
    w = world
    w.ecs.tasks["taskdead"] = {"status": "STOPPED", "protected": False, "reason": "Task failed ELB health checks"}
    w.db.sadd("tasks", "taskdead")
    w.db.hset("task:taskdead", mapping={"arn": w.ecs.arn("taskdead"), "state": "DRAINING",
                                        "dialer_stopped": 1, "pending_calls": 0})
    try:
        controller.scale_in()
    except SystemExit:
        pass
    assert w.ecs.desired == 4 and w.ecs.schedule() != "blocked", \
        f"desired={w.ecs.desired} running={len(w.ecs.running())} all protected -> DEPLOYMENT_BLOCKED"


# ---------------------------------------------------------------- extra edges
def test_x1_drain_timeout_keeps_count(world):
    w = world
    w.auto_drain = False
    with pytest.raises(SystemExit, match="did not stop dialing"):
        controller.scale_in()
    assert w.ecs.desired == 4 and w.db.hget(f"task:{D}", "state") == "DRAINING"
    assert not ecs_ops(w, "update_task_protection")


def test_x2_describe_tasks_empty_handled(world):
    """Stopped tasks age out of DescribeTasks (~1 h). A late resume must not crash with IndexError."""
    w = world
    w.crash_on_log = ("state_recorded", lambda f: f["state"] == "COUNT_LOWERED")
    with pytest.raises(Crash):
        controller.scale_in()
    del w.ecs.tasks[D]
    try:
        controller.scale_in()
    except IndexError:
        pytest.fail("describe_tasks(...)['tasks'][0] -> IndexError when the task has aged out")
