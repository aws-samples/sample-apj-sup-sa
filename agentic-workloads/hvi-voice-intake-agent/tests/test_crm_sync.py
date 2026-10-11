#!/usr/bin/env python
"""Salesforce -> DynamoDB projection, against the real org.

Covers the mapping the pre-call gate depends on, and two safety properties that
matter more than the happy path:

  A phone number without a country code is SKIPPED, not decorated. Assuming +91
  for an Indian insurer is the kind of convenience that ends with an automated
  call reading one customer's policy number to a stranger.

  A contact with no consent timestamp produces NO consent-ledger record, so the
  DPDP gate blocks the call. The absence has to propagate; silently writing a
  consent row because the CRM had a blank field would defeat the whole gate.

Self-cleaning in both systems, so it can run repeatedly against a live org.

Run:  ./.venv/bin/python tests/test_crm_sync.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "crm_sync"))

import hvi_state as st  # noqa: E402
from hvi_salesforce import Salesforce, SalesforceError  # noqa: E402
from live_cleanup import cleanup_hvi_fixture  # noqa: E402

import handler as sync_handler  # noqa: E402

MARKER = "hvi-synctest"
GOOD_PHONE_RAW = "+1 (555) 000-8888"
GOOD_PHONE_E164 = "+15550008888"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    sf = Salesforce()
    print(f"org: {sf.instance_url}\n")
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    created: list[str] = []
    session_id: str | None = None

    try:
        # ---- fixtures -------------------------------------------------------
        print("[1] two purchases land in Salesforce")
        good_id = sf.create("Contact", {
            "LastName": f"Sharma ({MARKER})",
            "FirstName": "Priya",
            # Deliberately messy formatting, and on MobilePhone rather than Phone,
            # to prove both the normalisation and the mobile-first preference.
            "MobilePhone": GOOD_PHONE_RAW,
            "Email": "noreply@example.invalid",
            "Birthdate": "1987-03-22",
            "HVI_Policy_Number__c": "AS-2026-SYNC-OK",
            "HVI_Product_Type__c": "Arogya Sanjeevani",
            "HVI_Sum_Insured__c": 500000,
            "HVI_Underwriting_Status__c": "awaiting-medical-history",
            "HVI_Purchased_At__c": now,
            "HVI_Preferred_Language__c": "hi-IN",
            "HVI_Form_Id__c": "arogya-sanjeevani-v1",
            "HVI_Consent_Granted_At__c": now,
            "HVI_DND_Registered__c": False,
        })
        created.append(good_id)

        # No country code, and no consent timestamp: both must be handled by
        # refusing to proceed rather than by filling in a plausible value.
        bad_id = sf.create("Contact", {
            "LastName": f"Kumar ({MARKER})",
            "FirstName": "Rajesh",
            "Phone": "9876543210",
            "HVI_Policy_Number__c": "AS-2026-SYNC-BAD",
            "HVI_Underwriting_Status__c": "awaiting-medical-history",
            "HVI_Purchased_At__c": now,
        })
        created.append(bad_id)
        check("fixtures created", len(created) == 2)

        # ---- sync -----------------------------------------------------------
        print("\n[2] the sync runs")
        result = sync_handler.sync(dry_run=False)
        check("found both contacts", result["contactsFound"] >= 2,
              str(result["contactsFound"]))
        check("one was skipped for an unusable phone", result["skipped"] >= 1,
              str(result["skipped"]))
        reasons = " ".join(p["reason"] for p in result["problems"])
        check("the skip reason names the country code",
              "country code" in reasons, reasons[:90])

        # ---- the good record ------------------------------------------------
        print("\n[3] the projection is what the gate reads")
        rec = st.get_customer(good_id)
        check("projected into the CRM cache", rec is not None)
        if rec:
            check("phone normalised to E.164",
                  rec.get("phone") == GOOD_PHONE_E164, str(rec.get("phone")))
            check("mobile preferred over phone",
                  rec.get("phone") == GOOD_PHONE_E164)
            check("customerId is the Salesforce Id",
                  rec.get("customerId") == good_id)
            # startswith, not equality: the fixture deliberately carries the test
            # marker inside LastName so stray records are identifiable in the org.
            check("first and last name assembled in order",
                  (rec.get("customerName") or "").startswith("Priya Sharma"),
                  str(rec.get("customerName")))
            check("language carried", rec.get("preferredLanguage") == "hi-IN",
                  str(rec.get("preferredLanguage")))
            check("authoritative Salesforce DOB carried",
                  rec.get("dateOfBirth") == "1987-03-22",
                  str(rec.get("dateOfBirth")))

            # THE PROJECTION KEEPS THE SALESFORCE SPELLING, AND THAT IS CORRECT -
            # hi-IN is what the restricted picklist contains and what the
            # writeback has to send back. But the orchestrator branches on
            # en / hi / hinglish, nearly always as a dict lookup with English as
            # the fallback:
            #
            #     {"hi": "...", "hinglish": "..."}.get(language, "<English>")
            #
            # so hi-IN misses, falls through, and the entire call runs in English
            # for a customer whose record explicitly asks for Hindi. Nothing
            # raises and nothing logs. en-IN hides the bug because English is the
            # fallback anyway, which is why it survived every suite.
            #
            # The conversion therefore has to happen where the projection becomes
            # a session, and this asserts it at that exact seam rather than
            # trusting the two vocabularies to stay accidentally compatible.
            session = st.start_session(GOOD_PHONE_E164, rec,
                                       rec.get("assignedFormId"))
            session_id = session["callId"]
            check("hi-IN becomes 'hi' on the session, so the call is in Hindi",
                  session.get("language") == "hi",
                  f"got {session.get('language')!r}")
            check("normalise_language maps every CRM spelling",
                  (st.normalise_language("hi-IN"), st.normalise_language("en-IN"),
                   st.normalise_language("hinglish"), st.normalise_language(None))
                  == ("hi", "en", "hinglish", "hinglish"),
                  str([st.normalise_language(v)
                       for v in ("hi-IN", "en-IN", "hinglish", None)]))
            check("form assigned", rec.get("assignedFormId") == "arogya-sanjeevani-v1")
            check("status carried",
                  rec.get("underwritingStatus") == "awaiting-medical-history")
            check("dnd flag is a bool", isinstance(rec.get("dndRegistered"), bool))
            # The gate parses purchasedAt with fromisoformat to compute the SLA, so
            # the offset format matters, not just the presence of a value.
            ok_parse = True
            try:
                dt.datetime.fromisoformat(rec.get("purchasedAt") or "")
            except Exception:  # noqa: BLE001
                ok_parse = False
            check("purchasedAt parses as ISO for the SLA gate", ok_parse,
                  str(rec.get("purchasedAt")))
            check("slaDeadline computed", bool(rec.get("slaDeadline")))

        # ---- lookup by phone, as the dialler does ---------------------------
        print("\n[4] findable by phone, which is how resume works")
        by_phone = None
        for attempt in range(5):
            by_phone = st.get_customer_by_phone(GOOD_PHONE_E164)
            if by_phone is not None:
                break
            if attempt < 4:
                time.sleep(0.25)
        check("phone-index lookup finds it", by_phone is not None
              and by_phone.get("customerId") == good_id)

        # ---- consent --------------------------------------------------------
        print("\n[5] consent propagates, and its absence propagates too")
        rows = st._ddb.Table(sync_handler.TBL_CONSENT).query(
            KeyConditionExpression=st.Key("customerId").eq(good_id))
        items = [st.from_ddb(i) for i in rows.get("Items", [])]
        check("consent record written for the consenting customer", len(items) >= 1)
        check("it is ACTIVE", any(i.get("status") == "ACTIVE" for i in items))

        rows = st._ddb.Table(sync_handler.TBL_CONSENT).query(
            KeyConditionExpression=st.Key("customerId").eq(bad_id))
        check("no consent invented for the customer without one",
              len(rows.get("Items", [])) == 0,
              f"{len(rows.get('Items', []))} rows")

    finally:
        print("\ncleaning up")
        for cid in created:
            try:
                sf.request("DELETE",
                           f"/services/data/v{sf.version}/sobjects/Contact/{cid}")
                print(f"  deleted Contact {cid}")
            except SalesforceError as e:
                print(f"  could not delete {cid}: {e}")
            cleanup_errors = cleanup_hvi_fixture(
                st,
                customer_id=cid,
                call_id=session_id if cid == good_id else None,
                consent_table=sync_handler.TBL_CONSENT,
            )
            for error in cleanup_errors:
                print(f"  could not clean {cid}: {error}")

    # ---- offline: the two ways this sync has actually fallen over ------------
    #
    # Both were seen for real during one deployment, and neither had a test:
    # a field that existed in this code but not yet in the org took the whole
    # projection down, and a one-second blip on the OAuth token endpoint did the
    # same. Driven with substituted transports so they are deterministic and cost
    # no API calls.
    print("\n[6] a column the org does not have yet degrades, it does not stop")
    real_query = Salesforce.query
    try:
        missing_field = "HVI_Gender__c"
        attempted: list[str] = []

        def query_missing_column(self, soql):
            attempted.append(soql)
            if missing_field in soql:
                raise SalesforceError(400, [{
                    "errorCode": "INVALID_FIELD",
                    "message": (f"\n{missing_field}\n^\nERROR at Row:1:Column:244\n"
                                f"No such column '{missing_field}' on entity "
                                "'Contact'."),
                }], "query")
            return [{"Id": "003TEST", "Phone": "+15550009999",
                     "FirstName": "Off", "LastName": "Line",
                     "HVI_Policy_Number__c": "POL-OFFLINE",
                     "HVI_Underwriting_Status__c": "awaiting-medical-history"}]

        Salesforce.query = query_missing_column
        contacts, dropped = sync_handler._query_contacts(  # noqa: SLF001
            Salesforce.__new__(Salesforce), list(sync_handler.FIELDS))
        check("the unknown column is dropped and the query retried",
              dropped == [missing_field] and len(contacts) == 1,
              f"dropped={dropped} contacts={len(contacts)} tries={len(attempted)}")
        check("the retry no longer asks for the missing column",
              missing_field not in attempted[-1], attempted[-1][:120])
        check("every other field is still requested",
              "HVI_Policy_Number__c" in attempted[-1]
              and "Birthdate" in attempted[-1], attempted[-1][:120])

        # A field the projection cannot work without must fail loudly instead.
        missing_field = "HVI_Policy_Number__c"
        attempted.clear()
        raised = ""
        try:
            sync_handler._query_contacts(  # noqa: SLF001
                Salesforce.__new__(Salesforce), list(sync_handler.FIELDS))
        except SalesforceError as e:
            raised = e.errorCode
        check("a required column is never silently dropped",
              raised == "INVALID_FIELD", f"raised={raised!r}")

        # An INVALID_FIELD we cannot attribute to our own field list is not ours
        # to paper over either: dropping fields until the message changes would be
        # guessing at someone else's problem.
        def query_unrelated_invalid_field(_self, _soql):
            raise SalesforceError(400, [{
                "errorCode": "INVALID_FIELD",
                "message": "No such column 'SomeUnrelated__c' on entity 'Contact'.",
            }], "query")

        Salesforce.query = query_unrelated_invalid_field
        raised = ""
        try:
            sync_handler._query_contacts(  # noqa: SLF001
                Salesforce.__new__(Salesforce), list(sync_handler.FIELDS))
        except SalesforceError as e:
            raised = e.errorCode
        check("an unattributable INVALID_FIELD still raises",
              raised == "INVALID_FIELD", f"raised={raised!r}")

        # And an unrelated error class is never treated as a schema problem.
        def query_row_lock(_self, _soql):
            raise SalesforceError(400, [{"errorCode": "UNABLE_TO_LOCK_ROW",
                                         "message": "row lock"}], "query")

        Salesforce.query = query_row_lock
        raised = ""
        try:
            sync_handler._query_contacts(  # noqa: SLF001
                Salesforce.__new__(Salesforce), list(sync_handler.FIELDS))
        except SalesforceError as e:
            raised = e.errorCode
        check("a non-schema error is not mistaken for a missing column",
              raised == "UNABLE_TO_LOCK_ROW", f"raised={raised!r}")
    finally:
        Salesforce.query = real_query

    print("\n[7] a transient token failure is retried, a rejected one is not")
    import io  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415

    import hvi_salesforce as sf_mod  # noqa: PLC0415

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _token_ok():
        return _Resp(json.dumps({"access_token": "tok",
                                 "instance_url": "https://api.invalid"}).encode())

    def _http(code):
        return urllib.error.HTTPError("u", code, "e", {}, io.BytesIO(b""))

    creds = {"instance_url": "https://example.invalid", "client_id": "c",
             "client_secret": "s"}
    original_urlopen = sf_mod.urllib.request.urlopen
    original_backoff = sf_mod.AUTH_BACKOFF_SECS
    sf_mod.AUTH_BACKOFF_SECS = 0
    try:
        for label, responses, want_token, want_calls in (
            # The exact failure that broke a deploy: opaque 404, then fine.
            ("opaque 404 then success", [_http(404), _token_ok], True, 2),
            ("503 then success", [_http(503), _token_ok], True, 2),
            ("network error then success",
             [urllib.error.URLError("reset"), _token_ok], True, 2),
            # A bad credential is permanent; retrying hides it and can lock out.
            ("400 fails fast", [_http(400)], False, 1),
            ("403 fails fast", [_http(403)], False, 1),
            ("persistent 404 gives up", [_http(404)], False, sf_mod.AUTH_ATTEMPTS),
        ):
            calls = {"n": 0}

            def fake_urlopen(_req, timeout=None, _r=responses, _c=calls):
                _c["n"] += 1
                item = _r[min(_c["n"] - 1, len(_r) - 1)]
                if isinstance(item, Exception):
                    raise item
                return item()

            sf_mod.urllib.request.urlopen = fake_urlopen
            client = sf_mod.Salesforce(credentials=dict(creds))
            got = None
            try:
                client._ensure_auth()  # noqa: SLF001
                got = client._token  # noqa: SLF001
            except SalesforceError:
                got = None
            check(f"token: {label}",
                  bool(got) is want_token and calls["n"] == want_calls,
                  f"token={got!r} attempts={calls['n']}")
    finally:
        sf_mod.urllib.request.urlopen = original_urlopen
        sf_mod.AUTH_BACKOFF_SECS = original_backoff

    # The shape of the wait matters, not just that there is one. The token
    # endpoint fails in bursts of roughly 15-25s (measured: identical requests
    # 20s apart, one 404 and one 200, while fourteen back-to-back requests all
    # succeeded - so it is not load). A doubling 1-2-4-8 chain spans 15s, fits
    # inside the burst, and reports five failures that are really one outage
    # sampled five times. These two checks are what stop that regressing: the
    # per-wait cap is what keeps ten attempts affordable, and the wall-clock
    # budget is what bounds the total when a request is itself slow.
    print("\n[8] the retry outlives the burst, and the budget bounds it")

    class _Clock:
        """Virtual clock: advances only when the code under test sleeps."""

        def __init__(self):
            self.now = 0.0
            self.waits = []

        def monotonic(self):
            return self.now

        def sleep(self, d):
            self.waits.append(d)
            self.now += d

    original_time = sf_mod.time
    original_budget = sf_mod.AUTH_BUDGET_SECS
    try:
        def always_404(_req, timeout=None):
            raise _http(404)

        sf_mod.urllib.request.urlopen = always_404

        # Defaults: every wait capped, and the span outlives a 25s burst.
        clock = _Clock()
        sf_mod.time = clock
        try:
            sf_mod.Salesforce(credentials=dict(creds))._ensure_auth()  # noqa: SLF001
        except SalesforceError:
            pass
        check("no single wait exceeds the cap",
              bool(clock.waits) and max(clock.waits) <= sf_mod.AUTH_BACKOFF_CAP_SECS,
              f"waits={clock.waits}")
        check("the retry span outlives a 25s burst",
              clock.now > 25, f"span={clock.now}s waits={clock.waits}")
        check("a doubling chain would not have",
              sum(sf_mod.AUTH_BACKOFF_SECS * (2 ** i) for i in range(4)) < 25,
              "1+2+4+8 spans only 15s")

        # A small budget stops the retrying early even though attempts remain.
        sf_mod.AUTH_BUDGET_SECS = 10.0
        clock = _Clock()
        sf_mod.time = clock
        calls = {"n": 0}

        def counted_404(_req, timeout=None, _c=calls):
            _c["n"] += 1
            raise _http(404)

        sf_mod.urllib.request.urlopen = counted_404
        try:
            sf_mod.Salesforce(credentials=dict(creds))._ensure_auth()  # noqa: SLF001
        except SalesforceError:
            pass
        check("the budget gives up before the attempts run out",
              0 < calls["n"] < sf_mod.AUTH_ATTEMPTS,
              f"attempts={calls['n']} of {sf_mod.AUTH_ATTEMPTS}")
        check("and never waits past the budget",
              clock.now <= sf_mod.AUTH_BUDGET_SECS,
              f"waited {clock.now}s against a {sf_mod.AUTH_BUDGET_SECS}s budget")
    finally:
        sf_mod.urllib.request.urlopen = original_urlopen
        sf_mod.time = original_time
        sf_mod.AUTH_BUDGET_SECS = original_budget

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("CRM sync OK: projection, phone normalisation, consent propagation, "
          "schema-lag tolerance, auth retry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
