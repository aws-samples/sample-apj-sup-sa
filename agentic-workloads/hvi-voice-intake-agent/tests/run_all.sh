#!/usr/bin/env bash
# Run every suite and report a single verdict.
#
# Ordered cheapest-first so a broken foundation fails fast rather than after a
# slow deployed-runtime test. Each suite writes real records to the live tables;
# that is deliberate, since the point is to test the deployed system rather than
# a mock of it.

set -uo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

PY="./.venv/bin/python"
declare -a NAMES=()
declare -a RESULTS=()
declare -a TIMES=()
FAILED=0

run() {
  local label="$1" script="$2"
  printf '\n\033[1;36m═══ %s\033[0m\n' "$label"
  local start; start=$(date +%s)
  if $PY "$script" >/tmp/hvi-test-out.txt 2>&1; then
    local dur=$(( $(date +%s) - start ))
    printf '\033[1;32m    PASS\033[0m  (%ss)\n' "$dur"
    tail -4 /tmp/hvi-test-out.txt | sed 's/^/    /'
    NAMES+=("$label"); RESULTS+=("PASS"); TIMES+=("${dur}s")
  else
    local dur=$(( $(date +%s) - start ))
    printf '\033[1;31m    FAIL\033[0m  (%ss)\n' "$dur"
    grep -E "FAIL|FAILED|Error|Traceback" /tmp/hvi-test-out.txt | head -12 | sed 's/^/    /'
    NAMES+=("$label"); RESULTS+=("FAIL"); TIMES+=("${dur}s")
    FAILED=$((FAILED + 1))
  fi
}

# Pure-local scoring and finite-model tests run first: they need no AWS call, and
# failures here are deterministic rather than deployment or model variance.
run "seed/schema exhaustive contracts" tests/test_seed_contracts.py
run "named conditions resolve and explain" tests/test_named_conditions_explainable.py
run "generated dialogue state model" tests/test_dialogue_state_model.py
run "generated conversation combinations" tests/test_conversation_combinations.py
run "graph handler exhaustive contracts" tests/test_graph_handler_contracts.py
run "confirmation email handler contract" tests/test_confirmation_handler.py
run "synchronous CRM submission contract" tests/test_submission_contract.py
# The durable transcript archive, and the seam that made Recording_URI__c and
# Transcript_URI__c blank on every call: the writeback always copied them from the
# session, and nothing ever put them there.
run "per-call S3 archive and its never-fail contract" tests/test_call_archive.py
# The other half of that record. A020 tells the caller the call is recorded, and
# for a long time nothing wrote audio anywhere, so the sentence was false and
# Recording_URI__c was blank. Pins that the stored object is a real stereo WAV
# under the same callId prefix as the transcript, that the archive DISCOVERS it
# (the media host has no DynamoDB access and cannot set the URI itself), and that
# every failure path returns None rather than failing the call.
run "per-call recording, stored and discoverable" tests/test_call_recording.py
# The SEAM between those two, which both of them passed straight over. Uploading
# the audio and reading session.recordingUri were each covered; nothing covered
# whether the URI ever got FROM one to the other.
run "the recording is linked to the call, not just stored" tests/test_recording_link.py
run "deterministic CRM phone lookup contract" tests/test_state_lookup_contract.py
run "form schema, checkpoint and resume" tests/test_resume_flow.py
run "inbound session bootstrap" tests/test_inbound_bootstrap.py
run "Cognito web-auth id-token verification" tests/test_web_auth.py
# These three hit the live Salesforce org. They create and delete their own
# records, so they are safe to re-run, but they do need hvi/salesforce to exist.
run "Salesforce contract (SYSTEM OF RECORD)" tests/test_salesforce.py
run "Salesforce -> DynamoDB projection" tests/test_crm_sync.py
run "CRM round trip, purchase to underwriting" tests/test_crm_roundtrip.py
run "runtime-added question, no redeploy" tests/test_dynamic_item.py
run "Neptune knowledge graph (ENFORCES)" tests/test_graph.py
run "OpenSearch classifier (INTERPRETS)" tests/test_classifier.py
run "classifier null class (noise is not disease)" tests/test_classifier_null_class.py
run "live text semantic matrix" tests/test_extract_semantic_matrix.py
run "underwriting conversation stress" tests/test_underwriting_stress.py
run "underwriting stress wave 2 (intersections)" tests/test_underwriting_stress2.py
# Guards the seam where every component passed but the wiring did not: the KG
# was seeded in romanised Latin while the STT emits Devanagari, and a blanket
# denial classified as asthma at 0.81 confidence.
run "STT to classifier seam" tests/test_stt_to_classifier.py
run "prompt cache, content-addressed and measured" tests/test_prompt_cache.py
run "orchestrator protocol enforcement" tests/test_orchestrator.py
# Sits next to the orchestrator suite because that one deliberately SKIPS the
# language turn - fresh_call() pre-confirms the language so its tests stay about
# the protocol. Without this file that skip would leave the feature untested, and
# the half most likely to regress is not the choice itself but the code-mixed
# input that has to keep working in both modes.
run "language selection and Hinglish input" tests/test_language_selection.py
run "condition screen 'yes' opens the protocol" tests/test_condition_screen_followup.py
# The other side of that screen: the branches that only open on a yes.
run "positive disclosures and subcategory branches" tests/test_positive_disclosures.py
run "echo of our own speech is rejected" tests/test_echo_rejection.py
run "repeat requests and named follow-ups" tests/test_repeat_and_naming.py
# The other end of "named follow-ups": what happens when naming it is not enough
# and the caller still does not understand. Pins the ladder - explain, re-ask,
# skip - and that anything skipped is promised to a human in the closing rather
# than vanishing.
run "confused callers get explained to, not repeated at" \
    tests/test_stuck_followup_recovery.py
