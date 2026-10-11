"""Contract checks for truthful synchronous CRM completion."""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "common"))

import tools  # noqa: E402


class FakeState:
    STATUS_COMPLETED = "completed"

    def __init__(self):
        self.finalize_flags: list[bool] = []
        self.statuses: list[tuple[str, str, str | None]] = []

    def finalize_session(self, call_id: str, mark_completed: bool = True) -> dict:
        self.finalize_flags.append(mark_completed)
        return {"callId": call_id, "answerCount": 7}

    def mark_status(self, call_id: str, status: str, note: str | None = None) -> None:
        self.statuses.append((call_id, status, note))


class FakeLambda:
    def __init__(self, body: dict, function_error: str | None = None):
        self.body = body
        self.function_error = function_error
        self.calls: list[dict] = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        response = {
            "StatusCode": 200,
            "Payload": io.BytesIO(json.dumps(self.body).encode()),
        }
        if self.function_error:
            response["FunctionError"] = self.function_error
        return response


def main() -> int:
    failures: list[str] = []
    checks = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" +
              (f"  <- {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(label)

    original_state, original_lambda = tools.st, tools._lambda
    try:
        state = FakeState()
        lambda_client = FakeLambda({"written": True})
        tools.st, tools._lambda = state, lambda_client
        result = tools.submit_to_crm("call-ok")
        check("local response is frozen without premature completion",
              state.finalize_flags == [False], str(state.finalize_flags))
        check("writeback invocation is synchronous",
              lambda_client.calls[0]["InvocationType"] == "RequestResponse",
              str(lambda_client.calls[0]))
        check("confirmed write marks the session completed",
              state.statuses == [("call-ok", "completed",
                                  "intake complete and CRM writeback confirmed")],
              str(state.statuses))
        check("successful result requires written true",
              result.get("written") is True and result.get("finalised") is True,
              str(result))

        for label, body, function_error in (
            ("negative writeback result", {"written": False, "error": "not written"}, None),
            ("Lambda function error", {"errorMessage": "writeback crashed"}, "Unhandled"),
        ):
            state = FakeState()
            lambda_client = FakeLambda(body, function_error)
            tools.st, tools._lambda = state, lambda_client
            raised = False
            try:
                tools.submit_to_crm(f"call-{label}")
            except RuntimeError:
                raised = True
            check(f"{label} raises", raised)
            check(f"{label} remains resumable", not state.statuses,
                  str(state.statuses))
            check(f"{label} still freezes a retryable local record",
                  state.finalize_flags == [False], str(state.finalize_flags))
    finally:
        tools.st, tools._lambda = original_state, original_lambda

    print(f"Executed {checks} CRM submission contract checks.")
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("CRM completion is synchronous, confirmed, and retryable on failure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
