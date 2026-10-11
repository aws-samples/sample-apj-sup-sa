#!/usr/bin/env bash
# In-region (ap-south-1) coturn TURN relay for the browser (WebRTC) media path.
#
# WHY A TURN RELAY IS NEEDED AT ALL
# ---------------------------------
# The browser voice surface (media/bot_web.py, web/app.js) has two transports and
# only one of them can ride CloudFront:
#   - Signalling (HTTPS: the page + POST /api/offer)  -> through CloudFront (TCP)
#   - Media      (WebRTC audio: ICE / DTLS / SRTP)     -> DIRECT, over UDP
# CloudFront proxies TCP/HTTP only, so it CANNOT carry the SRTP audio. When a
# direct browser<->EC2 UDP path is blocked by NAT (the media host sits behind
# 1:1 NAT; strict client NATs need help), the call needs a TURN relay to forward
# the SRTP. STUN alone only discovers addresses — it moves no audio — so STUN can
# live anywhere; TURN carries the actual voice.
#
# WHY THE RELAY MUST BE IN ap-south-1
# -----------------------------------
# A TURN relay forwards the CALL AUDIO, which is PHI. A relay in any other region
# would move that audio out of India and break the DPDP residency guarantee the
# whole system rests on. This is the single hardest constraint on this surface:
# the TURN box is pinned to $DATA_REGION and this script refuses to run elsewhere.
#
# WHY A DEDICATED t4g.small EC2, NOT THE EXISTING ${PREFIX}-asterisk HOST
# ----------------------------------------------------------------------
# Reusing the asterisk media host was considered and rejected:
#   - Its security group is deliberately CIDR-locked to Chime's SIP/RTP ranges
#     with a NARROW RTP window (10000-10500 for one call). A public TURN relay
#     needs UDP 3478 open far wider and a separate relay port range; bolting that
#     onto the SIP bridge's SG broadens the attack surface of the box that
#     terminates real phone calls.
#   - coturn and Asterisk both want to own UDP relay ports; co-tenancy invites
#     port collisions and couples two unrelated lifecycles (a TURN restart would
#     touch the call path).
# A dedicated t4g.small (~$0.02/hr in ap-south-1) keeps the relay's blast radius
# to itself, gets its own SG, and can be torn down without touching telephony.
# It is the simplest DURABLE shape: a plain systemd coturn, no orchestration.
#
# WHY SHORT-LIVED TURN REST CREDENTIALS, NOT A STATIC USER/PASSWORD
# ----------------------------------------------------------------
# coturn's use-auth-secret ("TURN REST API" model): instead of a fixed username
# and password, the media host derives a time-boxed credential from a SHARED
# SECRET —  username = "<unix-expiry>", password = base64(HMAC-SHA1(secret,
# username)). The credential the browser ever sees is valid for minutes and
# cannot be replayed after it expires, so a leaked ICE config is not a standing
# key to the relay. The shared secret lives in SSM Parameter Store (SecureString),
# never in this repo, and only the media host and the relay ever hold it.
# docs/WEB_DEPLOY.md flags "prefer short-lived TURN REST credentials" — this is that.
#
# WHAT THIS CREATES (all reuse-by-name, safe to re-run)
#   1. SSM SecureString  /hvi/turn/shared-secret   (generated once on first run)
#   2. Security group    ${PREFIX}-turn-sg         (UDP 3478 + relay range + 5349)
#   3. EC2 t4g.small     ${PREFIX}-turn            (Ubuntu, coturn via user-data)
#   4. Elastic IP        ${PREFIX}-turn            (external-ip/relay-ip = this EIP)
#
# NOTE: like the other numbered scripts this WRITES infra; the caller runs it.
# Reference: docs/WEB_DEPLOY.md ("Media path — UDP and TURN, kept in-region").
#
# Usage
#   bash scripts/35-turn.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
NAME="${PREFIX}-turn"

