"""Shared fixtures. No AWS access: boto3 clients are replaced with an in-memory FakeECS, Valkey with
fakeredis, and the controller's clock with a fake clock that drives the simulated world."""
import os
import secrets
import sys
from pathlib import Path

import pytest

# Isolate from the user's real AWS config/credentials before anything imports boto3.
# A stray AWS_PROFILE would make boto3 look up a profile that /dev/null doesn't have (ProfileNotFound).
os.environ.pop("AWS_PROFILE", None)
os.environ["AWS_CONFIG_FILE"] = "/dev/null"
os.environ["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
# Dummy credentials so boto3 never picks up real ones; generated per run, never sent anywhere.
os.environ["AWS_ACCESS_KEY_ID"] = "test-" + secrets.token_hex(8)
os.environ["AWS_SECRET_ACCESS_KEY"] = secrets.token_hex(20)
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["CLUSTER"] = "voice-cluster"
os.environ["SERVICE"] = "voice-calling"
os.environ["VALKEY_HOST"] = "valkey.invalid"

# The app under test is this repository's app/ by default, so `pytest tests` works from a fresh clone.
# Optional override: APP_REPO=<path to another checkout> (absolute, or relative to the current directory)
# tests that checkout's app/ instead, for example to run the same tests against an older revision.
TESTS_DIR = Path(__file__).resolve().parent
APP = TESTS_DIR.parent / "app"
if os.environ.get("APP_REPO"):
    APP = Path(os.environ["APP_REPO"]).expanduser().resolve() / "app"
if not (APP / "controller.py").is_file():
    raise RuntimeError(f"no app/controller.py under {APP.parent} (check APP_REPO)")
print(f"[conftest] app under test: {APP}", file=sys.stderr)
sys.path.insert(0, str(APP))
sys.path.insert(0, str(TESTS_DIR))

import fakeredis  # noqa: E402

import controller  # noqa: E402
from fake_ecs import FakeClock, FakeECS, World  # noqa: E402


@pytest.fixture
def world(monkeypatch):
    """4 RUNNING, protected, ACCEPTING tasks A-D; D is least busy. desiredCount=4."""
    ecs = FakeECS(ids=["taskaaaa", "taskbbbb", "taskcccc", "taskdddd"])
    db = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    events = []
    w = World(ecs, db, events)
    for tid, calls in zip(ecs.ids, [5, 3, 4, 1]):
        db.sadd("tasks", tid)
        db.hset(f"task:{tid}", mapping={"arn": ecs.arn(tid), "state": "ACCEPTING", "active_calls": calls,
                                        "pending_calls": 2, "placed_total": 10, "dialer_stopped": 0})
    clock = FakeClock(w)
    monkeypatch.setattr(controller, "ecs", ecs)
    monkeypatch.setattr(controller, "db", db)
    monkeypatch.setattr(controller, "time", clock)

    def log(event, **fields):
        events.append(("log", event, fields))
        w.on_log(event, fields)

    monkeypatch.setattr(controller, "log", log)
    return w