run "retraction, quoted read-backs, deferred floor" tests/test_retraction_and_readback.py
# Guards the ACCEPTANCE side of a bad read-back: a caller who agrees to a
# misclassification, which is how bronchitis reached Chronic Viral Hepatitis on a
# live call with the file stamped as needing no review.
run "uncorroborated codes still reach a human" tests/test_uncorroborated_provenance.py
# The one question nearest neighbour cannot answer: "not in my vocabulary". Guards
# both directions - the veto must fire on coeliac disease reaching Type 2 Diabetes,
# and must NOT fire on a correct reference code, because a false veto discards a
# real disclosure and under-reports the patient's history.
run "classifier may decline to answer" tests/test_condition_adjudication.py
run "every condition's protocol, all 38" tests/test_protocol_paths.py
run "spoken dates in three scripts" tests/test_spoken_dates.py
# An identifier field must validate its identifier. A920 was typed "text", which is
# coerce's permissive fallback, so a live call filed the caller's own question as
# their national health ID.
run "ABHA health ID is validated, skip honoured" tests/test_abha_identifier.py
# Four things a real English call got wrong that all looked plausible in the
# record: a confirmed email stored as not_captured, the caller's spoken year
# overwritten with another, a list element the model invented to report its own
# uncertainty, and 'Skip' filed as an ABHA number because the demo form had never
# been rebuilt after the abha type was added.
run "defects found in a live English call" tests/test_live_call_defects.py
# THE GAP THAT LET ALL FOUR THROUGH. Six suites share one ScriptedExtract double
# that SUPPLIES the extractor's output, so they cannot test whether a stored value
# is faithful to what the caller said - the test author writes the value. This
# compares the two columns instead, and proves it has teeth against a frozen copy
# of the call: it must flag those four turns and leave the other 26 alone.
run "the record says what the caller said" tests/test_transcript_fidelity.py
# Barge-in was assumed to work and was not wired at all: pipecat 1.7's output
# transport acts on InterruptionFrame and nothing in this pipeline emitted one, so
# the agent talked over the caller for the whole of every prompt.
run "the caller can interrupt the agent" tests/test_bargein.py
# One trunk, two pjsip endpoints, and both directions have broken once. The split
# is the fix and it looks redundant, so it is pinned: inbound must never carry
# callerid, outbound must, and outbound must have no identify section that would
# let an inbound INVITE select it.
run "outbound caller ID cannot leak into inbound" tests/test_asterisk_trunk_config.py
# Resume inherited answers from a DIFFERENT questionnaire collected 19 hours
# earlier, so a live call never asked about tobacco or alcohol and still produced
# a complete-looking 29-answer record.
run "resume does not inherit the wrong form or a stale call" tests/test_resume_guards.py
# Reads the finished record the way a customer and an underwriter read it, in all
# three languages, including the review list.
run "record sanity in en, hi and hinglish" tests/test_record_sanity.py
# Every condition's sub-questions, checked in the EMAIL rather than in the
# database, in all three languages. Answers are unique markers so an answer
# appearing under the wrong condition is provable rather than plausible.
run "summary email, all 38 conditions x 3 languages" \
    tests/test_summary_email_all_conditions.py
run "distress corroboration and BMI cross-check" tests/test_safety_guards.py
run "unexpected medication verified once" tests/test_med_verification.py
run "segmented TTS and cache coherence" tests/test_segmented_tts.py
# Crosses the DEPLOYMENT seam, which nothing else here does. Every other suite
# loads Lambda handlers in-process with importlib.
run "deployed Lambdas exist and work over the wire" tests/test_deployed_lambdas.py
# The local Converse loop.
run "local agent container, full reasoning loop" tests/test_agent_local.py
run "deployed AgentCore runtime" tests/test_agentcore_deployed.py

printf '\n\033[1m%-42s %-6s %s\033[0m\n' "SUITE" "RESULT" "TIME"
printf '%s\n' "------------------------------------------------------------"
for i in "${!NAMES[@]}"; do
  colour=$([ "${RESULTS[$i]}" = "PASS" ] && printf '\033[32m' || printf '\033[31m')
  printf '%-42s %b%-6s\033[0m %s\n' "${NAMES[$i]}" "$colour" "${RESULTS[$i]}" "${TIMES[$i]}"
done

printf '\n'
if [ "$FAILED" -gt 0 ]; then
  printf '\033[1;31m%d of %d suites failed.\033[0m\n\n' "$FAILED" "${#NAMES[@]}"
  exit 1
fi
printf '\033[1;32mAll %d suites passed.\033[0m\n\n' "${#NAMES[@]}"