# ---- guard: PHI-carrying relay belongs in ap-south-1 -----------------------
if [ "$R" != "ap-south-1" ]; then
  hvi_err "DATA_REGION is '$R', not ap-south-1. A TURN relay forwards call audio"
  hvi_err "(PHI); running it outside India breaks DPDP residency. Refusing."
  exit 1
fi

VPC="${VPC_ID:-vpc-0123456789abcdef0}"
# coturn's listener and the standard STUN/TURN port.
TURN_PORT=3478
TURN_TLS_PORT=5349
# Relay port range coturn hands out for the actual media. Kept modest — a handful
# of concurrent browser calls — rather than the whole ephemeral range, so the SG
# rule and the coturn config agree on a bounded, auditable window.
RELAY_MIN="${HVI_TURN_RELAY_MIN:-49160}"
RELAY_MAX="${HVI_TURN_RELAY_MAX:-49200}"
SSM_SECRET_NAME="/hvi/turn/shared-secret"

# ---- 1. shared secret in SSM (generate once, reuse thereafter) -------------
# SecureString so it is encrypted at rest with the account KMS key. Generated
# with the AWS CLI's own random bytes on the host running this script; never
# written to the repo or echoed in full.
if aws ssm get-parameter --region "$R" --name "$SSM_SECRET_NAME" \
      --with-decryption >/dev/null 2>&1; then
  hvi_warn "TURN shared secret already in SSM at $SSM_SECRET_NAME"
else
  hvi_log "generating TURN shared secret into SSM $SSM_SECRET_NAME"
  SECRET=$(openssl rand -hex 32)
  aws ssm put-parameter --region "$R" \
    --name "$SSM_SECRET_NAME" --type SecureString --value "$SECRET" \
    --tags "Key=Project,Value=health-voice-intake" >/dev/null
  unset SECRET
  hvi_record "turn.secret" "$SSM_SECRET_NAME"
fi

