#!/usr/bin/env bash
# Dedicated VPC in DATA_REGION for Neptune and the GPU inference tasks.
#
# ap-south-1 has VPC quota headroom (1 of 5 used), so unlike the earlier
# us-east-1 attempt we create a dedicated VPC rather than borrowing the default.
# That means we can attach gateway endpoints without altering a route table
# shared with unrelated workloads.
#
# Layout
#   - 2 private subnets across 2 AZs (Neptune subnet groups require >= 2 AZs)
#   - 1 public subnet + IGW + NAT so the GPU inference tasks can pull model
#     weights from Hugging Face on first start
#   - Gateway endpoints for DynamoDB and S3 (free, and keeps that traffic off NAT)
#
# Set HVI_SKIP_NAT=true to skip the NAT gateway (~$32/mo) if the inference
# containers will be pre-baked with weights instead of downloading them.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

REGION="$DATA_REGION"
VPC_NAME="${PREFIX}-vpc"
CIDR="10.42.0.0/16"

# ---- VPC --------------------------------------------------------------------
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" \
  --filters "Name=tag:Name,Values=${VPC_NAME}" \
  --query 'Vpcs[0].VpcId' --output text 2>/dev/null || echo "None")

if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
  hvi_log "creating VPC $VPC_NAME ($CIDR) in $REGION"
  VPC_ID=$(aws ec2 create-vpc --cidr-block "$CIDR" --region "$REGION" \
    --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=${VPC_NAME}},{Key=Project,Value=health-voice-intake}]" \
    --query 'Vpc.VpcId' --output text)
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames --region "$REGION"
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support   --region "$REGION"
  hvi_record "vpc.id" "$VPC_ID"
else
  hvi_warn "reusing VPC $VPC_ID"
fi
hvi_log "VPC_ID=$VPC_ID"

# ---- Subnets ----------------------------------------------------------------
AZ1="${REGION}a"
AZ2="${REGION}b"

make_subnet() {
  local az="$1" cidr="$2" name="$3" public="$4" sid
  sid=$(aws ec2 describe-subnets --region "$REGION" \
    --filters "Name=tag:Name,Values=${name}" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'Subnets[0].SubnetId' --output text 2>/dev/null || echo "None")
  if [ "$sid" = "None" ] || [ -z "$sid" ]; then
    sid=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block "$cidr" \
      --availability-zone "$az" --region "$REGION" \
      --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=${name}},{Key=Project,Value=health-voice-intake}]" \
      --query 'Subnet.SubnetId' --output text)
    hvi_log "created subnet $name ($cidr, $az)"
    if [ "$public" = "true" ]; then
      aws ec2 modify-subnet-attribute --subnet-id "$sid" --map-public-ip-on-launch --region "$REGION"
    fi
  else
    hvi_warn "reusing subnet $name"
  fi
  printf '%s' "$sid"
}

SUBNET_A=$(make_subnet "$AZ1" "10.42.1.0/24" "${PREFIX}-private-a" false)
SUBNET_B=$(make_subnet "$AZ2" "10.42.2.0/24" "${PREFIX}-private-b" false)
SUBNET_PUB=$(make_subnet "$AZ1" "10.42.10.0/24" "${PREFIX}-public-a" true)
hvi_record "vpc.subnetA" "$SUBNET_A"
hvi_record "vpc.subnetB" "$SUBNET_B"
hvi_record "vpc.subnetPub" "$SUBNET_PUB"

# ---- Internet gateway -------------------------------------------------------
IGW_ID=$(aws ec2 describe-internet-gateways --region "$REGION" \
  --filters "Name=attachment.vpc-id,Values=${VPC_ID}" \
  --query 'InternetGateways[0].InternetGatewayId' --output text 2>/dev/null || echo "None")
if [ "$IGW_ID" = "None" ] || [ -z "$IGW_ID" ]; then
  hvi_log "creating and attaching internet gateway"
  IGW_ID=$(aws ec2 create-internet-gateway --region "$REGION" \
    --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=${PREFIX}-igw}]" \
    --query 'InternetGateway.InternetGatewayId' --output text)
  aws ec2 attach-internet-gateway --internet-gateway-id "$IGW_ID" --vpc-id "$VPC_ID" --region "$REGION"
