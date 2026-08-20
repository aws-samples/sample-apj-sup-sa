terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Fully configured via: terraform init -backend-config=backend.tfbackend
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}

locals {
  allowed_ips               = var.allowed_ips
  ec2_role_name             = "${var.project_name}-ec2-role"
  instance_profile_name     = "${var.project_name}-instance-profile"
  bedrock_logging_role_name = "${var.project_name}-bedrock-logging-role"
  bedrock_read_policy_name  = "${var.project_name}-ec2-read-bedrock-logs"
  dcv_license_policy_name   = "${var.project_name}-dcv-license-access"
  common_tags = {
    Project = var.project_name
  }
}

# IAM Role for EC2 to access Bedrock
resource "aws_iam_role" "robotics_role" {
  name = local.ec2_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_access" {
  role       = aws_iam_role.robotics_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

# S3 access for Amazon DCV licensing (free on EC2)
resource "aws_iam_role_policy" "dcv_license" {
  name = local.dcv_license_policy_name
  role = aws_iam_role.robotics_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = "arn:aws:s3:::dcv-license.${var.aws_region}/*"
    }]
  })
}

resource "aws_iam_instance_profile" "robotics_profile" {
  name = local.instance_profile_name
  role = aws_iam_role.robotics_role.name
}

# -----------------------------------------------------------------------------
# Bedrock Invocation Logging
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "bedrock_invocations" {
  count             = var.enable_bedrock_invocation_logging ? 1 : 0
  name              = var.bedrock_log_group_name
  retention_in_days = 365
}

resource "aws_iam_role" "bedrock_logging_role" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0
  name  = local.bedrock_logging_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "bedrock.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_logging_policy" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0
  name  = "${var.project_name}-bedrock-logging-cloudwatch"
  role  = aws_iam_role.bedrock_logging_role[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "${aws_cloudwatch_log_group.bedrock_invocations[0].arn}:*"
    }]
  })
}

resource "aws_bedrock_model_invocation_logging_configuration" "main" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0

  logging_config {
    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.bedrock_invocations[0].name
      role_arn       = aws_iam_role.bedrock_logging_role[0].arn
    }
    text_data_delivery_enabled      = true
    image_data_delivery_enabled     = true
    embedding_data_delivery_enabled = false
  }

  depends_on = [aws_iam_role_policy.bedrock_logging_policy]
}

resource "aws_iam_role_policy" "ec2_read_bedrock_logs" {
  count = var.enable_bedrock_invocation_logging ? 1 : 0
  name  = local.bedrock_read_policy_name
  role  = aws_iam_role.robotics_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:FilterLogEvents",
        "logs:GetLogEvents",
        "logs:DescribeLogStreams",
        "logs:DescribeLogGroups",
      ]
      Resource = [
        aws_cloudwatch_log_group.bedrock_invocations[0].arn,
        "${aws_cloudwatch_log_group.bedrock_invocations[0].arn}:*",
      ]
    }]
  })
}

resource "aws_security_group" "robotics_sg" {
  name        = "${var.project_name}-sg"
  description = "Security group for v1 robotics EC2"

  ingress {
    description = "SSH"
    from_port   = var.ssh_port
    to_port     = var.ssh_port
    protocol    = "tcp"
    cidr_blocks = local.allowed_ips
  }

  ingress {
    description = "Amazon DCV"
    from_port   = var.dcv_port
    to_port     = var.dcv_port
    protocol    = "tcp"
    cidr_blocks = local.allowed_ips
  }

  ingress {
    description = "Backend API"
    from_port   = var.api_port
    to_port     = var.api_port
    protocol    = "tcp"
    cidr_blocks = local.allowed_ips
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-sg"
  })
}

resource "aws_instance" "robotics" {
  ami                    = var.isaac_sim_ami[var.aws_region]
  instance_type          = var.instance_type
  key_name               = var.key_name
  iam_instance_profile   = aws_iam_instance_profile.robotics_profile.name
  vpc_security_group_ids = [aws_security_group.robotics_sg.id]

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    iops        = 6000
    throughput  = 500
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data = <<-EOF
    #!/bin/bash
    set -e
    exec > >(tee /var/log/user-data.log) 2>&1
    echo "Starting user-data script at $(date)"

    echo "${base64encode("${var.instance_user}:${var.instance_password}")}" | base64 --decode | chpasswd
    python3 -m pip install boto3

    mkdir -p "${var.project_home}"
    chown -R "${var.instance_user}:${var.instance_user}" "${var.project_home}"

    echo "Setup complete at $(date)"
  EOF

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-instance"
  })
}

resource "aws_eip" "robotics" {
  count    = var.enable_elastic_ip ? 1 : 0
  instance = aws_instance.robotics.id
  domain   = "vpc"

  tags = merge(local.common_tags, {
    Name = "${var.project_name}-eip"
  })
}
