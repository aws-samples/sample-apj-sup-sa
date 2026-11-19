"""Tests for the second round of fixes: N items were found in live tests, L items in code review."""
import pytest

import controller
from fake_ecs import Crash

D = "taskdddd"


def ecs_ops(w, op):
    return [kw for o, kw in w.ecs.calls if o == op]


# ---------------------------------------------------------------- N1: desiredStatus vs lastStatus
def test_n1_resume_waits_for_draining_task_to_stop(world):
    """Live retest (b): the controller dies after COUNT_LOWERED. ECS sets D's desiredStatus to STOPPED at
    once, but D keeps draining (lastStatus RUNNING) for minutes. The restarted controller must wait for D
    to stop, not delete its record and report success while D is still running."""
    w = world
    w.ecs.stop_delay_ticks = 5
    w.crash_on_log = ("state_recorded", lambda f: f["state"] == "COUNT_LOWERED")
    with pytest.raises(Crash):
        controller.scale_in()
    w.tick()
    assert w.ecs.tasks[D]["desired"] == "STOPPED" and w.ecs.tasks[D]["status"] == "RUNNING"
    status_when_record_deleted = []
    real_delete = w.db.delete

    def delete(*keys):
        if f"task:{D}" in keys:
            status_when_record_deleted.append(w.ecs.tasks[D]["status"])
        return real_delete(*keys)
    w.db.delete = delete
    controller.scale_in()
    assert status_when_record_deleted == ["STOPPED"], \
        f"D's record deleted while D was {status_when_record_deleted} (still draining)"
    assert w.ecs.desired == 3 and [kw["desiredCount"] for kw in ecs_ops(w, "update_service")] == [3]
    assert any(e[1] == "task_stopped" for e in w.events)


def test_n1_prune_keeps_a_task_ecs_is_still_stopping(world):
    """Registry pruning must not drop a task that has left ListTasks(RUNNING) but is still draining."""
    w = world
    w.ecs.tasks["taskeeee"] = {"status": "RUNNING", "desired": "STOPPED", "protected": False, "reason": None}
    w.db.sadd("tasks", "taskeeee")
    w.db.hset("task:taskeeee", mapping={"arn": w.ecs.arn("taskeeee"), "state": "ACCEPTING"})
    controller.status()
    assert "taskeeee" in w.db.smembers("tasks"), "pruned a task that is still running"
    w.ecs.tasks["taskeeee"]["status"] = "STOPPED"
    controller.status()
    assert "taskeeee" not in w.db.smembers("tasks") and not w.db.exists("task:taskeeee")


def test_n1_prune_drops_task_unknown_to_describe_tasks(world):
    """A task that has aged out of DescribeTasks (MISSING) is gone."""
    w = world
    w.db.sadd("tasks", "taskold1")
    w.db.hset("task:taskold1", mapping={"arn": w.ecs.arn("taskold1"), "state": "ACCEPTING"})
    controller.status()
    assert "taskold1" not in w.db.smembers("tasks")


# ---------------------------------------------------------------- N2: orphaned registry hashes
def test_n2_orphan_hash_of_stopped_task_is_pruned(world):
    """Live retest: task:3b954ce2 was recreated by the task's publish() after the controller deleted it,
    outside the `tasks` set, and stayed forever. Pruning must sweep such hashes once the task is gone."""
    w = world
    w.ecs.tasks["taskorph"] = {"status": "STOPPED", "desired": "STOPPED", "protected": False, "reason": "x"}
    w.db.hset("task:taskorph", mapping={"arn": w.ecs.arn("taskorph"), "proc_state": "STOPPING"})
    w.ecs.tasks["taskorp2"] = {"status": "RUNNING", "desired": "STOPPED", "protected": False, "reason": None}
    w.db.hset("task:taskorp2", mapping={"arn": w.ecs.arn("taskorp2"), "proc_state": "ACCEPTING"})
    controller.status()
    assert not w.db.exists("task:taskorph"), "orphan hash of a stopped task was not pruned"
    assert w.db.exists("task:taskorp2"), "pruned the hash of a task that is still running"


# ---------------------------------------------------------------- N4: abort an interrupted removal
def _interrupt_after_unprotected(w):
    w.crash_on_log = ("state_recorded", lambda f: f["state"] == "UNPROTECTED")
    with pytest.raises(Crash):
        controller.scale_in()
    assert w.ecs.tasks[D]["protected"] is False


def test_n4_abort_restores_interrupted_removal(world):
    w = world
    _interrupt_after_unprotected(w)
    controller.abort()
    rec = w.db.hgetall(f"task:{D}")
    assert w.ecs.tasks[D]["protected"] is True and rec["state"] == "ACCEPTING"
    assert "target_desired" not in rec and "desired_before" not in rec
    assert w.ecs.desired == 4 and not ecs_ops(w, "update_service")
    utp = ecs_ops(w, "update_task_protection")[-1]
    assert utp["tasks"] == [D] and utp["protectionEnabled"] is True


