"""One phone number per suite, so resumable sessions cannot collide.

THE FAILURE THIS EXISTS TO STOP
-------------------------------
Twelve suites shared +12025550143 (Priya Sharma, on the seeded CRM). Each one
creates sessions on it, and several deliberately leave one unfinished. Meanwhile
find_resumable_session queries the phone index newest-first with Limit=10 and
returns the first resumable row it sees.

So a suite that drops its own call and asserts "the resumable session is MINE"
can be handed a stranger's - one created seconds later by the next suite along.
Measured: after a run of the affected suites, four leftover `in_progress`
sessions were sitting on that number, and test_orchestrator and test_resume_flow
failed five checks between them. Both passed when run alone, and both passed
again once the leftovers were retired, which is the signature of a shared-state
flake rather than a defect.

Order-dependent, so it fails on someone else's change and it fails in CI more
often than locally. Worth removing rather than remembering.

HOW A NEW NUMBER GETS A CUSTOMER
--------------------------------
The suites need get_customer_by_phone to resolve, so a bare unused number is no
good. reserve() clones the seeded template customer under a suite-specific
customerId and phone, writing one row to the CRM cache table.

Cloning rather than seeding twelve new Salesforce Contacts, for two reasons. The
CRM cache is a PROJECTION of Salesforce, so a row that exists only here is exactly
what an unsynced customer looks like - which several suites already rely on. And
lambdas/crm_sync only ever put_item, never deletes, so a cloned row survives a
real sync instead of vanishing mid-run.

The clone keeps the template's NAME and policy number. Suites assert on
"Priya Sharma" appearing in a prompt, and those assertions are about prompt
rendering, not about which number was dialled.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import hvi_state as st  # noqa: E402

# The seeded customer every clone is made from. Still used directly by suites that
# only READ a customer and never create a session, and by the demo scripts.
TEMPLATE_PHONE = "+12025550143"

# +1555000xxxx is the standard fictional-US block, already used for the numbers
# that are isolated today (+15550001111, +15550007777, +15550008888,
# +15550009999). Starting at 0100 keeps a clear gap from those.
_BASE = 100

# Suite -> index. Explicit rather than derived from a hash, so the number a suite
# uses is greppable and stable when suites are added or renamed. Append only; do
# not renumber, or a rename silently inherits another suite's leftovers.
_SUITES = {
    "test_orchestrator": 0,
    "test_resume_flow": 1,
    "test_condition_screen_followup": 2,
    "test_record_sanity": 3,
    "test_summary_email_all_conditions": 4,
    "test_agent_local": 5,
    "test_underwriting_stress": 6,
    "test_language_selection": 7,
    "test_positive_disclosures": 8,
    "test_agentcore_deployed": 9,
    "test_adversarial_conversations": 10,
    "test_med_verification": 11,
    # 12 and 13 were speculatively allocated to test_underwriting_stress2 and
    # fuzz_condition_paths, then withdrawn: stress2 imports test_underwriting_stress
    # and so deliberately shares its number, and fuzz_condition_paths drives the
    # dialogue model with no phone at all. The gap is left rather than closed,
    # because renumbering is what makes a rename inherit someone else's leftovers.
    "test_transcript_fidelity": 14,
    "diag_lung_screen_replay": 15,
    "test_screen_yes_never_lost": 16,
    # Drives the published demo script end to end, so it creates a full session per
    # run. Reserved specifically to keep it OFF the demo number: a leftover session
    # there is what makes a live demo open with "we were cut off earlier".
    "test_demo_script_doc": 17,
    # Replays a live call up to the lung screen, so it creates a session per run.
    "test_asthma_danda_replay": 18,
    "test_screen_denial_corrects_answer": 19,
    # 20 was allocated to test_parent_prose_fills_detail, for a carry-forward that
    # filled A510 from the A500 answer's own prose. Withdrawn: no available signal
    # separates "she named the reason" from "she restated the screen", and guessing
    # wrong loses a disclosure - see tests/diag_carry_guard_separation.py. Left here
    # rather than reused, per the append-only rule above: a later suite taking 20
    # would inherit this one's leftover sessions.
}


def phone_for(suite: str) -> str:
    """The number reserved for one suite. Raises on an unregistered name.

    Deliberately not a silent fallback to the template: a typo would then put the
    suite straight back onto the shared number and reintroduce the flake quietly.
    """
    if suite not in _SUITES:
        raise KeyError(
            f"{suite!r} has no reserved phone. Add it to _SUITES in "
            f"tests/_phones.py - append a new index, do not reuse one.")
    return f"+1555000{_BASE + _SUITES[suite]:04d}"


def all_phones() -> list[str]:
    """Every reserved number, for tools/retire_sessions.py --test-phones."""
    return [phone_for(name) for name in _SUITES]


def _template() -> dict:
    customer = st.get_customer_by_phone(TEMPLATE_PHONE)
    if not customer:
        raise RuntimeError(
            f"the template customer {TEMPLATE_PHONE} is not in {st.TBL_CRM}. "
            f"Seed it first (scripts/32-crm-lambdas.sh, then the CRM sync) - "
            f"the per-suite numbers are clones of it.")
    return customer


def reserve(suite: str, quiet: bool = False) -> str:
    """The suite's own number, with a customer record and no leftovers.

    Idempotent, and cheap enough to call at import time: the clone is written
    only when it is missing, and retiring is a no-op when nothing is resumable.

    Call this instead of hardcoding a number:

        import _phones
        PHONE = _phones.reserve("test_orchestrator")
    """
    phone = phone_for(suite)

    existing = st.get_customer_by_phone(phone)
    if not existing:
        template = _template()
        clone = dict(template)
        clone["customerId"] = f"CUST-SUITE-{suite}"
        clone["phone"] = phone
        # Marked so the row is identifiable in the table and by the retire tool,
        # and so nobody mistakes it for a projected Salesforce record.
        clone["fixtureFor"] = suite
        clone["syncedAt"] = st.now_iso()
        st._ddb.Table(st.TBL_CRM).put_item(Item=st.to_ddb(clone))  # noqa: SLF001
        if not quiet:
            print(f"  [_phones] created fixture customer for {suite} on {phone}")

    retired = retire_leftovers(phone)
    if retired and not quiet:
        print(f"  [_phones] retired {retired} leftover session(s) on {phone}")
    return phone


def retire_leftovers(phone: str) -> int:
    """Retire every resumable session on a number. Returns how many.

    Bounded rather than while-True: find_resumable_session returns the most
    recent, so a row that never leaves RESUMABLE would spin forever.
    """
    count = 0
    for _ in range(50):
        session = st.find_resumable_session(phone)
        if not session:
            break
        st.mark_status(session["callId"], st.STATUS_ABANDONED,
                       note="retired by tests/_phones.reserve")
        count += 1
    return count