else
  hvi_warn "reusing internet gateway $IGW_ID"
fi

# ---- Public route table -----------------------------------------------------
RT_PUB=$(aws ec2 describe-route-tables --region "$REGION" \
  --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:Name,Values=${PREFIX}-rt-public" \
  --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null || echo "None")
if [ "$RT_PUB" = "None" ] || [ -z "$RT_PUB" ]; then
  RT_PUB=$(aws ec2 create-route-table --vpc-id "$VPC_ID" --region "$REGION" \
    --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=${PREFIX}-rt-public}]" \
    --query 'RouteTable.RouteTableId' --output text)
  aws ec2 create-route --route-table-id "$RT_PUB" --destination-cidr-block 0.0.0.0/0 \
    --gateway-id "$IGW_ID" --region "$REGION" >/dev/null
  aws ec2 associate-route-table --route-table-id "$RT_PUB" --subnet-id "$SUBNET_PUB" --region "$REGION" >/dev/null
  hvi_log "created public route table $RT_PUB"
else
  hvi_warn "reusing public route table $RT_PUB"
fi

# ---- Private route table (+ optional NAT) -----------------------------------
RT_PRIV=$(aws ec2 describe-route-tables --region "$REGION" \
  --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:Name,Values=${PREFIX}-rt-private" \
  --query 'RouteTables[0].RouteTableId' --output text 2>/dev/null || echo "None")
if [ "$RT_PRIV" = "None" ] || [ -z "$RT_PRIV" ]; then
  RT_PRIV=$(aws ec2 create-route-table --vpc-id "$VPC_ID" --region "$REGION" \
    --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=${PREFIX}-rt-private}]" \
    --query 'RouteTable.RouteTableId' --output text)
  aws ec2 associate-route-table --route-table-id "$RT_PRIV" --subnet-id "$SUBNET_A" --region "$REGION" >/dev/null
  aws ec2 associate-route-table --route-table-id "$RT_PRIV" --subnet-id "$SUBNET_B" --region "$REGION" >/dev/null
  hvi_log "created private route table $RT_PRIV"
else
  hvi_warn "reusing private route table $RT_PRIV"
fi

if [ "${HVI_SKIP_NAT:-false}" != "true" ]; then
  NAT_ID=$(aws ec2 describe-nat-gateways --region "$REGION" \
    --filter "Name=vpc-id,Values=${VPC_ID}" "Name=state,Values=available,pending" \
    --query 'NatGateways[0].NatGatewayId' --output text 2>/dev/null || echo "None")
  if [ "$NAT_ID" = "None" ] || [ -z "$NAT_ID" ]; then
    hvi_log "allocating EIP and creating NAT gateway (needed to pull model weights)"
    EIP_ALLOC=$(aws ec2 allocate-address --domain vpc --region "$REGION" \
      --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${PREFIX}-nat-eip}]" \
      --query 'AllocationId' --output text)
    NAT_ID=$(aws ec2 create-nat-gateway --subnet-id "$SUBNET_PUB" \
      --allocation-id "$EIP_ALLOC" --region "$REGION" \
      --tag-specifications "ResourceType=natgateway,Tags=[{Key=Name,Value=${PREFIX}-nat}]" \
      --query 'NatGateway.NatGatewayId' --output text)
    hvi_record "vpc.nat" "$NAT_ID"
    hvi_log "NAT $NAT_ID creating; adding default route once available"
    aws ec2 wait nat-gateway-available --nat-gateway-ids "$NAT_ID" --region "$REGION"
    aws ec2 create-route --route-table-id "$RT_PRIV" --destination-cidr-block 0.0.0.0/0 \
      --nat-gateway-id "$NAT_ID" --region "$REGION" >/dev/null
    hvi_log "NAT route added"
  else
    hvi_warn "reusing NAT gateway $NAT_ID"
  fi
else
  hvi_warn "HVI_SKIP_NAT=true - private subnets have no egress"
fi

# ---- Security groups --------------------------------------------------------
get_or_make_sg() {
  local name="$1" desc="$2" sgid
  sgid=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=${name}" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
  if [ "$sgid" = "None" ] || [ -z "$sgid" ]; then
    sgid=$(aws ec2 create-security-group --group-name "$name" --description "$desc" \
      --vpc-id "$VPC_ID" --region "$REGION" --query 'GroupId' --output text)
    hvi_log "created SG $name"
  else
    hvi_warn "reusing SG $name"
  fi
  printf '%s' "$sgid"
}

