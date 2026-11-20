"""Helpers for deploy-and-invoke.ipynb: request bodies, response checks and the concurrency run.

Nothing here calls AWS by itself. `run_level` takes the function that sends one request, so the same code works
with any client.
"""
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor

TEAMS = {"billing": "Payments, charges, invoices and refunds",
         "shipping": "Delivery, tracking and returns",
         "technical": "App errors, crashes and outages",
         "account": "Login, password and profile settings"}

# Synthetic support tickets. Each template belongs to one team, so every generated ticket carries the team a
# person would route it to; the notebook reports how often the model agrees.
_ISSUES = {
    "billing": [
        "I was charged {amount} twice for order {order} and only one parcel arrived.",
        "My {plan} plan renewed at {amount} even though I cancelled it on {date}.",
        "The invoice for order {order} shows {amount}, but the checkout page said a lower price.",
        "I returned order {order} on {date} and the refund of {amount} has not reached my card.",
        "A payment of {amount} to your company appeared on my statement and I do not recognise it.",
    ],
    "shipping": [
        "Order {order} was due on {date} and the tracking page has not moved for a week.",
        "The courier marked order {order} as delivered on {date}, but nothing came to my door.",
        "Order {order} arrived with the {product} broken and I would like to send it back.",
        "Can I change the delivery address for order {order}? I am moving before {date}.",
        "Half of order {order} arrived on {date}; the {product} is still missing.",
    ],
    "technical": [
        "The app crashes every time I open the {product} page, since the update on {date}.",
        "Your website shows error 500 when I try to view order {order}.",
        "Notifications stopped working on my phone after {date}, so I miss delivery updates.",
        "The {product} configurator freezes at the last step and I cannot finish the order.",
        "Search returns no results for {product}, although it was there on {date}.",
    ],
    "account": [
        "I cannot log in: the password reset email never arrives.",
        "Someone changed the email address on my account on {date} and it was not me.",
        "Please delete my account and the personal data you hold about me.",
        "The two-factor code is sent to an old phone number I no longer have.",
        "I want to change the name on my profile; the form rejects it since {date}.",
    ],
}
_PRODUCTS = ["coffee grinder", "standing desk", "rain jacket", "phone case", "air fryer", "yoga mat",
             "desk lamp", "backpack", "kettle", "monitor arm", "bike helmet", "blender"]
_PLANS = ["monthly", "annual", "family", "premium"]
_OPENERS = ["Hello,", "Hi team,", "Good morning.", "To whom it may concern:", "Hey,", ""]
_URGENT = ["I need this fixed today.", "This is urgent, I am travelling tomorrow.",
           "Please answer as soon as possible.", "I have asked three times already."]
_CALM = ["No rush, whenever you get a chance.", "Thanks in advance.", "Just letting you know.", ""]


def tickets(seed: int = 20261013):
    """Yield (state, labels) forever. Every state is different: each carries its own ticket number."""
    rng = random.Random(seed)   # nosec B311  # noqa: S311  (synthetic text, not security)
    number = 100000 + rng.randrange(800000)
    while True:
        number += 1 + rng.randrange(7)
        team = rng.choice(list(_ISSUES))
        urgent = rng.random() < 0.4
        issue = rng.choice(_ISSUES[team]).format(
            amount=f"${rng.randrange(5, 900)}.{rng.randrange(100):02d}", order=f"#{rng.randrange(10**6, 10**7)}",
            date=f"{rng.randrange(1, 29)} {rng.choice(['March', 'April', 'May', 'June', 'July', 'August'])}",
            product=rng.choice(_PRODUCTS), plan=rng.choice(_PLANS))
        tail = rng.choice(_URGENT if urgent else _CALM)
        state = " ".join(s for s in (f"Ticket {number}.", rng.choice(_OPENERS), issue, tail) if s)
        yield state, {"team": team, "urgent": urgent}


