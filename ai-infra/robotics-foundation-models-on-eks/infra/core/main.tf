terraform {
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix  = substr(replace(lower("${var.project_name}-${var.environment}"), "/[^a-z0-9-]/", "-"), 0, 40)
  short_name   = substr(local.name_prefix, 0, 24)
  cluster_name = "${local.name_prefix}-eks"
  azs          = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  network_azs  = slice(data.aws_availability_zones.available.names, 0, max(var.az_count, var.karpenter_az_count))

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Reference   = "aws-osmo"
    },
    var.tags
  )
}

resource "aws_kms_key" "osmo" {
  description             = "KMS key for ${local.name_prefix} OSMO reference resources"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "osmo" {
  name          = "alias/${local.name_prefix}-osmo"
  target_key_id = aws_kms_key.osmo.key_id
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.21"

  name = "${local.name_prefix}-vpc"
  cidr = var.vpc_cidr

  azs             = local.network_azs
  private_subnets = [for index, _ in local.network_azs : cidrsubnet(var.vpc_cidr, 4, index)]
  public_subnets  = [for index, _ in local.network_azs : cidrsubnet(var.vpc_cidr, 4, index + 8)]

  enable_dns_hostnames = true
  enable_dns_support   = true
  enable_nat_gateway   = true
  single_nat_gateway   = var.single_nat_gateway

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
    "karpenter.sh/discovery"          = local.cluster_name
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.37"

  cluster_name                    = local.cluster_name
  cluster_version                 = var.eks_cluster_version
  kms_key_deletion_window_in_days = 7

  cluster_endpoint_private_access      = true
  cluster_endpoint_public_access       = length(var.cluster_endpoint_public_access_cidrs) > 0
  cluster_endpoint_public_access_cidrs = var.cluster_endpoint_public_access_cidrs

  enable_cluster_creator_admin_permissions = true

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = slice(module.vpc.private_subnets, 0, var.az_count)
  control_plane_subnet_ids = slice(module.vpc.private_subnets, 0, var.az_count)

  node_security_group_tags = {
    "karpenter.sh/discovery" = local.cluster_name
  }

  node_security_group_additional_rules = {
    efa_self_ingress_all = {
      description = "Node to node all ingress for EFA NCCL and MPI workloads"
      protocol    = "-1"
      from_port   = 0
      to_port     = 0
      type        = "ingress"
      self        = true
    }
    efa_self_egress_all = {
      description = "Node to node all egress for EFA NCCL and MPI workloads"
      protocol    = "-1"
      from_port   = 0
      to_port     = 0
      type        = "egress"
      self        = true
    }
  }

  cluster_addons = {
    coredns = {
      most_recent = true
    }
    eks-pod-identity-agent = {
      most_recent = true
    }
    kube-proxy = {
      most_recent = true
    }
    vpc-cni = {
      before_compute = true
      most_recent    = true
      configuration_values = jsonencode({
        env = {
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
  }

  eks_managed_node_groups = {
    system = {
      name           = "system"
      ami_type       = "AL2023_x86_64_STANDARD"
      capacity_type  = "ON_DEMAND"
      instance_types = var.system_node_instance_types

      min_size     = var.system_node_min_size
      max_size     = var.system_node_max_size
      desired_size = var.system_node_desired_size

      labels = {
        role = "system"
      }

      iam_role_additional_policies = {
        AmazonSSMManagedInstanceCore = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
      }
    }
  }
}

module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "~> 20.37"

  cluster_name          = module.eks.cluster_name
  enable_v1_permissions = true

  create_pod_identity_association = true

  iam_policy_statements = [
    {
      actions   = ["iam:ListInstanceProfiles"]
      resources = ["*"]
    }
  ]

  node_iam_role_use_name_prefix = false
  node_iam_role_name            = "${local.name_prefix}-karpenter-node"
  node_iam_role_additional_policies = {
    AmazonSSMManagedInstanceCore = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }

  tags = local.tags
}

resource "aws_security_group" "postgres" {
  name_prefix = "${local.name_prefix}-postgres-"
  description = "Allow PostgreSQL from EKS worker nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "PostgreSQL from EKS nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "redis" {
  name_prefix = "${local.name_prefix}-redis-"
  description = "Allow Redis from EKS worker nodes"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Redis from EKS nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "random_password" "default_admin" {
  length  = 43
  special = false
}

module "postgres" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.10"

  identifier = "${local.name_prefix}-postgres"

  engine               = "postgres"
  engine_version       = var.postgres_engine_version
  family               = var.postgres_family
  major_engine_version = var.postgres_major_engine_version
  instance_class       = var.postgres_instance_class

  allocated_storage     = var.postgres_allocated_storage
  max_allocated_storage = var.postgres_max_allocated_storage
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.osmo.arn

  db_name  = var.postgres_database_name
  username = var.postgres_username
  password = random_password.postgres.result
  port     = 5432

  manage_master_user_password = false
  multi_az                    = var.reference_ha
  publicly_accessible         = false
  deletion_protection         = var.deletion_protection
  skip_final_snapshot         = true

  create_db_subnet_group = true
  subnet_ids             = slice(module.vpc.private_subnets, 0, var.az_count)
  vpc_security_group_ids = [aws_security_group.postgres.id]

  backup_retention_period = var.reference_ha ? 7 : 1
  apply_immediately       = true

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  create_db_option_group          = false
  create_db_parameter_group       = false
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${local.short_name}-redis"
  subnet_ids = slice(module.vpc.private_subnets, 0, var.az_count)
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${local.short_name}-redis"
  description          = "Redis for OSMO robotics workflows"

  engine         = "redis"
  engine_version = var.redis_engine_version
  node_type      = var.redis_node_type
  port           = 6379

  num_cache_clusters         = var.redis_num_cache_nodes
  automatic_failover_enabled = var.redis_num_cache_nodes > 1
  multi_az_enabled           = var.redis_num_cache_nodes > 1

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis.result
  kms_key_id                 = aws_kms_key.osmo.arn

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]
  apply_immediately  = true
}

resource "aws_s3_bucket" "osmo" {
  bucket        = "${local.short_name}-artifacts-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = var.s3_force_destroy
}

resource "aws_s3_bucket_public_access_block" "osmo" {
  bucket = aws_s3_bucket.osmo.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "osmo" {
  bucket = aws_s3_bucket.osmo.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "osmo" {
  bucket = aws_s3_bucket.osmo.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.osmo.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "osmo" {
  bucket = aws_s3_bucket.osmo.id

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {
      prefix = ""
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_ecr_repository" "workloads" {
  name                 = "${local.name_prefix}-workloads"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.ecr_force_delete

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.osmo.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "workloads" {
  repository = aws_ecr_repository.workloads.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Retain the most recent workload images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 25
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "osmo_assume_role" {
  statement {
    effect = "Allow"

    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.cluster_oidc_issuer_url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.cluster_oidc_issuer_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.osmo_namespace}:${var.osmo_service_account_name}"]
    }
  }
}

resource "aws_iam_role" "osmo_service_account" {
  name               = "${local.name_prefix}-osmo-sa"
  assume_role_policy = data.aws_iam_policy_document.osmo_assume_role.json
}

data "aws_iam_policy_document" "osmo_service_account" {
  statement {
    sid       = "ListOsmoBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.osmo.arn]
  }

  statement {
    sid    = "UseOsmoBucketObjects"
    effect = "Allow"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:ListMultipartUploadParts",
      "s3:PutObject"
    ]
    resources = ["${aws_s3_bucket.osmo.arn}/*"]
  }

  statement {
    sid    = "UseOsmoKmsKey"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo"
    ]
    resources = [aws_kms_key.osmo.arn]
  }
}

resource "aws_iam_policy" "osmo_service_account" {
  name   = "${local.name_prefix}-osmo-sa"
  policy = data.aws_iam_policy_document.osmo_service_account.json
}

resource "aws_iam_role_policy_attachment" "osmo_service_account" {
  role       = aws_iam_role.osmo_service_account.name
  policy_arn = aws_iam_policy.osmo_service_account.arn
}

resource "aws_iam_user" "osmo_workflow_data" {
  name          = "${local.name_prefix}-workflow-data"
  force_destroy = true
}

data "aws_iam_policy_document" "osmo_workflow_data" {
  source_policy_documents = [data.aws_iam_policy_document.osmo_service_account.json]

  statement {
    sid       = "AllowSelfPolicySimulationForOsmoValidation"
    effect    = "Allow"
    actions   = ["iam:SimulatePrincipalPolicy"]
    resources = [aws_iam_user.osmo_workflow_data.arn]
  }
}

resource "aws_iam_access_key" "osmo_workflow_data" {
  user = aws_iam_user.osmo_workflow_data.name
}

resource "aws_iam_user_policy" "osmo_workflow_data" {
  name   = "${local.name_prefix}-workflow-data"
  user   = aws_iam_user.osmo_workflow_data.name
  policy = data.aws_iam_policy_document.osmo_workflow_data.json
}

resource "aws_secretsmanager_secret" "osmo_runtime" {
  name                    = "${local.name_prefix}/osmo/runtime"
  description             = "Runtime connection details for OSMO deployment wrapper"
  kms_key_id              = aws_kms_key.osmo.arn
  recovery_window_in_days = var.secret_recovery_window_in_days
}

resource "aws_secretsmanager_secret_version" "osmo_runtime" {
  secret_id = aws_secretsmanager_secret.osmo_runtime.id
  secret_string = jsonencode({
    postgres_host                   = module.postgres.db_instance_address
    postgres_port                   = module.postgres.db_instance_port
    postgres_database               = var.postgres_database_name
    postgres_username               = var.postgres_username
    postgres_password               = random_password.postgres.result
    redis_host                      = aws_elasticache_replication_group.redis.primary_endpoint_address
    redis_port                      = 6379
    redis_auth_token                = random_password.redis.result
    default_admin_token             = random_password.default_admin.result
    osmo_artifacts_bucket           = aws_s3_bucket.osmo.id
    workflow_data_access_key_id     = aws_iam_access_key.osmo_workflow_data.id
    workflow_data_secret_access_key = aws_iam_access_key.osmo_workflow_data.secret
  })
}