SG_LAMBDA=$(get_or_make_sg  "${PREFIX}-lambda-sg"  "hvi in-VPC Lambda egress")
SG_NEPTUNE=$(get_or_make_sg "${PREFIX}-neptune-sg" "hvi Neptune ingress")
SG_INFER=$(get_or_make_sg   "${PREFIX}-infer-sg"   "hvi Whisper/OmniVoice inference tasks")
hvi_record "vpc.sgLambda"  "$SG_LAMBDA"
hvi_record "vpc.sgNeptune" "$SG_NEPTUNE"
hvi_record "vpc.sgInfer"   "$SG_INFER"

allow() {
  local target="$1" port="$2" source_sg="$3" err
  if err=$(aws ec2 authorize-security-group-ingress --group-id "$target" \
        --protocol tcp --port "$port" --source-group "$source_sg" \
        --region "$REGION" 2>&1 >/dev/null); then
    hvi_log "allowed $source_sg -> $target on tcp/$port"
  elif printf '%s' "$err" | grep -q 'InvalidPermission.Duplicate'; then
    hvi_warn "rule $source_sg -> $target tcp/$port already present"
  else
    hvi_err "failed: $err"; exit 1
  fi
}

allow "$SG_NEPTUNE" 8182 "$SG_LAMBDA"    # Gremlin
allow "$SG_INFER"   8080 "$SG_LAMBDA"    # STT/TTS HTTP
allow "$SG_NEPTUNE" 8182 "$SG_INFER"     # agent tools may query the graph

# ---- Free gateway endpoints (our own route table, so no shared blast radius) -
for svc in dynamodb s3; do
  existing=$(aws ec2 describe-vpc-endpoints --region "$REGION" \
    --filters "Name=vpc-id,Values=${VPC_ID}" "Name=service-name,Values=com.amazonaws.${REGION}.${svc}" \
    --query 'VpcEndpoints[0].VpcEndpointId' --output text 2>/dev/null || echo "None")
  if [ "$existing" = "None" ] || [ -z "$existing" ]; then
    hvi_log "creating gateway endpoint for $svc"
    aws ec2 create-vpc-endpoint --vpc-id "$VPC_ID" \
      --service-name "com.amazonaws.${REGION}.${svc}" \
      --route-table-ids "$RT_PRIV" --region "$REGION" \
      --tag-specifications "ResourceType=vpc-endpoint,Tags=[{Key=Name,Value=${PREFIX}-${svc}-ep}]" \
      --query 'VpcEndpoint.VpcEndpointId' --output text >/dev/null
  else
    hvi_warn "gateway endpoint for $svc exists"
  fi
done

# ---- Neptune subnet group ---------------------------------------------------
SUBNET_GROUP="${PREFIX}-neptune-subnets"
if aws neptune describe-db-subnet-groups --db-subnet-group-name "$SUBNET_GROUP" \
     --region "$REGION" >/dev/null 2>&1; then
  hvi_warn "Neptune subnet group exists"
else
  hvi_log "creating Neptune subnet group"
  aws neptune create-db-subnet-group \
    --db-subnet-group-name "$SUBNET_GROUP" \
    --db-subnet-group-description "hvi Neptune private subnets" \
    --subnet-ids "$SUBNET_A" "$SUBNET_B" \
    --region "$REGION" >/dev/null
fi

cat > config/env.local.sh <<EOF
# Generated by scripts/20-vpc.sh - do not edit by hand.
export VPC_ID="$VPC_ID"
export SUBNET_A="$SUBNET_A"
export SUBNET_B="$SUBNET_B"
export SUBNET_PUB="$SUBNET_PUB"
export SG_LAMBDA="$SG_LAMBDA"
export SG_NEPTUNE="$SG_NEPTUNE"
export SG_INFER="$SG_INFER"
export RT_PRIVATE="$RT_PRIV"
export NEPTUNE_SUBNET_GROUP="$SUBNET_GROUP"
EOF

hvi_log "wrote config/env.local.sh"
cat config/env.local.sh
