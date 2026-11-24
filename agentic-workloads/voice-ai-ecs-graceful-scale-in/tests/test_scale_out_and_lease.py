"""Scale-out during a removal, and lease renewal when a WATCH transaction is discarded."""
import time as real_time

import pytest
import redis

import controller
from test_controller import add_new_task_on_scale_out, ecs_ops

D = "taskdddd"


# ---------------------------------------------------------------- scale-out during a removal
@pytest.mark.parametrize("state", ["DRAINING", "UNPROTECTED"])
def test_scale_out_refused_before_count_lowered(world, state):
    w = world
    w.db.hset(f"task:{D}", "state", state)
    with pytest.raises(SystemExit, match="run abort"):
        controller.scale_out()
    assert not ecs_ops(w, "update_service")


def test_scale_out_waits_until_ecs_is_stopping_the_removed_task(world):
    """The count is lowered but ECS hasn't picked D yet. Raising the count now could leave D running,
    unprotected, so scale-out must wait."""
    w = world
    w.db.hset(f"task:{D}", "state", "COUNT_LOWERED")
    w.ecs.tasks[D]["protected"] = False
    w.ecs.desired = 3
    with pytest.raises(SystemExit, match="hasn't started stopping"):
        controller.scale_out()
    assert not ecs_ops(w, "update_service")


def test_scale_out_allowed_while_removed_task_drains(world):
    """ECS is stopping D (desiredStatus STOPPED, still draining). Capacity can be added at once, and D
    doesn't count as a surplus task."""
    w = world
    w.db.hset(f"task:{D}", "state", "COUNT_LOWERED")
    w.ecs.tasks[D].update(protected=False, desired="STOPPED")
    w.ecs.desired = 3
    add_new_task_on_scale_out(w)
    controller.scale_out()
    assert w.ecs.desired == 4 and any(e[1] == "new_task_accepting" for e in w.events)


# ---------------------------------------------------------------- lease renewal
def flaky_pipeline(db, failures):
    """Make the first `failures` EXEC calls raise WatchError, as a discarded transaction does."""
    real = db.pipeline
    n = {"i": 0}

    def pipeline(*a, **k):
        p = real(*a, **k)
        execute = p.execute

        def flaky(*a2, **k2):
            if n["i"] < failures:
                n["i"] += 1
                raise redis.WatchError("transaction discarded")
            return execute(*a2, **k2)
        p.execute = flaky
        return p
    return pipeline


def test_renew_lease_retries_a_discarded_transaction(world, monkeypatch):
    w = world
    w.db.set(controller.LEASE_KEY, controller.me, ex=5)
    monkeypatch.setattr(w.db, "pipeline", flaky_pipeline(w.db, failures=1))
    assert controller.renew_lease() is True
    assert w.db.ttl(controller.LEASE_KEY) > 5, "the lease wasn't extended"


def test_renew_lease_reports_unknown_not_lost_when_always_discarded(world, monkeypatch):
    w = world
    w.db.set(controller.LEASE_KEY, controller.me)
    monkeypatch.setattr(w.db, "pipeline", flaky_pipeline(w.db, failures=100))
    assert controller.renew_lease() is None


def test_renew_lease_reports_lost_when_another_controller_owns_it(world):
    w = world
    w.db.set(controller.LEASE_KEY, "another-controller")
    assert controller.renew_lease() is False


@pytest.mark.parametrize("result, lost", [(None, False), (False, True)])
def test_heartbeat_stops_only_on_a_real_loss(monkeypatch, result, lost):
    monkeypatch.setattr(controller, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(controller, "renew_lease", lambda: result)
    monkeypatch.setattr(controller, "log", lambda *a, **k: None)
    hb = controller.LeaseHeartbeat()
    hb.start()
    real_time.sleep(0.2)
    hb.stop()
    assert hb.lost.is_set() is lost
