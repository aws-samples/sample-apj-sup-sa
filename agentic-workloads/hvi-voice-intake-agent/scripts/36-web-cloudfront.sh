#!/usr/bin/env bash
# CloudFront front for the WebRTC browser surface (media/bot_web.py). REAL path.
#
#   browser (HTTPS) -> CloudFront (TLS) -> EC2 :8095 -> bot_web /api/offer + page
#   browser <=== WebRTC audio (UDP/SRTP) ===> EC2, DIRECT via the in-region TURN
#                                             relay from scripts/35-turn.sh
#
# WHAT CLOUDFRONT DOES HERE, AND WHAT IT DOES NOT
# It fronts only the SIGNALLING and the static page: GET / (index.html, app.js),
# GET /api/health, and POST /api/offer (the SDP exchange). TLS is the whole point
# - getUserMedia refuses the mic without a secure context, and CloudFront brings
# HTTPS without a certificate on the box. It CANNOT carry the media: WebRTC audio
# is UDP (ICE/DTLS/SRTP) negotiated peer-to-peer, and CloudFront proxies TCP only.
# The audio therefore goes DIRECT to the instance via the coturn relay stood up by
# scripts/35-turn.sh, which is why that relay must be in-region. This is the
# difference from scripts/75-ws-test.sh, whose WebSocket audio IS TCP and so does
# ride CloudFront end to end. See docs/WEB_DEPLOY.md.
#
# WHY THIS ALSO CREATES A SERVICE
# Unlike the WS bridge, bot_web.py has no deployed systemd unit yet - it has only
# ever run locally via scripts/78-web-local.sh. So this script also installs an
# hvi-web service on the media host, running media.bot_web with the WebRTC + auth
# environment (HVI_WEB_AUTH=cognito and the Cognito vars printed by 34-cognito.sh,
# plus the HVI_WEB_ICE_SERVERS printed by 35-turn.sh), before fronting it.
#
# ORDER: run 34-cognito.sh and 35-turn.sh FIRST, put their printed values into
# /etc/hvi/web.env on the host (this script scaffolds that file if absent), then
# run this. A misconfigured pool makes bot_web exit at startup by design.
#
# Usage
#   bash scripts/36-web-cloudfront.sh          # prints the HTTPS page URL
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh
R="$DATA_REGION"

# Residency: the media host and its front live in-region beside the PHI. A
# distribution is global by nature, but its ORIGIN and the audio path stay here.
if [ "$R" != "ap-south-1" ]; then
  hvi_err "DATA_REGION is '$R', not ap-south-1. The WebRTC surface serves PHI"
  hvi_err "and its origin/media must stay in-region. Refusing."
  exit 1
fi

WEB_PORT="${HVI_WEB_PORT:-8095}"

# ---- find the media host ----------------------------------------------------
IID=$(aws ec2 describe-instances --region "$R" \
  --filters "Name=tag:Name,Values=${PREFIX}-asterisk" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[0].InstanceId' --output text)
if [ -z "$IID" ] || [ "$IID" = "None" ]; then
  hvi_err "no running ${PREFIX}-asterisk media host found in $R"
  exit 1
fi
EIP=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
VPC=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].VpcId' --output text)
hvi_log "media host $IID at $EIP (vpc $VPC), web port $WEB_PORT"

# ---- security group: WEB_PORT from CloudFront origin-facing IPs only ---------
# Same reasoning as 75-ws-test.sh: a SEPARATE group so the CloudFront prefix list
# (~55 rules at full weight) does not blow the rules-per-group quota on the main
# group that already carries the Chime SIP and media ranges.
PL=$(aws ec2 describe-managed-prefix-lists --region "$R" \
  --filters "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing" \
  --query 'PrefixLists[0].PrefixListId' --output text)
WEBSG=$(aws ec2 describe-security-groups --region "$R" \
  --filters "Name=group-name,Values=${PREFIX}-web-sg" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ -z "$WEBSG" ] || [ "$WEBSG" = "None" ]; then
  WEBSG=$(aws ec2 create-security-group --region "$R" --vpc-id "$VPC" \
    --group-name "${PREFIX}-web-sg" \
    --description "CloudFront origin to bot_web WebRTC signalling" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$WEBSG" \
    --ip-permissions "IpProtocol=tcp,FromPort=${WEB_PORT},ToPort=${WEB_PORT},PrefixListIds=[{PrefixListId=$PL,Description=cloudfront-web}]" >/dev/null
  hvi_log "created $WEBSG with ${WEB_PORT} from CloudFront prefix list $PL"
else
  hvi_log "reusing $WEBSG"
fi
CURRENT=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].SecurityGroups[].GroupId' --output text)
if ! grep -q "$WEBSG" <<<"$CURRENT"; then
  # shellcheck disable=SC2086
  aws ec2 modify-instance-attribute --region "$R" --instance-id "$IID" \
    --groups $CURRENT "$WEBSG"
  hvi_log "attached $WEBSG to $IID"
fi

