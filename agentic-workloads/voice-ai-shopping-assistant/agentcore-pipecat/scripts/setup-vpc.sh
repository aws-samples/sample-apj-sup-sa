#!/bin/bash

# Create VPC infrastructure for AgentCore Runtime (VPC mode required for Daily's UDP).
# Creates VPC with public/private subnets across 2 AZs, a NAT gateway, and a security group.
# NAT Gateway + Elastic IP incur ongoing cost (~$32/month). Run cleanup-vpc.sh to tear down.

set -e

if [ ! -f "./agent/.env" ]; then
    echo "❌ Error: agent/.env not found"
    exit 1
fi

echo "Loading environment variables..."
set -a
source ./agent/.env
set +a

echo ""
echo "Creating VPC..."
VPC_ID=$(aws ec2 create-vpc \
    --cidr-block 10.0.0.0/16 \
    --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=agentcore-aisle-vpc}]' \
    --region "$AWS_REGION" --query 'Vpc.VpcId' --output text)
echo "✅ VPC created: $VPC_ID"

aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames --region "$AWS_REGION"

echo ""
echo "Creating Internet Gateway..."
IGW_ID=$(aws ec2 create-internet-gateway \
    --tag-specifications 'ResourceType=internet-gateway,Tags=[{Key=Name,Value=agentcore-aisle-igw}]' \
    --region "$AWS_REGION" --query 'InternetGateway.InternetGatewayId' --output text)
aws ec2 attach-internet-gateway --vpc-id "$VPC_ID" --internet-gateway-id "$IGW_ID" --region "$AWS_REGION"
echo "✅ Internet Gateway: $IGW_ID"

echo ""
echo "Creating subnets..."
AZ1=$(aws ec2 describe-availability-zones --region "$AWS_REGION" --query 'AvailabilityZones[0].ZoneName' --output text)
AZ2=$(aws ec2 describe-availability-zones --region "$AWS_REGION" --query 'AvailabilityZones[1].ZoneName' --output text)
echo "Using AZs: $AZ1, $AZ2"

PUBLIC_SUBNET_1=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.1.0/24 --availability-zone "$AZ1" \
    --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=agentcore-public-subnet-1}]' \
    --region "$AWS_REGION" --query 'Subnet.SubnetId' --output text)
PUBLIC_SUBNET_2=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.2.0/24 --availability-zone "$AZ2" \
    --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=agentcore-public-subnet-2}]' \
    --region "$AWS_REGION" --query 'Subnet.SubnetId' --output text)
PRIVATE_SUBNET_1=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.11.0/24 --availability-zone "$AZ1" \
    --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=agentcore-private-subnet-1}]' \
    --region "$AWS_REGION" --query 'Subnet.SubnetId' --output text)
PRIVATE_SUBNET_2=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.0.12.0/24 --availability-zone "$AZ2" \
    --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=agentcore-private-subnet-2}]' \
    --region "$AWS_REGION" --query 'Subnet.SubnetId' --output text)
echo "✅ Public: $PUBLIC_SUBNET_1, $PUBLIC_SUBNET_2"
echo "✅ Private: $PRIVATE_SUBNET_1, $PRIVATE_SUBNET_2"

echo ""
echo "Allocating Elastic IP for NAT Gateway..."
EIP_ALLOC_ID=$(aws ec2 allocate-address --domain vpc \
    --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=Name,Value=agentcore-aisle-nat-eip}]' \
    --region "$AWS_REGION" --query 'AllocationId' --output text)
echo "✅ Elastic IP: $EIP_ALLOC_ID"

echo ""
echo "Creating NAT Gateway (this may take a few minutes)..."
NAT_GW_ID=$(aws ec2 create-nat-gateway --subnet-id "$PUBLIC_SUBNET_1" --allocation-id "$EIP_ALLOC_ID" \
    --tag-specifications 'ResourceType=natgateway,Tags=[{Key=Name,Value=agentcore-aisle-nat}]' \
    --region "$AWS_REGION" --query 'NatGateway.NatGatewayId' --output text)
aws ec2 wait nat-gateway-available --nat-gateway-ids "$NAT_GW_ID" --region "$AWS_REGION"
echo "✅ NAT Gateway: $NAT_GW_ID"

echo ""
echo "Creating route tables..."
PUBLIC_RT_ID=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
    --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=agentcore-public-rt}]' \
    --region "$AWS_REGION" --query 'RouteTable.RouteTableId' --output text)
aws ec2 create-route --route-table-id "$PUBLIC_RT_ID" --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW_ID" --region "$AWS_REGION"
aws ec2 associate-route-table --subnet-id "$PUBLIC_SUBNET_1" --route-table-id "$PUBLIC_RT_ID" --region "$AWS_REGION"
aws ec2 associate-route-table --subnet-id "$PUBLIC_SUBNET_2" --route-table-id "$PUBLIC_RT_ID" --region "$AWS_REGION"
echo "✅ Public route table: $PUBLIC_RT_ID"

PRIVATE_RT_ID=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
    --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=agentcore-private-rt}]' \
    --region "$AWS_REGION" --query 'RouteTable.RouteTableId' --output text)
aws ec2 create-route --route-table-id "$PRIVATE_RT_ID" --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "$NAT_GW_ID" --region "$AWS_REGION"
aws ec2 associate-route-table --subnet-id "$PRIVATE_SUBNET_1" --route-table-id "$PRIVATE_RT_ID" --region "$AWS_REGION"
aws ec2 associate-route-table --subnet-id "$PRIVATE_SUBNET_2" --route-table-id "$PRIVATE_RT_ID" --region "$AWS_REGION"
echo "✅ Private route table: $PRIVATE_RT_ID"

echo ""
echo "Creating security group..."
SG_ID=$(aws ec2 create-security-group --group-name agentcore-aisle-sg \
    --description "Security group for AgentCore Aisle runtime" --vpc-id "$VPC_ID" \
    --tag-specifications 'ResourceType=security-group,Tags=[{Key=Name,Value=agentcore-aisle-sg}]' \
    --region "$AWS_REGION" --query 'GroupId' --output text)
aws ec2 authorize-security-group-egress --group-id "$SG_ID" \
    --ip-permissions IpProtocol=-1,FromPort=-1,ToPort=-1,IpRanges='[{CidrIp=0.0.0.0/0}]' \
    --region "$AWS_REGION" 2>/dev/null || true
echo "✅ Security group: $SG_ID"

echo ""
echo "Saving VPC configuration to vpc-config.env..."
cat > vpc-config.env << EOF
# VPC Configuration for AgentCore Runtime — generated by setup-vpc.sh on $(date)
VPC_ID=$VPC_ID
IGW_ID=$IGW_ID
NAT_GW_ID=$NAT_GW_ID
EIP_ALLOC_ID=$EIP_ALLOC_ID
PUBLIC_SUBNET_1=$PUBLIC_SUBNET_1
PUBLIC_SUBNET_2=$PUBLIC_SUBNET_2
PRIVATE_SUBNET_1=$PRIVATE_SUBNET_1
PRIVATE_SUBNET_2=$PRIVATE_SUBNET_2
PUBLIC_RT_ID=$PUBLIC_RT_ID
PRIVATE_RT_ID=$PRIVATE_RT_ID
SG_ID=$SG_ID
AWS_REGION=$AWS_REGION
EOF

echo ""
echo "=========================================="
echo "VPC ready. Next: ./scripts/launch.sh"
echo "=========================================="