# ---- 2. security group (reuse by name) -------------------------------------
SG=$(aws ec2 describe-security-groups --region "$R" \
  --filters "Name=group-name,Values=${NAME}-sg" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  hvi_log "creating security group ${NAME}-sg"
  SG=$(aws ec2 create-security-group --region "$R" \
    --group-name "${NAME}-sg" --vpc-id "$VPC" \
    --description "coturn TURN relay: STUN/TURN + relay media range" \
    --query 'GroupId' --output text)

  # STUN/TURN control plane. A TURN relay is a public rendezvous point by nature
  # — the browser must reach it from wherever the user is — so 3478 is open to
  # the internet. This is intentional and safe ONLY because auth is required:
  # coturn's use-auth-secret rejects any allocation without a valid short-lived
  # credential, so an open port is not an open relay.
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$SG" \
    --ip-permissions "IpProtocol=udp,FromPort=$TURN_PORT,ToPort=$TURN_PORT,IpRanges=[{CidrIp=0.0.0.0/0,Description=stun-turn}]" >/dev/null
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$SG" \
    --ip-permissions "IpProtocol=tcp,FromPort=$TURN_PORT,ToPort=$TURN_PORT,IpRanges=[{CidrIp=0.0.0.0/0,Description=stun-turn-tcp}]" >/dev/null

  # Relay media range. This is where the SRTP actually flows once an allocation
  # is granted; bounded to RELAY_MIN..RELAY_MAX so the exposure is a known window
  # rather than the full ephemeral range.
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$SG" \
    --ip-permissions "IpProtocol=udp,FromPort=$RELAY_MIN,ToPort=$RELAY_MAX,IpRanges=[{CidrIp=0.0.0.0/0,Description=turn-relay-media}]" >/dev/null

  # TLS TURN (turns:) on 5349 — "if easy", and it is: one more rule. Lets clients
  # on networks that block UDP fall back to TURN-over-TLS on a firewall-friendly
  # port. coturn serves it from the same shared-secret auth.
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$SG" \
    --ip-permissions "IpProtocol=tcp,FromPort=$TURN_TLS_PORT,ToPort=$TURN_TLS_PORT,IpRanges=[{CidrIp=0.0.0.0/0,Description=turns-tls}]" >/dev/null

  hvi_log "security group $SG created (no SSH ingress; management is via SSM)"
else
  hvi_warn "security group $SG already exists"
fi

# ---- 3. instance role (SSM management, no SSH) -----------------------------
# Same posture as the asterisk box: management on the AWS control plane, port 22
# closed. Reuse-by-name so a re-run does not trip on an existing role.
ROLE="${NAME}-role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  hvi_log "creating $ROLE"
  aws iam create-role --role-name "$ROLE" \
    --description "coturn TURN relay: SSM management" \
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

# ---- 4. instance (reuse by name) -------------------------------------------
AMI=$(aws ssm get-parameter --region "$R" \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query 'Parameter.Value' --output text)
hvi_log "ubuntu 24.04 arm64 AMI: $AMI"

# A subnet that assigns public IPs, in this VPC. Reuse the asterisk public subnet
# convention if env.local.sh recorded one; otherwise pick any map-public subnet.
# HVI_TURN_SUBNET wins over the shared SUBNET_PUB so the relay can be placed in a
# different AZ when the default one is out of instance capacity (t4g.small in
# ap-south-1a returned InsufficientInstanceCapacity). The relay is stateless and
# public-IP addressed, so its AZ is independent of the media host's.
SUBNET="${HVI_TURN_SUBNET:-${SUBNET_PUB:-}}"
if [ -z "$SUBNET" ]; then
  SUBNET=$(aws ec2 describe-subnets --region "$R" \
    --filters "Name=vpc-id,Values=$VPC" "Name=map-public-ip-on-launch,Values=true" \
    --query 'Subnets[0].SubnetId' --output text 2>/dev/null || echo "None")
fi
if [ -z "$SUBNET" ] || [ "$SUBNET" = "None" ]; then
  hvi_err "no public subnet found in $VPC. Run scripts/70-asterisk.sh (creates"
  hvi_err "public subnets) or set SUBNET_PUB in config/env.local.sh."
  exit 1
fi
hvi_log "public subnet: $SUBNET"

# coturn config is templated at boot from instance metadata + the SSM secret, so
# there is no config to bake in and rotate here. external-ip/relay-ip are set to
# the box's own public IP (the EIP, associated just below) — TURN candidates MUST
# advertise the reachable public address or the browser dials an unroutable one.
cat > /tmp/hvi-turn-userdata.sh <<USERDATA
#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y coturn awscli jq

# The instance's own public IP is what TURN must advertise as its relay address.
PUB=\$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 || true)

# Short-lived REST credential model: coturn validates username/password derived
# from this shared secret, so no static credential is ever stored on the relay.
SECRET=\$(aws ssm get-parameter --region ${R} --name ${SSM_SECRET_NAME} \
  --with-decryption --query 'Parameter.Value' --output text)

cat > /etc/turnserver.conf <<CONF
listening-port=${TURN_PORT}
tls-listening-port=${TURN_TLS_PORT}
min-port=${RELAY_MIN}
max-port=${RELAY_MAX}
fingerprint
use-auth-secret
static-auth-secret=\${SECRET}
realm=hvi.turn
external-ip=\${PUB}
relay-ip=\${PUB}
no-tcp-relay
no-multicast-peers
no-cli
# Do not relay to link-local / private ranges: a TURN relay that will forward to
# 169.254/10/172.16/192.168 is an SSRF pivot into the VPC. Media is public UDP.
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
denied-peer-ip=169.254.0.0-169.254.255.255
CONF

# unset so the secret does not linger in the shell environment
unset SECRET

sed -i 's/^#TURNSERVER_ENABLED=1/TURNSERVER_ENABLED=1/' /etc/default/coturn || true
echo 'TURNSERVER_ENABLED=1' > /etc/default/coturn
systemctl enable coturn
systemctl restart coturn
touch /var/log/hvi-turn-bootstrap-done
USERDATA

