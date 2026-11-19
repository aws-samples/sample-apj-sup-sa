"""In-memory fake of the Amazon ECS API calls the controller uses, plus a tiny simulated world:
calling tasks that react to DRAINING, and an ECS scheduler that only stops UNPROTECTED surplus tasks."""


class Crash(BaseException):
    """Simulates the controller process dying (not caught by `except Exception`)."""


class InvalidParameterException(Exception):
    pass


class FakeECS:
    ACCT = "111122223333"
    REGION = "us-east-1"

    def __init__(self, ids, cluster="voice-cluster"):
        self.cluster = cluster
        self.ids = list(ids)
        self.tasks = {t: {"status": "RUNNING", "protected": True, "reason": None} for t in ids}
        self.desired = len(ids)
        self.pending = 0
        self.calls = []            # (op, kwargs)
        self.before = {}           # op -> callable(kwargs)  (may raise Crash / mutate state)
        self.after = {}            # op -> callable(kwargs, resp)
        self.update_failures = {}  # task id -> reason   (UpdateTaskProtection failures[])
        self.get_failures = set()  # task ids reported in GetTaskProtection failures[]
        self.page_size = 100
        self.stop_delay_ticks = 0  # (N1) ticks a stopped task keeps draining (lastStatus RUNNING)
        self.events = None

    def arn(self, tid):
        return f"arn:aws:ecs:{self.REGION}:{self.ACCT}:task/{self.cluster}/{tid}"

    @staticmethod
    def tid(x):
        return x.rsplit("/", 1)[-1]

    def _call(self, op, kwargs):
        self.calls.append((op, dict(kwargs)))
        if self.events is not None:
            self.events.append(("ecs", op, dict(kwargs)))
        if op in self.before:
            self.before[op](kwargs)

    def _after(self, op, kwargs, resp):
        if op in self.after:
            self.after[op](kwargs, resp)
        return resp

    def running(self):
        """What ListTasks(desiredStatus=RUNNING) returns: lastStatus RUNNING and desiredStatus RUNNING.
        (Updated for N1.) A task ECS is stopping has desiredStatus STOPPED but can stay lastStatus
        RUNNING while it drains; see draining() and stop_delay_ticks."""
        return [t for t, v in self.tasks.items()
                if v["status"] == "RUNNING" and v.get("desired", "RUNNING") == "RUNNING"]

    def draining(self):
        """desiredStatus STOPPED, lastStatus still RUNNING (ECS is stopping it)."""
        return [t for t, v in self.tasks.items()
                if v["status"] == "RUNNING" and v.get("desired", "RUNNING") == "STOPPED"]

    # --- API surface -------------------------------------------------------
    def list_tasks(self, cluster, serviceName, desiredStatus, nextToken=None, **_):
        self._call("list_tasks", dict(cluster=cluster, serviceName=serviceName, nextToken=nextToken))
        if desiredStatus == "RUNNING":
            run = self.running()
        else:  # desiredStatus STOPPED: being stopped or already stopped
            run = [t for t, v in self.tasks.items() if v.get("desired", "RUNNING") == "STOPPED"
                   or v["status"] == "STOPPED"]
        start = int(nextToken or 0)
        page = run[start:start + self.page_size]
        resp = {"taskArns": [self.arn(t) for t in page]}
        if start + self.page_size < len(run):
            resp["nextToken"] = str(start + self.page_size)
        return self._after("list_tasks", {}, resp)

    def get_paginator(self, op):
        """(Added for M3/L1.) Minimal boto3-style paginator over list_tasks' nextToken."""
        assert op == "list_tasks", op
        fake = self

        class Paginator:
            def paginate(self, **kw):
                token = None
                while True:
                    page = fake.list_tasks(**kw, nextToken=token)
                    yield page
                    token = page.get("nextToken")
                    if not token:
                        return
        return Paginator()

    def describe_services(self, cluster, services):
        self._call("describe_services", dict(cluster=cluster, services=services))
        return self._after("describe_services", {}, {"services": [{
            "serviceName": services[0], "desiredCount": self.desired,
            "runningCount": len(self.running()), "pendingCount": self.pending}]})

    def get_task_protection(self, cluster, tasks):
        kw = dict(cluster=cluster, tasks=list(tasks))
        self._call("get_task_protection", kw)
        if len(tasks) > 100:
            raise InvalidParameterException("tasks: up to 100")
        prot, fail = [], []
        for t in tasks:
            tid = self.tid(t)
            if tid in self.get_failures or tid not in self.tasks or self.tasks[tid]["status"] == "STOPPED":
                fail.append({"arn": self.arn(tid), "reason": "MISSING"})
            else:
                prot.append({"taskArn": self.arn(tid), "protectionEnabled": self.tasks[tid]["protected"]})
        return self._after("get_task_protection", kw, {"protectedTasks": prot, "failures": fail})

    def update_task_protection(self, cluster, tasks, protectionEnabled, expiresInMinutes=None):
        kw = dict(cluster=cluster, tasks=list(tasks), protectionEnabled=protectionEnabled)
        self._call("update_task_protection", kw)
        if len(tasks) > 10:
            raise InvalidParameterException("tasks: up to 10")
        prot, fail = [], []
        for t in tasks:
            tid = self.tid(t)
            if tid in self.update_failures:
                fail.append({"arn": self.arn(tid), "reason": self.update_failures[tid]})
            else:
                self.tasks[tid]["protected"] = protectionEnabled
                prot.append({"taskArn": self.arn(tid), "protectionEnabled": protectionEnabled})
        # HTTP 200 even when failures[] is non-empty, as the real API does.
        return self._after("update_task_protection", kw, {"protectedTasks": prot, "failures": fail})

    def update_service(self, cluster, service, desiredCount):
        kw = dict(cluster=cluster, service=service, desiredCount=desiredCount)
        self._call("update_service", kw)
        self.desired = desiredCount
        return self._after("update_service", kw, {"service": {"desiredCount": desiredCount}})

    def describe_tasks(self, cluster, tasks):
        kw = dict(cluster=cluster, tasks=list(tasks))
        self._call("describe_tasks", kw)
        out, fail = [], []
        for t in tasks:
            tid = self.tid(t)
            if tid in self.tasks:
                v = self.tasks[tid]
                out.append({"taskArn": self.arn(tid), "lastStatus": v["status"],
                            "desiredStatus": v.get("desired", "RUNNING"), "stoppedReason": v["reason"]})
            else:  # (Updated for N1.) Aged out or unknown: reported under failures, as the real API does.
                fail.append({"arn": self.arn(tid), "reason": "MISSING"})
        return self._after("describe_tasks", kw, {"tasks": out, "failures": fail})

    # --- scheduler ---------------------------------------------------------
    def schedule(self):
        """ECS: if running > desired, stop an UNPROTECTED task. Protected tasks are never chosen.
        A stopped task gets desiredStatus STOPPED at once; its lastStatus becomes STOPPED after
        stop_delay_ticks further ticks (0 = at once, the original behaviour)."""
        for t in self.draining():
            v = self.tasks[t]
            v["stop_in"] = v.get("stop_in", 0) - 1
            if v["stop_in"] <= 0:
                v["status"] = "STOPPED"
        while len(self.running()) > self.desired:
            victims = [t for t in self.running() if not self.tasks[t]["protected"]]
            if not victims:
                return "blocked"
            v = self.tasks[victims[0]]
            v["desired"] = "STOPPED"
            v["reason"] = "Scaling activity initiated by deployment"
            v["stop_in"] = self.stop_delay_ticks
            if self.stop_delay_ticks <= 0:
                v["status"] = "STOPPED"
        return "ok"


