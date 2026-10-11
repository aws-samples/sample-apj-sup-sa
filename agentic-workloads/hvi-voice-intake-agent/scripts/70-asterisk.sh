#!/usr/bin/env bash
# Stand up Asterisk in ap-south-1 as a SIP<->AudioSocket bridge.
#
# WHY THIS EXISTS
# ---------------
# Chime SDK PSTN Audio (the SIP media application service) is action-based: bot
# audio can only be a completed S3 file via PlayAudio, and caller audio only
# arrives as a finished S3 recording via RecordAudio. Actions run sequentially on
# a leg, so you cannot play and record at once. That makes true voice barge-in
# impossible through the SMA path - which is the one thing the whole cascading
# pipeline is supposed to deliver.
#
# Chime CAN interrupt in-progress audio (PlayAudio, RecordAudio and Pause are
# documented as interruptible, driven by UpdateSipMediaApplicationCall). What it
# cannot do is DETECT that the caller started speaking, because there is no
# out-of-band live audio feed short of Kinesis Video Streams, which is
# fragment-buffered and adds seconds.
#
# So we bypass the SMA entirely and use the Voice Connector as what it actually
# is: a plain SIP trunk. Asterisk terminates the SIP leg, owns the RTP, and hands
# raw audio to the agent over AudioSocket. Now VAD, interruption and streaming
# TTS are all ours.
#
# WHY THIS IS ALSO THE TATA COMMS INTEGRATION
# -------------------------------------------
# A carrier terminating SIP onto our own media server is exactly the shape Tata
# Comms will take. Swapping Chime for Tata is a trunk definition in
# pjsip.conf - no application change anywhere. Building it against Chime now
# means the Tata switch is configuration, not a project.
#
# WHY ap-south-1 AND NOT NEXT TO THE VOICE CONNECTOR
# --------------------------------------------------
# Chime SDK Voice is not available in Mumbai, so there is a cross-region hop
# whichever side Asterisk sits on - the audio has to reach ap-south-1 regardless,
# because that is where STT, the LLM and TTS live. Putting Asterisk in ap-south-1
# makes the BARGE-IN LOOP local: detect speech, stop playback, all inside one
# region. In us-east-1 every interrupt would pay a round trip. It also keeps
# media in India for DPDP, and matches the production topology where Tata
# terminates directly into ap-south-1 and the hop disappears.
#
# Cost: t4g.medium is roughly $0.034/hr in ap-south-1, plus an EIP. Tear down
# with scripts/79-asterisk-down.sh.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
NAME="${PREFIX}-asterisk"

# Instance selection is a ladder, not a single type, and it spans availability
# zones. The first attempt died with InsufficientInstanceCapacity: t4g.medium had
# nothing free in ap-south-1a, which was the only public subnet we had. Asterisk
# bridging one call is not demanding, so anything from t4g.small upward is ample
# and the point is simply to find metal.
INSTANCE_TYPES=(${ASTERISK_INSTANCE_TYPES:-t4g.small t4g.medium t4g.large c6g.medium m6g.medium})
VPC="${VPC_ID:-vpc-0123456789abcdef0}"
VC_ID="${VOICE_CONNECTOR_ID:-abcdefghij0123456789}"

# Ubuntu rather than Amazon Linux 2023: `apt install asterisk` is one line and
# well travelled, whereas AL2023 has no Asterisk package and would mean building
# from source inside user-data - a slow step that fails opaquely.
AMI=$(aws ssm get-parameter --region "$R" \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query 'Parameter.Value' --output text)
hvi_log "ubuntu 24.04 arm64 AMI: $AMI"

# Chime SDK Voice Connector network ranges for us-east-1, from
# docs.aws.amazon.com/chime-sdk/latest/ag/network-config.html
CHIME_SIP_CIDR="3.80.16.0/23"
CHIME_MEDIA_CIDRS=(
  "3.80.16.0/23"
  "52.55.62.128/25"
  "52.55.63.0/25"
  "34.212.95.128/25"
  "34.223.21.0/25"
)
RTP_START=10000
RTP_END=10500   # narrow on purpose: one concurrent call needs 2 ports

# --- public subnets, one per AZ we are willing to land in -------------------
# Only a single public subnet existed (ap-south-1a), which turned an AZ-local
# capacity shortage into a hard failure. A second public subnet in 1b gives the
# launch somewhere else to go.
RT_PUB=$(aws ec2 describe-route-tables --region "$R" \
  --filters "Name=association.subnet-id,Values=${SUBNET_PUB}" \
  --query 'RouteTables[0].RouteTableId' --output text)
hvi_log "public route table: $RT_PUB"