# ---- hvi-web systemd service on the host ------------------------------------
# Scaffolds /etc/hvi/web.env if absent so the box does not silently serve with no
# auth. The operator fills in the Cognito + ICE values from 34/35 before this
# serves for real; bot_web.py fails fast at startup if HVI_WEB_AUTH=cognito but
# the pool env is incomplete, which is the behaviour we want here.
bash scripts/ssm-run.sh "
  set -e
  mkdir -p /etc/hvi
  if [ ! -f /etc/hvi/web.env ]; then
    cat > /etc/hvi/web.env <<ENV
# Fill from scripts/34-cognito.sh and scripts/35-turn.sh, then: systemctl restart hvi-web
HVI_WEB_AUTH=cognito
HVI_COGNITO_REGION=${R}
HVI_COGNITO_USER_POOL_ID=
HVI_COGNITO_APP_CLIENT_ID=
HVI_WEB_ICE_SERVERS=
HVI_WEB_PORT=${WEB_PORT}
ENV
    chmod 600 /etc/hvi/web.env
  fi
  cat > /etc/systemd/system/hvi-web.service <<UNIT
[Unit]
Description=HVI browser WebRTC surface (bot_web)
After=hvi-media.service
Requires=hvi-media.service

[Service]
User=hvi
Group=hvi
EnvironmentFile=/etc/hvi/media.env
EnvironmentFile=/etc/hvi/web.env
WorkingDirectory=/opt/hvi
# Bind ALL interfaces, not loopback: CloudFront reaches bot_web on the box's
# PUBLIC IP:${WEB_PORT}, so 127.0.0.1 would be unreachable and every request
# would 502. Exposure is bounded by ${PREFIX}-web-sg, which admits only the
# CloudFront origin-facing prefix list on this port; /api/offer is token-gated.
ExecStart=/opt/hvi/venv/bin/python -m media.bot_web --host 0.0.0.0 --port ${WEB_PORT}
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/hvi /var/lib/hvi

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now hvi-web
  sleep 2
  systemctl is-active hvi-web
" | tail -3

# ---- CloudFront distribution (idempotent by comment tag) ---------------------
EXISTING=$(aws cloudfront list-distributions --query \
  "DistributionList.Items[?Comment=='hvi-web'].DomainName | [0]" --output text 2>/dev/null || true)
if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  DOMAIN="$EXISTING"
  hvi_log "reusing CloudFront distribution $DOMAIN"
else
  hvi_log "creating CloudFront distribution (a few minutes to deploy)"
  # CachingDisabled (4135ea2d) so /api/offer and the RTVI responses are never
  # cached; AllViewer (216adef6) so the POST body and headers reach the origin.
  # http-only to the origin is acceptable because WEB_PORT admits CloudFront IPs
  # only; the browser<->CloudFront hop is TLS (redirect-to-https).
  DOMAIN=$(aws cloudfront create-distribution --distribution-config "{
    \"CallerReference\": \"hvi-web-$(date +%s)\",
    \"Comment\": \"hvi-web\",
    \"Enabled\": true,
    \"Origins\": {\"Quantity\": 1, \"Items\": [{
      \"Id\": \"media-host\", \"DomainName\": \"ec2-${EIP//./-}.${R}.compute.amazonaws.com\",
      \"CustomOriginConfig\": {\"HTTPPort\": ${WEB_PORT}, \"HTTPSPort\": 443,
        \"OriginProtocolPolicy\": \"http-only\",
        \"OriginReadTimeout\": 60, \"OriginKeepaliveTimeout\": 60}}]},
    \"DefaultCacheBehavior\": {
      \"TargetOriginId\": \"media-host\",
      \"ViewerProtocolPolicy\": \"redirect-to-https\",
      \"AllowedMethods\": {\"Quantity\": 7,
        \"Items\": [\"GET\",\"HEAD\",\"OPTIONS\",\"PUT\",\"POST\",\"PATCH\",\"DELETE\"],
        \"CachedMethods\": {\"Quantity\": 2, \"Items\": [\"GET\",\"HEAD\"]}},
      \"CachePolicyId\": \"4135ea2d-6df8-44a3-9df3-4b5a84be39ad\",
      \"OriginRequestPolicyId\": \"216adef6-5c7f-47e4-b989-5492eafa07d3\"}
  }" --query 'Distribution.DomainName' --output text)
  hvi_log "created $DOMAIN"
fi

hvi_log ""
hvi_log "WEBRTC PAGE (allow a few minutes on first deploy):"
hvi_log "  https://${DOMAIN}/"
hvi_log ""
hvi_log "BEFORE IT WORKS END TO END:"
hvi_log "  1. Fill /etc/hvi/web.env on the host with the Cognito + ICE values from"
hvi_log "     scripts/34-cognito.sh and scripts/35-turn.sh, then:"
hvi_log "       sudo systemctl restart hvi-web"
hvi_log "  2. Set the Cognito redirectUri / callback to https://${DOMAIN}/ and"
hvi_log "     re-run scripts/34-cognito.sh (HVI_WEB_CALLBACK_URL=https://${DOMAIN}/)."
hvi_log "  3. Inject window.HVI_AUTH into the page with domain/clientId/region and"
hvi_log "     redirectUri=https://${DOMAIN}/ (see docs/WEB_DEPLOY.md)."
hvi_log ""
hvi_log "Media (audio) does NOT flow through CloudFront - it is UDP direct via the"
hvi_log "in-region TURN relay from scripts/35-turn.sh. Confirm in coturn's logs."