class World:
    """Glue between FakeECS, the fake Valkey and the controller's log/clock, with crash hooks."""

    def __init__(self, ecs, db, events):
        self.ecs, self.db, self.events = ecs, db, events
        ecs.events = events
        self.crash_on_log = None   # (event, predicate) -> raise Crash after that log line
        self.auto_drain = True     # simulated tasks stop dialing when told to drain
        self.tick_hooks = []

    def on_log(self, event, fields):
        if self.crash_on_log and self.crash_on_log[0] == event and self.crash_on_log[1](fields):
            self.crash_on_log = None
            raise Crash(f"crash after log {event} {fields}")

    def tick(self):
        if self.auto_drain:
            for tid in self.db.smembers("tasks"):
                st = self.db.hget(f"task:{tid}", "state")
                if st in ("DRAINING", "UNPROTECTED", "COUNT_LOWERED"):
                    # renewal_stopped added for H1: the fixed controller requires it before releasing.
                    self.db.hset(f"task:{tid}", mapping={"dialer_stopped": 1, "pending_calls": 0,
                                                         "renewal_stopped": 1})
        for h in self.tick_hooks:
            h()
        self.ecs.schedule()

    def log_events(self):
        return [e[1] for e in self.events if e[0] == "log"]


class FakeClock:
    """Replaces the controller's `time` module: sleep() advances the clock and ticks the world."""

    def __init__(self, world):
        self.now = 1_800_000_000.0
        self.world = world

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.world.tick()