ensure_public_subnet() {
  local az="$1" cidr="$2" tag="$3" existing
  existing=$(aws ec2 describe-subnets --region "$R" \
    --filters "Name=vpc-id,Values=$VPC" "Name=tag:Name,Values=$tag" \
    --query 'Subnets[0].SubnetId' --output text 2>/dev/null || echo "None")
  if [ "$existing" != "None" ] && [ -n "$existing" ]; then
    echo "$existing"
    return
  fi
  local sid
  sid=$(aws ec2 create-subnet --region "$R" --vpc-id "$VPC" \
    --availability-zone "$az" --cidr-block "$cidr" \
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$tag},{Key=Project,Value=health-voice-intake}]" \
    --query 'Subnet.SubnetId' --output text)
  aws ec2 associate-route-table --region "$R" \
    --subnet-id "$sid" --route-table-id "$RT_PUB" >/dev/null
  aws ec2 modify-subnet-attribute --region "$R" \
    --subnet-id "$sid" --map-public-ip-on-launch >/dev/null
  echo "$sid"
}

SUBNET_PUB_B=$(ensure_public_subnet "${R}b" "10.42.201.0/24" "${PREFIX}-public-1b")
hvi_log "public subnets: ${SUBNET_PUB} (1a), ${SUBNET_PUB_B} (1b)"
CANDIDATE_SUBNETS=("$SUBNET_PUB_B" "$SUBNET_PUB")

# --- security group ---------------------------------------------------------
SG=$(aws ec2 describe-security-groups --region "$R" \
  --filters "Name=group-name,Values=${NAME}-sg" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  hvi_log "creating security group ${NAME}-sg"
  SG=$(aws ec2 create-security-group --region "$R" \
    --group-name "${NAME}-sg" --vpc-id "$VPC" \
    --description "Asterisk SIP/RTP from Chime Voice Connector only" \
    --query 'GroupId' --output text)

  # SIP signalling. Scoped to Chime's published range rather than 0.0.0.0/0 -
  # an open 5060 attracts SIP scanners and toll-fraud attempts within minutes.
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$SG" \
    --ip-permissions "IpProtocol=udp,FromPort=5060,ToPort=5060,IpRanges=[{CidrIp=$CHIME_SIP_CIDR,Description=chime-sip-signalling}]" >/dev/null

  # RTP media. Chime sends media from several ranges distinct from signalling.
  RANGES=""
  for c in "${CHIME_MEDIA_CIDRS[@]}"; do
    RANGES="${RANGES}{CidrIp=$c,Description=chime-media},"
  done
  RANGES="${RANGES%,}"
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$SG" \
    --ip-permissions "IpProtocol=udp,FromPort=$RTP_START,ToPort=$RTP_END,IpRanges=[$RANGES]" >/dev/null

  hvi_log "security group $SG created (no SSH ingress; management is via SSM)"
else
  hvi_warn "security group $SG already exists"
fi

# --- instance role so we can manage it without exposing SSH -----------------
ROLE="${NAME}-role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  hvi_log "creating $ROLE"
  aws iam create-role --role-name "$ROLE" \
    --description "Asterisk SIP bridge: SSM management plus S3 prompt reads" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  hvi_log "waiting for the instance profile to propagate"
  sleep 12
else
  hvi_warn "role $ROLE already exists"
fi

# --- user data --------------------------------------------------------------
# Asterisk config is written by 71-asterisk-config.sh over SSM rather than baked
# in here, so the trunk can be retuned without replacing the instance.
cat > /tmp/hvi-asterisk-userdata.sh <<'USERDATA'
#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y asterisk asterisk-modules tcpdump ngrep jq

# AudioSocket ships in mainline Asterisk but the module is not always loaded by
# default; make it explicit so a missing module is a config error rather than a
# silent dialplan failure at call time.
cat >> /etc/asterisk/modules.conf <<'MODCONF'

; Required for the SIP <-> agent audio bridge.
load => func_audiosocket.so
load => chan_audiosocket.so
load => app_audiosocket.so
MODCONF

systemctl enable asterisk
touch /var/log/hvi-asterisk-bootstrap-done
USERDATA

# --- instance ---------------------------------------------------------------
EXISTING=$(aws ec2 describe-instances --region "$R" \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' --output text 2>/dev/null || echo "")

if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  hvi_warn "instance $EXISTING already exists; not replacing it"
  IID="$EXISTING"
else
  # Walk (subnet x instance type) until one has capacity. Capacity is per-AZ and
  # per-type, so a single fixed choice is a coin flip - which is how the first
  # run failed.
  IID=""
  for subnet in "${CANDIDATE_SUBNETS[@]}"; do
    for itype in "${INSTANCE_TYPES[@]}"; do
      hvi_log "trying $itype in $subnet"
      if IID=$(aws ec2 run-instances --region "$R" \
          --image-id "$AMI" \
          --instance-type "$itype" \
          --subnet-id "$subnet" \
          --security-group-ids "$SG" \
          --iam-instance-profile "Name=$ROLE" \
          --associate-public-ip-address \
          --metadata-options "HttpTokens=required" \
          --user-data "file:///tmp/hvi-asterisk-userdata.sh" \
          --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=health-voice-intake}]" \
          --query 'Instances[0].InstanceId' --output text 2>/dev/null); then
        hvi_log "launched $IID as $itype in $subnet"
        ASTERISK_SUBNET="$subnet"
        break 2
      fi
      hvi_warn "  no capacity for $itype in $subnet"
      IID=""
    done
  done
  if [ -z "$IID" ]; then
    hvi_err "no capacity for any of: ${INSTANCE_TYPES[*]}"
    hvi_err "across subnets: ${CANDIDATE_SUBNETS[*]}"
    rm -f /tmp/hvi-asterisk-userdata.sh
    exit 1
  fi
fi
rm -f /tmp/hvi-asterisk-userdata.sh

aws ec2 wait instance-running --region "$R" --instance-ids "$IID"

# --- elastic IP -------------------------------------------------------------
# A stable address matters because the Voice Connector allow-list is CIDR-based;
# a public IP that changes on stop/start silently breaks the trunk.
ALLOC=$(aws ec2 describe-addresses --region "$R" \
  --filters "Name=tag:Name,Values=$NAME" \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null || echo "None")
if [ "$ALLOC" = "None" ] || [ -z "$ALLOC" ]; then
  hvi_log "allocating an elastic IP"
  ALLOC=$(aws ec2 allocate-address --region "$R" --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=health-voice-intake}]" \
    --query 'AllocationId' --output text)
