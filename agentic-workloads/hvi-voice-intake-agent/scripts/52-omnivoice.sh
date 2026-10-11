#!/usr/bin/env bash
# Deploy OmniVoice zero-shot TTS as a SageMaker real-time endpoint in ap-south-1.
#
# OmniVoice is the TTS named in the requirement. Polly has been standing in
# because this endpoint did not exist; this script is what removes the stand-in.
#
# WHY A model.tar.gz AND NOT HF_MODEL_ID
# Whisper needed neither Docker build nor S3 artifact: transformers ships a
# working ASR pipeline, so HF_MODEL_ID was enough. OmniVoice has its own library
# and a generate() that needs a reference clip, so it needs a custom handler -
# and the handler plus the reference voice travel as a model.tar.gz.
#
# WHY THE PYTORCH DLC AND NOT THE HUGGINGFACE DLC
# omnivoice requires transformers>=5.3.0; the HF DLC pins 4.51.3 and its serving
# toolkit depends on it. Upgrading transformers by a major version underneath the
# thing that serves requests is how you get a container that pip-installs cleanly
# and then fails the ping health check. Nothing in the PyTorch DLC imports
# transformers.
#
# LICENSING, RECORDED HERE ON PURPOSE
# The omnivoice CODE is Apache 2.0 but the pretrained WEIGHTS are CC-BY-NC,
# because the training data includes Emilia, which is non-commercial only. That
# is fine for this demo and NOT fine for production customer calls. Anyone
# promoting this beyond a POC needs a different voice or a licence.
#
# Cost: one more warm GPU alongside hvi-whisper, roughly $0.7/hr on g4dn.xlarge.
# Tear down with scripts/59-omnivoice-down.sh when the demo is over.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
ACC="$AWS_ACCOUNT_ID"
MODEL_NAME="${PREFIX}-omnivoice"
ENDPOINT_CONFIG="${PREFIX}-omnivoice-config"
ENDPOINT="${PREFIX}-omnivoice"
ROLE_NAME="${PREFIX}-sagemaker-role"
BUCKET="${PREFIX}-artifacts-${ACC}-${R}"
KEY="omnivoice/model.tar.gz"

# Same ladder logic as Whisper: ap-south-1 ran out of A10G once already, and a
# single instance type turns a transient capacity gap into a hard failure. The
# checkpoint is 3.3GB and runs in fp16, so the 16GB T4 in g4dn is ample - g5 is
# only faster, not more capable, for this model.
#
# ml.g6.xlarge IS DELIBERATELY ABSENT, and this cost a failed create-endpoint.
# SageMaker reserves quota against EVERY pool in an InstancePools ladder, not just
# the one the variant runs on - because it may fall back to any of them at any
# time. hvi-whisper runs on g4dn.xlarge but its five-entry ladder includes
# g6.xlarge, whose account limit is 1, so that quota is fully consumed and any
# second endpoint naming g6.xlarge is rejected with a message about a type it
# would never actually have used. A long ladder is not free: it reserves breadth.
# scripts/51-gpu-quota.py prints the reserved-vs-running split and emits a ladder
# that is safe to request right now.
# LEADS WITH g5 (A10G), NOT g4dn (T4), AND THAT IS A MEASURED CHOICE.
# The T4 synthesised at model p50 1712ms (tests/test_omnivoice.py reports model
# time separately from transport, so this is compute, not the round trip to
# ap-south-1). That puts a turn at roughly 463ms STT + 600ms LLM + 1712ms TTS =
# 2.8s, which is the same sluggish range that disqualified Whisper-only STT.
# The advertised RTF of 0.025 is datacentre-class silicon; a T4 is several times
# slower. A10G has roughly double the fp16 throughput, so g5 leads and g4dn stays
# as the capacity fallback - a slow voice beats no voice.
#
# TWO ENTRIES, NOT FIVE, AND THAT IS DELIBERATE. A long ladder is greedy: it
# reserves one unit of quota from every type it names, so two five-entry ladders
# (Whisper's and this one) exhausted every narrow quota in the account -
# g4dn.2xlarge, g5.2xlarge, g6.xlarge and g6.2xlarge all sat at limit while
# nothing was running on any of them. An in-place update makes it worse, because
# blue/green holds the old and new reservations at once, so a type with quota 1
# can never survive an update of the endpoint that already reserves it.
# g5.xlarge and g4dn.xlarge each have a limit of 4, which leaves room for both
# endpoints plus updates, and they cover the two GPU families that matter here:
# A10G for speed, T4 as the capacity fallback.
INSTANCE_POOLS="${TTS_INSTANCE_POOLS:-ml.g5.xlarge,ml.g4dn.xlarge}"

