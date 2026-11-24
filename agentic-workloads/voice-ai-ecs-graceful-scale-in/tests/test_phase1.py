"""New tests for phase-1 behaviour that the original suite doesn't cover (lease heartbeat, SIGTERM).
They use real time for the heartbeat thread, so intervals are patched down to tens of milliseconds.
Against the original repo these fail by design (the features don't exist there)."""
import os
import signal
import time as real_time

import pytest

import controller


def test_p1_heartbeat_extends_lease(world, monkeypatch):
    monkeypatch.setattr(controller, "HEARTBEAT_SECONDS", 0.02)
    hb = controller.LeaseHeartbeat()
    monkeypatch.setattr(controller, "heartbeat", hb)
    controller.acquire_lease()
    assert 0 < world.db.ttl("controller:lease") <= 60
    world.db.expire("controller:lease", 5)
    hb.start()
    try:
        real_time.sleep(0.2)
        assert world.db.ttl("controller:lease") > 5, "heartbeat did not extend the lease"
        assert not hb.lost.is_set()
    finally:
        controller.release_lease()
    assert not world.db.exists("controller:lease")


def test_p2_heartbeat_detects_lost_lease(world, monkeypatch):
    monkeypatch.setattr(controller, "HEARTBEAT_SECONDS", 0.02)
    hb = controller.LeaseHeartbeat()
    monkeypatch.setattr(controller, "heartbeat", hb)
    controller.acquire_lease()
    world.db.set("controller:lease", "other-controller", ex=60)  # ours expired; someone else took it
    hb.start()
    try:
        real_time.sleep(0.2)
        assert hb.lost.is_set()
        with pytest.raises(SystemExit, match="lost the controller lease"):
            controller.check_lease()
    finally:
        controller.release_lease()
    assert world.db.get("controller:lease") == "other-controller", "released a lease it didn't own"


def test_p3_sigterm_releases_lease(world, monkeypatch):
    """ECS stops the controller with SIGTERM: the lease must be released so a restart can resume."""
    hb = controller.LeaseHeartbeat()
    monkeypatch.setattr(controller, "heartbeat", hb)
    seen = {}

    def fake_scale_in():
        seen["lease"] = world.db.get("controller:lease")
        os.kill(os.getpid(), signal.SIGTERM)
        real_time.sleep(2)  # the handler interrupts this
        seen["not_interrupted"] = True
    monkeypatch.setattr(controller, "scale_in", fake_scale_in)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(SystemExit, match="SIGTERM"):
            controller.main(["controller.py", "scale-in"])
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert seen.get("lease") == controller.me and "not_interrupted" not in seen
    assert not world.db.exists("controller:lease"), "lease left behind after SIGTERM"
