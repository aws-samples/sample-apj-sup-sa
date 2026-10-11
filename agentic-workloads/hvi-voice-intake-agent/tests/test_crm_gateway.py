"""The CRM-writeback-over-Gateway path (Option A, step 2): selector default, flag
routing, and the written=True safety contract - none of which touch AWS.

Proves: (1) crm_client() is direct (None) by default and a GatewayCRM only when the
flag/force says so; (2) submit_to_crm routes the writeback through the gateway when
CRM_VIA_GATEWAY is on, still runs finalize_session + mark_status (the state
machine's own DynamoDB work, here stubbed), and returns recordId/recordUrl; (3) a
gateway reply without written=True is raised, never returned as a completed record.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import hvi_crm_gateway as gw  # noqa: E402
import tools  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def test_selector() -> None:
    print("[1] selector: direct by default, gateway only when asked")
    os.environ.pop("CRM_VIA_GATEWAY", None)
    check("default is direct (None)", gw.crm_client() is None)
    check("force='gateway' returns a GatewayCRM",
          isinstance(gw.crm_client(force="gateway"), gw.GatewayCRM))
    os.environ["CRM_VIA_GATEWAY"] = "1"
    try:
        check("CRM_VIA_GATEWAY=1 selects the gateway",
              isinstance(gw.crm_client(), gw.GatewayCRM))
    finally:
        os.environ.pop("CRM_VIA_GATEWAY", None)


class _StubCRM:
    def __init__(self, body):
        self.body = body
        self.called_with = None

    def submit(self, call_id):
        self.called_with = call_id
        if not self.body.get("written"):
            raise gw.CRMGatewayError(200, self.body.get("error") or "not written",
                                     "submit")
        return self.body


def _stub_state(monkeypatch_finalise=True):
    """Stub the DynamoDB-touching state calls so the test needs no AWS."""
    calls = {"finalised": False, "completed": False}
    orig_fin = tools.st.finalize_session
    orig_mark = tools.st.mark_status

    def fake_finalize(call_id, mark_completed=False):
        calls["finalised"] = True
        return {"answerCount": 27}

    def fake_mark(call_id, status, note=""):
        calls["completed"] = (status == tools.st.STATUS_COMPLETED)

    tools.st.finalize_session = fake_finalize
    tools.st.mark_status = fake_mark
    return calls, (orig_fin, orig_mark)


def test_submit_routes_through_gateway() -> None:
    print("\n[2] submit_to_crm routes writeback through the gateway when selected")
    stub = _StubCRM({"written": True, "recordId": "003XX", "recordUrl": "https://x"})
    calls, (orig_fin, orig_mark) = _stub_state()
    sys.modules["hvi_crm_gateway"].crm_client = lambda force=None: stub
    try:
        out = tools.submit_to_crm("call-crm")
        check("gateway.submit called with the call id", stub.called_with == "call-crm")
        check("finalize_session still ran (state machine's own work)",
              calls["finalised"])
        check("mark_status COMPLETED still ran", calls["completed"])
        check("returns written + recordId/recordUrl from the gateway body",
              out.get("written") is True and out.get("recordId") == "003XX"
              and out.get("recordUrl") == "https://x", str(out))
    finally:
        tools.st.finalize_session = orig_fin
        tools.st.mark_status = orig_mark
        # restore selector import
        import importlib
        importlib.reload(sys.modules["hvi_crm_gateway"])


def test_not_written_is_raised() -> None:
    print("\n[3] a gateway reply without written=True is raised, not completed")
    stub = _StubCRM({"written": False, "error": "Salesforce 500"})
    calls, (orig_fin, orig_mark) = _stub_state()
    sys.modules["hvi_crm_gateway"].crm_client = lambda force=None: stub
    raised = False
    try:
        tools.submit_to_crm("call-crm-fail")
    except Exception:
        raised = True
    finally:
        tools.st.finalize_session = orig_fin
        tools.st.mark_status = orig_mark
        import importlib
        importlib.reload(sys.modules["hvi_crm_gateway"])
    check("an unconfirmed writeback raises rather than marking completed", raised)
    check("session was NOT marked completed on failure", not calls["completed"])


def main() -> int:
    test_selector()
    test_submit_routes_through_gateway()
    test_not_written_is_raised()
    print()
    if failures:
        print(f"{len(failures)} of {checks} crm-gateway checks FAILED")
        return 1
    print(f"All {checks} crm-gateway checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