DLC="763104351884.dkr.ecr.${R}.amazonaws.com/pytorch-inference:2.6.0-gpu-py312-cu124-ubuntu22.04-sagemaker"

# ---- Generation settings ----------------------------------------------------
# SPEAKING RATE. A caller said the agent was too fast, and the numbers agreed: the
# first clone ran 199 wpm against conversational English at 140-160.
#
# 0.85 is an interval estimate, not a measurement, and the interval is why:
#   - the reference clip reads at 159 wpm (tests/diag_reference_clip.py)
#   - the only clone rate we have measured came out 1.19x its reference, but that
#     was cloning a POLLY reference, which is out of distribution for a model
#     trained on human speech. The multiplier may well be nearer 1.0 for a human
#     clip, and we will not know until this endpoint is rebuilt and measured.
# So the true rate is somewhere in 159-189 wpm, and 0.85 is the factor that stays
# inside the acceptable band across that whole range (161 wpm at the fast end,
# 135 at the slow end) rather than being optimal for either.
#
# MEASURE IT AND THEN SET IT: tests/diag_omnivoice_rate.py reports actual wpm.
OMNIVOICE_SPEED="${OMNIVOICE_SPEED:-0.85}"

# PER LANGUAGE. These are MEASURED against the live endpoint with
# tests/diag_omnivoice_rate.py --sweep, not derived:
#
#              reference   speed   measured   target
#   en           159 wpm    0.78    152 wpm   151   (human clip, ref.wav)
#   hi           191 wpm    1.06    183 wpm   181   (polly Kajal, ref-hi.wav)
#   hinglish     159 wpm    0.78    152 wpm   151   (falls back to ref.wav)
#
# THE RATE MULTIPLIER IS A PROPERTY OF THE REFERENCE, NOT THE MODEL, and that is the
# whole reason this is per-language rather than one number. Measured:
#
#   en reference 159 wpm -> clone 194 wpm at speed 1.0   = x1.22  (FASTER)
#   hi reference 191 wpm -> clone 178 wpm at speed 1.0   = x0.93  (SLOWER)
#
# So there is no single "clone runs 1.19x" constant to correct for. Both were first
# guessed from the English figure, and the Hindi guess (0.72) was 47% off - it would
# have run Hindi at 148 wpm against a natural 191, a noticeable drag, and nothing
# would have flagged it because Hindi wpm looks unremarkable next to English wpm.
#
# The target is 95% of each REFERENCE's own natural rate, not a shared wpm number:
# Devanagari word boundaries do not correspond to English ones, so a shared figure
# means different things per language. 95% because this is a medical intake call,
# often with an elderly caller.
#
# Hinglish matches English because it uses the SAME reference - Polly transliterates
# the Latin words in code-mixed text, so there is deliberately no Hinglish reference.
# If a human Hinglish clip is ever recorded, re-measure.
#
# Re-measure after ANY reference change:
#   ./.venv/bin/python tests/diag_omnivoice_rate.py --sweep 0.7,0.8,0.9 --language en
OMNIVOICE_SPEED_EN="${OMNIVOICE_SPEED_EN:-0.78}"
OMNIVOICE_SPEED_HI="${OMNIVOICE_SPEED_HI:-1.06}"
OMNIVOICE_SPEED_HINGLISH="${OMNIVOICE_SPEED_HINGLISH:-0.78}"

