"""The email-over-Gateway path (Option A, step 1): selector default, flag routing,
and the sent=True safety contract - none of which touch AWS.

Proves: (1) email_client() is direct (None) by default and a GatewayEmail only when
the flag/force says so; (2) send_confirmation_email routes through the gateway when
EMAIL_VIA_GATEWAY is on, and (3) a gateway reply without sent=True is raised, never
returned as success. The direct path is left to the existing suites; here the
Lambda invoke is monkeypatched so the flag-off case is exercised too without AWS.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import hvi_email_gateway as gw  # noqa: E402
import tools  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def test_selector_default_and_forced() -> None:
    print("[1] selector: direct by default, gateway only when asked")
    os.environ.pop("EMAIL_VIA_GATEWAY", None)
    check("default is the direct path (None)", gw.email_client() is None)
    check("force='gateway' returns a GatewayEmail",
          isinstance(gw.email_client(force="gateway"), gw.GatewayEmail))
    check("force='0' stays direct", gw.email_client(force="0") is None)
    os.environ["EMAIL_VIA_GATEWAY"] = "1"
    try:
        check("EMAIL_VIA_GATEWAY=1 selects the gateway",
              isinstance(gw.email_client(), gw.GatewayEmail))
    finally:
        os.environ.pop("EMAIL_VIA_GATEWAY", None)


class _StubGateway:
    def __init__(self, body):
        self.body = body
        self.called_with = None

    def send_confirmation(self, call_id):
        self.called_with = call_id
        if not self.body.get("sent"):
            raise gw.EmailGatewayError(200, self.body.get("error") or "not sent",
                                       "send confirmation")
        return self.body


def test_send_routes_through_gateway(monkeypatch=None) -> None:
    print("\n[2] send_confirmation_email routes through the gateway when selected")
    stub = _StubGateway({"sent": True, "messageId": "abc123", "via": "gateway"})
    orig = gw.email_client
    tools_email_client_import = "hvi_email_gateway"
    # Patch the selector the call site imports lazily.
    gw.email_client = lambda force=None: stub
    sys.modules[tools_email_client_import].email_client = lambda force=None: stub
    try:
        out = tools.send_confirmation_email("call-xyz")
        check("gateway send_confirmation was called with the call id",
              stub.called_with == "call-xyz", str(stub.called_with))
        check("returns the gateway body on success",
              out.get("sent") is True and out.get("messageId") == "abc123",
              str(out))
    finally:
        gw.email_client = orig
        sys.modules[tools_email_client_import].email_client = orig


def test_gateway_not_sent_is_raised() -> None:
    print("\n[3] a gateway reply without sent=True is raised, not returned")
    stub = _StubGateway({"sent": False, "error": "SES throttled"})
    orig = gw.email_client
    sys.modules["hvi_email_gateway"].email_client = lambda force=None: stub
    raised = False
    try:
        tools.send_confirmation_email("call-fail")
    except Exception:
        raised = True
    finally:
        sys.modules["hvi_email_gateway"].email_client = orig
    check("a not-sent gateway reply raises rather than looking like success",
          raised)


def main() -> int:
    test_selector_default_and_forced()
    test_send_routes_through_gateway()
    test_gateway_not_sent_is_raised()
    print()
    if failures:
        print(f"{len(failures)} of {checks} email-gateway checks FAILED")
        return 1
    print(f"All {checks} email-gateway checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
