#!/usr/bin/env bash
# Configure Asterisk as a SIP trunk to the Chime Voice Connector, bridging call
# audio to the agent over AudioSocket.
#
# Written over SSM rather than baked into user-data so the trunk can be retuned
# without replacing the instance - SIP config is exactly the thing you iterate on.
#
# THE EC2 NAT TRAP
# ----------------
# An EC2 instance with an Elastic IP does NOT see that address on its interface;
# it sees a private VPC address, and the EIP is translated at the edge. Asterisk
# writes whatever it believes its address to be into the SIP Contact header and
# the SDP connection line. Left alone it advertises 10.42.x.x, Chime dutifully
# sends RTP there, and the call connects with total silence in one or both
# directions - the classic one-way-audio symptom that looks like a codec fault.
#
# external_signaling_address and external_media_address override that with the
# EIP, and local_net tells Asterisk which destinations are genuinely internal so
# it does not rewrite addresses for traffic that never leaves the VPC.
#
# WHY IP AUTHENTICATION AND NOT A PASSWORD
# ----------------------------------------
# Chime Voice Connector trunks authenticate by source IP: our EIP sits in the
# termination CidrAllowedList, and inbound INVITEs arrive from Chime's published
# signalling range. There are no SIP credentials to configure, so the pjsip
# 'identify' section matching Chime's CIDR is what associates an inbound call
# with the endpoint.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
VC_ID="${VOICE_CONNECTOR_ID:-abcdefghij0123456789}"

# Required, not defaulted. It becomes from_user on the chime endpoint, and an
# empty From is what makes Chime answer every outbound INVITE with 486 Busy Here.
# Failing here with a clear message beats writing a config that looks fine and
# rejects every call.
: "${OUTBOUND_CALLER_ID:?set OUTBOUND_CALLER_ID to a DID assigned to the Voice Connector, e.g. +12025550143}"

IID="${ASTERISK_INSTANCE_ID:-}"
if [ -z "$IID" ]; then
  IID=$(aws ec2 describe-instances --region "$R" \
    --filters "Name=tag:Name,Values=${PREFIX}-asterisk" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[0].InstanceId' --output text)
fi

EIP=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
PRIVATE_IP=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)
VPC_CIDR=$(aws ec2 describe-vpcs --region "$R" --vpc-ids "${VPC_ID}" \
  --query 'Vpcs[0].CidrBlock' --output text)
VC_HOST=$(aws chime-sdk-voice get-voice-connector --voice-connector-id "$VC_ID" \
  --region "$TELEPHONY_REGION" --query 'VoiceConnector.OutboundHostName' --output text)

hvi_log "instance    $IID"
hvi_log "public IP   $EIP"
hvi_log "private IP  $PRIVATE_IP"
hvi_log "VPC CIDR    $VPC_CIDR"
hvi_log "VC host     $VC_HOST"

# AudioSocket server: the Pipecat bot runs on this same instance, so call audio
# never leaves the box. Only the per-turn agent call goes over the network, and
# that is request/response rather than streaming media.
AUDIOSOCKET_PORT="${AUDIOSOCKET_PORT:-8090}"

# --- pjsip.conf -------------------------------------------------------------
cat > /tmp/hvi-pjsip.conf <<PJSIP
; Managed by scripts/71-asterisk-config.sh - edits here are overwritten.

[global]
type=global
; IP-based identification first: Chime trunks carry no SIP credentials, so
; matching on source address is what associates an INVITE with the endpoint.
endpoint_identifier_order=ip,username
; Keep our address out of the User-Agent advertised to the carrier.
user_agent=hvi-intake

