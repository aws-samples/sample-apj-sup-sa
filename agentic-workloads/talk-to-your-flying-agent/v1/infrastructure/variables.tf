variable "aws_region" {
  description = "AWS region - Tokyo/Seoul have Isaac Sim AMI"
  type        = string
  default     = "ap-northeast-1"
}

variable "instance_type" {
  description = "EC2 instance type - g6e required for Isaac Sim AMI"
  type        = string
  default     = "g6e.8xlarge"
}

variable "isaac_sim_ami" {
  description = "Isaac Sim marketplace AMI ID (region-specific)"
  type        = map(string)
  default = {
    "ap-northeast-1" = "ami-0fc5724978e744bb6"
  }
}

variable "root_volume_size" {
  description = "Root volume size in GB (Isaac Sim needs minimum 512GB)"
  type        = number
  default     = 512
}

variable "key_name" {
  description = "SSH key pair name"
  type        = string
}

variable "allowed_ips" {
  description = "List of CIDR blocks allowed SSH / DCV / API access."
  type        = list(string)
}

variable "project_name" {
  description = "Project name for tagging and resource naming"
  type        = string
  default     = "flying-agent-v1"
}

variable "enable_elastic_ip" {
  description = "Create Elastic IP for stable access"
  type        = bool
  default     = true
}

variable "ssh_port" {
  description = "SSH port exposed on the instance security group."
  type        = number
  default     = 22
}

variable "dcv_port" {
  description = "Amazon DCV HTTPS port exposed on the instance security group."
  type        = number
  default     = 8443
}

variable "api_port" {
  description = "Backend API port exposed on the instance security group."
  type        = number
  default     = 8888
}

variable "instance_user" {
  description = "Linux username used for SSH and DCV login."
  type        = string
  default     = "ubuntu"
}

variable "instance_password" {
  description = "Password to set for the instance user, used by DCV login."
  type        = string
  sensitive   = true
}

variable "project_home" {
  description = "Directory created on the instance for the project files."
  type        = string
  default     = "/home/ubuntu/flying-agent-v1"
}

variable "bedrock_log_group_name" {
  description = "CloudWatch log group name for Bedrock invocation logging."
  type        = string
  default     = "/aws/bedrock/modelinvocations"
}