fi
aws ec2 associate-address --region "$R" --instance-id "$IID" --allocation-id "$ALLOC" >/dev/null
EIP=$(aws ec2 describe-addresses --region "$R" --allocation-ids "$ALLOC" \
  --query 'Addresses[0].PublicIp' --output text)
hvi_log "asterisk public IP: $EIP"

# --- point the Voice Connector at Asterisk ----------------------------------
# The VC was already configured as a SIP trunk to a customer host (a leftover
# 35.174.95.215 from an earlier CDK sample), so this is a repoint rather than a
# new integration.
hvi_log "updating Voice Connector $VC_ID termination allow-list to $EIP/32"
aws chime-sdk-voice put-voice-connector-termination \
  --voice-connector-id "$VC_ID" --region "$TELEPHONY_REGION" \
  --termination "$(jq -nc --arg ip "$EIP/32" '{
      CpsLimit: 1,
      CidrAllowedList: [$ip],
      CallingRegions: ["US","IN"],
      Disabled: false
    }')" >/dev/null

hvi_log "updating origination route to $EIP:5060/UDP"
aws chime-sdk-voice put-voice-connector-origination \
  --voice-connector-id "$VC_ID" --region "$TELEPHONY_REGION" \
  --origination "$(jq -nc --arg ip "$EIP" '{
      Routes: [{Host: $ip, Port: 5060, Protocol: "UDP", Priority: 1, Weight: 1}],
      Disabled: false
    }')" >/dev/null

# CallingRegions above now includes IN. It was US-only, which would have
# rejected any outbound call to an Indian mobile with a 403 at the trunk - a
# failure that looks like a dialplan bug rather than a policy one.
hvi_log "CallingRegions set to US,IN so outbound calls to Indian mobiles are allowed"

# --- record for later scripts ----------------------------------------------
touch config/env.local.sh
for kv in "ASTERISK_INSTANCE_ID=$IID" "ASTERISK_IP=$EIP" "ASTERISK_SG=$SG"; do
  key="${kv%%=*}"
  if grep -q "^export ${key}=" config/env.local.sh 2>/dev/null; then
    sed -i.bak "s|^export ${key}=.*|export ${kv}|" config/env.local.sh && rm -f config/env.local.sh.bak
  else
    printf 'export %s\n' "$kv" >> config/env.local.sh
  fi
done
hvi_record "asterisk.instance" "$IID"

hvi_log ""
hvi_log "instance:   $IID"
hvi_log "public IP:  $EIP"
hvi_log "bootstrap takes 2-3 minutes (apt install asterisk)"
hvi_log "next: bash scripts/71-asterisk-config.sh"