# 32 is the documented quality setting, 16 the fast one. At 32 the g5.xlarge
# measured model p50 1986ms against the <1200ms turn budget (tests/
# test_omnivoice.py), which made every turn sluggish; 16 halves the diffusion
# compute and is verified two ways after deployment: STT round-trip keyword
# survival at 16 vs 32 (tests/diag_omnivoice_steps.py, using the handler's
# bounded per-request num_step override) and human listening on the written
# WAVs. If quality at 16 ever disappoints, sweep per-request before touching
# this default - an endpoint update costs 15-25 minutes per attempt.
OMNIVOICE_NUM_STEP="${OMNIVOICE_NUM_STEP:-16}"

# A medical-history form is mostly numbers. Without this, "162" risks being read
# digit by digit. The handler probes it during warmup and disables it if the
# WeTextProcessing extra is missing, so this is safe to leave on.
OMNIVOICE_NORMALIZE_TEXT="${OMNIVOICE_NORMALIZE_TEXT:-1}"

# ---- Reference voice --------------------------------------------------------
# Zero-shot TTS has no built-in voices, so the reference clip IS the agent's
# voice.
#
# This used to synthesise a reference with Polly when one was missing, which meant
# a missing reference silently produced a robot cloning a robot. That path is gone,
# so a missing reference is now a hard stop with instructions: it needs a human
# recording, and only a human can supply one.
if [ ! -f build/omnivoice/ref.wav ] || [ ! -f build/omnivoice/ref.txt ]; then
  hvi_err "no reference voice at build/omnivoice/ref.wav"
  hvi_err ""
  hvi_err "OmniVoice is zero-shot: the reference clip IS the agent's voice, and it"
  hvi_err "must be a real human recording. Record 4-8 seconds of REFERENCE_TEXT"
  hvi_err "(see tools/make_reference_voice.py) and then:"
  hvi_err ""
  hvi_err "  afconvert -f WAVE -d LEI16@16000 -c 1 mine.m4a build/omnivoice/mine.wav"
  hvi_err "  ./.venv/bin/python tests/diag_reference_clip.py build/omnivoice/mine.wav"
  hvi_err "  ./.venv/bin/python tools/make_reference_voice.py --from-clip build/omnivoice/mine.wav"
  exit 1
fi
hvi_log "reference: $(wc -c < build/omnivoice/ref.wav | tr -d ' ') bytes, transcript $(wc -c < build/omnivoice/ref.txt | tr -d ' ') chars"

# Per-language references are optional. Cross-lingual cloning imports the
# reference language's accent, so a Hindi turn spoken from an English reference
# sounds anglicised - shipping ref-hi.wav is what fixes that. Absent ones fall
# back to the default in the handler, which logs the fallback.
REF_FILES=(build/omnivoice/ref.wav build/omnivoice/ref.txt)
for lang in en hi hinglish; do
  if [ -f "build/omnivoice/ref-${lang}.wav" ] && [ -f "build/omnivoice/ref-${lang}.txt" ]; then
    REF_FILES+=("build/omnivoice/ref-${lang}.wav" "build/omnivoice/ref-${lang}.txt")
    hvi_log "  + ${lang} reference"
  else
    hvi_warn "  no ${lang} reference; ${lang} will use the default voice"
  fi
done

# ---- Artifact bucket --------------------------------------------------------
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  hvi_warn "bucket $BUCKET exists"
else
  hvi_log "creating bucket $BUCKET"
  aws s3api create-bucket --bucket "$BUCKET" --region "$R" \
    --create-bucket-configuration "LocationConstraint=$R" >/dev/null
  # A model artifact bucket has no reason to be public, and blocking it at
  # creation is cheaper than discovering it later in a security review.
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  hvi_record "s3.artifacts" "$BUCKET"
fi