def test_n4_abort_after_external_count_change_unblocks_scale_out(world):
    """Live retest (c2): after the safe 'count changed' exit, abort is the way out."""
    w = world
    _interrupt_after_unprotected(w)
    w.ecs.desired = 5
    w.ecs.tasks["tasknew01"] = {"status": "RUNNING", "protected": True, "reason": None}
    with pytest.raises(SystemExit, match="count changed"):
        controller.scale_in()
    controller.abort()
    assert w.db.hget(f"task:{D}", "state") == "ACCEPTING" and w.ecs.desired == 5
    assert not [t for t, v in controller.tasks_in_store().items() if v.get("state") in controller.IN_PROGRESS]


def test_n4_abort_refused_once_count_lowered(world):
    w = world
    w.crash_on_log = ("state_recorded", lambda f: f["state"] == "COUNT_LOWERED")
    w.ecs.stop_delay_ticks = 50
    with pytest.raises(Crash):
        controller.scale_in()
    with pytest.raises(SystemExit, match="already lowered"):
        controller.abort()
    assert w.db.hget(f"task:{D}", "state") == "COUNT_LOWERED"


def test_n4_abort_refused_when_lowered_but_not_recorded(world):
    """UpdateService landed but the run died before recording COUNT_LOWERED (b8)."""
    w = world
    w.ecs.stop_delay_ticks = 50
    w.ecs.after["update_service"] = lambda kw, resp: (_ for _ in ()).throw(Crash("after update_service"))
    with pytest.raises(Crash):
        controller.scale_in()
    with pytest.raises(SystemExit, match="count was lowered"):
        controller.abort()
    assert w.ecs.tasks[D]["protected"] is False


def test_n4_abort_refused_when_task_not_running(world):
    w = world
    _interrupt_after_unprotected(w)
    w.ecs.tasks[D]["status"] = "STOPPED"
    with pytest.raises(SystemExit, match="not running"):
        controller.abort()
    assert not [kw for kw in ecs_ops(w, "update_task_protection") if kw["protectionEnabled"]]


def test_n4_abort_with_nothing_in_progress(world):
    with pytest.raises(SystemExit, match="nothing to abort"):
        controller.abort()


# ---------------------------------------------------------------- N5: drain gate is logged
def test_n5_drain_ready_logs_renewal_gate(world):
    w = world
    controller.scale_in()
    ready = [e[2] for e in w.events if e[0] == "log" and e[1] == "drain_ready"]
    assert ready and ready[0]["renewal_stopped"] == "1" and "renewal_in_flight" in ready[0]
    assert ready[0]["dialer_stopped"] == "1" and ready[0]["pending_calls"] == "0"


def test_n5_waiting_for_renewal_is_logged(world):
    """The task keeps a renewal in flight: the controller logs what it is waiting for."""
    w = world
    w.auto_drain = False

    def drain_but_renewing():
        if w.db.hget(f"task:{D}", "state") == "DRAINING":
            w.db.hset(f"task:{D}", mapping={"dialer_stopped": 1, "pending_calls": 0,
                                            "renewal_stopped": 0, "renewal_in_flight": 1})
    w.tick_hooks.append(drain_but_renewing)
    with pytest.raises(SystemExit, match="did not stop dialing"):
        controller.scale_in()
    waiting = [e[2] for e in w.events if e[0] == "log" and e[1] == "drain_waiting"]
    assert waiting and waiting[-1]["renewal_in_flight"] == "1" and waiting[-1]["renewal_stopped"] == "0"
    assert not ecs_ops(w, "update_task_protection"), "released while a renewal was in flight"


# ---------------------------------------------------------------- scale-out fast fail
def test_scale_out_fails_fast_when_blocked(world):
    """First live run (c) recovery: desired 2, 3 protected tasks. scale-out used to raise the count and
    then wait 600 s for a new task that never started."""
    w = world
    w.ecs.desired = 3  # 4 running, all protected
    t0 = controller.time.time()
    with pytest.raises(SystemExit, match="service is blocked: 4 protected"):
        controller.scale_out()
    assert not ecs_ops(w, "update_service") and controller.time.time() - t0 < 5


def test_scale_out_still_works_when_steady(world):
    w = world

    def launch():
        while len(w.ecs.running()) < w.ecs.desired:
            tid = f"tasknew{len(w.ecs.tasks):02d}"
            w.ecs.tasks[tid] = {"status": "RUNNING", "protected": True, "reason": None}
            w.db.sadd("tasks", tid)
            w.db.hset(f"task:{tid}", mapping={"arn": w.ecs.arn(tid), "state": "ACCEPTING"})
    w.tick_hooks.append(launch)
    controller.scale_out()
    assert w.ecs.desired == 5 and any(e[1] == "new_task_accepting" for e in w.events)
