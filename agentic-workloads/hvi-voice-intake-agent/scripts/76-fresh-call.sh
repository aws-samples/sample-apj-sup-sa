#!/usr/bin/env bash
# Preflight before a demo call: prove the next call starts clean.
#
# WHY THIS EXISTS. Three separate things have each made a live call look broken,
# and none of them were visible until the call was underway:
#
#   1. HVI_ALWAYS_FRESH silently deployed as 0, so the call RESUMED a previous
#      session and skipped every question already answered on it. A redeploy did
#      that without a word.
#   2. An unfinished session from an earlier call, so the greeting was "we were
#      cut off earlier" and the caller was dropped mid-questionnaire.
#   3. The caller assigned to the 27-item rehearsal form, which holds 2 of the 13
#      condition screens - so it asked about asthma and diabetes and nothing else.
#
# Each is a one-line check and none of them is checkable from the phone. Run this,
# read the three lines, then dial.
#
# Usage
#   bash scripts/76-fresh-call.sh                    # the seeded demo number
#   bash scripts/76-fresh-call.sh +919876543210
#   RETIRE=0 bash scripts/76-fresh-call.sh           # report only, change nothing
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh
[ -f config/env.local.sh ] && source config/env.local.sh

PHONE="${1:-${DEMO_PHONE:-+911234567890}}"
RETIRE="${RETIRE:-1}"
PY="./.venv/bin/python"

echo "=== preflight for $PHONE"

# ---- 1. does the runtime start every call fresh? ----------------------------
FRESH=$(aws bedrock-agentcore-control get-agent-runtime \
  --region "$DATA_REGION" --agent-runtime-id "$AGENT_RUNTIME_ID" \
  --query 'environmentVariables.HVI_ALWAYS_FRESH' --output text 2>/dev/null || echo "?")
if [ "$FRESH" = "1" ]; then
  echo "[1/3] HVI_ALWAYS_FRESH=1  - every inbound call starts a new questionnaire"
  echo "        Demo mode: an unfinished session is DISCARDED on the next call,"
  echo "        so resume cannot be demonstrated and a dropped call loses answers."
else
  # NOT AN ERROR, WHICH IT USED TO CLAIM TO BE.
  #
  # This read "wrong for a demo. Fix with: HVI_ALWAYS_FRESH=1", because the bug
  # that prompted the whole script was this flipping to 0 by ACCIDENT - the deploy
  # script defaulted it and nothing exported it, so three redeploys turned demo
  # mode off silently and calls opened mid-protocol. But 0 is also the correct
  # production value and the setting you want if resume is one of the things being
  # shown, and a preflight that reports a deliberate choice as a defect gets
  # ignored - which is the same argument written against the condition-screen
  # count below. The value is still printed first and loudest, so an unintended 0
  # is still visible; what changed is that the consequence and the fix are stated
  # instead of a verdict.
  echo "[1/3] HVI_ALWAYS_FRESH=$FRESH  - RESUME IS ON."
  echo "        A repeat call from this number CONTINUES its last unfinished"
  echo "        session: the caller is told they were cut off earlier and picks up"
  echo "        at the first unanswered item. Correct for production, and required"
  echo "        if you are demonstrating resume."
  echo "        It also means [3/3] below is the step that matters - an abandoned"
  echo "        rehearsal WILL be inherited by your next call unless it is retired,"
  echo "        and the printed scripts will not line up. This preflight retires"
  echo "        them by default."
  echo "        If you did not intend resume to be on:"
  echo "          HVI_ALWAYS_FRESH=1 bash scripts/40-agentcore.sh"
fi

# ---- 2. which questionnaire is this caller on? ------------------------------
echo "[2/3] assigned form:"
$PY - "$PHONE" <<'PY'
import json
import os
import sys

sys.path.insert(0, "common")
os.environ.setdefault("DATA_REGION", os.environ.get("DATA_REGION", "ap-south-1"))
import hvi_state as st

phone = sys.argv[1]
cust = st.get_customer_by_phone(phone) or {}
assigned = cust.get("assignedFormId")

# Which items ARE the condition screens comes from the production seed, not from
# a guess at the id pattern. An earlier version of this check used
# itemId.startswith("A2") and reported 10 of 13 for the complete form, because
# A300, A310 and A320 start with A3 - a preflight that cries wolf is worse than
# none, since the next real warning gets ignored.
seed = json.load(open("seed/form-arogya-sanjeevani-v1.json"))
screens = {i["itemId"] for i in seed["items"]
           if i.get("followUpGroup") == "condition_detail"}
if not screens:
    screens = {i["itemId"] for i in seed["items"]
               if i.get("section") == "conditions" and i.get("answerType") == "boolean"}

items = st.get_form_items(assigned or "arogya-sanjeevani-v1", include_inactive=True)
have = sorted(screens & {i["itemId"] for i in items})
print(f"        {assigned or '(none - falls back to the default)'}  -  "
      f"{len(items)} items, {len(have)} of {len(screens)} condition screens")
if len(have) < len(screens):
    missing = sorted(screens - set(have))
    print(f"        <-- SHORT FORM, missing {missing}.")
    print("        It will not ask about those conditions. Fix with:")
    print(f"          {sys.executable} tools/form_admin.py assign-form "
          f"--phone {phone} --reset")
PY

# ---- 3. is there an unfinished session waiting to be resumed? ---------------
echo "[3/3] unfinished sessions:"
if [ "$RETIRE" = "1" ]; then
  $PY tools/dial.py --retire-sessions --phone "$PHONE" 2>&1 | sed 's/^/        /'
else
  echo "        RETIRE=0, not touching them. Run without RETIRE=0 to clear."
fi

echo
echo "Ready. Dial $PHONE."