# ---- Package ---------------------------------------------------------------
# Layout the PyTorch inference toolkit expects:
#   code/inference.py, code/requirements.txt  -> handler and its deps
#   ref.wav, ref.txt                          -> extracted to /opt/ml/model
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/code"
cp models/omnivoice/code/inference.py "$STAGE/code/"
cp models/omnivoice/code/requirements.txt "$STAGE/code/"
cp "${REF_FILES[@]}" "$STAGE/"

TGZ="build/omnivoice/model.tar.gz"
rm -f "$TGZ"
# COPYFILE_DISABLE stops macOS writing AppleDouble "._foo" siblings into the
# archive. They are invisible locally and then appear as junk next to every file
# in /opt/ml/model, where a "._ref.wav" is at best confusing and at worst picked
# up by a glob.
COPYFILE_DISABLE=1 tar -czf "$TGZ" -C "$STAGE" .
hvi_log "packaged $(wc -c < "$TGZ" | tr -d ' ') bytes -> s3://$BUCKET/$KEY"
aws s3 cp "$TGZ" "s3://$BUCKET/$KEY" --only-show-errors
MODEL_DATA="s3://$BUCKET/$KEY"

# ---- Role: allow reading the artifact ---------------------------------------
# The existing inline policy grants hvi-audio-* only, so the artifacts bucket
# needs its own statement rather than being silently unreadable.
hvi_log "granting $ROLE_NAME read on s3://$BUCKET"
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "${PREFIX}-sagemaker-artifacts" \
  --policy-document "$(jq -nc --arg b "$BUCKET" \
    '{Version:"2012-10-17",
      Statement:[{Sid:"ReadModelArtifacts",Effect:"Allow",
                  Action:["s3:GetObject","s3:ListBucket"],
                  Resource:["arn:aws:s3:::\($b)","arn:aws:s3:::\($b)/*"]}]}')"
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)

# ---- Model ------------------------------------------------------------------
if aws sagemaker describe-model --model-name "$MODEL_NAME" --region "$R" >/dev/null 2>&1; then
  hvi_warn "model $MODEL_NAME exists - deleting to apply current settings"
  aws sagemaker delete-model --model-name "$MODEL_NAME" --region "$R"
fi

hvi_log "creating model $MODEL_NAME"
aws sagemaker create-model \
  --model-name "$MODEL_NAME" \
  --execution-role-arn "$ROLE_ARN" \
  --region "$R" \
  --tags Key=Project,Value=health-voice-intake \
  --primary-container "$(jq -nc --arg img "$DLC" --arg data "$MODEL_DATA" \
      --arg speed "$OMNIVOICE_SPEED" --arg numstep "$OMNIVOICE_NUM_STEP" \
      --arg normalize "$OMNIVOICE_NORMALIZE_TEXT" \
      --arg speeden "$OMNIVOICE_SPEED_EN" --arg speedhi "$OMNIVOICE_SPEED_HI" \
      --arg speedhing "$OMNIVOICE_SPEED_HINGLISH" \
      '{
         Image: $img,
         ModelDataUrl: $data,
         Environment: {
           SAGEMAKER_PROGRAM: "inference.py",
           SAGEMAKER_SUBMIT_DIRECTORY: "/opt/ml/model/code",
           SAGEMAKER_CONTAINER_LOG_LEVEL: "20",
           SAGEMAKER_REGION: "ap-south-1",
           OMNIVOICE_MODEL_ID: "k2-fsa/OmniVoice",
           OMNIVOICE_SPEED: $speed,
           OMNIVOICE_SPEED_EN: $speeden,
           OMNIVOICE_SPEED_HI: $speedhi,
           OMNIVOICE_SPEED_HINGLISH: $speedhing,
           OMNIVOICE_NUM_STEP: $numstep,
           OMNIVOICE_NORMALIZE_TEXT: $normalize,
           # Same trap as the Whisper deploy: hf_transfer against the Xet CDN
           # returned 403 on the weight shards and the container never came up.
           # Plain HTTP is slower, which is why the health-check window below is
           # the maximum SageMaker allows.
           HF_HUB_ENABLE_HF_TRANSFER: "0",
           # /opt/ml/model is read-only, so the Hub cache must live elsewhere or
           # the download fails with a permission error that reads like a network
           # problem.
           HF_HOME: "/tmp/hf",
           TS_DEFAULT_RESPONSE_TIMEOUT: "300"
         }
       }')" \
  --query 'ModelArn' --output text