[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
; See the header comment: without these two the SDP advertises the private
; address and the call is silent.
external_signaling_address=${EIP}
external_media_address=${EIP}
local_net=${VPC_CIDR}

[chime-aor]
type=aor
contact=sip:${VC_HOST}:5060
qualify_frequency=60

[chime]
type=endpoint
context=from-chime
aors=chime-aor
; G.711 only. The PSTN leg is 8kHz narrowband regardless, so offering wideband
; codecs invites a transcode that costs latency and buys nothing.
disallow=all
allow=ulaw,alaw
; direct_media=no keeps RTP flowing through Asterisk rather than being
; renegotiated end to end. We need the media in our hands - that is the entire
; reason this box exists.
direct_media=no
; Carrier-side NAT hygiene: answer RTP to wherever it actually came from, and
; reply signalling to the source port rather than whatever the header claims.
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=rfc4733
from_domain=${EIP}
; OUTBOUND CALLER ID, and the reason this line is load-bearing.
;
; Without from_user, PJSIP builds the From header as
;   From: "Anonymous" <sip:anonymous@${EIP}>
; and Chime answers the INVITE with "SIP/2.0 486 Busy Here" - which reads like the
; callee is on another call and is nothing of the kind. Chime requires the From to
; be a number provisioned to this Voice Connector before it will originate to the
; PSTN, and it refuses an anonymous From.
;
; That failure was also invisible from the CLI: 'channel originate' exited 0,
; printed nothing, created no channel, and the 486 only appeared with
; 'pjsip set logger on'. Setting it on the endpoint rather than per call is
; deliberate - there is one Voice Connector and one outbound DID, so a channel
; variable would be another place for it to go missing.
; from_user ONLY. Deliberately NOT `callerid`.
;
; from_user sets the user part of the outgoing From URI, which is the one thing
; Chime needs. `callerid` looks like it does the same job but applies to channels
; for this endpoint in BOTH directions, so it also overwrites the caller ID of
; INBOUND calls with our own number.
;
; That cost a debugging cycle. With callerid set, an inbound caller was identified
; as +18005550199 - our own toll-free DID - which is seeded in the CRM as
; CUST-1002 Rajesh Kumar, preferred language hi-IN. So the agent answered a call
; from a US number by greeting a different customer by name, in Hindi, and reading
; out that customer's policy number. A caller-ID setting for outbound silently
; became a data-disclosure bug on inbound.
from_user=${OUTBOUND_CALLER_ID}

[chime-identify]
type=identify
endpoint=chime
; Chime SDK Voice Connector SIP signalling range for us-east-1.
match=3.80.16.0/23

; --------------------------------------------------------------------------
; OUTBOUND-ONLY ENDPOINT. Same trunk, second endpoint, and the split is the
; whole point.
;
; The endpoint above deliberately carries from_user and NOT callerid, because
; callerid applies to channels in BOTH directions and overwrote the caller ID of
; inbound calls - the data-disclosure bug described at length above. That fix left
; outbound presenting:
;
;   From: "Anonymous" <sip:+1XXXXXXXXXX@EIP>
;
; The number is right, so Chime originates it: the INVITE gets 100 Trying and then
; 180 Ringing. The destination then rejected it with 486 Busy Here in the SAME
; SECOND as the 180 - too fast to be a human declining, and Indian operators
; commonly refuse anonymous international calls to mobiles. Presenting "Anonymous"
; on a health-insurance call is also wrong on its own terms; TRAI expects
; identifiable CLI on a commercial call.
;
; So the caller ID lives on an endpoint that ONLY ever originates.
;
; WHY INBOUND CANNOT LAND HERE, which is the safety property that matters:
;   - endpoint_identifier_order=ip,username (see [global]).
;   - IP matching happens first and is driven by `identify` sections. There is
;     exactly one, chime-identify, and it names endpoint=chime. This endpoint has
;     no identify section, so no source address can select it.
;   - Username matching, the fallback, compares the endpoint NAME against the user
;     part of the incoming From. That is the caller's phone number. It is never the
;     literal string "chime-out".
; Adding an identify section here, or renaming this endpoint to a phone number,
; would reintroduce the disclosure bug. tests/test_asterisk_trunk_config.py fails
; if either happens.
[chime-out-aor]
type=aor
contact=sip:${VC_HOST}:5060
qualify_frequency=60

[chime-out]
type=endpoint
; Never used - nothing inbound reaches this endpoint - but an endpoint must name a
; context, and pointing it at the real one keeps a typo from being the thing that
; matters if that ever changes.
context=from-chime
aors=chime-out-aor
disallow=all
allow=ulaw,alaw
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=rfc4733
from_domain=${EIP}
from_user=${OUTBOUND_CALLER_ID}
; The one line this endpoint exists for. Number AND display name, so the From
; header carries an identifiable originator instead of "Anonymous".
callerid=${INSURER_NAME:-Meridian Health Cover} <${OUTBOUND_CALLER_ID}>
PJSIP

# --- extensions.conf --------------------------------------------------------
cat > /tmp/hvi-extensions.conf <<DIALPLAN
; Managed by scripts/71-asterisk-config.sh - edits here are overwritten.

[globals]
AUDIOSOCKET_HOST=127.0.0.1
AUDIOSOCKET_PORT=${AUDIOSOCKET_PORT}

; Inbound from the carrier. Every call is handed straight to the agent's
; AudioSocket server; there is no IVR and no menu, because the agent drives the
; whole conversation.
;
; PATTERN NOTE, and this one cost a test call: Asterisk's X matches a DIGIT
; (0-9) only, so _X. does NOT match an E.164 number. Chime presents the dialled
; number with the leading plus - '+18005550199' - so every inbound INVITE was
; rejected with "extension not found in context 'from-chime'" while the caller
; just heard ringing. The character class accepts both the E.164 form and a bare
; national form, since carriers differ on this and Tata may well present digits
; only.
[from-chime]
exten => _[+0-9].,1,NoOp(hvi inbound: \${CALLERID(num)} -> \${EXTEN})
 same => n,Set(CHANNEL(hangup_handler_push)=hvi-hangup,s,1)
 same => n,Answer()
; AudioSocket carries 8kHz signed-linear mono. Asking Asterisk for slin here
; means the transcode from ulaw happens once, on this box, instead of the agent
; having to decode G.711 itself.
 same => n,Set(CHANNEL(audioreadformat)=slin)
 same => n,Set(CHANNEL(audiowriteformat)=slin)
; The UUID is how the agent correlates this media session with call state in
; DynamoDB, which is what makes resume-after-drop possible.
 same => n,Set(CALLUUID=\${SHELL(uuidgen | tr -d '\n')})
 same => n,Verbose(0, hvi audiosocket session \${CALLUUID} from \${CALLERID(num)})
; Hand the caller's number to the media process. AudioSocket carries a UUID and
; nothing else, and the orchestrator needs the number to create or resume this
; caller's session. Shared directory rather than /tmp because the media unit runs
; with PrivateTmp=true and would never see a file written to Asterisk's /tmp.
 same => n,System(printf '%s' "\${CALLERID(num)}" > /var/lib/hvi/calls/\${CALLUUID})
 same => n,AudioSocket(\${CALLUUID},\${AUDIOSOCKET_HOST}:\${AUDIOSOCKET_PORT})
 same => n,Hangup()

; Outbound intake call, answered. THIS IS THE PRODUCT PATH: a new CRM record
; means a customer bought a policy, and the dialler rings them within the 48h
; SLA. The inbound context above exists for callbacks and for testing.
;
; The dialler has ALREADY created the session and left a ticket carrying the
; callId at /var/lib/hvi/pending/<e164>. The extension here IS the destination
; number - that is deliberate, it is how the ticket is found - so renaming
; pending/<number> to calls/<uuid> hands the callId to the media pipeline. The
; rename is atomic, so a stale ticket cannot be picked up by a later call, and if
; the ticket is missing the bot will say so rather than silently treating an
; outbound call as an unidentified inbound one.
[hvi-outbound]
exten => _[+0-9].,1,NoOp(hvi outbound answered by \${EXTEN})
 same => n,Set(CHANNEL(hangup_handler_push)=hvi-hangup,s,1)
 same => n,Answer()
 same => n,Set(CHANNEL(audioreadformat)=slin)
 same => n,Set(CHANNEL(audiowriteformat)=slin)
 same => n,Set(CALLUUID=\${SHELL(uuidgen | tr -d '\n')})
 same => n,Verbose(0, hvi outbound session \${CALLUUID} to \${EXTEN})
 same => n,System(mv /var/lib/hvi/pending/\${EXTEN} /var/lib/hvi/calls/\${CALLUUID})
 same => n,AudioSocket(\${CALLUUID},\${AUDIOSOCKET_HOST}:\${AUDIOSOCKET_PORT})
 same => n,Hangup()

; Plain outbound trunk, no intake attached. Kept for connectivity testing.
; Same pattern fix as inbound: we dial E.164 (+91...) so the leading plus must
; match, and _X. would silently never fire.
[to-chime]
exten => _[+0-9].,1,NoOp(hvi outbound to \${EXTEN})
 same => n,Set(CALLERID(num)=\${OUTBOUND_CALLERID})
 same => n,Dial(PJSIP/\${EXTEN}@chime,60)
 same => n,Hangup()

; Hangup handler: fires on every call teardown, including the caller simply
; dropping. This is the hook that lets a mid-protocol disconnect be recorded as
; resumable rather than lost.
[hvi-hangup]
exten => s,1,NoOp(hvi hangup: \${CALLUUID} cause=\${HANGUPCAUSE})
; Drop the caller-id handoff file. Per-call state with no reason to outlive the
; call, and leaving it would accumulate one small file per call forever.
 same => n,System(rm -f /var/lib/hvi/calls/\${CALLUUID})
 same => n,Return()
DIALPLAN

# --- rtp.conf ---------------------------------------------------------------
# The port range must match the security group, which is narrow deliberately:
# every open UDP port on a public SIP host is attack surface.
cat > /tmp/hvi-rtp.conf <<'RTPCONF'
; Managed by scripts/71-asterisk-config.sh - edits here are overwritten.
[general]
rtpstart=10000
rtpend=10500
; Send RTP back to the address packets actually arrive from. Carriers commonly
; sit behind their own NAT and the SDP address is not always reachable.
strictrtp=yes
RTPCONF

# --- push over SSM ----------------------------------------------------------
# Base64 so the payloads survive the SSM parameter round trip intact; quoting
# raw SIP config through JSON is a reliable way to corrupt it.
PJSIP_B64=$(base64 < /tmp/hvi-pjsip.conf | tr -d '\n')
DIALPLAN_B64=$(base64 < /tmp/hvi-extensions.conf | tr -d '\n')
RTP_B64=$(base64 < /tmp/hvi-rtp.conf | tr -d '\n')
rm -f /tmp/hvi-pjsip.conf /tmp/hvi-extensions.conf /tmp/hvi-rtp.conf

hvi_log "writing Asterisk config over SSM"
bash scripts/ssm-run.sh "
set -e
echo '$PJSIP_B64'    | base64 -d > /etc/asterisk/pjsip.conf
echo '$DIALPLAN_B64' | base64 -d > /etc/asterisk/extensions.conf
echo '$RTP_B64'      | base64 -d > /etc/asterisk/rtp.conf
chown asterisk:asterisk /etc/asterisk/pjsip.conf /etc/asterisk/extensions.conf /etc/asterisk/rtp.conf
systemctl restart asterisk
sleep 4
echo '--- transport ---'
asterisk -rx 'pjsip show transports'
echo '--- endpoint ---'
asterisk -rx 'pjsip show endpoint chime' | head -20
echo '--- dialplan ---'
asterisk -rx 'dialplan show from-chime'
" "$IID"

hvi_log ""
hvi_log "Asterisk configured. Chime -> $EIP:5060/UDP -> AudioSocket 127.0.0.1:$AUDIOSOCKET_PORT"
hvi_log "next: run the Pipecat bot on the instance, then place a test call"