EXISTING=$(aws ec2 describe-instances --region "$R" \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' --output text 2>/dev/null || echo "")

if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  hvi_warn "instance $EXISTING already exists; not replacing it"
  IID="$EXISTING"
else
  hvi_log "launching t4g.small $NAME in $SUBNET"
  IID=$(aws ec2 run-instances --region "$R" \
    --image-id "$AMI" \
    --instance-type "${HVI_TURN_INSTANCE_TYPE:-t4g.small}" \
    --subnet-id "$SUBNET" \
    --security-group-ids "$SG" \
    --iam-instance-profile "Name=$ROLE" \
    --associate-public-ip-address \
    --metadata-options "HttpTokens=required" \
    --user-data "file:///tmp/hvi-turn-userdata.sh" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=health-voice-intake}]" \
    --query 'Instances[0].InstanceId' --output text)
  hvi_log "launched $IID"
  hvi_record "turn.instance" "$IID"
fi
rm -f /tmp/hvi-turn-userdata.sh

aws ec2 wait instance-running --region "$R" --instance-ids "$IID"

# ---- elastic IP (stable relay address) -------------------------------------
# TURN advertises this as its relay-ip, so it must not change on stop/start.
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
hvi_log "TURN public IP: $EIP"

# ---- persist for later scripts ---------------------------------------------
touch config/env.local.sh
for kv in "HVI_TURN_INSTANCE_ID=$IID" "HVI_TURN_IP=$EIP" "HVI_TURN_SG=$SG"; do
  key="${kv%%=*}"
  if grep -q "^export ${key}=" config/env.local.sh 2>/dev/null; then
    sed -i.bak "s|^export ${key}=.*|export ${kv}|" config/env.local.sh && rm -f config/env.local.sh.bak
  else
    printf 'export %s\n' "$kv" >> config/env.local.sh
  fi
done

# ---- footer: exactly what to set / run next --------------------------------
# HVI_WEB_ICE_SERVERS format (media/bot_web.py already parses it): comma-separated
# entries of "url|user|credential". For the short-lived REST model the media host
# MINTS user/credential per session from the shared secret; the static entries
# below are the STUN line (no creds) plus a TURN line the host will fill with a
# time-boxed credential. Shown here with placeholders the media wiring replaces.
hvi_log ""
hvi_log "coturn relay up. instance=$IID eip=$EIP region=$R"
hvi_log "bootstrap takes ~1-2 min (apt install coturn); check with:"
hvi_log "  bash scripts/ssm-run.sh 'systemctl is-active coturn; ls -l /var/log/hvi-turn-bootstrap-done' $IID"
hvi_log ""
hvi_log "SET ON THE MEDIA HOST — HVI_WEB_ICE_SERVERS (url|user|credential, comma-sep):"
hvi_log "  stun:${EIP}:${TURN_PORT}|turn:${EIP}:${TURN_PORT}|<mint-per-session>|<hmac-cred>"
hvi_log ""
hvi_log "  STUN needs no credential (address discovery only, moves no audio)."
hvi_log "  TURN user/credential are SHORT-LIVED: the media host derives them from"
hvi_log "  the shared secret in SSM ($SSM_SECRET_NAME):"
hvi_log "    user       = <unix-expiry-epoch>            e.g. now + 3600"
hvi_log "    credential = base64(HMAC-SHA1(secret, user))"
hvi_log "  So no standing credential ever ships to the browser."
hvi_log ""
hvi_log "Read the shared secret (media host / operator only):"
hvi_log "  aws ssm get-parameter --region $R --name $SSM_SECRET_NAME --with-decryption \\"
hvi_log "    --query 'Parameter.Value' --output text"
hvi_log ""
hvi_log "Next: wire HVI_WEB_ICE_SERVERS on the media host, then verify a real call"
hvi_log "relays through THIS box (coturn logs) — not a foreign relay. See docs/WEB_DEPLOY.md."