# ---- Endpoint config --------------------------------------------------------
if aws sagemaker describe-endpoint-config --endpoint-config-name "$ENDPOINT_CONFIG" \
     --region "$R" >/dev/null 2>&1; then
  hvi_warn "endpoint config exists - deleting"
  aws sagemaker delete-endpoint-config --endpoint-config-name "$ENDPOINT_CONFIG" --region "$R"
fi

# 3600s is the maximum SageMaker permits, and it is needed: container start pays
# a pip install (omnivoice drags in transformers 5, gradio, librosa, accelerate)
# AND a 3.3GB checkpoint download from the Hub, before the warmup generate.
hvi_log "creating endpoint config, pools: $INSTANCE_POOLS"
aws sagemaker create-endpoint-config \
  --endpoint-config-name "$ENDPOINT_CONFIG" \
  --region "$R" \
  --tags Key=Project,Value=health-voice-intake \
  --production-variants "$(jq -nc --arg m "$MODEL_NAME" --arg pools "$INSTANCE_POOLS" \
      '[{
          VariantName: "AllTraffic",
          ModelName: $m,
          InitialInstanceCount: 1,
          InitialVariantWeight: 1.0,
          ContainerStartupHealthCheckTimeoutInSeconds: 3600,
          ModelDataDownloadTimeoutInSeconds: 1800,
          VariantInstanceProvisionTimeoutInSeconds: 600,
          InstancePools: (
            $pools | split(",")
                   | to_entries
                   | map({InstanceType: .value, Priority: (.key + 1)})
          )
        }]')" \
  --query 'EndpointConfigArn' --output text

# ---- Endpoint ---------------------------------------------------------------
EXISTING=$(aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" \
  --region "$R" --query 'EndpointStatus' --output text 2>/dev/null || echo "NONE")

# A Failed endpoint cannot be updated into health; it has to be rebuilt.
if [ "$EXISTING" = "Failed" ]; then
  hvi_warn "endpoint is Failed - deleting before rebuild"
  aws sagemaker delete-endpoint --endpoint-name "$ENDPOINT" --region "$R"
  while aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" --region "$R" >/dev/null 2>&1; do
    printf '.'; sleep 5
  done
  printf '\n'
  EXISTING="NONE"
fi

if [ "$EXISTING" != "NONE" ]; then
  hvi_log "updating endpoint $ENDPOINT (was $EXISTING)"
  aws sagemaker update-endpoint --endpoint-name "$ENDPOINT" \
    --endpoint-config-name "$ENDPOINT_CONFIG" --region "$R" \
    --query 'EndpointArn' --output text
else
  hvi_log "creating endpoint $ENDPOINT"
  aws sagemaker create-endpoint --endpoint-name "$ENDPOINT" \
    --endpoint-config-name "$ENDPOINT_CONFIG" --region "$R" \
    --tags Key=Project,Value=health-voice-intake \
    --query 'EndpointArn' --output text
  hvi_record "sagemaker.omnivoice" "$ENDPOINT"
fi

touch config/env.local.sh
if grep -q '^export OMNIVOICE_ENDPOINT=' config/env.local.sh 2>/dev/null; then
  hvi_warn "OMNIVOICE_ENDPOINT already in config/env.local.sh"
else
  printf 'export OMNIVOICE_ENDPOINT="%s"\n' "$ENDPOINT" >> config/env.local.sh
  hvi_log "appended OMNIVOICE_ENDPOINT to config/env.local.sh"
fi

hvi_log ""
hvi_log "provisioning: pip install + 3.3GB checkpoint download, expect 15-25 min"
hvi_log "watch:  bash scripts/53-omnivoice-status.sh"
hvi_log "then:   HVI_TTS=omnivoice bash scripts/72-media-deploy.sh"