def routing_request(state: str) -> dict:
    """One decision per request: which team handles the ticket (a 4-way `choice`)."""
    return {"state": state, "questions": {
        "team": {"type": "choice", "instructions": "Which team should handle this support ticket?",
                 "criteria": TEAMS}}}


def triage_request(state: str) -> dict:
    """Three decisions about one ticket: a `choice`, a `noul` (yes/no) and a `score` (ordered levels)."""
    return {"state": state, "questions": {
        "team": {"type": "choice", "instructions": "Which team should handle this support ticket?",
                 "criteria": TEAMS},
        "urgent": {"type": "noul", "instructions": "Should this ticket be answered today?"},
        "severity": {"type": "score", "instructions": "How badly is the customer affected?",
                     "criteria": ["cosmetic", "annoying", "blocks the customer"]}}}


def check(request: dict, response: dict, tol: float = 1e-3) -> list[str]:
    """What is wrong with one response to `request`; an empty list means it is well formed.

    Checks the shape TorchCast's decision server returns: one answer per question, of the asked type, with
    probabilities over exactly the asked options that sum to 1.
    """
    problems = []
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(request["questions"]):
        return [f"answers for {sorted(answers or {})}, asked {sorted(request['questions'])}"]
    for name, q in request["questions"].items():
        a = answers[name]
        if a.get("type") != q["type"]:
            problems.append(f"{name}: type {a.get('type')!r}, asked {q['type']!r}")
            continue
        if q["type"] == "noul":
            if not 0.0 <= a.get("noul", -1) <= 1.0:
                problems.append(f"{name}: noul {a.get('noul')!r} is not a probability")
            continue
        options = list(q["criteria"]) if q["type"] == "choice" else [str(i) for i in range(len(q["criteria"]))]
        probs = a.get("probabilities") or {}
        if sorted(probs) != sorted(options):
            problems.append(f"{name}: probabilities over {sorted(probs)}, asked {sorted(options)}")
            continue
        if abs(sum(probs.values()) - 1.0) > tol or min(probs.values()) < 0:
            problems.append(f"{name}: probabilities sum to {sum(probs.values()):.4f}")
        if q["type"] == "choice" and a.get("choice") != max(probs, key=probs.get):
            problems.append(f"{name}: choice {a.get('choice')!r} is not the most probable option")
        if q["type"] == "score" and not 0.0 <= a.get("score", -1) <= len(options) - 1:
            problems.append(f"{name}: score {a.get('score')!r} outside 0..{len(options) - 1}")
    return problems


def percentile(values: list[float], q: float) -> float:
    """The q-th percentile with linear interpolation (numpy's default), without needing numpy."""
    xs = sorted(values)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def run_level(send, bodies: list[dict], concurrency: int) -> dict:
    """Send `bodies` with `concurrency` requests in flight and time each one.

    `send(body)` returns the parsed response or raises. Each of the `concurrency` threads sends its next request
    as soon as the previous one returns (a closed loop), so the endpoint sees exactly `concurrency` requests in
    flight until the list runs out. Latency is measured per request around `send`; throughput is completed
    requests over the wall time of the whole level.
    """
    def timed(body):
        t0 = time.perf_counter()
        try:
            out = send(body)
            return time.perf_counter() - t0, out, None
        except Exception as e:  # noqa: BLE001  counted and reported, never hidden
            return time.perf_counter() - t0, None, e

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(timed, bodies))
    wall = time.perf_counter() - t0
    ok = [(dt, out) for dt, out, err in results if err is None]
    errors = [err for _dt, _out, err in results if err is not None]
    ms = [dt * 1000 for dt, _out in ok]
    return {"concurrency": concurrency, "requests": len(bodies), "ok": len(ok), "errors": len(errors),
            "decisions_per_s": len(ok) / wall, "wall_s": wall,
            **{f"p{q}_ms": percentile(ms, q) for q in (10, 50, 90, 99)},
            "responses": [out for _dt, out, _e in results], "error_samples": [repr(e)[:300] for e in errors[:5]]}
