#!/bin/bash

# Tear down the VPC infrastructure created by setup-vpc.sh (stops the NAT Gateway cost).
# Reads resource IDs from vpc-config.env and deletes in dependency order.

set -e

if [ ! -f "vpc-config.env" ]; then
    echo "❌ vpc-config.env not found — nothing to clean up."
    exit 1
fi
source vpc-config.env

echo "Tearing down VPC $VPC_ID in $AWS_REGION ..."

if [ -n "$NAT_GW_ID" ]; then
    echo "Deleting NAT Gateway $NAT_GW_ID ..."
    aws ec2 delete-nat-gateway --nat-gateway-id "$NAT_GW_ID" --region "$AWS_REGION" || true
    echo "Waiting for NAT Gateway to delete..."
    aws ec2 wait nat-gateway-deleted --nat-gateway-ids "$NAT_GW_ID" --region "$AWS_REGION" || true
fi

if [ -n "$EIP_ALLOC_ID" ]; then
    echo "Releasing Elastic IP $EIP_ALLOC_ID ..."
    aws ec2 release-address --allocation-id "$EIP_ALLOC_ID" --region "$AWS_REGION" || true
fi

for SUBNET in "$PUBLIC_SUBNET_1" "$PUBLIC_SUBNET_2" "$PRIVATE_SUBNET_1" "$PRIVATE_SUBNET_2"; do
    [ -n "$SUBNET" ] && { echo "Deleting subnet $SUBNET ..."; aws ec2 delete-subnet --subnet-id "$SUBNET" --region "$AWS_REGION" || true; }
done

for RT in "$PUBLIC_RT_ID" "$PRIVATE_RT_ID"; do
    [ -n "$RT" ] && { echo "Deleting route table $RT ..."; aws ec2 delete-route-table --route-table-id "$RT" --region "$AWS_REGION" || true; }
done

if [ -n "$SG_ID" ]; then
    echo "Deleting security group $SG_ID ..."
    aws ec2 delete-security-group --group-id "$SG_ID" --region "$AWS_REGION" || true
fi

if [ -n "$IGW_ID" ]; then
    echo "Detaching and deleting Internet Gateway $IGW_ID ..."
    aws ec2 detach-internet-gateway --internet-gateway-id "$IGW_ID" --vpc-id "$VPC_ID" --region "$AWS_REGION" || true
    aws ec2 delete-internet-gateway --internet-gateway-id "$IGW_ID" --region "$AWS_REGION" || true
fi

if [ -n "$VPC_ID" ]; then
    echo "Deleting VPC $VPC_ID ..."
    aws ec2 delete-vpc --vpc-id "$VPC_ID" --region "$AWS_REGION" || true
fi

mv vpc-config.env "vpc-config.env.deleted.$(date +%s)" 2>/dev/null || true
echo "✅ VPC teardown complete."
