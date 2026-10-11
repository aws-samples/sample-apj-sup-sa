#!/usr/bin/env bash
# Browser test harness on the media host, fronted by CloudFront. TESTING ONLY.
#
#   browser (https, mic allowed) -> CloudFront (TLS/WSS) -> EC2 :8791 -> AudioSocket
#
# THIS IS THE WEBSOCKET BRIDGE, not the WebRTC page. It matters: a WebSocket is
# TCP, so CloudFront proxies the audio end to end and no STUN/TURN or UDP ingress
# is needed. The WebRTC surface (media/bot_web.py, web/app.js) is different -
# there CloudFront can carry only the HTTPS signalling and the SRTP audio is UDP
# peer-to-peer, so it needs an in-region TURN relay. That path is planned in
# docs/WEB_DEPLOY.md; this script does NOT deploy it.
#
# CloudFront rather than API Gateway: APIGW WebSocket APIs invoke a Lambda per
# message, and duplex telephony audio is ~100 messages/second per call - the
# wrong integration shape. CloudFront proxies the byte stream as-is and brings
# the HTTPS the browser requires before it will grant microphone access.
#
# Security posture:
#   - port 8791 admits ONLY CloudFront's origin-facing IPs (managed prefix list)
#   - the bridge itself 403s anything without the shared token
#   - the token is generated here once and stored in /etc/hvi/ws-test.env
#
# Usage
#   bash scripts/75-ws-test.sh          # prints the URL with token
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh
R="$DATA_REGION"

IID=$(aws ec2 describe-instances --region "$R" \
  --filters "Name=tag:Name,Values=${PREFIX}-asterisk" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[0].InstanceId' --output text)
EIP=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
SG=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' --output text)
hvi_log "media host $IID at $EIP (sg $SG)"

# ---- security group: 8791 from CloudFront origin-facing only ----------------
# A SEPARATE group, attached to the instance alongside the main one. The
# CloudFront origin-facing prefix list counts against the rules-per-group quota
# at its full weight (~55 entries), and the main group already carries the
# Chime SIP and media ranges - adding it there exceeded the limit. Instances
# take multiple groups precisely for this.
PL=$(aws ec2 describe-managed-prefix-lists --region "$R" \
  --filters "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing" \
  --query 'PrefixLists[0].PrefixListId' --output text)
VPC=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].VpcId' --output text)
WSSG=$(aws ec2 describe-security-groups --region "$R" \
  --filters "Name=group-name,Values=${PREFIX}-ws-test-sg" "Name=vpc-id,Values=$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ -z "$WSSG" ] || [ "$WSSG" = "None" ]; then
  WSSG=$(aws ec2 create-security-group --region "$R" --vpc-id "$VPC" \
    --group-name "${PREFIX}-ws-test-sg" \
    --description "TEST ONLY: CloudFront to ws test bridge" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --region "$R" --group-id "$WSSG" \
    --ip-permissions "IpProtocol=tcp,FromPort=8791,ToPort=8791,PrefixListIds=[{PrefixListId=$PL,Description=cloudfront-ws-test}]" >/dev/null
  hvi_log "created $WSSG with 8791 from CloudFront prefix list $PL"
else
  hvi_log "reusing $WSSG"
fi
CURRENT=$(aws ec2 describe-instances --region "$R" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].SecurityGroups[].GroupId' --output text)
if ! grep -q "$WSSG" <<<"$CURRENT"; then
  # shellcheck disable=SC2086
  aws ec2 modify-instance-attribute --region "$R" --instance-id "$IID" \
    --groups $CURRENT "$WSSG"
  hvi_log "attached $WSSG to $IID"
fi

# ---- shared token, generated once on the host -------------------------------
bash scripts/ssm-run.sh '
  set -e
  mkdir -p /etc/hvi
  if [ ! -f /etc/hvi/ws-test.env ]; then
    TOKEN=$(head -c 24 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 32)
    printf "WS_TEST_TOKEN=%s\nWS_TEST_PHONE=+911234567890\n" "$TOKEN" > /etc/hvi/ws-test.env
    chmod 600 /etc/hvi/ws-test.env
  fi
  cat > /etc/systemd/system/hvi-ws-test.service <<UNIT
[Unit]
Description=HVI browser test bridge (TEST ONLY - not the call path)
After=hvi-media.service
Requires=hvi-media.service

[Service]
User=hvi
Group=hvi
EnvironmentFile=/etc/hvi/media.env
EnvironmentFile=/etc/hvi/ws-test.env
WorkingDirectory=/opt/hvi
ExecStart=/opt/hvi/venv/bin/python -m media.ws_test_bridge
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
  systemctl enable --now hvi-ws-test
  sleep 2
  systemctl is-active hvi-ws-test
' | tail -3

# ---- CloudFront distribution (idempotent by comment tag) ---------------------
EXISTING=$(aws cloudfront list-distributions --query \
  "DistributionList.Items[?Comment=='hvi-ws-test'].DomainName | [0]" --output text 2>/dev/null || true)
if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  DOMAIN="$EXISTING"
  hvi_log "reusing CloudFront distribution $DOMAIN"
else
  hvi_log "creating CloudFront distribution (takes a few minutes to deploy)"
  # CachingDisabled + AllViewer managed policies: WebSockets must not be cached
  # and the token query string has to reach the origin.
  DOMAIN=$(aws cloudfront create-distribution --distribution-config "{
    \"CallerReference\": \"hvi-ws-test-$(date +%s)\",
    \"Comment\": \"hvi-ws-test\",
    \"Enabled\": true,
    \"Origins\": {\"Quantity\": 1, \"Items\": [{
      \"Id\": \"media-host\", \"DomainName\": \"ec2-${EIP//./-}.${R}.compute.amazonaws.com\",
      \"CustomOriginConfig\": {\"HTTPPort\": 8791, \"HTTPSPort\": 443,
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

# grep on this side, not inside the SSM command: ssm-run wraps output in status
# lines, and `tail -1` was grabbing one of those - the first run printed a URL
# with an EMPTY token, which 403s and looks like a broken deployment.
TOKEN=$(bash scripts/ssm-run.sh 'cat /etc/hvi/ws-test.env' | grep WS_TEST_TOKEN | cut -d= -f2)
hvi_log ""
hvi_log "TEST PAGE (allow a few minutes on first deploy):"
hvi_log "  https://${DOMAIN}/?token=${TOKEN}"
hvi_log ""
hvi_log "Sessions arrive as +911234567890 (the seeded demo customer)."
hvi_log "This harness is for iteration; sign-off remains a real phone call."
