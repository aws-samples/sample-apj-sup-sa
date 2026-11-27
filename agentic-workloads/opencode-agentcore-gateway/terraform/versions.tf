terraform {
  required_version = ">= 1.9"
  required_providers {
    aws      = { source = "hashicorp/aws", version = "~> 6.69" }
    archive  = { source = "hashicorp/archive", version = "~> 2.7" }
    external = { source = "hashicorp/external", version = "~> 2.3" }
    random   = { source = "hashicorp/random", version = "~> 3.7" }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
  default_tags {
    tags = { Project = "opencode-agentcore-gateway", Component = "gateway" }
  }
}
